"""
SciToolkit CLI - Command-line interface for managing scientific agentic toolkits.

This module provides the main CLI commands for creating, validating, publishing,
installing, and managing scientific toolkits.
"""

import click
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn
import sys
import subprocess
import json
from pathlib import Path
from datetime import datetime
import yaml
import tarfile
import tempfile
import requests
import shutil

console = Console()


# ── Agent-friendliness: --yes / --no / --no-input across all commands ──────
#
# Per STATUS.md §"Named principles" (flag-equivalence) and the Tier-1 polish
# pass: every interactive prompt must have a flag-driven equivalent so the
# CLI is usable from coding agents and CI without a TTY.
#
# Conventions:
# - ``--yes`` / ``-y``: answer Yes to all confirms.
# - ``--no``: answer No to all confirms.
# - ``--no-input``: skip prompts entirely. Use the prompt's stated default
#   for confirms; for required text prompts, fail with a clear error
#   pointing at the flag that bypasses it.
# - Non-TTY stdin implicitly sets ``--no-input`` (per manager Q3 answer).
#
# Mutually exclusive: at most one of --yes / --no / --no-input may be set.


def _interactive_options(f):
    """Decorator: add --yes/-y, --no, --no-input to a Click command.

    Apply via ``@_interactive_options`` above other decorators. The flags are
    surfaced as kwargs ``yes``, ``no``, ``no_input`` on the command function.
    Pass them into ``_resolve_prompt_mode()`` to get a single resolved mode.
    """
    f = click.option(
        "--no-input", "no_input", is_flag=True, default=False,
        help="Don't prompt; use defaults or fail. Implied when stdin is not a TTY.",
    )(f)
    f = click.option(
        "--no", "no_", is_flag=True, default=False,
        help="Answer No to all confirmation prompts.",
    )(f)
    f = click.option(
        "-y", "--yes", "yes", is_flag=True, default=False,
        help="Answer Yes to all confirmation prompts.",
    )(f)
    return f


def _resolve_prompt_mode(yes: bool, no_: bool, no_input: bool) -> str:
    """Reduce the three flags + TTY status to a single mode.

    Returns one of:
        "yes"   — accept any confirm
        "no"    — decline any confirm
        "skip"  — non-interactive: confirms use their default; required text
                  prompts fail with a flag-pointing error
        "ask"   — interactive prompt (default in a TTY)
    """
    flags_set = sum(int(b) for b in (yes, no_, no_input))
    if flags_set > 1:
        raise click.UsageError(
            "--yes, --no, and --no-input are mutually exclusive."
        )
    if yes:
        return "yes"
    if no_:
        return "no"
    if no_input:
        return "skip"
    if not sys.stdin.isatty():
        return "skip"
    return "ask"


def _confirm(
    message: str,
    *,
    default: bool,
    mode: str,
    consequential: bool = False,
) -> bool:
    """Confirmation prompt that honors the resolved interactive mode.

    ``consequential=True`` flips the ``skip`` mode's behavior: instead of
    using the prompt's stated default, we treat skip as "no" (refuse to do
    a destructive thing implicitly). Use for deletes, replacements, and
    other irreversible actions.
    """
    if mode == "yes":
        return True
    if mode == "no":
        return False
    if mode == "skip":
        # Consequential prompts never auto-yes in skip mode, even if their
        # interactive default is True. Benign prompts use their default.
        if consequential:
            return False
        return default
    return click.confirm(message, default=default)


def _format_bytes(n: int) -> str:
    """Render a byte count in the largest unit that keeps it >= 1.

    Avoids "0.0 MB" for kilobyte-scale tarballs and "1234567.8 kB" for
    multi-MB ones. Uses 1024-based units throughout (B, kB, MB, GB).
    """
    n = int(n)
    if n < 1024:
        return f"{n} B"
    if n < 1024 ** 2:
        return f"{n / 1024:.1f} kB"
    if n < 1024 ** 3:
        return f"{n / (1024 ** 2):.2f} MB"
    return f"{n / (1024 ** 3):.2f} GB"


def _require_input(
    label: str,
    *,
    mode: str,
    bypass_flag: str,
    hide_input: bool = False,
) -> str:
    """Required text prompt. In skip mode, error with a flag pointer.

    Use for inputs that have no sensible default (toolkit name, auth
    token). The error names the flag that supplies the value
    non-interactively.
    """
    if mode == "skip":
        raise click.UsageError(
            f"{label} is required. Pass {bypass_flag} when running "
            "non-interactively."
        )
    return click.prompt(label, hide_input=hide_input)


class _SectionedGroup(click.Group):
    """Click group whose ``--help`` renders commands in named sections.

    Each command's name is checked against ``COMMAND_SECTIONS``; matched
    commands appear under their section header, anything unmatched falls
    through to a generic "Other commands" tail. This keeps the help text
    scannable as the CLI grows past the eight-or-nine-command mark where
    a flat alphabetical list stops being useful.
    """

    COMMAND_SECTIONS = [
        (
            "Authoring & publishing",
            ["init", "validate", "login", "publish"],
        ),
        (
            "Installing & serving",
            ["search", "install", "uninstall", "list", "serve", "logs", "groups"],
        ),
    ]

    def format_commands(self, ctx, formatter):
        commands = {name: self.get_command(ctx, name) for name in self.list_commands(ctx)}
        commands = {n: c for n, c in commands.items() if c is not None and not c.hidden}

        seen: set[str] = set()
        for header, names in self.COMMAND_SECTIONS:
            rows = []
            for name in names:
                cmd = commands.get(name)
                if cmd is None:
                    continue
                seen.add(name)
                rows.append((name, cmd.get_short_help_str(limit=120)))
            if rows:
                with formatter.section(header):
                    formatter.write_dl(rows)

        # Anything not pre-classified ends up here so a future-added command
        # is still discoverable even before this list is updated.
        rows = [
            (name, cmd.get_short_help_str(limit=120))
            for name, cmd in commands.items()
            if name not in seen
        ]
        if rows:
            with formatter.section("Other commands"):
                formatter.write_dl(rows)


@click.group(cls=_SectionedGroup)
@click.version_option(version="0.1.0", prog_name="scitoolkit")
def main():
    """
    SciToolkit - Scientific agentic tools made easy

    A platform for creating, publishing, and using AI tools for science.
    """
    pass


@main.command()
@click.argument('name', required=False)
@click.option('--path', '-p', default=None, help='Directory to create toolkit in')
@click.option('--with-docker', is_flag=True, help='Include Dockerfile template')
@_interactive_options
def init(name, path, with_docker, yes, no_, no_input):
    """
    Initialize a new toolkit from template.

    If the toolkit exists in the registry, pre-fills metadata.
    Otherwise, creates a fresh template.

    Creates a new toolkit directory with the standard structure:
    - toolkit.yaml (metadata)
    - tools/ (tool definitions)
    - skills/ (skill guides)
    - requirements.txt (dependencies)
    - README.md (documentation)
    - Dockerfile (optional, if --with-docker is used)

    Example:
        scitoolkit init my-awesome-toolkit
        scitoolkit init my-toolkit --with-docker
    """
    from .toolkit import create_toolkit_from_template
    import requests

    mode = _resolve_prompt_mode(yes, no_, no_input)

    # Interactive mode if no name provided
    if not name:
        if mode != "skip":
            console.print(Panel.fit(
                "[bold cyan]SciToolkit Initialization[/bold cyan]\n"
                "Let's create your new toolkit!",
                border_style="cyan"
            ))
        name = _require_input("Toolkit name", mode=mode, bypass_flag="NAME (positional argument)")

    # Check if toolkit exists in registry
    api_url = "https://api.scitoolkit.org"
    registry_metadata = None

    try:
        console.print(f"Checking if '{name}' exists in registry...")
        response = requests.get(f"{api_url}/api/toolkits/{name}", timeout=5)

        if response.status_code == 200:
            registry_metadata = response.json()
            latest_version = registry_metadata.get('latest_version', 'unknown')
            console.print(f"[green]✓ Found {name} in registry (v{latest_version})[/green]")
            console.print("Pre-filling metadata from registry...")
        elif response.status_code == 404:
            console.print(f"[dim]Toolkit not found in registry. Creating new template...[/dim]")
        else:
            console.print(f"[yellow]Could not check registry (status {response.status_code})[/yellow]")
    except requests.exceptions.RequestException as e:
        console.print(f"[yellow]Could not connect to registry: {e}[/yellow]")
        console.print("Creating new template...")

    # Determine target path
    target_path = Path(path) if path else Path.cwd() / name

    try:
        toolkit_path = create_toolkit_from_template(
            name=name,
            path=target_path,
            with_docker=with_docker,
            registry_metadata=registry_metadata
        )

        console.print(f"\n[bold green]✓[/bold green] Toolkit created at: [cyan]{toolkit_path}[/cyan]")

        if registry_metadata:
            console.print("\n[bold]Next steps:[/bold]")
            console.print(f"  1. cd {name}")
            console.print("  2. Add your tools in the tools/ directory")
            console.print("  3. Run 'scitoolkit validate'")
            console.print(f"  4. Run 'scitoolkit login {name}' with your token")
            console.print("  5. Run 'scitoolkit publish'")
        else:
            console.print("\n[bold]Next steps:[/bold]")
            console.print(f"  1. cd {name}")
            console.print("  2. Create toolkit on https://scitoolkit.org")
            console.print("  3. Edit toolkit.yaml with your details")
            console.print("  4. Add your tools in the tools/ directory")
            console.print(f"  5. Run 'scitoolkit login {name}'")
            console.print("  6. Run 'scitoolkit validate' and then 'scitoolkit publish'")

    except Exception as e:
        console.print(f"[bold red]✗[/bold red] Error creating toolkit: {e}", style="red")
        sys.exit(1)


