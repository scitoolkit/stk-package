"""
Toolkit validation using Pydantic schemas.

Defines the schema for toolkit.yaml and provides validation functions
to ensure toolkits meet the required structure and format.
"""

import os
import sys
from pathlib import Path
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, field_validator, EmailStr
import yaml


# Hardcoded fallback for the category whitelist when the registry is
# unreachable. Must stay in sync with stk-website/lib/categories.ts
# (FALLBACK_CATEGORIES). The registry is the source of truth at runtime —
# this list is only used when the network is down or the endpoint is
# pre-deployment.
FALLBACK_CATEGORIES = [
    'astro',
    'hep',
    'quantum',
    'neutrino',
    'bio',
    'chem',
    'materials',
    'utils',
    'other',
]


_categories_cache: Optional[List[str]] = None


def get_allowed_categories() -> List[str]:
    """Return the canonical category id whitelist.

    Tries ``GET {API}/api/categories`` first; falls back to
    ``FALLBACK_CATEGORIES`` on any error (network, non-200, malformed JSON,
    timeout). Cached for the duration of a single CLI invocation to avoid
    re-fetching across multiple validations in the same run.

    Why fall back instead of failing: ``scitoolkit validate`` runs
    pre-commit and in offline CI; the registry being down must not break
    those flows. The backend re-validates on upload anyway.
    """
    global _categories_cache
    if _categories_cache is not None:
        return _categories_cache

    api_url = os.environ.get("SCITOOLKIT_API_URL", "https://api.scitoolkit.org")
    try:
        # Lazy import so plain Pydantic validation (used as a library) doesn't
        # pull in requests just to read a yaml file.
        import requests
        resp = requests.get(f"{api_url}/api/categories", timeout=5)
        if resp.status_code != 200:
            raise RuntimeError(f"status {resp.status_code}")
        data = resp.json()
        # Accept either {"categories": [...]} or a bare list.
        raw = data.get("categories") if isinstance(data, dict) else data
        if not isinstance(raw, list):
            raise RuntimeError("unexpected response shape")
        ids: List[str] = []
        for item in raw:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                ids.append(item["id"])
            elif isinstance(item, str):
                ids.append(item)
        if not ids:
            raise RuntimeError("empty category list")
        _categories_cache = ids
        return ids
    except Exception:
        print(
            "[scitoolkit] could not reach registry to verify category; "
            "using built-in list",
            file=sys.stderr,
        )
        _categories_cache = list(FALLBACK_CATEGORIES)
        return _categories_cache


def _check_skill_frontmatter(skill_path: Path) -> Optional[str]:
    """Return a human-readable warning if a skill's frontmatter is missing
    or incomplete; ``None`` if it's fine.

    Claude Code's skill discovery expects YAML frontmatter at the top of
    each ``SKILL.md`` with at least ``name`` and ``description``. We
    warn rather than error here because (a) older toolkits predate this
    requirement and (b) the install-time surfacer synthesizes frontmatter
    when missing, so the toolkit still works.
    """
    # Lazy import; we don't want validation.py to depend on the skills
    # module at import time (creates a circular if skills.py ever
    # imports validation in the future).
    try:
        from .skills import parse_frontmatter
    except Exception:
        return None
    try:
        text = skill_path.read_text(encoding="utf-8")
    except OSError:
        return None
    fm, _body = parse_frontmatter(text)
    docs_url = "https://scitoolkit.org/docs/authoring#skills"
    if fm is None:
        return (
            f"skills/{skill_path.name}: missing YAML frontmatter. "
            f"Add a `---`-delimited block at the top with `name:` and "
            f"`description:` fields. See {docs_url} for the format."
        )
    if not fm.is_complete():
        missing = [
            k for k in ("name", "description")
            if not getattr(fm, k)
        ]
        return (
            f"skills/{skill_path.name}: frontmatter missing required "
            f"field{'s' if len(missing) != 1 else ''}: "
            f"{', '.join(missing)}. See {docs_url} for the format."
        )
    return None


class ToolDefinition(BaseModel):
    """Definition of a single tool in the toolkit."""
    name: str = Field(..., description="Tool name (alphanumeric and underscores only)")
    function: str = Field(..., description="Python function path (e.g., tools.my_tool)")
    description: str = Field(..., description="Brief description of what the tool does")

    @field_validator('name')
    @classmethod
    def validate_name(cls, v):
        """Validate tool name format."""
        if not v.replace('_', '').replace('-', '').isalnum():
            raise ValueError('Tool name must be alphanumeric (underscores and hyphens allowed)')
        return v


