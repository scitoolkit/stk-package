"""
Toolkit validation using Pydantic schemas.

Defines the schema for scitoolkit.yaml and provides validation functions
to ensure toolkits meet the required structure and format.
"""

from pathlib import Path
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, field_validator, EmailStr
import yaml


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
    """Schema for scitoolkit.yaml metadata file."""
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

    @field_validator('category')
    @classmethod
    def validate_category(cls, v):
        """Validate category is one of the allowed values."""
        if v is None:
            return v

        allowed_categories = [
            'astro', 'astrophysics',
            'hep', 'high-energy-physics',
            'quantum', 'quantum-computing',
            'neutrino',
            'cosmology',
            'general',
            'other'
        ]

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
    1. scitoolkit.yaml exists and is valid
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

    # Check for scitoolkit.yaml
    yaml_file = toolkit_path / "scitoolkit.yaml"
    if not yaml_file.exists():
        result.is_valid = False
        result.errors.append("Missing required file: scitoolkit.yaml")
        return result

    # Parse and validate scitoolkit.yaml
    try:
        with open(yaml_file, 'r') as f:
            yaml_data = yaml.safe_load(f)

        metadata = ToolkitMetadata(**yaml_data)
        result.metadata = metadata

    except yaml.YAMLError as e:
        result.is_valid = False
        result.errors.append(f"Invalid YAML in scitoolkit.yaml: {e}")
        return result

    except Exception as e:
        result.is_valid = False
        result.errors.append(f"Invalid scitoolkit.yaml: {e}")
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
        mcp_files = ['toolkit_registry.py', 'server_stdio.py', '__init__.py']
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

    return result


def load_toolkit_metadata(toolkit_path: Path) -> Optional[ToolkitMetadata]:
    """
    Load toolkit metadata from scitoolkit.yaml.

    Args:
        toolkit_path: Path to toolkit directory

    Returns:
        ToolkitMetadata object or None if invalid
    """
    yaml_file = toolkit_path / "scitoolkit.yaml"

    if not yaml_file.exists():
        return None

    try:
        with open(yaml_file, 'r') as f:
            yaml_data = yaml.safe_load(f)

        return ToolkitMetadata(**yaml_data)

    except Exception:
        return None