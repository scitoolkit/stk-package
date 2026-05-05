# Package Agent - Implement `scitoolkit publish` Command

**Date:** 2026-04-09
**Mission:** Implement the publish command to upload toolkits to the registry
**Priority:** CRITICAL - Final piece needed for MVP

---

## Context

The backend publish endpoint is **LIVE and working** at `https://api.scitoolkit.org/api/toolkits/{name}/publish`.

**What's already done:**
- ✅ Backend accepts tarball uploads with toolkit token authentication
- ✅ Backend extracts version from `scitoolkit.yaml` inside tarball
- ✅ Backend validates tarball structure (requires: scitoolkit.yaml, requirements.txt, README.md, tools/)
- ✅ Backend stores files and creates version records
- ✅ `scitoolkit init` creates proper Orchestral-compliant structure
- ✅ `scitoolkit validate` checks structure
- ✅ `scitoolkit login {toolkit_name}` stores token at `~/.scitoolkit/{toolkit_name}/token`

**What's missing:**
- ❌ `scitoolkit publish` command implementation

---

## Your Mission

Implement the `scitoolkit publish` command that:
1. Reads `scitoolkit.yaml` from current directory
2. Validates toolkit structure
3. Creates tarball of the toolkit
4. Reads authentication token from `~/.scitoolkit/{toolkit_name}/token`
5. Uploads tarball to backend API
6. Displays success/error messages

---

## Backend API Specification

### Endpoint: POST /api/toolkits/{name}/publish

**URL:** `https://api.scitoolkit.org/api/toolkits/{toolkit_name}/publish`

**Authentication:** `Authorization: Bearer {toolkit_token}`

**Request:**
```http
POST /api/toolkits/aster/publish HTTP/1.1
Host: api.scitoolkit.org
Authorization: Bearer toolkit_a1b2c3d4e5f6...
Content-Type: multipart/form-data; boundary=----WebKitFormBoundary

------WebKitFormBoundary
Content-Disposition: form-data; name="file"; filename="aster-0.1.0.tar.gz"
Content-Type: application/gzip

<binary tarball data>
------WebKitFormBoundary--
```

**Important:**
- NO separate `version` form field needed
- Backend extracts version from `scitoolkit.yaml` INSIDE the tarball
- Tarball must be named: `{toolkit_name}-{version}.tar.gz`

**Response (Success):**
```json
{
  "status": "published",
  "toolkit_name": "aster",
  "version": "0.1.0",
  "file_size": 2485760,
  "published_at": "2026-04-09T14:30:00Z",
  "message": "Successfully published aster v0.1.0"
}
```

**Response (Error - Bad Token):**
```json
{
  "detail": "Invalid token"
}
```

**Response (Error - Duplicate Version):**
```json
{
  "detail": "Version 0.1.0 already exists for toolkit aster"
}
```

**Response (Error - Invalid Structure):**
```json
{
  "detail": "Tarball validation failed: missing scitoolkit.yaml"
}
```

---

## Implementation Guide

### Step 1: Add `publish` Command to CLI

**File:** `scitoolkit/cli.py`