@main.command()
@click.argument('path', required=False, default='.')
def validate(path):
    """
    Validate a toolkit's structure and configuration.

    Checks:
    - scitoolkit.yaml exists and is valid
    - Required files are present
    - Tool definitions are valid
    - Dependencies can be parsed

    Example:
        scitoolkit validate
        scitoolkit validate ./my-toolkit
    """
    from .validation import validate_toolkit

    toolkit_path = Path(path).resolve()

    console.print(Panel.fit(
        f"[bold cyan]Validating toolkit at:[/bold cyan]\n{toolkit_path}",
        border_style="cyan"
    ))

    try:
        result = validate_toolkit(toolkit_path)

        if result.is_valid:
            console.print("\n[bold green]✓ Toolkit is valid![/bold green]")

            # Show summary
            table = Table(title="Toolkit Summary", show_header=False)
            table.add_column("Field", style="cyan")
            table.add_column("Value", style="white")

            table.add_row("Name", result.metadata.name)
            table.add_row("Version", result.metadata.version)
            table.add_row("Author", result.metadata.author)
            table.add_row("Tools", str(len(result.metadata.tools)))

            console.print(table)
        else:
            console.print("\n[bold red]✗ Validation failed[/bold red]")
            for error in result.errors:
                console.print(f"  [red]•[/red] {error}")

            # Show warnings (helpful hints)
            if result.warnings:
                console.print()
                for warning in result.warnings:
                    console.print(f"  [yellow]hint:[/yellow] {warning}")

            sys.exit(1)

    except Exception as e:
        console.print(f"\n[bold red]✗[/bold red] Error during validation: {e}", style="red")
        sys.exit(1)


@main.command()
@click.argument('toolkit_name')
@click.option(
    '--token', 'token_flag', default=None,
    help='Provide the publish token non-interactively (for agents and CI).',
)
@_interactive_options
def login(toolkit_name, token_flag, yes, no_, no_input):
    """
    Authenticate for publishing a specific toolkit.

    Prompts for the toolkit's publish token and stores it securely.
    Get your toolkit token from https://scitoolkit.org after creating the toolkit.

    \b
    Example:
        scitoolkit login my-toolkit
        scitoolkit login my-toolkit --token toolkit_abc...
    """
    mode = _resolve_prompt_mode(yes, no_, no_input)

    if token_flag is not None:
        token = token_flag
    else:
        if mode != "skip":
            console.print(f"\n[bold blue]Authenticating for toolkit: {toolkit_name}[/bold blue]\n")
            console.print("Get your toolkit token from: [link]https://scitoolkit.org[/link]")
            console.print(f"(Create the toolkit '{toolkit_name}' first, then copy its publish token)\n")
        token = _require_input(
            "Enter your toolkit token",
            mode=mode,
            bypass_flag="--token",
            hide_input=True,
        )

    if not token.startswith('toolkit_'):
        console.print("[yellow]Warning: token should start with 'toolkit_'[/yellow]")
        if not _confirm("Continue anyway?", default=True, mode=mode):
            sys.exit(0)

    # Create directory for this toolkit
    from .config import CONFIG_DIR
    config_dir = CONFIG_DIR / toolkit_name
    config_dir.mkdir(parents=True, exist_ok=True)

    # Store token
    token_file = config_dir / 'token'
    token_file.write_text(token)

    # Set secure permissions (owner read/write only)
    import os
    os.chmod(token_file, 0o600)

    console.print(f"\n[green]✓ Token stored at: {token_file}[/green]")
    console.print(f"\nYou can now run 'scitoolkit publish' from the {toolkit_name} directory.")


@main.command()
@click.option('--dry-run', is_flag=True, help='Validate without uploading')
@click.option(
    '--allow-version-decrease', 'allow_decrease', is_flag=True, default=False,
    help=(
        'Allow publishing a version lower than the latest already on the '
        'registry. Use only when you know what you are doing — most users '
        'should bump the version forward.'
    ),
)
def publish(dry_run, allow_decrease):
    """
    Publish toolkit to SciToolkit registry.

    Packages the current directory as a tarball and uploads it to the registry.
    Requires a valid toolkit token stored via 'scitoolkit login {toolkit_name}'.

    \b
    Example:
        scitoolkit publish
        scitoolkit publish --dry-run  # Test without uploading
    """
    console.print("\n[bold blue]Publishing toolkit to SciToolkit registry...[/bold blue]\n")

    # Step 1: Find and read toolkit.yaml
    yaml_path = Path.cwd() / 'toolkit.yaml'
    if not yaml_path.exists():
        console.print("[red]✗ Error: toolkit.yaml not found in current directory[/red]")
        console.print("Make sure you're in the toolkit root directory.")
        sys.exit(1)

    try:
        with open(yaml_path, 'r') as f:
            config = yaml.safe_load(f)
    except Exception as e:
        console.print(f"[red]✗ Error reading toolkit.yaml: {e}[/red]")
        sys.exit(1)

    toolkit_name = config.get('name')
    version = config.get('version')

    if not toolkit_name or not version:
        console.print("[red]✗ Error: toolkit.yaml must contain 'name' and 'version' fields[/red]")
        sys.exit(1)

    console.print(f"Toolkit: [bold]{toolkit_name}[/bold]")
    console.print(f"Version: [bold]{version}[/bold]\n")

    # Step 2: Validate toolkit structure
    console.print("Validating toolkit structure...")

    from .validation import validate_toolkit

    result = validate_toolkit(Path.cwd())
    if not result.is_valid:
        console.print("[red]✗ Validation failed:[/red]")
        for error in result.errors:
            console.print(f"  [red]•[/red] {error}")
        console.print("\nRun 'scitoolkit validate' for details.")
        sys.exit(1)

    console.print("[green]✓ Toolkit structure is valid[/green]\n")

    # Step 2b: Pre-flight version check against the registry. Catches the
    # two common ways to get bounced at upload: re-publishing an existing
    # version, and accidentally regressing the version number. Skip for
    # --dry-run (offline) and when the user explicitly asked for a
    # decrease via --allow-version-decrease.
    if not dry_run:
        from .versioning import is_strictly_greater, max_version, suggest_next_version
        api_url_check = "https://api.scitoolkit.org"
        try:
            r = requests.get(
                f"{api_url_check}/api/toolkits/{toolkit_name}", timeout=5,
            )
        except requests.exceptions.RequestException:
            r = None
        if r is not None and r.status_code == 200:
            try:
                tk_meta = r.json()
                versions = [
                    v.get("version") for v in tk_meta.get("versions") or []
                    if isinstance(v, dict) and v.get("version")
                ]
            except Exception:
                versions = []
            if version in versions:
                suggested = suggest_next_version(version) or "<bumped version>"
                console.print(
                    f"[red]✗ Version {version} already exists on the "
                    f"registry for {toolkit_name}.[/red]"
                )
                console.print(
                    f"Bump the version in [cyan]toolkit.yaml[/cyan] "
                    f"(e.g. [bold]version: {suggested}[/bold]) and re-run."
                )
                sys.exit(1)
            latest = max_version(versions)
            if latest:
                gt = is_strictly_greater(version, latest)
                if gt is False and not allow_decrease:
                    console.print(
                        f"[red]✗ Version {version} is not greater than "
                        f"the latest published version ({latest}).[/red]"
                    )
                    console.print(
                        "Pass [cyan]--allow-version-decrease[/cyan] if you "
                        "really need to publish an older version, or bump "
                        "the version in [cyan]toolkit.yaml[/cyan]."
                    )
                    sys.exit(1)
                if gt is False and allow_decrease:
                    # Telemetry: how often does this escape hatch fire?
                    # If never, deprecate the flag. If often, the rule
                    # was wrong.
                    from .logging.logger import get_logger
                    get_logger().log_event(
                        event="version_decrease_allowed",
                        toolkit=toolkit_name,
                        message=f"publishing {version} over latest {latest}",
                        level="warn",
                        from_version=latest,
                        to_version=version,
                    )
                    console.print(
                        f"[yellow]⚠[/yellow] Publishing {version} which is "
                        f"older than the registry's latest ({latest}). "
                        "Logged for telemetry."
                    )
                if gt is None:
                    # Unparseable; not our place to block, registry will
                    # decide. Just warn.
                    console.print(
                        f"[yellow]Could not compare version {version} "
                        f"with registry's {latest} — proceeding anyway.[/yellow]"
                    )
        # If the request failed or returned non-200, fall through silently.
        # The registry itself is the final authority — it will reject on
        # upload if there's a real conflict.

    # Step 3: Create tarball. We do this before reading the token so that
    # `--dry-run` (whose whole purpose is "test the package without
    # uploading") doesn't require the user to have authenticated yet.
    console.print("Creating tarball...")

    tarball_name = f"{toolkit_name}-{version}.tar.gz"
    tarball_path = Path(tempfile.gettempdir()) / tarball_name

    try:
        create_tarball(Path.cwd(), tarball_path, toolkit_name)
        console.print(
            f"[green]✓ Created {tarball_name} "
            f"({_format_bytes(tarball_path.stat().st_size)})[/green]\n"
        )
    except Exception as e:
        console.print(f"[red]✗ Error creating tarball: {e}[/red]")
        sys.exit(1)

    if dry_run:
        console.print("[yellow]Dry run mode — skipping auth and upload[/yellow]")
        console.print(f"Tarball created at: {tarball_path}")
        console.print("\nTo publish for real, run: [cyan]scitoolkit publish[/cyan]")
        return

    # Step 4: Read authentication token (real publishes only).
    from .config import CONFIG_DIR
    token_path = CONFIG_DIR / toolkit_name / 'token'

    if not token_path.exists():
        console.print(f"[red]✗ Error: No authentication token found for '{toolkit_name}'[/red]")
        console.print(f"\nRun 'scitoolkit login {toolkit_name}' to authenticate.")
        sys.exit(1)

    try:
        token = token_path.read_text().strip()
    except Exception as e:
        console.print(f"[red]✗ Error reading token: {e}[/red]")
        sys.exit(1)

    console.print(f"Using token from: [dim]{token_path}[/dim]\n")

    # Step 5: Upload to backend
    console.print("Uploading to registry...")

    api_url = "https://api.scitoolkit.org"
    upload_url = f"{api_url}/api/toolkits/{toolkit_name}/publish"

    try:
        with open(tarball_path, 'rb') as f:
            files = {'file': (tarball_name, f, 'application/gzip')}
            headers = {'Authorization': f'Bearer {token}'}

            # Show progress
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console
            ) as progress:
                task = progress.add_task("[cyan]Uploading...", total=None)

                response = requests.post(
                    upload_url,
                    files=files,
                    headers=headers,
                    timeout=300  # 5 minutes
                )

        # Clean up temp file
        tarball_path.unlink()

        if response.status_code == 201:
            data = response.json()
            console.print("\n[bold green]✓ Successfully published![/bold green]\n")
            console.print(f"Toolkit:   {data['toolkit_name']}")
            console.print(f"Version:   {data['version']}")
            console.print(f"Size:      {data['file_size'] / (1024*1024):.2f} MB")
            console.print(f"Published: {data['published_at']}")
            console.print(f"\nView at: [link]https://scitoolkit.org/toolkit/{toolkit_name}[/link]")

        elif response.status_code == 409:
            from .versioning import suggest_next_version
            console.print(
                f"\n[red]✗ Version {version} already exists for {toolkit_name}.[/red]"
            )
            suggested = suggest_next_version(version)
            if suggested:
                console.print(
                    f"Bump the version in [cyan]toolkit.yaml[/cyan] "
                    f"(e.g. [bold]version: {suggested}[/bold]) and re-run "
                    "[cyan]scitoolkit publish[/cyan]."
                )
            else:
                console.print(
                    "Bump the version in [cyan]toolkit.yaml[/cyan] and "
                    "re-run [cyan]scitoolkit publish[/cyan]."
                )
            console.print(
                "[dim]Tip: run `scitoolkit validate` before publishing — "
                "it catches version-already-exists locally.[/dim]"
            )
            sys.exit(1)

        elif response.status_code == 401:
            console.print("\n[red]✗ Authentication failed. Invalid token.[/red]")
            console.print(f"Run 'scitoolkit login {toolkit_name}' to re-authenticate.")
            sys.exit(1)

        else:
            try:
                error = response.json()
                error_msg = error.get('detail', 'Unknown error')
            except:
                error_msg = response.text or 'Unknown error'

            console.print(f"\n[red]✗ Upload failed: {error_msg}[/red]")
            console.print(f"Status code: {response.status_code}")
            sys.exit(1)

    except requests.exceptions.RequestException as e:
        console.print(f"\n[red]✗ Network error: {e}[/red]")
        console.print("Please check your internet connection and try again.")
        sys.exit(1)
    except Exception as e:
        console.print(f"\n[red]✗ Unexpected error: {e}[/red]")
        import traceback
        console.print(f"[dim]{traceback.format_exc()}[/dim]")
        sys.exit(1)


