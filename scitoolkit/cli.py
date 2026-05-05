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

@click.group()
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
def init(name, path, with_docker):
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

    # Interactive mode if no name provided
    if not name:
        console.print(Panel.fit(
            "[bold cyan]SciToolkit Initialization[/bold cyan]\n"
            "Let's create your new toolkit!",
            border_style="cyan"
        ))
        name = click.prompt('Toolkit name', type=str)

    # Check if toolkit exists in registry
    api_url = "https://api.scitoolkit.org"
    registry_metadata = None

    try:
        console.print(f"🔍 Checking if '{name}' exists in registry...")
        response = requests.get(f"{api_url}/api/toolkits/{name}", timeout=5)

        if response.status_code == 200:
            registry_metadata = response.json()
            latest_version = registry_metadata.get('latest_version', 'unknown')
            console.print(f"[green]✓ Found {name} in registry (v{latest_version})[/green]")
            console.print("Pre-filling metadata from registry...")
        elif response.status_code == 404:
            console.print(f"[dim]Toolkit not found in registry. Creating new template...[/dim]")
        else:
            console.print(f"[yellow]⚠ Could not check registry (status {response.status_code})[/yellow]")
    except requests.exceptions.RequestException as e:
        console.print(f"[yellow]⚠ Could not connect to registry: {e}[/yellow]")
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
        console.print("  5. Run 'scitoolkit validate' to check everything is correct")

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
                    console.print(f"  [yellow]💡[/yellow] {warning}")

            sys.exit(1)

    except Exception as e:
        console.print(f"\n[bold red]✗[/bold red] Error during validation: {e}", style="red")
        sys.exit(1)