```python
import click
import yaml
import tarfile
import tempfile
import requests
from pathlib import Path
from rich.console import Console
from rich.progress import Progress

console = Console()

@cli.command()
@click.option('--force', is_flag=True, help='Overwrite existing version')
def publish(force):
    """
    Publish toolkit to SciToolkit registry.

    Packages the current directory as a tarball and uploads it to the registry.
    Requires a valid toolkit token stored via 'scitoolkit login'.
    """
    console.print("\n[bold blue]Publishing toolkit to SciToolkit registry...[/bold blue]\n")

    # Step 1: Find and read scitoolkit.yaml
    yaml_path = Path.cwd() / 'scitoolkit.yaml'
    if not yaml_path.exists():
        console.print("[red]Error: scitoolkit.yaml not found in current directory[/red]")
        console.print("Make sure you're in the toolkit root directory.")
        raise click.Abort()

    try:
        with open(yaml_path, 'r') as f:
            config = yaml.safe_load(f)
    except Exception as e:
        console.print(f"[red]Error reading scitoolkit.yaml: {e}[/red]")
        raise click.Abort()

    toolkit_name = config.get('name')
    version = config.get('version')

    if not toolkit_name or not version:
        console.print("[red]Error: scitoolkit.yaml must contain 'name' and 'version' fields[/red]")
        raise click.Abort()

    console.print(f"📦 Toolkit: [bold]{toolkit_name}[/bold]")
    console.print(f"🏷️  Version: [bold]{version}[/bold]\n")

    # Step 2: Validate toolkit structure
    console.print("🔍 Validating toolkit structure...")

    from scitoolkit.validation import validate_toolkit

    try:
        validate_toolkit(Path.cwd())
        console.print("[green]✓ Toolkit structure is valid[/green]\n")
    except Exception as e:
        console.print(f"[red]✗ Validation failed: {e}[/red]")
        console.print("\nRun 'scitoolkit validate' for details.")
        raise click.Abort()

    # Step 3: Read authentication token
    token_path = Path.home() / '.scitoolkit' / toolkit_name / 'token'

    if not token_path.exists():
        console.print(f"[red]Error: No authentication token found for '{toolkit_name}'[/red]")
        console.print(f"\nRun 'scitoolkit login {toolkit_name}' to authenticate.")
        raise click.Abort()

    try:
        token = token_path.read_text().strip()
    except Exception as e:
        console.print(f"[red]Error reading token: {e}[/red]")
        raise click.Abort()

    console.print(f"🔑 Using token from: {token_path}\n")

    # Step 4: Create tarball
    console.print("📦 Creating tarball...")

    tarball_name = f"{toolkit_name}-{version}.tar.gz"
    tarball_path = Path(tempfile.gettempdir()) / tarball_name

    try:
        create_tarball(Path.cwd(), tarball_path, toolkit_name)
        file_size_mb = tarball_path.stat().st_size / (1024 * 1024)
        console.print(f"[green]✓ Created {tarball_name} ({file_size_mb:.2f} MB)[/green]\n")
    except Exception as e:
        console.print(f"[red]Error creating tarball: {e}[/red]")
        raise click.Abort()

    # Step 5: Upload to backend
    console.print("🚀 Uploading to registry...")

    api_url = "https://api.scitoolkit.org"
    upload_url = f"{api_url}/api/toolkits/{toolkit_name}/publish"

    try:
        with open(tarball_path, 'rb') as f:
            files = {'file': (tarball_name, f, 'application/gzip')}
            headers = {'Authorization': f'Bearer {token}'}

            with Progress() as progress:
                task = progress.add_task("[cyan]Uploading...", total=100)

                response = requests.post(
                    upload_url,
                    files=files,
                    headers=headers,
                    timeout=300  # 5 minutes
                )

                progress.update(task, completed=100)

        # Clean up temp file
        tarball_path.unlink()

        if response.status_code == 201:
            data = response.json()
            console.print("\n[bold green]✓ Successfully published![/bold green]\n")
            console.print(f"Toolkit: {data['toolkit_name']}")
            console.print(f"Version: {data['version']}")
            console.print(f"Size: {data['file_size'] / (1024*1024):.2f} MB")
            console.print(f"Published: {data['published_at']}")
            console.print(f"\n🌐 View at: https://scitoolkit.org/toolkit/{toolkit_name}")

        elif response.status_code == 409:
            error = response.json()
            console.print(f"\n[yellow]Version {version} already exists.[/yellow]")
            console.print(f"Increment the version in scitoolkit.yaml or use --force to overwrite.")
            raise click.Abort()

        elif response.status_code == 401:
            console.print("\n[red]Authentication failed. Invalid token.[/red]")
            console.print(f"Run 'scitoolkit login {toolkit_name}' to re-authenticate.")
            raise click.Abort()

        else:
            error = response.json()
            console.print(f"\n[red]Upload failed: {error.get('detail', 'Unknown error')}[/red]")
            raise click.Abort()

    except requests.exceptions.RequestException as e:
        console.print(f"\n[red]Network error: {e}[/red]")
        console.print("Please check your internet connection and try again.")
        raise click.Abort()
    except Exception as e:
        console.print(f"\n[red]Unexpected error: {e}[/red]")
        raise click.Abort()


def create_tarball(source_dir: Path, output_path: Path, toolkit_name: str):
    """
    Create a gzipped tarball of the toolkit.

    Excludes: .git/, __pycache__/, *.pyc, .DS_Store, venv/, .venv/
    """
    exclude_patterns = {
        '.git', '__pycache__', '.pyc', '.DS_Store',
        'venv', '.venv', '.pytest_cache', '.mypy_cache',
        '*.egg-info', 'dist', 'build'
    }

    def should_exclude(path: Path) -> bool:
        """Check if path should be excluded from tarball."""
        for part in path.parts:
            if part in exclude_patterns:
                return True
            if any(part.endswith(pattern) for pattern in exclude_patterns if pattern.startswith('*')):
                return True
        return False

    with tarfile.open(output_path, 'w:gz') as tar:
        # Add all files/dirs except excluded ones
        for item in source_dir.rglob('*'):
            if should_exclude(item.relative_to(source_dir)):
                continue

            arcname = item.relative_to(source_dir)
            tar.add(item, arcname=arcname)
```