@main.command()
@click.argument('query', required=False)
@click.option('--category', '-c', help='Filter by category (astro, hep, quantum, etc.)')
def search(query, category):
    """
    Search for toolkits in the registry.

    Example:
        scitoolkit search exoplanet
        scitoolkit search --category astro
        scitoolkit search transit --category astro
    """
    console.print("[yellow]The search command is not yet implemented.[/yellow]")
    console.print("This will be added in Phase 3 of the development.")
    sys.exit(1)


def get_current_python() -> str:
    """
    Get current Python version in 'X.Y' format.

    Returns:
        str: Python version (e.g., '3.11')
    """
    return f"{sys.version_info.major}.{sys.version_info.minor}"


def has_conda() -> bool:
    """
    Check if conda or mamba is available.

    Returns:
        bool: True if conda/mamba is available
    """
    return shutil.which('conda') is not None or shutil.which('mamba') is not None


def load_toolkit_yaml(toolkit_path: Path) -> dict:
    """
    Load and parse toolkit.yaml.

    Args:
        toolkit_path: Path to toolkit directory

    Returns:
        dict: Parsed toolkit configuration

    Raises:
        FileNotFoundError: If toolkit.yaml doesn't exist
        yaml.YAMLError: If YAML parsing fails
    """
    yaml_path = toolkit_path / 'toolkit.yaml'
    if not yaml_path.exists():
        raise FileNotFoundError(f"Missing toolkit.yaml in {toolkit_path}")

    with open(yaml_path) as f:
        return yaml.safe_load(f)


def detect_environment_type(toolkit_path: Path, config: dict) -> tuple:
    """
    Detect the appropriate environment type for a toolkit.

    Implements auto-detection logic:
    - Explicit docker_required: true → docker
    - Has Dockerfile → docker
    - Different Python version + conda available → conda
    - Different Python version + no conda → docker (with warning)
    - Same Python version → venv

    Args:
        toolkit_path: Path to toolkit directory
        config: Parsed toolkit.yaml configuration

    Returns:
        tuple: (env_type, python_version)
            env_type: 'venv', 'conda', or 'docker'
            python_version: Required Python version (e.g., '3.11')
    """
    env_config = config.get('environment', {})

    # 1. Explicit docker requirement
    if env_config.get('docker_required'):
        python_version = env_config.get('python', get_current_python())
        return ('docker', python_version)

    # 2. Has Dockerfile
    if (toolkit_path / 'Dockerfile').exists():
        python_version = env_config.get('python', get_current_python())
        return ('docker', python_version)

    # 3. Check Python version requirement
    required_py = env_config.get('python', get_current_python())
    current_py = get_current_python()

    if required_py != current_py:
        # Different Python version needed
        if has_conda():
            return ('conda', required_py)
        else:
            # No conda available, will need Docker
            return ('docker', required_py)

    # 4. Default: venv (same Python version, pure Python deps)
    return ('venv', current_py)


def _run_pip_with_progress(
    cmd: list,
    console: Console,
    label: str,
) -> None:
    """Run a pip subprocess, streaming live status messages from its output.

    Pip emits ``Collecting <pkg>`` and ``Installing collected packages: ...``
    lines on stdout. We stream those into a Rich ``Status`` spinner so the
    user can see what's happening during long installs (otherwise the step
    looks frozen for 30+ seconds while building wheels).

    Pip has no machine-readable progress; this is best-effort cosmetic. Full
    output is buffered and replayed on failure so the diagnostic is still
    available.
    """
    import re

    collecting_re = re.compile(r"^Collecting\s+([A-Za-z0-9._\-]+)")
    installing_re = re.compile(r"^Installing collected packages:\s*(.+)$")
    building_re = re.compile(r"^Building wheel for\s+([A-Za-z0-9._\-]+)")

    captured: list[str] = []
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    with console.status(f"[bold blue]{label}...") as status:
        assert proc.stdout is not None
        for line in proc.stdout:
            captured.append(line)
            line = line.rstrip()
            if not line:
                continue
            m = collecting_re.match(line)
            if m:
                status.update(f"[bold blue]{label}: collecting {m.group(1)}...")
                continue
            m = building_re.match(line)
            if m:
                status.update(f"[bold blue]{label}: building wheel for {m.group(1)}...")
                continue
            m = installing_re.match(line)
            if m:
                pkgs = m.group(1).strip()
                # Truncate long lists so the status line doesn't wrap.
                if len(pkgs) > 60:
                    pkgs = pkgs[:57] + "..."
                status.update(f"[bold blue]{label}: installing {pkgs}...")
                continue

    rc = proc.wait()
    if rc != 0:
        # Replay the captured output so the user sees pip's actual error.
        console.print("".join(captured))
        raise subprocess.CalledProcessError(rc, cmd, output="".join(captured))


def setup_venv_environment(toolkit_path: Path, console: Console) -> Path:
    """
    Create virtual environment and install dependencies.

    Args:
        toolkit_path: Path to toolkit directory
        console: Rich console for output

    Returns:
        Path: Path to Python executable in venv

    Raises:
        subprocess.CalledProcessError: If venv creation or pip install fails
    """
    venv_path = toolkit_path / '.venv'
    requirements_path = toolkit_path / 'requirements.txt'

    # Create venv
    with console.status("[bold blue]Creating virtual environment..."):
        subprocess.run(
            [sys.executable, '-m', 'venv', str(venv_path)],
            check=True,
            capture_output=True
        )
    console.print("[green]✓ Virtual environment created[/green]")

    # Get pip and python paths (platform-specific)
    if sys.platform == 'win32':
        pip_path = venv_path / 'Scripts' / 'pip.exe'
        python_path = venv_path / 'Scripts' / 'python.exe'
    else:
        pip_path = venv_path / 'bin' / 'pip'
        python_path = venv_path / 'bin' / 'python'

    # Upgrade pip first
    with console.status("[bold blue]Upgrading pip..."):
        subprocess.run(
            [str(pip_path), 'install', '--upgrade', 'pip', '--quiet'],
            check=True,
            capture_output=True
        )

    # Install dependencies from requirements.txt
    if requirements_path.exists():
        _run_pip_with_progress(
            [str(pip_path), 'install', '-r', str(requirements_path)],
            console,
            "Installing dependencies",
        )
        console.print("[green]✓ Dependencies installed[/green]")
    else:
        console.print("[dim]No requirements.txt — toolkit has no dependencies[/dim]")

    # Install orchestral-ai + mcp SDK. Both are required: orchestral provides
    # the @define_tool decorator and tool plumbing; mcp is what the per-toolkit
    # subprocess (scitoolkit serve's host) uses to expose tools over HTTP.
    _run_pip_with_progress(
        [str(pip_path), 'install', 'orchestral-ai', 'mcp'],
        console,
        "Installing orchestral-ai and mcp",
    )
    console.print("[green]✓ Orchestral + MCP SDK installed[/green]")

    return python_path


