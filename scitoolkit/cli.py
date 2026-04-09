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
import sys
from pathlib import Path

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

    Creates a new toolkit directory with the standard structure:
    - scitoolkit.yaml (metadata)
    - tools/ (tool definitions)
    - requirements.txt (dependencies)
    - README.md (documentation)
    - Dockerfile (optional, if --with-docker is used)

    Example:
        scitoolkit init my-awesome-toolkit
        scitoolkit init my-toolkit --with-docker
    """
    from .toolkit import create_toolkit_from_template

    # Interactive mode if no name provided
    if not name:
        console.print(Panel.fit(
            "[bold cyan]SciToolkit Initialization[/bold cyan]\n"
            "Let's create your new toolkit!",
            border_style="cyan"
        ))
        name = click.prompt('Toolkit name', type=str)

    # Determine target path
    target_path = Path(path) if path else Path.cwd() / name

    try:
        toolkit_path = create_toolkit_from_template(
            name=name,
            path=target_path,
            with_docker=with_docker
        )

        console.print(f"\n[bold green]✓[/bold green] Toolkit created at: [cyan]{toolkit_path}[/cyan]")
        console.print("\n[bold]Next steps:[/bold]")
        console.print(f"  1. cd {name}")
        console.print("  2. Edit scitoolkit.yaml with your toolkit details")
        console.print("  3. Add your tools in the tools/ directory")
        console.print("  4. Update requirements.txt with dependencies")
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
            sys.exit(1)

    except Exception as e:
        console.print(f"\n[bold red]✗[/bold red] Error during validation: {e}", style="red")
        sys.exit(1)


@main.command()
@click.option('--api-key', '-k', help='API key to store (if not provided, will prompt)')
def login(api_key):
    """
    Authenticate with SciToolkit registry.

    Stores your API key for publishing toolkits. You can generate an API key
    at https://scitoolkit.org after creating an account.

    Example:
        scitoolkit login
        scitoolkit login --api-key stk_abc123...
    """
    from .config import save_api_key, validate_api_key_format

    console.print(Panel.fit(
        "[bold cyan]SciToolkit Login[/bold cyan]\n"
        "Store your API key for publishing toolkits",
        border_style="cyan"
    ))

    # Get API key
    if not api_key:
        console.print("\nGet your API key from: [link]https://scitoolkit.org/settings[/link]")
        api_key = click.prompt('Enter API key', hide_input=True, type=str)

    # Validate format
    if not validate_api_key_format(api_key):
        console.print("[bold red]✗[/bold red] Invalid API key format. Expected: stk_...", style="red")
        sys.exit(1)

    try:
        # Save to config
        save_api_key(api_key)
        console.print("\n[bold green]✓[/bold green] API key saved successfully!")
        console.print("You can now publish toolkits with 'scitoolkit publish'")

    except Exception as e:
        console.print(f"[bold red]✗[/bold red] Error saving API key: {e}", style="red")
        sys.exit(1)


@main.command()
@click.argument('path', required=False, default='.')
@click.option('--dry-run', is_flag=True, help='Validate without uploading')
def publish(path, dry_run):
    """
    Publish a toolkit to the SciToolkit registry.

    This will:
    1. Validate the toolkit
    2. Package it into a tarball
    3. Upload to the registry

    You must be logged in first (use 'scitoolkit login').

    Example:
        scitoolkit publish
        scitoolkit publish ./my-toolkit
        scitoolkit publish --dry-run  # Test without uploading
    """
    console.print("[bold yellow]⚠[/bold yellow] The publish command is not yet implemented.")
    console.print("This will be added in Phase 2 of the development.")
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
    console.print("[bold yellow]⚠[/bold yellow] The install command is not yet implemented.")
    console.print("This will be added in Phase 3 of the development.")
    sys.exit(1)


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


if __name__ == '__main__':
    main()