---

## Step 2: Update `login` Command

The `scitoolkit login` command needs to store the token for a specific toolkit.

**Current behavior:** Stores global API key
**New behavior:** Stores toolkit-specific token

```python
@cli.command()
@click.argument('toolkit_name')
def login(toolkit_name):
    """
    Authenticate for publishing a specific toolkit.

    Prompts for the toolkit's publish token and stores it securely.
    """
    console.print(f"\n[bold blue]Authenticating for toolkit: {toolkit_name}[/bold blue]\n")

    token = click.prompt("Enter your toolkit token", hide_input=True)

    if not token.startswith('toolkit_'):
        console.print("[yellow]Warning: Token should start with 'toolkit_'[/yellow]")
        if not click.confirm("Continue anyway?"):
            raise click.Abort()

    # Create directory for this toolkit
    config_dir = Path.home() / '.scitoolkit' / toolkit_name
    config_dir.mkdir(parents=True, exist_ok=True)

    # Store token
    token_file = config_dir / 'token'
    token_file.write_text(token)
    token_file.chmod(0o600)  # Read/write for owner only

    console.print(f"\n[green]✓ Token stored at: {token_file}[/green]")
    console.print(f"\nYou can now run 'scitoolkit publish' from the {toolkit_name} directory.")
```

---

## Step 3: Add Dependencies

Add to `pyproject.toml`:

```toml
[project]
dependencies = [
    "click>=8.0.0",
    "pyyaml>=6.0",
    "requests>=2.28.0",
    "rich>=13.0.0",
    # ... existing dependencies
]
```

---

## Testing Strategy

### Local Testing

1. **Create test toolkit:**
   ```bash
   cd /tmp
   scitoolkit init test-toolkit
   cd test-toolkit
   ```

2. **Get token from web:**
   - Go to https://scitoolkit.org
   - Create toolkit "test-toolkit"
   - Copy token

3. **Authenticate:**
   ```bash
   scitoolkit login test-toolkit
   # Paste token when prompted
   ```

4. **Edit scitoolkit.yaml:**
   ```yaml
   name: test-toolkit
   version: 0.1.0  # Make sure version is set
   category: other
   description: Test toolkit for publish command
   # ...
   ```

5. **Publish:**
   ```bash
   scitoolkit publish
   ```

6. **Verify:**
   - Check output for success message
   - Visit https://scitoolkit.org/toolkit/test-toolkit
   - Should show version 0.1.0
   - Download link should work

7. **Test duplicate version:**
   ```bash
   scitoolkit publish
   # Should fail with "Version 0.1.0 already exists"
   ```

8. **Test version increment:**
   ```bash
   # Edit scitoolkit.yaml → version: 0.1.1
   scitoolkit publish
   # Should succeed, creating second version
   ```

---

## Error Handling

Handle these error cases:

1. **No scitoolkit.yaml:** Clear error message
2. **Missing version field:** Error with example
3. **No token stored:** Prompt to run `scitoolkit login`
4. **Invalid token:** Suggest re-authenticating
5. **Network errors:** Suggest checking connection
6. **Duplicate version:** Suggest incrementing version
7. **Validation failures:** Reference `scitoolkit validate`
8. **Large files:** Show progress bar for uploads >5MB

---

## Success Criteria

- ✅ `scitoolkit publish` uploads tarball to backend
- ✅ Backend creates version record
- ✅ Toolkit status changes from 'unpublished' to 'published'
- ✅ Version appears on dashboard
- ✅ Download works via `GET /api/download/{name}/{version}`
- ✅ Error messages are clear and actionable
- ✅ Token stored securely (chmod 600)

---

## Next Steps (After This)

Once `publish` is working:

1. **Package Agent:** Implement `scitoolkit install` command
2. **Frontend Agent:** Add toolkit management UX (versions tab, metadata editor)
3. **Package Agent:** Implement `scitoolkit serve` (MCP server)
4. **🎉 MVP COMPLETE**

---

**You have everything you need!** The backend is live and ready. Let's complete the publish command and achieve MVP! 🚀