def verify_conda_available():
    """
    Check if conda or mamba is available.

    Raises:
        click.ClickException: If conda/mamba not found
    """
    if not has_conda():
        raise click.ClickException(
            "Conda/Mamba not found!\n\n"
            "This toolkit requires a different Python version.\n"
            "Please install conda or mamba:\n"
            "  - Miniconda: https://docs.conda.io/en/latest/miniconda.html\n"
            "  - Mamba: https://mamba.readthedocs.io/\n\n"
            "Alternatively, Docker mode (Phase 3B) will support this toolkit."
        )


def cleanup_conda_environment(env_name: str):
    """
    Remove conda environment if it exists.

    Args:
        env_name: Name of conda environment to remove
    """
    conda_cmd = 'mamba' if shutil.which('mamba') else 'conda'
    try:
        subprocess.run(
            [conda_cmd, 'env', 'remove', '-n', env_name, '-y', '--quiet'],
            capture_output=True
        )
    except Exception:
        pass  # Best effort cleanup


def setup_conda_environment(
    toolkit_path: Path,
    toolkit_name: str,
    python_version: str,
    console: Console
) -> str:
    """
    Create conda environment and install dependencies.

    Args:
        toolkit_path: Path to toolkit directory
        toolkit_name: Name of toolkit
        python_version: Required Python version (e.g., '3.9')
        console: Rich console for output

    Returns:
        str: Conda environment name

    Raises:
        subprocess.CalledProcessError: If conda commands fail
    """
    env_name = f"scitoolkit-{toolkit_name}"
    requirements_path = toolkit_path / 'requirements.txt'

    # Prefer mamba (faster) if available, fallback to conda
    conda_cmd = 'mamba' if shutil.which('mamba') else 'conda'

    with console.status(f"[bold blue]Creating conda environment '{env_name}'..."):
        # Create conda environment with specific Python version
        try:
            subprocess.run(
                [conda_cmd, 'create', '-n', env_name, f'python={python_version}', '-y', '--quiet'],
                check=True,
                capture_output=True,
                text=True
            )
        except subprocess.CalledProcessError as e:
            console.print(f"[red]✗ Failed to create conda environment[/red]")
            if e.stderr:
                console.print(f"[red]Error: {e.stderr[:500]}[/red]")
            raise

    console.print(f"[green]✓ Conda environment '{env_name}' created (Python {python_version})[/green]")

    # Install dependencies from requirements.txt
    if requirements_path.exists():
        try:
            _run_pip_with_progress(
                [conda_cmd, 'run', '-n', env_name, 'pip', 'install',
                 '-r', str(requirements_path)],
                console,
                f"Installing dependencies in '{env_name}'",
            )
            console.print("[green]✓ Dependencies installed[/green]")
        except subprocess.CalledProcessError:
            console.print(f"[yellow]Some dependencies failed to install[/yellow]")
            # Don't raise - might be non-critical
    else:
        console.print("[dim]No requirements.txt found[/dim]")

    # Install orchestral-ai + mcp SDK (see venv setup for rationale).
    try:
        _run_pip_with_progress(
            [conda_cmd, 'run', '-n', env_name, 'pip', 'install',
             'orchestral-ai', 'mcp'],
            console,
            f"Installing orchestral + mcp in '{env_name}'",
        )
    except subprocess.CalledProcessError:
        console.print(f"[red]✗ Failed to install orchestral/mcp[/red]")
        raise

    console.print("[green]✓ Orchestral + MCP SDK installed[/green]")

    return env_name