class ToolkitMetadata(BaseModel):
    """Schema for toolkit.yaml metadata file."""
    name: str = Field(..., description="Toolkit name")
    version: str = Field(..., description="Version (semantic versioning recommended)")
    description: str = Field(..., description="Brief description of the toolkit")
    author: str = Field(..., description="Author name")
    email: Optional[EmailStr] = Field(None, description="Author email")
    license: Optional[str] = Field("MIT", description="License type")
    homepage: Optional[str] = Field(None, description="Homepage or repository URL")
    category: Optional[str] = Field(None, description="Category (astro, hep, quantum, etc.)")
    keywords: Optional[List[str]] = Field(default_factory=list, description="Keywords for search")
    python_version: Optional[str] = Field("3.11", description="Required Python version")
    expected_toolkits: Optional[List[str]] = Field(
        default_factory=list,
        description=(
            "Other toolkits this one is designed to work alongside. "
            "Surfaced on install (with offer to install them too) and "
            "rendered on the website's detail page. No runtime coupling — "
            "each runs as its own serve subprocess; the agent picks which to call."
        ),
    )
    tools: List[ToolDefinition] = Field(..., description="List of tools in this toolkit")

    @field_validator('name')
    @classmethod
    def validate_name(cls, v):
        """Validate toolkit name format."""
        if not v.replace('_', '').replace('-', '').isalnum():
            raise ValueError('Toolkit name must be alphanumeric (underscores and hyphens allowed)')
        if len(v) < 3:
            raise ValueError('Toolkit name must be at least 3 characters')
        return v.lower()

    @field_validator('version')
    @classmethod
    def validate_version(cls, v):
        """Validate version format (semantic versioning)."""
        parts = v.split('.')
        if len(parts) < 2:
            raise ValueError('Version should be in format: major.minor or major.minor.patch')
        return v

    @field_validator('expected_toolkits')
    @classmethod
    def validate_expected_toolkits(cls, v):
        """Each entry must be a valid toolkit name (the registry will
        verify existence at upload time; we just check shape here)."""
        if not v:
            return v
        for entry in v:
            if not isinstance(entry, str):
                raise ValueError(
                    f"expected_toolkits entries must be strings, got {type(entry).__name__}"
                )
            if not entry.replace('_', '').replace('-', '').isalnum():
                raise ValueError(
                    f"expected_toolkits entry '{entry}' must be alphanumeric "
                    "(underscores and hyphens allowed)"
                )
            if len(entry) < 3:
                raise ValueError(
                    f"expected_toolkits entry '{entry}' is too short (min 3 chars)"
                )
        return [e.lower() for e in v]

    @field_validator('category')
    @classmethod
    def validate_category(cls, v):
        """Validate category is one of the allowed values."""
        if v is None:
            return v

        allowed_categories = get_allowed_categories()

        if v.lower() not in allowed_categories:
            raise ValueError(f'Category must be one of: {", ".join(allowed_categories)}')

        return v.lower()


class ValidationResult(BaseModel):
    """Result of toolkit validation."""
    is_valid: bool
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    metadata: Optional[ToolkitMetadata] = None