@main.command()
@click.argument('toolkit_name')
def login(toolkit_name):
    """
    Authenticate for publishing a specific toolkit.

    Prompts for the toolkit's publish token and stores it securely.
    Get your toolkit token from https://scitoolkit.org after creating the toolkit.

    Example:
        scitoolkit login my-toolkit
    """
    console.print(f"\n[bold blue]Authenticating for toolkit: {toolkit_name}[/bold blue]\n")
    console.print("Get your toolkit token from: [link]https://scitoolkit.org[/link]")
    console.print(f"(Create the toolkit '{toolkit_name}' first, then copy its publish token)\n")

    token = click.prompt("Enter your toolkit token", hide_input=True)

    if not token.startswith('toolkit_'):
        console.print("[yellow]⚠ Warning: Token should start with 'toolkit_'[/yellow]")
        if not click.confirm("Continue anyway?"):
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
def publish(dry_run):
    """
    Publish toolkit to SciToolkit registry.

    Packages the current directory as a tarball and uploads it to the registry.
    Requires a valid toolkit token stored via 'scitoolkit login {toolkit_name}'.

    Example:
        scitoolkit publish
        scitoolkit publish --dry-run  # Test without uploading
    """
    console.print("\n[bold blue]📦 Publishing toolkit to SciToolkit registry...[/bold blue]\n")

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

    console.print(f"📦 Toolkit: [bold]{toolkit_name}[/bold]")
    console.print(f"🏷️  Version: [bold]{version}[/bold]\n")

    # Step 2: Validate toolkit structure
    console.print("🔍 Validating toolkit structure...")

    from .validation import validate_toolkit

    result = validate_toolkit(Path.cwd())
    if not result.is_valid:
        console.print("[red]✗ Validation failed:[/red]")
        for error in result.errors:
            console.print(f"  [red]•[/red] {error}")
        console.print("\nRun 'scitoolkit validate' for details.")
        sys.exit(1)

    console.print("[green]✓ Toolkit structure is valid[/green]\n")

    # Step 3: Read authentication token
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

    console.print(f"🔑 Using token from: [dim]{token_path}[/dim]\n")

    # Step 4: Create tarball
    console.print("📦 Creating tarball...")

    tarball_name = f"{toolkit_name}-{version}.tar.gz"
    tarball_path = Path(tempfile.gettempdir()) / tarball_name

    try:
        create_tarball(Path.cwd(), tarball_path, toolkit_name)
        file_size_mb = tarball_path.stat().st_size / (1024 * 1024)
        console.print(f"[green]✓ Created {tarball_name} ({file_size_mb:.2f} MB)[/green]\n")
    except Exception as e:
        console.print(f"[red]✗ Error creating tarball: {e}[/red]")
        sys.exit(1)

    if dry_run:
        console.print("[yellow]🧪 Dry run mode - skipping upload[/yellow]")
        console.print(f"Tarball created at: {tarball_path}")
        console.print("\nTo publish for real, run: scitoolkit publish")
        return

    # Step 5: Upload to backend
    console.print("🚀 Uploading to registry...")

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
            console.print(f"📦 Toolkit: {data['toolkit_name']}")
            console.print(f"🏷️  Version: {data['version']}")
            console.print(f"📊 Size: {data['file_size'] / (1024*1024):.2f} MB")
            console.print(f"📅 Published: {data['published_at']}")
            console.print(f"\n🌐 View at: [link]https://scitoolkit.org/toolkit/{toolkit_name}[/link]")

        elif response.status_code == 409:
            console.print(f"\n[yellow]⚠ Version {version} already exists for {toolkit_name}[/yellow]")
            console.print("Increment the version in scitoolkit.yaml to publish a new version.")
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
    console.print("[bold yellow]⚠[/bold yellow] The search command is not yet implemented.")
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
        with console.status("[bold blue]Installing dependencies..."):
            subprocess.run(
                [str(pip_path), 'install', '-r', str(requirements_path), '--quiet'],
                check=True,
                capture_output=True
            )
        console.print("[green]✓ Dependencies installed[/green]")
    else:
        console.print("[yellow]⚠ No requirements.txt found (toolkit has no dependencies)[/yellow]")

    # Install orchestral-ai (required for all toolkits)
    with console.status("[bold blue]Installing orchestral-ai..."):
        subprocess.run(
            [str(pip_path), 'install', 'orchestral-ai', '--quiet'],
            check=True,
            capture_output=True
        )
    console.print("[green]✓ Orchestral installed[/green]")

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
        with console.status(f"[bold blue]Installing dependencies in '{env_name}'..."):
            try:
                subprocess.run(
                    [conda_cmd, 'run', '-n', env_name, 'pip', 'install', '-r', str(requirements_path), '--quiet'],
                    check=True,
                    capture_output=True,
                    text=True
                )
                console.print("[green]✓ Dependencies installed[/green]")
            except subprocess.CalledProcessError as e:
                console.print(f"[yellow]⚠ Some dependencies failed to install[/yellow]")
                if e.stderr:
                    console.print(f"[dim]{e.stderr[:300]}[/dim]")
                # Don't raise - might be non-critical
    else:
        console.print("[dim]No requirements.txt found[/dim]")

    # Install orchestral-ai (required for all toolkits)
    with console.status(f"[bold blue]Installing orchestral in '{env_name}'..."):
        try:
            subprocess.run(
                [conda_cmd, 'run', '-n', env_name, 'pip', 'install', 'orchestral-ai', '--quiet'],
                check=True,
                capture_output=True,
                text=True
            )
        except subprocess.CalledProcessError as e:
            console.print(f"[red]✗ Failed to install orchestral[/red]")
            if e.stderr:
                console.print(f"[red]Error: {e.stderr[:500]}[/red]")
            raise

    console.print("[green]✓ Orchestral installed[/green]")

    return env_name