@main.command()
@click.argument('name')
@click.option('--version', '-v', help='Specific version to install (default: latest)')
@click.option(
    '--no-skills', 'no_skills', is_flag=True, default=False,
    help="Don't surface the toolkit's skills into ~/.claude/skills/.",
)
@_interactive_options
def install(name, version, no_skills, yes, no_, no_input):
    """
    Install a toolkit from the registry.

    \b
    This will:
      1. Download the toolkit (latest, or --version <v>)
      2. Create an isolated environment (venv or conda, auto-detected)
      3. Install dependencies, then orchestral-ai + mcp
      4. Surface the toolkit's skills into ~/.claude/skills/ (unless --no-skills)

    \b
    Examples:
        scitoolkit install aster                   # latest
        scitoolkit install aster --version 1.2.0   # pin a version
        scitoolkit install aster -v 1.2.0          # short alias
        scitoolkit install aster --no-skills       # don't touch ~/.claude/skills/
    """
    mode = _resolve_prompt_mode(yes, no_, no_input)

    console.print(f"\n[bold blue]Installing toolkit: {name}[/bold blue]\n")

    # Step 1: Fetch toolkit metadata from registry
    console.print("Fetching toolkit metadata...")

    api_url = "https://api.scitoolkit.org"

    try:
        response = requests.get(f"{api_url}/api/toolkits/{name}", timeout=10)

        if response.status_code == 404:
            console.print(f"[red]✗ Toolkit '{name}' not found in registry[/red]")
            console.print("\nSearch for toolkits: [cyan]scitoolkit search {query}[/cyan]")
            sys.exit(1)

        if response.status_code != 200:
            console.print(f"[red]✗ Error fetching metadata (status {response.status_code})[/red]")
            sys.exit(1)

        toolkit_meta = response.json()

        # Determine version to install
        if not version:
            version = toolkit_meta.get('latest_version')
            if not version:
                console.print(f"[red]✗ Toolkit has no published versions[/red]")
                sys.exit(1)
            console.print(f"[green]✓ Found {name} v{version} (latest)[/green]")
        else:
            # Verify version exists.
            available_versions = toolkit_meta.get('versions', [])
            version_numbers = [
                v.get('version') for v in available_versions
                if isinstance(v, dict) and v.get('version')
            ]
            if version not in version_numbers:
                console.print(f"[red]✗ Version {version} not found for {name}.[/red]")
                if version_numbers:
                    # Show newest first; users almost always want recent.
                    from .versioning import parse_version
                    sorted_versions = sorted(
                        version_numbers,
                        key=lambda v: parse_version(v) or (0, 0, 0),
                        reverse=True,
                    )
                    shown = sorted_versions[:5]
                    extra = (
                        f" (and {len(sorted_versions) - 5} older)"
                        if len(sorted_versions) > 5 else ""
                    )
                    console.print(
                        f"Available versions: {', '.join(shown)}{extra}"
                    )
                    console.print(
                        f"\nInstall the latest with: "
                        f"[cyan]scitoolkit install {name}[/cyan]"
                    )
                else:
                    console.print(f"This toolkit has no published versions yet.")
                sys.exit(1)
            console.print(f"[green]✓ Found {name} v{version}[/green]")

    except requests.exceptions.RequestException as e:
        console.print(f"[red]✗ Network error: {e}[/red]")
        sys.exit(1)

    # Step 2: Check if already installed
    from .config import TOOLKITS_DIR
    toolkit_dir = TOOLKITS_DIR / name

    if toolkit_dir.exists():
        # Check installed version
        meta_file = toolkit_dir / '.stk_meta.json'
        if meta_file.exists():
            try:
                with open(meta_file) as f:
                    installed_meta = json.load(f)
                    installed_version = installed_meta.get('version')

                if installed_version == version:
                    # Reinstall is benign — same version, just re-fetched.
                    console.print(f"[yellow]{name} v{version} is already installed.[/yellow]")
                    if not _confirm("Reinstall?", default=True, mode=mode):
                        sys.exit(0)
                else:
                    # Version replacement is consequential — destroys the old install.
                    console.print(f"[yellow]{name} v{installed_version} is already installed.[/yellow]")
                    console.print(f"Installing v{version} will replace it.")
                    if not _confirm(
                        "Continue?", default=False, mode=mode, consequential=True,
                    ):
                        sys.exit(0)
            except (json.JSONDecodeError, IOError):
                pass

        # Remove existing installation
        shutil.rmtree(toolkit_dir)

    # Step 3: Download tarball. The progress bar self-announces ("Downloading
    # <name>...") and is transient — once it completes, the "✓ Downloaded"
    # line below takes its place. So no separate "Downloading toolkit..."
    # heading is needed.

    # Get tarball URL from version info
    tarball_url = None
    for v in toolkit_meta.get('versions', []):
        if isinstance(v, dict) and v.get('version') == version:
            tarball_url = v.get('tarball_url')
            break

    if not tarball_url:
        # Fallback: construct URL
        tarball_url = f"{api_url}/api/toolkits/{name}/download/{version}"

    try:
        import tempfile
        from rich.progress import Progress, DownloadColumn, BarColumn, TransferSpeedColumn, TextColumn

        tarball_response = requests.get(tarball_url, stream=True, timeout=60)

        if tarball_response.status_code != 200:
            console.print(f"[red]✗ Download failed (status {tarball_response.status_code})[/red]")
            sys.exit(1)

        # Get file size
        total_size = int(tarball_response.headers.get('content-length', 0))

        # Download with progress bar
        tarball_path = Path(tempfile.gettempdir()) / f"{name}-{version}.tar.gz"

        with Progress(
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            DownloadColumn(),
            TransferSpeedColumn(),
            console=console,
            # Erase the progress bar on completion so the persistent
            # "✓ Downloaded ..." line just below it isn't redundant with
            # a stale bar in the scrollback.
            transient=True,
        ) as progress:
            task = progress.add_task(f"Downloading {name}-{version}.tar.gz", total=total_size)

            with open(tarball_path, 'wb') as f:
                for chunk in tarball_response.iter_content(chunk_size=8192):
                    f.write(chunk)
                    progress.update(task, advance=len(chunk))

        console.print(
            f"[green]✓ Downloaded {name}-{version}.tar.gz "
            f"({_format_bytes(tarball_path.stat().st_size)})[/green]"
        )

    except requests.exceptions.RequestException as e:
        console.print(f"[red]✗ Download error: {e}[/red]")
        sys.exit(1)

    # Step 4: Extract tarball
    console.print(f"Extracting to {toolkit_dir}...")

    toolkit_dir.mkdir(parents=True, exist_ok=True)

    try:
        import tarfile
        with tarfile.open(tarball_path, 'r:gz') as tar:
            tar.extractall(path=toolkit_dir)

        file_count = sum(1 for _ in toolkit_dir.rglob('*'))
        console.print(f"[green]✓ Extracted {file_count} files[/green]\n")

        # Clean up tarball
        tarball_path.unlink()

    except Exception as e:
        console.print(f"[red]✗ Extraction error: {e}[/red]")
        sys.exit(1)

    # Step 5: Detect environment type
    console.print("Detecting environment requirements...")

    try:
        toolkit_config = load_toolkit_yaml(toolkit_dir)
        env_type, python_version = detect_environment_type(toolkit_dir, toolkit_config)

        console.print(f"[green]✓ Environment: {env_type} (Python {python_version})[/green]")

        # Special messages for conda/docker
        if env_type == 'conda' and not has_conda():
            console.print("[yellow]Warning: conda not detected. Install conda/mamba or use Docker mode.[/yellow]\n")
        elif env_type == 'docker':
            if (toolkit_dir / 'Dockerfile').exists():
                console.print("[blue]Docker mode: toolkit has custom Dockerfile[/blue]")
            else:
                current_py = get_current_python()
                if python_version != current_py:
                    console.print(f"[blue]Docker mode: requires Python {python_version} (current: {current_py})[/blue]")
            console.print("[yellow]Docker mode will be available in Phase 3B[/yellow]\n")

    except FileNotFoundError as e:
        console.print(f"[red]✗ Error: {e}[/red]")
        sys.exit(1)
    except yaml.YAMLError as e:
        console.print(f"[red]✗ Invalid toolkit.yaml: {e}[/red]")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]✗ Environment detection error: {e}[/red]")
        sys.exit(1)

    # Step 6: Refuse Docker mode (Phase 3B) before doing any work
    if env_type == 'docker':
        console.print(
            "[red]✗ This toolkit requires Docker mode, which is not yet supported.[/red]\n"
            "[yellow]  Docker mode is planned for Phase 3B.[/yellow]"
        )
        # Roll back the extracted toolkit so we don't leave a broken install behind
        shutil.rmtree(toolkit_dir, ignore_errors=True)
        sys.exit(1)

    # Setup_script warning (Phase 3C - not yet implemented)
    has_setup_script = (toolkit_dir / 'setup.py').exists()
    if has_setup_script:
        console.print(
            "[yellow]This toolkit declares a custom setup script (setup.py).[/yellow]\n"
            "[yellow]  The setup system is planned for Phase 3C.[/yellow]\n"
            "[yellow]  Install will continue, but some functionality may not work until setup runs.[/yellow]\n"
        )

    # Step 7: Setup environment
    console.print()
    python_path = None
    env_name = None

    try:
        if env_type == 'venv':
            console.print("[bold blue]Setting up environment...[/bold blue]\n")
            python_path = setup_venv_environment(toolkit_dir, console)

        elif env_type == 'conda':
            verify_conda_available()
            console.print("[bold blue]Setting up environment...[/bold blue]\n")
            env_name = setup_conda_environment(toolkit_dir, name, python_version, console)

    except Exception as e:
        console.print(f"\n[red]✗ Environment setup failed: {e}[/red]")

        # Show error details if it's a subprocess error
        if isinstance(e, subprocess.CalledProcessError):
            if e.stderr:
                error_output = e.stderr.decode() if isinstance(e.stderr, bytes) else str(e.stderr)
                console.print(f"[red]Error output: {error_output[:500]}[/red]")  # Limit to 500 chars

        # Clean up partial installation
        console.print(f"[yellow]Cleaning up {toolkit_dir}...[/yellow]")
        if env_type == 'conda' and env_name:
            cleanup_conda_environment(env_name)
        shutil.rmtree(toolkit_dir)
        raise click.ClickException("Installation failed")

    # Step 8: Save metadata with everything serve will need
    skills_dir = toolkit_dir / 'skills'
    if skills_dir.exists():
        # Filter out macOS AppleDouble metadata files ("._foo.md")
        skill_files = sorted(
            p for p in skills_dir.glob('*.md') if not p.name.startswith('._')
        )
    else:
        skill_files = []
    tools_count = len(toolkit_config.get('tools', []) or [])

    meta = {
        'name': name,
        'version': version,
        'environment': env_type,
        'python_version': python_version,
        'tools_count': tools_count,
        'has_skills': len(skill_files) > 0,
        'skills_count': len(skill_files),
        'has_setup_script': has_setup_script,
        'needs_setup': has_setup_script,  # Setup-script toolkits need 3C to run
        'installed_at': datetime.now().isoformat(),
    }

    # Environment-specific fields (consumed by serve to launch the per-toolkit subprocess)
    if env_type == 'venv':
        meta['python_path'] = str(python_path)
    elif env_type == 'conda':
        meta['env_name'] = env_name

    meta_file = toolkit_dir / '.stk_meta.json'
    meta_file.write_text(json.dumps(meta, indent=2))

    # Step 9: Success message
    console.print(f"\n[bold green]✓ Successfully installed {name} v{version}[/bold green]\n")

    if env_type == 'venv':
        console.print(f"Environment: venv (Python {python_version})")
    elif env_type == 'conda':
        console.print(f"Environment: conda env '{env_name}' (Python {python_version})")

    if tools_count > 0:
        console.print(f"Tools: {tools_count} available")

    if skill_files:
        console.print(f"Skills: {len(skill_files)} guide{'s' if len(skill_files) != 1 else ''} available")
        # List each skill (strip .md extension for readability)
        for skill_file in skill_files:
            console.print(f"  [dim]•[/dim] {skill_file.stem}")

        # Surface skills into ~/.claude/skills/ so Claude Code picks them
        # up automatically. Best-effort — failures here don't fail install.
        if not no_skills:
            try:
                from .skills import install_skills_for_toolkit, CLAUDE_SKILLS_DIR
                surfaced = install_skills_for_toolkit(name, toolkit_dir)
                if surfaced:
                    console.print(
                        f"[dim]Surfaced to {CLAUDE_SKILLS_DIR}/ "
                        f"({len(surfaced)} entr{'ies' if len(surfaced) != 1 else 'y'})[/dim]"
                    )
            except Exception as e:
                console.print(
                    f"[yellow]Could not surface skills to ~/.claude/skills: {e}[/yellow]"
                )

    # Expected toolkits — companion installs the author flagged. No runtime
    # coupling; we just prompt (TTY) or message (skip mode) and install
    # accepted ones recursively. Already-installed ones are silently skipped.
    expected = toolkit_config.get('expected_toolkits') or []
    expected = [e for e in expected if isinstance(e, str)]
    if expected:
        from .config import TOOLKITS_DIR
        not_installed = [
            e for e in expected
            if not (TOOLKITS_DIR / e).exists()
        ]
        if not_installed:
            console.print(
                f"\n[bold]{name} is designed to work with:[/bold] "
                f"{', '.join(expected)}"
            )
            if mode == "skip":
                console.print(
                    f"[dim]Not installed: {', '.join(not_installed)}.[/dim]"
                )
                console.print(
                    f"[dim]Install with: [cyan]scitoolkit install "
                    f"{' '.join(not_installed)}[/cyan][/dim]"
                )
            else:
                console.print(
                    f"[dim]The following are not yet installed: "
                    f"{', '.join(not_installed)}[/dim]"
                )
                if _confirm(
                    "Install them now?", default=True, mode=mode,
                ):
                    runner_ctx = click.get_current_context()
                    for companion in not_installed:
                        console.print(
                            f"\n[dim]── Installing companion toolkit: "
                            f"{companion} ──[/dim]"
                        )
                        # Re-invoke install with the same skip/yes mode.
                        # We forward --no-input rather than --yes so the
                        # companion's own consequential prompts (replace,
                        # etc.) still abort safely.
                        try:
                            runner_ctx.invoke(
                                install,
                                name=companion,
                                version=None,
                                no_skills=no_skills,
                                yes=False,
                                no_=False,
                                no_input=True,
                            )
                        except SystemExit as e:
                            if e.code not in (0, None):
                                console.print(
                                    f"[yellow]Companion toolkit "
                                    f"'{companion}' did not install "
                                    f"cleanly (exit {e.code}); skipping.[/yellow]"
                                )

    console.print(f"\n[bold]Ready to use! Try:[/bold]")
    console.print(f"  [cyan]stk list[/cyan]")
    console.print(f"  [cyan]stk serve {name}[/cyan]")
    console.print()