def validate_toolkit(toolkit_path: Path) -> ValidationResult:
    """
    Validate a toolkit's structure and configuration.

    Checks:
    1. toolkit.yaml exists and is valid
    2. Required files are present
    3. Tools directory exists
    4. Tool files are present
    5. requirements.txt is parseable

    Args:
        toolkit_path: Path to toolkit directory

    Returns:
        ValidationResult with validation status and details
    """
    result = ValidationResult(is_valid=True)
    toolkit_path = Path(toolkit_path)

    # Check if path exists
    if not toolkit_path.exists():
        result.is_valid = False
        result.errors.append(f"Toolkit path does not exist: {toolkit_path}")
        return result

    if not toolkit_path.is_dir():
        result.is_valid = False
        result.errors.append(f"Toolkit path is not a directory: {toolkit_path}")
        return result

    # Check for toolkit.yaml
    yaml_file = toolkit_path / "toolkit.yaml"
    if not yaml_file.exists():
        result.is_valid = False
        result.errors.append("Missing required file: toolkit.yaml")

        # Check if subdirectories contain toolkit.yaml (helpful hint)
        subdirs_with_toolkit = []
        try:
            for item in toolkit_path.iterdir():
                if item.is_dir() and (item / "toolkit.yaml").exists():
                    subdirs_with_toolkit.append(item.name)
        except PermissionError:
            pass

        if subdirs_with_toolkit:
            result.warnings.append(
                f"Found toolkit(s) in subdirectories: {', '.join(subdirs_with_toolkit)}\n"
                f"  Hint: cd into one of these directories and run 'scitoolkit validate' again"
            )

        return result

    # Parse and validate toolkit.yaml
    try:
        with open(yaml_file, 'r') as f:
            yaml_data = yaml.safe_load(f)

        metadata = ToolkitMetadata(**yaml_data)
        result.metadata = metadata

    except yaml.YAMLError as e:
        result.is_valid = False
        result.errors.append(f"Invalid YAML in toolkit.yaml: {e}")
        return result

    except Exception as e:
        result.is_valid = False
        result.errors.append(f"Invalid toolkit.yaml: {e}")
        return result

    # Check for required files
    required_files = ['README.md', 'requirements.txt']
    for filename in required_files:
        file_path = toolkit_path / filename
        if not file_path.exists():
            result.warnings.append(f"Missing recommended file: {filename}")

    # Check for tools directory
    tools_dir = toolkit_path / "tools"
    if not tools_dir.exists():
        result.is_valid = False
        result.errors.append("Missing required directory: tools/")
        return result

    # Check that tools directory has __init__.py
    init_file = tools_dir / "__init__.py"
    if not init_file.exists():
        result.is_valid = False
        result.errors.append("Missing required file: tools/__init__.py (Orchestral requires explicit tool exports)")
    else:
        # Check that tools/__init__.py exports tools
        try:
            content = init_file.read_text()
            if '__all__' not in content and 'import' not in content:
                result.warnings.append(
                    "tools/__init__.py should export tools. "
                    "Add: from tools.your_tool import your_tool"
                )
        except Exception:
            pass

    # Check for MCP server files (required for Orchestral integration)
    mcp_dir = toolkit_path / "mcp"
    if not mcp_dir.exists():
        result.is_valid = False
        result.errors.append("Missing required directory: mcp/ (needed for MCP server)")
    else:
        mcp_files = ['server_stdio.py', '__init__.py']
        for filename in mcp_files:
            file_path = mcp_dir / filename
            if not file_path.exists():
                result.is_valid = False
                result.errors.append(f"Missing required MCP file: mcp/{filename}")

    # Check that tool files exist
    for tool in metadata.tools:
        # Parse function path (e.g., "tools.my_tool" -> "tools/my_tool.py")
        function_parts = tool.function.split('.')

        if len(function_parts) < 2:
            result.errors.append(f"Invalid function path for tool '{tool.name}': {tool.function}")
            result.is_valid = False
            continue

        # Check if the module file exists
        module_path = toolkit_path / f"{function_parts[0]}.py"
        if not module_path.exists():
            # Try as a package
            module_path = toolkit_path / function_parts[0] / f"{function_parts[1]}.py"
            if not module_path.exists():
                result.warnings.append(
                    f"Tool file not found for '{tool.name}': {function_parts[0]}/{function_parts[1]}.py"
                )

    # Validate requirements.txt if it exists
    requirements_file = toolkit_path / "requirements.txt"
    if requirements_file.exists():
        try:
            with open(requirements_file, 'r') as f:
                requirements = f.read()

            # Basic validation - just check it's readable
            if not requirements.strip():
                result.warnings.append("requirements.txt is empty")

            # Check for orchestral-ai dependency (required for Orchestral tools)
            if 'orchestral-ai' not in requirements.lower() and 'orchestral' not in requirements.lower():
                result.is_valid = False
                result.errors.append(
                    "requirements.txt must include 'orchestral-ai>=1.0.0' "
                    "(required for Orchestral tool framework)"
                )

        except Exception as e:
            result.warnings.append(f"Could not read requirements.txt: {e}")

    # Validate skills/ directory (optional)
    skills_dir = toolkit_path / 'skills'
    if skills_dir.exists():
        if not skills_dir.is_dir():
            result.errors.append("skills/ exists but is not a directory")
            result.is_valid = False
        else:
            # Check for markdown files. Filter out macOS AppleDouble files.
            skill_files = [
                p for p in skills_dir.glob('*.md')
                if not p.name.startswith('._')
            ]
            if not skill_files:
                result.warnings.append("skills/ directory exists but is empty (consider adding skill guides)")

            # Frontmatter check: each skill should carry name + description
            # at the top so Claude Code (when surfaced into ~/.claude/skills/)
            # can index it. Warning-only — backward compat with toolkits
            # that predate the requirement; the install-time surfacer
            # synthesizes frontmatter when missing.
            for sf in skill_files:
                fm_problem = _check_skill_frontmatter(sf)
                if fm_problem:
                    result.warnings.append(fm_problem)

            # Validate skills metadata in toolkit.yaml if present
            if metadata and hasattr(metadata, 'skills') and metadata.skills:
                for skill in metadata.skills:
                    if isinstance(skill, dict):
                        skill_file_path = skill.get('file', '')
                        skill_name = skill.get('name', 'unknown')
                    else:
                        # If skills is just a list of strings
                        skill_file_path = str(skill)
                        skill_name = skill_file_path

                    full_skill_path = toolkit_path / skill_file_path
                    if not full_skill_path.exists():
                        result.errors.append(f"Skill file referenced in toolkit.yaml not found: {skill_file_path}")
                        result.is_valid = False

    return result


def load_toolkit_metadata(toolkit_path: Path) -> Optional[ToolkitMetadata]:
    """
    Load toolkit metadata from toolkit.yaml.

    Args:
        toolkit_path: Path to toolkit directory

    Returns:
        ToolkitMetadata object or None if invalid
    """
    yaml_file = toolkit_path / "toolkit.yaml"

    if not yaml_file.exists():
        return None

    try:
        with open(yaml_file, 'r') as f:
            yaml_data = yaml.safe_load(f)

        return ToolkitMetadata(**yaml_data)

    except Exception:
        return None