# Toolkit Setup System - Specification

**Status:** Draft for Phase 3B
**Date:** 2026-04-20
**Author:** Manager Agent

---

## Problem Statement

Many scientific toolkits need configuration beyond their bundled code:

- **Data files:** Large datasets that can't be bundled (e.g., ASTER's opacity files ~2.3GB)
- **API keys:** Credentials for external services (OpenAI, NASA APIs)
- **Paths:** Where to write outputs, where to find local data
- **Environment variables:** System-level config the toolkit depends on
- **Stateful tools:** Tools that need injected state (e.g., `base_directory`) not provided by the agent at runtime

Scientists should not have to learn a new system for each toolkit. The setup experience must be:

1. **Consistent** - All toolkits look and feel the same
2. **Simple** - Trivial cases need ~3 lines of code
3. **Flexible** - Complex cases (ASTER) are still possible
4. **Safe** - No config means no serving; user is warned clearly

---

## Core Model

Every tool is a function with two kinds of arguments:

```
tool(RuntimeFields, StateFields)
       ↑              ↑
  Agent provides   Toolkit provides
  (visible in MCP) (invisible to MCP)
```

- **RuntimeFields** are what the AI agent sees and fills in (e.g., `star_name`, `planet_params`)
- **StateFields** are injected by SciToolkit from toolkit config (e.g., `opacity_path`, `base_directory`, `api_key`)

MCP sees only RuntimeFields. StateFields are invisible to the protocol - SciToolkit handles them at the execution layer.

---

## Two-Tier Architecture

### Tier 1: Declarative (toolkit.yaml)

For simple config collection. No validation, no logic, just "ask the user for these values and save them."

```yaml
# toolkit.yaml
config:
  opacity_path:
    type: path
    description: "Path to opacity data files"
    required: true

  max_workers:
    type: integer
    description: "Number of parallel workers"
    default: 4

  api_endpoint:
    type: string
    description: "API endpoint URL"
    default: "https://api.example.com"

env_vars:
  required:
    - OPENAI_API_KEY
  optional:
    - ANTHROPIC_API_KEY
```

**Supported types:**
- `path` - File or directory path (expanded, `~` allowed)
- `string` - Plain text
- `secret` - Hidden input (for API keys)
- `integer` - Whole number
- `boolean` - True/false
- `choice` - One of a fixed list

**What declarative does NOT do:**
- File existence validation
- File content validation (e.g., "must be .h5")
- Downloads
- Multi-step workflows
- Any custom logic

If you need any of that, use Tier 2.

### Tier 2: Script (setup.py)

For anything non-trivial. A Python file at the toolkit root that SciToolkit calls.

```python
# setup.py
from scitoolkit.setup import SetupContext

def setup(ctx: SetupContext) -> bool:
    """Interactive setup. Called by: scitoolkit setup <toolkit>"""
    # ... logic here ...
    return True

def validate(ctx: SetupContext) -> bool:
    """Check if setup is complete. Called by: scitoolkit serve"""
    # ... logic here ...
    return True
```

**Activated by:**
```yaml
# toolkit.yaml
setup_script: true
```

When `setup_script: true`, SciToolkit:
1. Prompts for declarative config first (if any)
2. Runs `setup.py::setup(ctx)` with context pre-loaded from declarative
3. On `scitoolkit serve`, calls `setup.py::validate(ctx)` to check readiness

---

## SetupContext API

The `ctx` object passed to `setup()` and `validate()` is a toolbox provided by SciToolkit.

### Output (print to user)

```python
ctx.info("Checking dependencies...")      # Blue info
ctx.warn("This will take a while")        # Yellow warning
ctx.error("File not found")               # Red error
ctx.hint("Try: scitoolkit setup aster")   # Dim hint
ctx.success("All set!")                   # Green success
```

All output uses consistent styling (Rich-based). Authors don't import Rich themselves.

### Input (prompt user)

```python
# Simple string prompt
name = ctx.prompt("Enter toolkit name:")
name = ctx.prompt("Enter name:", default="aster")

# Typed prompts (with auto-validation)
path = ctx.prompt_path("Data path:")              # Expands ~, returns Path
path = ctx.prompt_path("Data path:", must_exist=True)  # Errors if missing
port = ctx.prompt_int("Port number:", default=8080)
count = ctx.prompt_int("Count:", min=1, max=100)

# Secrets (hidden input)
key = ctx.prompt_secret("API key:")

# Yes/no
proceed = ctx.confirm("Download 2.3GB of data?", default=False)

# Menu choice
choice = ctx.choice(
    "How would you like to proceed?",
    [
        ("download", "Download automatically (~2.3GB)"),
        ("path", "I have the data, let me provide the path"),
        ("cancel", "Cancel setup"),
    ]
)
# Returns the key: "download", "path", or "cancel"
```