@main.command(name='list')
def list_cmd():
    """
    List all installed toolkits.

    Reads .stk_meta.json from each installed toolkit and displays a summary
    table. Does not activate any environments — purely metadata-driven.

    Example:
        scitoolkit list
    """
    from .config import TOOLKITS_DIR

    if not TOOLKITS_DIR.exists():
        console.print("[dim]No toolkits installed.[/dim]")
        console.print("\nInstall one with: [cyan]scitoolkit install <name>[/cyan]")
        return

    # Discover installed toolkits by their .stk_meta.json files.
    entries: list[tuple[Path, dict]] = []
    broken = []
    for toolkit_dir in sorted(TOOLKITS_DIR.iterdir()):
        if not toolkit_dir.is_dir():
            continue
        meta_file = toolkit_dir / '.stk_meta.json'
        if not meta_file.exists():
            broken.append(toolkit_dir.name)
            continue
        try:
            entries.append((toolkit_dir, json.loads(meta_file.read_text())))
        except (json.JSONDecodeError, IOError):
            broken.append(toolkit_dir.name)

    if not entries and not broken:
        console.print("[dim]No toolkits installed.[/dim]")
        console.print("\nInstall one with: [cyan]scitoolkit install <name>[/cyan]")
        return

    home = str(Path.home())

    table = Table(title="Installed Toolkits")
    table.add_column("Name", style="cyan", no_wrap=True)
    table.add_column("Version", style="white")
    table.add_column("Environment", style="white")
    table.add_column("Tools", justify="right", style="dark_orange")
    table.add_column("Skills", justify="right", style="dark_orange")
    table.add_column("Path", style="dim", overflow="fold")

    for toolkit_dir, meta in entries:
        env_type = meta.get('environment', 'unknown')
        py_ver = meta.get('python_version', '?')
        env_label = f"{env_type} (py{py_ver})"

        # Show ~/-relative path so the column doesn't blow out.
        path_str = str(toolkit_dir)
        if path_str.startswith(home):
            path_str = "~" + path_str[len(home):]

        name_cell = meta.get('name', '?')
        if meta.get('needs_setup'):
            name_cell += " [yellow](setup needed)[/yellow]"

        table.add_row(
            name_cell,
            meta.get('version', '?'),
            env_label,
            str(meta.get('tools_count', 0)),
            str(meta.get('skills_count', 0)),
            path_str,
        )

    console.print(table)

    if broken:
        console.print(
            f"\n[yellow]{len(broken)} toolkit director"
            f"{'ies' if len(broken) != 1 else 'y'} missing or unreadable .stk_meta.json:[/yellow]"
        )
        for name in broken:
            console.print(f"  [dim]•[/dim] {name}")
        console.print("[dim]These were likely installed by an older CLI; reinstall to fix.[/dim]")


@main.command()
@click.argument('name')
@_interactive_options
def uninstall(name, yes, no_, no_input):
    """
    Uninstall a toolkit.

    Removes the toolkit directory and any associated conda environment.
    Venv environments live inside the toolkit directory, so they're cleaned
    up automatically.

    Example:
        scitoolkit uninstall aster
        scitoolkit uninstall aster --yes
    """
    from .config import TOOLKITS_DIR

    mode = _resolve_prompt_mode(yes, no_, no_input)

    toolkit_dir = TOOLKITS_DIR / name
    if not toolkit_dir.exists():
        console.print(f"[red]✗ Toolkit '{name}' is not installed.[/red]")
        console.print("\nList installed toolkits: [cyan]scitoolkit list[/cyan]")
        sys.exit(1)

    # Read metadata if available so we know whether there's a conda env to remove
    meta_file = toolkit_dir / '.stk_meta.json'
    meta = {}
    if meta_file.exists():
        try:
            meta = json.loads(meta_file.read_text())
        except (json.JSONDecodeError, IOError):
            console.print(
                "[yellow]Could not read .stk_meta.json. "
                "Proceeding with directory removal only.[/yellow]"
            )

    env_type = meta.get('environment')
    env_name = meta.get('env_name')
    version = meta.get('version', '?')

    # Confirm
    console.print(f"\n[bold]Uninstalling {name} v{version}[/bold]")
    console.print(f"  Directory: [dim]{toolkit_dir}[/dim]")
    if env_type == 'conda' and env_name:
        console.print(f"  Conda env: [dim]{env_name}[/dim]")

    # Uninstall is consequential — deletes the toolkit dir and possibly a
    # conda env. In skip mode require explicit --yes to proceed.
    if not _confirm(
        "\nProceed?", default=False, mode=mode, consequential=True,
    ):
        console.print("[dim]Cancelled.[/dim]")
        sys.exit(0)

    # Remove conda env first (best effort) so a failure here doesn't leave us
    # with an orphan env after the directory is gone.
    if env_type == 'conda' and env_name:
        with console.status(f"[bold blue]Removing conda environment '{env_name}'..."):
            cleanup_conda_environment(env_name)
        console.print(f"[green]✓ Removed conda environment '{env_name}'[/green]")

    # Remove toolkit directory (covers venv inside it too)
    try:
        shutil.rmtree(toolkit_dir)
        console.print(f"[green]✓ Removed {toolkit_dir}[/green]")
    except OSError as e:
        console.print(f"[red]✗ Could not remove directory: {e}[/red]")
        sys.exit(1)

    # Best-effort cleanup of surfaced skills under ~/.claude/skills/.
    # Only removes directories carrying the scitoolkit-managed marker, so
    # user-placed skills with the same name prefix are untouched.
    try:
        from .skills import uninstall_skills_for_toolkit
        removed_skills = uninstall_skills_for_toolkit(name)
        if removed_skills:
            console.print(
                f"[green]✓[/green] Removed {len(removed_skills)} skill"
                f"{'s' if len(removed_skills) != 1 else ''} from ~/.claude/skills/"
            )
    except Exception as e:
        console.print(
            f"[yellow]Could not clean up ~/.claude/skills entries: {e}[/yellow]"
        )

    console.print(f"\n[bold green]✓ Uninstalled {name}[/bold green]")


class _ServeGroup(click.Group):
    """``serve``-specific group that lets toolkit names appear as positional
    args without colliding with subcommand dispatch.

    Click's normal behavior is to interpret the first positional after the
    group name as a subcommand. Without this override, ``stk serve
    arxiv-search`` would error with "No such command 'arxiv-search'."
    Forcing users to write ``-t arxiv-search`` is mechanically correct but
    unfriendly.

    Strategy: rewrite ``args`` early in ``parse_args``. Bare positional
    names that aren't reserved subcommand names get rewritten as
    ``-t NAME`` pairs. Subcommand calls (``serve enable foo``,
    ``serve config --show``) take the unmodified path.

    The reserved subcommand names are listed in ``RESERVED`` below; any
    toolkit colliding with one of those would still need ``-t``. The
    validator's name shape allows ``config`` etc. as toolkit names so
    this is technically possible, but rare.
    """

    RESERVED = {"enable", "disable", "enable-tool", "disable-tool", "config"}

    def parse_args(self, ctx, args):
        # Walk leading non-flag tokens. If none of them match a reserved
        # subcommand, treat them all as ``-t`` values. If any of them
        # *does* match a reserved name, we leave args untouched and let
        # Click's normal subcommand dispatch handle it.
        leading: list[str] = []
        rest_idx = 0
        while rest_idx < len(args) and not args[rest_idx].startswith("-"):
            leading.append(args[rest_idx])
            rest_idx += 1
        if leading and not any(t in self.RESERVED for t in leading):
            rewritten: list[str] = []
            for name in leading:
                rewritten.extend(["-t", name])
            rewritten.extend(args[rest_idx:])
            args = rewritten
        return super().parse_args(ctx, args)