@main.command()
@click.argument('name')
@click.option('--version', '-v', help='Specific version to install (default: latest)')
def install(name, version):
    """
    Install a toolkit from the registry.

    This will:
    1. Download the toolkit
    2. Create an isolated environment
    3. Install dependencies

    Example:
        scitoolkit install aster
        scitoolkit install aster --version 1.2.0
    """
    console.print(f"\n[bold blue]📥 Installing toolkit: {name}[/bold blue]\n")

    # Step 1: Fetch toolkit metadata from registry
    console.print("🔍 Fetching toolkit metadata...")

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
            console.print(f"[green]✓ Found {name} v{version} (latest)[/green]\n")
        else:
            # Verify version exists
            available_versions = toolkit_meta.get('versions', [])
            version_numbers = [v.get('version') for v in available_versions if isinstance(v, dict)]
            if version not in version_numbers:
                console.print(f"[red]✗ Version {version} not found[/red]")
                if version_numbers:
                    console.print(f"Available versions: {', '.join(version_numbers)}")
                sys.exit(1)
            console.print(f"[green]✓ Found {name} v{version}[/green]\n")

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
                    console.print(f"[yellow]⚠ {name} v{version} is already installed[/yellow]")
                    if not click.confirm("Reinstall?", default=False):
                        sys.exit(0)
                else:
                    console.print(f"[yellow]⚠ {name} v{installed_version} is already installed[/yellow]")
                    console.print(f"Installing v{version} will replace it.")
                    if not click.confirm("Continue?", default=False):
                        sys.exit(0)
            except (json.JSONDecodeError, IOError):
                pass

        # Remove existing installation
        shutil.rmtree(toolkit_dir)

    # Step 3: Download tarball
    console.print("📦 Downloading toolkit...")

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
            console=console
        ) as progress:
            task = progress.add_task(f"Downloading {name}-{version}.tar.gz", total=total_size)

            with open(tarball_path, 'wb') as f:
                for chunk in tarball_response.iter_content(chunk_size=8192):
                    f.write(chunk)
                    progress.update(task, advance=len(chunk))

        file_size_mb = tarball_path.stat().st_size / (1024 * 1024)
        console.print(f"[green]✓ Downloaded {name}-{version}.tar.gz ({file_size_mb:.1f} MB)[/green]\n")

    except requests.exceptions.RequestException as e:
        console.print(f"[red]✗ Download error: {e}[/red]")
        sys.exit(1)

    # Step 4: Extract tarball
    console.print(f"📂 Extracting to {toolkit_dir}...")

    toolkit_dir.mkdir(parents=True, exist_ok=True)

    try:
        import tarfile
        with tarfile.open(tarball_path, 'r:gz') as tar:
            tar.extractall(path=toolkit_dir)

        file_count = len(list(toolkit_dir.rglob('*')))
        console.print(f"[green]✓ Extracted {file_count} files[/green]\n")

        # Clean up tarball
        tarball_path.unlink()

    except Exception as e:
        console.print(f"[red]✗ Extraction error: {e}[/red]")
        sys.exit(1)

    # Step 5: Detect environment type
    console.print("🔍 Detecting environment requirements...")

    try:
        toolkit_config = load_toolkit_yaml(toolkit_dir)
        env_type, python_version = detect_environment_type(toolkit_dir, toolkit_config)

        # Display detection result
        env_icons = {
            'venv': '🐍',
            'conda': '🅒',
            'docker': '🐳'
        }
        icon = env_icons.get(env_type, '❓')

        console.print(f"[green]✓ Environment: {icon} {env_type} (Python {python_version})[/green]\n")

        # Special messages for conda/docker
        if env_type == 'conda' and not has_conda():
            console.print("[yellow]⚠ Warning: Conda not detected. Install conda/mamba or use Docker mode.[/yellow]\n")
        elif env_type == 'docker':
            if (toolkit_dir / 'Dockerfile').exists():
                console.print("[blue]ℹ Docker mode: Toolkit has custom Dockerfile[/blue]")
            else:
                current_py = get_current_python()
                if python_version != current_py:
                    console.print(f"[blue]ℹ Docker mode: Requires Python {python_version} (current: {current_py})[/blue]")
            console.print("[yellow]⚠ Docker mode will be available in Phase 3B[/yellow]\n")

    except FileNotFoundError as e:
        console.print(f"[red]✗ Error: {e}[/red]")
        sys.exit(1)
    except yaml.YAMLError as e:
        console.print(f"[red]✗ Invalid toolkit.yaml: {e}[/red]")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]✗ Environment detection error: {e}[/red]")
        sys.exit(1)

    # Step 6: Setup environment
    console.print()
    python_path = None
    env_name = None

    try:
        if env_type == 'venv':
            console.print("[bold blue]🔧 Setting up environment...[/bold blue]\n")
            python_path = setup_venv_environment(toolkit_dir, console)

        elif env_type == 'conda':
            verify_conda_available()
            console.print("[bold blue]🔧 Setting up environment...[/bold blue]\n")
            env_name = setup_conda_environment(toolkit_dir, name, python_version, console)

        elif env_type == 'docker':
            console.print("[yellow]⚠ Docker mode will be available in Phase 3B[/yellow]")
            console.print("[yellow]  Environment detection complete, but Docker setup deferred.[/yellow]\n")

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

    # Step 7: Save metadata with environment info
    meta = {
        'name': name,
        'version': version,
        'environment': env_type,
        'python_version': python_version,
        'needs_setup': env_type == 'docker',  # Only docker needs future setup
        'installed_at': datetime.now().isoformat(),
        'tools_count': len(toolkit_config.get('tools', [])) if 'tools' in toolkit_config else 0,
        'has_skills': (toolkit_dir / 'skills').exists()
    }

    # Add environment-specific fields
    if env_type == 'venv':
        meta['python_path'] = str(python_path)
    elif env_type == 'conda':
        meta['env_name'] = env_name

    meta_file = toolkit_dir / '.stk_meta.json'
    meta_file.write_text(json.dumps(meta, indent=2))

    # Step 8: Success message
    console.print(f"\n[bold green]✅ Successfully installed {name} v{version}![/bold green]\n")

    # Environment summary
    env_icons = {
        'venv': '🐍',
        'conda': '🅒',
        'docker': '🐳'
    }
    icon = env_icons.get(env_type, '❓')

    if env_type == 'venv':
        console.print(f"Environment: {icon} venv (Python {python_version})")
    elif env_type == 'conda':
        console.print(f"Environment: {icon} conda environment '{env_name}' (Python {python_version})")
    elif env_type == 'docker':
        console.print(f"Environment: {icon} docker (setup pending)")

    # Tools and skills info
    tools_count = meta['tools_count']
    if tools_count > 0:
        console.print(f"Tools: {tools_count} available")

    if meta['has_skills']:
        skills_dir = toolkit_dir / 'skills'
        skill_files = list(skills_dir.glob('*.md')) if skills_dir.exists() else []
        console.print(f"Skills: {len(skill_files)} guides available")

    # Next steps
    console.print(f"\n[bold]Ready to use! Try:[/bold]")
    console.print(f"  [cyan]scitoolkit list[/cyan]")
    if env_type != 'docker':  # Show serve for venv and conda
        console.print(f"  [cyan]scitoolkit serve[/cyan]")
    console.print()