### Config (read/write toolkit config)

```python
# Read current config (from .env file)
path = ctx.get_config('opacity_path')
path = ctx.get_config('opacity_path', default='~/.aster/opacity')

# Write config (auto-saves to .env)
ctx.set_config('opacity_path', '/data/opacity')
ctx.set_config('use_gpu', True)

# Bulk access
all_config = ctx.config   # Dict-like access
```

### Downloads

```python
# Download a file with progress bar
ctx.download(
    url="https://data.scitoolkit.org/aster/opacity.tar.gz",
    destination=Path.home() / '.aster' / 'opacity',
    description="Downloading opacity data",
    size_hint="2.3GB",        # For progress display
    extract=True,             # Auto-extract .tar.gz, .zip
    sha256="abc123..."        # Optional checksum verification
)
```

### Paths

```python
# Well-known paths
ctx.toolkit_path          # Path to extracted toolkit
ctx.data_dir              # ~/.scitoolkit/data/<toolkit_name>/ (auto-created)
ctx.cache_dir             # ~/.scitoolkit/cache/<toolkit_name>/
```

### Raw Python

SetupContext is not a walled garden. Authors can drop to raw Python at any time:

```python
def setup(ctx):
    # Use subprocess, requests, any library
    import subprocess
    result = subprocess.run(['nvcc', '--version'], capture_output=True)

    if result.returncode == 0:
        ctx.success("CUDA detected")
        ctx.set_config('use_gpu', True)
    else:
        ctx.warn("CUDA not found, using CPU mode")
        ctx.set_config('use_gpu', False)

    return True
```

---

## Runtime: How StateFields Reach Tools

### Storage

After setup, config is stored in:
```
~/.scitoolkit/toolkits/<toolkit_name>/.env
```

### Execution Flow

When `scitoolkit serve` invokes a tool:

1. Load `.env` for the toolkit
2. Validate required values exist (call `setup.py::validate()` if present)
3. Construct subprocess environment:
   - User's env vars (inherited)
   - Toolkit's `.env` contents
   - Bundled as `SCITOOLKIT_CONFIG` JSON var
4. Run tool in subprocess with this environment

### Tool Code

Tools access StateFields via a helper:

```python
from orchestral import define_tool
from scitoolkit.runtime import get_config
import json

@define_tool
def run_forward_model(planet_params: dict) -> str:
    """
    Run ASTER forward model.

    Args:
        planet_params: Planet parameters (radius, mass, period)
    """
    # RuntimeField: planet_params (from agent)
    # StateFields: from config
    opacity_path = get_config('opacity_path')
    base_dir = get_config('base_directory')
    max_workers = get_config('max_workers', default=4)

    result = aster_model(
        planet_params,
        opacity_path=opacity_path,
        output_dir=base_dir,
        n_workers=max_workers
    )
    return json.dumps(result)
```

The tool signature only contains RuntimeFields. StateFields come from `get_config()`.

---

## User-Facing Commands

### `scitoolkit install <toolkit>`

Normal install + setup detection.

```
📥 Installing toolkit: aster
✓ Downloaded and extracted
✓ Environment created (venv, Python 3.12)
✓ Dependencies installed

⚠️  ASTER requires additional setup

Required configuration:
  • opacity_path (path to opacity data)
  • max_workers (default: 4)

Run: scitoolkit setup aster
```

### `scitoolkit setup <toolkit>`

Runs interactive setup.

```
ASTER Setup
───────────

1. opacity_path: Path to opacity data files
   [no current value]

How would you like to proceed?
  [1] Download automatically (~2.3GB)
  [2] I have the data - let me provide the path
  [3] Skip for now

Choice: 1

Downloading opacity data...
[████████████████] 2.3GB / 2.3GB (12.5 MB/s)
✓ Extracted to ~/.scitoolkit/data/aster/opacity

✓ Found 47 opacity files

2. max_workers (default: 4): [press Enter for default]

✅ ASTER setup complete!

Run: scitoolkit serve
```

### `scitoolkit setup <toolkit> --reset`

Clears current config and re-runs setup.

### `scitoolkit setup <toolkit> --check`

Validates current config without re-running setup. Useful for debugging.

### `scitoolkit serve`

Skips toolkits whose validate() returns False:

```
Checking toolkits...
  ✓ simple-api: Ready (3 tools)
  ✗ aster: Setup incomplete
      └─ opacity_path does not contain .h5 files
      └─ Run: scitoolkit setup aster

Starting MCP server with 1 toolkit (3 tools)...
```

---

## Error Handling Principles

1. **Never let a broken toolkit break the server.** `serve` skips invalid toolkits rather than crashing.

2. **Always tell the user what to do next.** Every error must have a hint:
   ```
   ✗ opacity_path does not exist
     Hint: Run `scitoolkit setup aster` to configure
   ```

3. **`setup.py` errors are caught and shown nicely.** A traceback from setup.py should be logged but not scare the user. Show:
   ```
   ✗ Setup failed: Could not reach download server

   Technical details logged to ~/.scitoolkit/logs/setup-aster-2026-04-20.log
   ```

4. **Partial setup is valid.** User can skip optional fields, come back later with `scitoolkit setup <toolkit>`.

---

## Security Considerations

### setup.py Executes Code

`setup.py` runs arbitrary Python. This is reviewed during manual curation.

Mitigations:
- Manual review before publication (Phase 1-3)
- Future: automated scanning (Bandit)
- setup.py runs in toolkit's isolated env (venv/conda/docker), not system Python
- SetupContext provides safe defaults (downloads verify SHA256, prompts sanitize input)

### Config Storage

`.env` files are plain text in `~/.scitoolkit/toolkits/<name>/.env`.

- User's responsibility to protect `~/.scitoolkit/`
- `secret` type config is never logged or displayed after entry
- `scitoolkit list` does not show secret values

### Subprocess Injection

StateFields are passed via env vars to the subprocess, not via command-line args (prevents accidental shell injection).

---

## Implementation Tasks

### Phase 3A-setup (after install command stabilizes)

1. **scitoolkit/setup/__init__.py** - SetupContext class
2. **scitoolkit/setup/declarative.py** - YAML-based config collection
3. **scitoolkit/setup/runner.py** - Invokes setup.py in toolkit env
4. **scitoolkit/runtime.py** - `get_config()` helper
5. **cli.py** - `setup` command (interactive + `--reset`, `--check`)
6. **cli.py** - Update `install` to detect setup requirement
7. **cli.py** - Update `serve` to call `validate()` before serving
8. **Templates** - Update `scitoolkit init` to scaffold setup.py

### Phase 3B-setup (polish)

1. Progress bars for downloads
2. SHA256 verification
3. Auto-extract tarballs/zips
4. `--reset` and `--check` flags
5. Setup logging to `~/.scitoolkit/logs/`

---

## Adoption Strategy

The system's flexibility is only valuable if authors actually use it. Key tactics:

1. **`scitoolkit init` creates working setup.py template** with comments
2. **Recipes doc** (separate file) shows copy-paste solutions
3. **ASTER is the flagship example** - its setup.py is the reference
4. **Lint warnings** - `scitoolkit validate` flags anti-patterns:
   - `input()` calls → "Use ctx.prompt() for consistent styling"
   - Direct `.env` writes → "Use ctx.set_config() instead"
   - Hardcoded paths → "Consider making this configurable"

See [SETUP_RECIPES.md](SETUP_RECIPES.md) for the recipes guide.

---

## Open Questions

1. **Should `setup.py` run in toolkit's isolated env or scitoolkit's env?**
   - Isolated: toolkit can import its own deps during setup
   - scitoolkit env: safer but limited
   - **Tentative decision:** Isolated (toolkit env). Some setups need toolkit-specific libs (e.g., ASTER's opacity downloader might use astropy).

2. **How do we handle updates to config schema across toolkit versions?**
   - If `aster 1.0` needs `opacity_path` and `aster 2.0` also needs `cache_dir`, what happens on upgrade?
   - **Tentative decision:** On install over existing, preserve old config, prompt for new fields only.

3. **Should config be sharable across machines?**
   - Users may want to export/import config (e.g., lab workstation → laptop)
   - **Tentative decision:** Defer. `scitoolkit setup <toolkit> --from-file config.env` could be added later.

4. **What about multi-profile support?**
   - User wants "dev" and "prod" ASTER configs
   - **Tentative decision:** Out of scope for MVP.

---

## See Also

- [SETUP_RECIPES.md](SETUP_RECIPES.md) - Copy-paste examples for common patterns
- [TOOLKIT_FORMAT_GUIDE.md](../../TOOLKIT_FORMAT_GUIDE.md) - Overall toolkit structure
- [PLATFORM_DECISIONS.md](../../PLATFORM_DECISIONS.md) - Architectural decisions