@main.group(cls=_ServeGroup, invoke_without_command=True)
@click.option(
    '--toolkit', '-t', 'toolkits_flag', multiple=True, metavar='NAME',
    help=(
        'Serve only this toolkit (repeatable). Replaces the default set for '
        'this invocation. Bare positional names also work: '
        'stk serve aster heptapod is equivalent to -t aster -t heptapod.'
    ),
)
@click.option(
    '--group', 'group_name', default=None,
    help='Serve a named tool group from ~/.scitoolkit/serve.yaml (one-shot).',
)
@click.option(
    '--enable-tool', 'enable_tool', multiple=True, metavar='TOOLKIT__TOOL',
    help=(
        'Enable a single tool, switching to allowlist mode for its toolkit '
        '(only listed tools serve). One-shot, does not persist. Repeatable.'
    ),
)
@click.option(
    '--disable-tool', 'disable_tool', multiple=True, metavar='TOOLKIT__TOOL',
    help=(
        'Disable a single tool from this serve session (one-shot, does not '
        'persist). Wins over --enable-tool for the same tool. Repeatable.'
    ),
)
@click.option(
    '--dry-run', '-d', 'dry_run', is_flag=True, default=False,
    help='Print the resolved serve set and exit without starting the server.',
)
@click.option(
    '--call-timeout', 'call_timeout', type=float, default=None,
    metavar='SECONDS',
    help=(
        'Per-tool-call timeout in seconds. Defaults to 60. Bump this for '
        'long-running scientific workflows; the orchestrator will fail a '
        'call rather than block the agent forever if a tool wedges.'
    ),
)
@click.option(
    '--no-tui', is_flag=True, default=True,
    help='Run without TUI. Currently the only supported mode.',
)
@click.pass_context
def serve(ctx, toolkits_flag, group_name, enable_tool, disable_tool, dry_run, call_timeout, no_tui):
    """
    Start the MCP server for installed toolkits.

    With no arguments, serves all installed toolkits (minus anything in
    ~/.scitoolkit/serve.yaml's default.toolkits.disabled). Pass toolkit
    names positionally, or use --toolkit / -t (repeatable), or --group to
    narrow the set for this invocation. Use the subcommands below to
    persistently edit defaults; see also "scitoolkit groups" for managing
    named subsets.

    \b
    Examples:
        stk serve                              # all installed
        stk serve aster                        # one toolkit (positional)
        stk serve aster arxiv-search           # several
        stk serve -t aster -t arxiv-search     # same, via flags
        stk serve --group exoplanet-pipeline   # named group
        stk serve aster --enable-tool aster__transit
        stk serve --disable-tool aster__heavy
        stk serve --dry-run                    # preview, then exit

    \b
    Persistent configuration lives in ~/.scitoolkit/serve.yaml. Edit
    directly, or use the subcommands below.

    Configure Claude Code with (use the canonical "scitoolkit" command in
    config files; "stk" works too but is documented as a convenience alias):

    \b
        {"mcpServers": {"scitoolkit": {"command": "scitoolkit",
                                       "args": ["serve"]}}}
    """
    toolkits = toolkits_flag
    # Subcommand path: don't run the server, defer to the subcommand.
    if ctx.invoked_subcommand is not None:
        return

    if call_timeout is not None and call_timeout <= 0:
        console.print(
            "[red]✗ --call-timeout must be a positive number of seconds.[/red]"
        )
        sys.exit(2)

    from .serve.config import (
        load_serve_config,
        resolve_serve_set,
        ServeConfigError,
        SERVE_CONFIG_PATH,
    )
    from .serve.orchestrator import discover_toolkits, serve as _serve_entry

    try:
        cfg = load_serve_config()
    except ServeConfigError as e:
        console.print(f"[red]Error in serve config:[/red] {e}")
        console.print(f"[dim]Edit {SERVE_CONFIG_PATH} or remove it to reset.[/dim]")
        sys.exit(1)

    discoveries = discover_toolkits()
    installed_names = [d.name for d in discoveries if d.skip_reason is None]

    try:
        resolved = resolve_serve_set(
            installed_toolkits=installed_names,
            config=cfg,
            positional_toolkits=list(toolkits),
            group_name=group_name,
            enable_tools=list(enable_tool),
            disable_tools=list(disable_tool),
        )
    except ServeConfigError as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(2)

    if dry_run:
        _print_resolution(resolved, discoveries)
        return

    # Bare serve flow with no narrowing flags should keep legacy behavior
    # (serve everything, no resolver). Only thread the resolved set when
    # the user actually narrowed something.
    narrowed = bool(toolkits or group_name or enable_tool or disable_tool
                    or cfg.default.disabled_toolkits or cfg.default.disabled_tools)

    # The MCP stdio protocol owns this process's stdin/stdout, so we do NOT
    # use the module-level `console` here (which writes to stdout). The
    # orchestrator builds its own stderr-bound Console.
    from .serve.orchestrator import DEFAULT_CALL_TIMEOUT_S
    timeout_s = call_timeout if call_timeout is not None else DEFAULT_CALL_TIMEOUT_S
    rc = _serve_entry(
        no_tui=True,
        resolved=resolved if narrowed else None,
        call_timeout_s=timeout_s,
    )
    sys.exit(rc)


def _print_resolution(resolved, discoveries) -> None:
    """Render --dry-run output: what would be served and how we got there.

    Two output modes, picked automatically:
      - Compact: when nothing is overriding the default (no positional,
        no group, no per-tool flag, no default disables). One-line
        resolution. Useful when the user is just confirming defaults.
      - Detailed: when overrides are layered. Per-toolkit tool counts and
        a step-by-step resolution path for debuggability.
    """
    installed_meta = {d.name: d for d in discoveries}

    # "No overrides" = exactly one entry in resolution_path AND it's the
    # untouched default. The resolver appends "default: all installed"
    # verbatim in that case.
    is_plain_default = (
        len(resolved.resolution_path) == 1
        and resolved.resolution_path[0] == "default: all installed"
        and not resolved.disable_qualified
    )

    console.print("\n[bold]Resolved serve set:[/bold]")
    if not resolved.toolkits:
        console.print("  [dim](nothing)[/dim]")
    for tk in resolved.toolkits:
        meta = installed_meta.get(tk)
        if meta is not None and meta.meta:
            total = meta.meta.get("tools_count", "?")
        else:
            total = "?"
        per_tool = resolved.tools.get(tk)
        if per_tool is None:
            disables_here = [
                q.split("__", 1)[1]
                for q in resolved.disable_qualified
                if q.startswith(f"{tk}__")
            ]
            if disables_here:
                # If we know the total, show "(N of M tools, except: X)".
                if isinstance(total, int):
                    served = total - len(disables_here)
                    console.print(
                        f"  [cyan]{tk}[/cyan] ({served} of {total} tools)"
                    )
                    console.print(f"    disabled: {', '.join(disables_here)}")
                else:
                    console.print(
                        f"  [cyan]{tk}[/cyan] (all tools except "
                        f"{', '.join(disables_here)})"
                    )
            else:
                console.print(f"  [cyan]{tk}[/cyan] ({total} of {total} tools)"
                              if isinstance(total, int)
                              else f"  [cyan]{tk}[/cyan] (all tools)")
        else:
            console.print(
                f"  [cyan]{tk}[/cyan] ({len(per_tool)} of {total} tools)"
            )
            console.print(f"    enabled: {', '.join(per_tool)}")

    if resolved.warnings:
        console.print("\n[bold yellow]Warnings:[/bold yellow]")
        for w in resolved.warnings:
            console.print(f"  [yellow]•[/yellow] {w}")

    if is_plain_default:
        from .serve.config import SERVE_CONFIG_PATH
        if SERVE_CONFIG_PATH.exists():
            console.print(f"\nResolution: default from {SERVE_CONFIG_PATH}")
        else:
            console.print(f"\nResolution: default (no {SERVE_CONFIG_PATH})")
        return

    console.print("\n[bold]Resolution path:[/bold]")
    for step in resolved.resolution_path:
        console.print(f"  {step}")


@serve.command('enable', short_help='Persistently enable a toolkit by default.')
@click.argument('toolkit')
def serve_enable(toolkit):
    """Persistently enable a toolkit (remove from default.toolkits.disabled)."""
    from .serve.config import load_serve_config, save_serve_config

    cfg = load_serve_config()
    if toolkit not in cfg.default.disabled_toolkits:
        console.print(f"[dim]'{toolkit}' is already enabled by default.[/dim]")
        return
    cfg.default.disabled_toolkits.remove(toolkit)
    save_serve_config(cfg)
    console.print(f"[green]✓[/green] '{toolkit}' will be served by default.")


@serve.command('disable', short_help='Persistently disable a toolkit by default.')
@click.argument('toolkit')
def serve_disable(toolkit):
    """Persistently disable a toolkit from the default serve set."""
    from .serve.config import load_serve_config, save_serve_config

    cfg = load_serve_config()
    if toolkit in cfg.default.disabled_toolkits:
        console.print(f"[dim]'{toolkit}' is already disabled.[/dim]")
        return
    cfg.default.disabled_toolkits.append(toolkit)
    save_serve_config(cfg)
    console.print(f"[green]✓[/green] '{toolkit}' will be skipped by default.")


@serve.command('enable-tool', short_help='Persistently enable a single tool by default.')
@click.argument('qualified', metavar='TOOLKIT__TOOL')
def serve_enable_tool(qualified):
    """Persistently re-enable a tool (remove from default.tools.disabled)."""
    from .serve.config import (
        load_serve_config, save_serve_config, _split_tool, ServeConfigError,
    )

    try:
        _split_tool(qualified)
    except ServeConfigError as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(2)

    cfg = load_serve_config()
    if qualified not in cfg.default.disabled_tools:
        console.print(f"[dim]'{qualified}' is already enabled by default.[/dim]")
        return
    cfg.default.disabled_tools.remove(qualified)
    save_serve_config(cfg)
    console.print(f"[green]✓[/green] '{qualified}' will be served by default.")


@serve.command('disable-tool', short_help='Persistently disable a single tool by default.')
@click.argument('qualified', metavar='TOOLKIT__TOOL')
def serve_disable_tool(qualified):
    """Persistently disable a single tool from the default serve set."""
    from .serve.config import (
        load_serve_config, save_serve_config, _split_tool, ServeConfigError,
    )

    try:
        _split_tool(qualified)
    except ServeConfigError as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(2)

    cfg = load_serve_config()
    if qualified in cfg.default.disabled_tools:
        console.print(f"[dim]'{qualified}' is already disabled.[/dim]")
        return
    cfg.default.disabled_tools.append(qualified)
    save_serve_config(cfg)
    console.print(f"[green]✓[/green] '{qualified}' will be skipped by default.")


@serve.command('config')
@click.option('--show', 'action', flag_value='show', default='show',
              help='Print the current serve config (default).')
@click.option('--edit', 'action', flag_value='edit',
              help='Open the serve config in $EDITOR.')
@click.option('--path', 'action', flag_value='path',
              help='Print the path to the serve config file.')