@main.command()
def list():
    """
    List all installed toolkits.

    Shows toolkits that have been installed locally with their versions
    and installation paths.

    Example:
        scitoolkit list
    """
    console.print("[bold yellow]⚠[/bold yellow] The list command is not yet implemented.")
    console.print("This will be added in Phase 3 of the development.")
    sys.exit(1)


@main.command()
@click.option('--port', '-p', default=3000, help='Port to run MCP server on')
@click.option('--stdio', is_flag=True, help='Use stdio transport instead of HTTP')
def serve(port, stdio):
    """
    Start MCP server for installed toolkits.

    This starts a Model Context Protocol server that exposes all installed
    toolkits to AI agents like Claude Code.

    Example:
        scitoolkit serve
        scitoolkit serve --stdio
        scitoolkit serve --port 8080
    """
    console.print("[bold yellow]⚠[/bold yellow] The serve command is not yet implemented.")
    console.print("This will be added in Phase 3 of the development.")
    sys.exit(1)


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
        # Add all files/dirs except excluded ones
        for item in source_dir.rglob('*'):
            if should_exclude(item):
                continue

            # Use relative path as arcname (no toolkit_name prefix)
            arcname = item.relative_to(source_dir)
            tar.add(item, arcname=arcname)


if __name__ == '__main__':
    main()