def serve_config(action):
    """Show, edit, or locate the serve config file."""
    from .serve.config import SERVE_CONFIG_PATH

    if action == 'path':
        console.print(str(SERVE_CONFIG_PATH))
        return
    if action == 'edit':
        click.edit(filename=str(SERVE_CONFIG_PATH))
        return
    # show
    if not SERVE_CONFIG_PATH.exists():
        console.print(f"[dim]No serve config yet at {SERVE_CONFIG_PATH}.[/dim]")
        console.print(
            "[dim]It will be created when you run a `scitoolkit serve` "
            "subcommand that edits state.[/dim]"
        )
        return
    console.print(SERVE_CONFIG_PATH.read_text())


@main.group()
def groups():
    """Manage tool groups in ~/.scitoolkit/serve.yaml."""
    pass


@groups.command('list')
def groups_list():
    """List all configured tool groups."""
    from .serve.config import load_serve_config

    cfg = load_serve_config()
    if not cfg.groups:
        console.print("[dim]No groups defined.[/dim]")
        console.print(
            "Create one with: [cyan]scitoolkit groups create <name> "
            "<toolkit> [<toolkit> ...][/cyan]"
        )
        return
    for name, g in cfg.groups.items():
        console.print(f"[bold cyan]{name}[/bold cyan]")
        console.print(f"  toolkits: {', '.join(g.toolkits) or '(none)'}")
        if g.disabled_tools:
            console.print(f"  disables: {', '.join(g.disabled_tools)}")


@groups.command('create')
@click.argument('name')
@click.argument('toolkits', nargs=-1, required=True)
def groups_create(name, toolkits):
    """Create a new group containing the given toolkits."""
    from .serve.config import load_serve_config, save_serve_config, Group

    cfg = load_serve_config()
    if name in cfg.groups:
        console.print(f"[red]Group '{name}' already exists.[/red]")
        console.print(f"Use [cyan]scitoolkit groups edit {name}[/cyan] to modify it.")
        sys.exit(1)
    cfg.groups[name] = Group(name=name, toolkits=list(toolkits))
    save_serve_config(cfg)
    console.print(f"[green]✓[/green] Created group '{name}' with {len(toolkits)} toolkit(s).")


@groups.command('edit')
def groups_edit():
    """Open the serve config in $EDITOR (groups live under `groups:`)."""
    from .serve.config import SERVE_CONFIG_PATH

    # Ensure the file exists so $EDITOR has something to open.
    if not SERVE_CONFIG_PATH.exists():
        SERVE_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        SERVE_CONFIG_PATH.write_text("groups: {}\n")
    click.edit(filename=str(SERVE_CONFIG_PATH))


@groups.command('delete')
@click.argument('name')
@_interactive_options
def groups_delete(name, yes, no_, no_input):
    """Delete a tool group from serve.yaml."""
    from .serve.config import load_serve_config, save_serve_config

    mode = _resolve_prompt_mode(yes, no_, no_input)
    cfg = load_serve_config()
    if name not in cfg.groups:
        console.print(f"[red]Group '{name}' does not exist.[/red]")
        sys.exit(1)
    if not _confirm(
        f"Delete group '{name}'?", default=False, mode=mode, consequential=True,
    ):
        console.print("[dim]Cancelled.[/dim]")
        sys.exit(0)
    del cfg.groups[name]
    save_serve_config(cfg)
    console.print(f"[green]✓[/green] Deleted group '{name}'.")


@main.command()
@click.option(
    '-n', '--lines', 'lines', type=int, default=50,
    help='Number of lines to show from the tail (default 50).',
)
@click.option(
    '-f/-F', '--follow/--no-follow', 'follow', default=True,
    help='Follow the log as new lines are appended (default: follow).',
)
@click.option(
    '--all', 'show_all', is_flag=True, default=False,
    help='Show the whole log, not just the tail. Implies --no-follow unless -f is also given.',
)
@click.option(
    '--raw', is_flag=True, default=False,
    help='Include the JSON mirror lines (the lines starting with "# ") in the output.',
)
def logs(lines, follow, show_all, raw):
    """
    Tail the serve log.

    The orchestrator writes structured events and tool-call traces to
    ~/.scitoolkit/logs/serve.log whenever ``scitoolkit serve`` is running.
    This command renders that log with colors so you can watch tool calls
    fire in real time while Claude Code uses them.

    \b
    Examples:
        scitoolkit logs                   # tail and follow (Ctrl-C to stop)
        scitoolkit logs --no-follow       # last 50 lines, then exit
        scitoolkit logs -n 200            # last 200 lines and follow
        scitoolkit logs --all --no-follow # full log to stdout
    """
    from .logging.logger import SERVE_LOG_PATH
    import time

    log_path = SERVE_LOG_PATH

    if not log_path.exists():
        console.print(
            "[dim]No serve log yet at "
            f"{log_path}.[/dim]"
        )
        console.print(
            "[dim]Start `scitoolkit serve` (or have Claude Code launch it) "
            "to generate one.[/dim]"
        )
        sys.exit(0)

    def _render(line: str) -> None:
        """Print one log line with appropriate styling, or skip it."""
        stripped = line.rstrip("\n")
        if not stripped:
            console.print()
            return
        # JSON mirror lines start with "# {...}" — usually skip; the human
        # line just above carries the same info.
        if stripped.startswith("# {"):
            if raw:
                console.print(f"[dim]{stripped}[/dim]")
            return
        # Session marker bars / banner: highlight in bold.
        if stripped.startswith("═") or stripped.startswith("serve session started") or stripped.startswith("pid "):
            console.print(f"[bold cyan]{stripped}[/bold cyan]")
            return
        # Prune banner.
        if stripped.startswith("# --- serve.log pruned"):
            console.print(f"[dim]{stripped}[/dim]")
            return
        # Tool call completion lines carry ✓ / ✗.
        if "✓ Completed" in stripped:
            console.print(f"[green]{stripped}[/green]")
            return
        if "✗ Failed" in stripped:
            console.print(f"[red]{stripped}[/red]")
            return
        # Event lines: "[ts] event=<name> ..."
        if " event=" in stripped:
            # Color by level cue in the message: warn/error tokens win, else dim.
            lower = stripped.lower()
            if "level=error" in lower or "crashed" in lower or "failed_permanently" in lower:
                console.print(f"[red]{stripped}[/red]")
            elif "level=warn" in lower or "skipped" in lower or "restarting" in lower:
                console.print(f"[yellow]{stripped}[/yellow]")
            else:
                console.print(f"[cyan]{stripped}[/cyan]")
            return
        # Tool call start / output lines.
        if "::" in stripped:
            console.print(stripped)
            return
        # Fallback.
        console.print(f"[dim]{stripped}[/dim]")

    # Initial dump: either the whole file, or the last N lines.
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            if show_all:
                initial = f.read().splitlines(keepends=True)
            else:
                # Read the whole file then keep last N — fine for serve.log
                # (capped at ~10 MB) and avoids reverse-seek complexity.
                initial = f.readlines()[-lines:]
            for line in initial:
                _render(line)
            offset = f.tell()
    except OSError as e:
        console.print(f"[red]Could not read {log_path}: {e}[/red]")
        sys.exit(1)

    if not follow and not show_all:
        return
    if show_all and not follow:
        return

    # Follow mode: poll for appended lines. Handles file truncation/rotation
    # by detecting size shrink and re-opening from the start.
    try:
        while True:
            try:
                size = log_path.stat().st_size
            except FileNotFoundError:
                # File was removed; wait for it to come back.
                time.sleep(1.0)
                continue
            if size < offset:
                # File was truncated or replaced; restart from the beginning.
                offset = 0
                console.print("[dim]--- log rotated, re-reading ---[/dim]")
            if size > offset:
                with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(offset)
                    for line in f:
                        _render(line)
                    offset = f.tell()
            time.sleep(0.5)
    except KeyboardInterrupt:
        # Quiet exit on Ctrl-C — no traceback noise.
        return


def create_tarball(source_dir: Path, output_path: Path, toolkit_name: str):
    """
    Create a gzipped tarball of the toolkit.

    Excludes: .git/, __pycache__/, *.pyc, .DS_Store, venv/, .venv/

    Args:
        source_dir: Source directory to package
        output_path: Where to write the tarball
        toolkit_name: Name of the toolkit (not used in arcname)
    """
    exclude_patterns = {
        '.git', '__pycache__', '.pyc', '.DS_Store',
        'venv', '.venv', '.pytest_cache', '.mypy_cache',
        '.egg-info', 'dist', 'build', '.tox', 'htmlcov',
        '.coverage', '.env', '.vscode', '.idea'
    }

    def should_exclude(path: Path) -> bool:
        """Check if path should be excluded from tarball."""
        rel_path = path.relative_to(source_dir)

        # Check each part of the path
        for part in rel_path.parts:
            if part in exclude_patterns:
                return True
            # Check for patterns like *.pyc
            if part.endswith('.pyc') or part.endswith('.pyo'):
                return True
            if '.egg-info' in part:
                return True

        return False

    with tarfile.open(output_path, 'w:gz') as tar:
        # Iterate files only (not directories) and add them non-recursively.
        # ``tar.add(dir, recursive=True)`` (the default) would walk the tree
        # itself and add everything inside, bypassing should_exclude — that's
        # how __pycache__/ contents leak in, and how every regular file
        # ends up duplicated (once via the dir walk, once via this loop).
        # Tarfile creates intermediate directory entries automatically when
        # we add a file at a nested arcname.
        for item in source_dir.rglob('*'):
            if not item.is_file():
                continue
            if should_exclude(item):
                continue
            arcname = item.relative_to(source_dir)
            tar.add(item, arcname=arcname, recursive=False)


if __name__ == '__main__':
    main()