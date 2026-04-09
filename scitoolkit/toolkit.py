"""
Toolkit creation and management functions.

Handles creating new toolkits from templates, packaging, and file operations.
"""

from pathlib import Path
from typing import Optional
import shutil


def get_template_path(filename: str) -> Path:
    """
    Get the path to a template file.

    Args:
        filename: Template filename

    Returns:
        Path to template file
    """
    # Templates are in the same package
    templates_dir = Path(__file__).parent / "templates"
    return templates_dir / filename


def render_template(template_path: Path, substitutions: dict) -> str:
    """
    Render a template file with substitutions.

    Simple template rendering using {{variable}} syntax.

    Args:
        template_path: Path to template file
        substitutions: Dictionary of variable names to values

    Returns:
        Rendered template content
    """
    with open(template_path, 'r') as f:
        content = f.read()

    # Simple template substitution
    for key, value in substitutions.items():
        placeholder = f"{{{{{key}}}}}"
        content = content.replace(placeholder, str(value))

    return content


def create_toolkit_from_template(
    name: str,
    path: Path,
    with_docker: bool = False,
    author: Optional[str] = None,
    email: Optional[str] = None,
    category: str = "general",
    python_version: str = "3.11"
) -> Path:
    """
    Create a new toolkit from template.

    Creates the standard toolkit directory structure:
    - scitoolkit.yaml
    - tools/ (with __init__.py and example_tool.py)
    - requirements.txt
    - README.md
    - Dockerfile (optional)

    Args:
        name: Toolkit name
        path: Path where toolkit should be created
        with_docker: Whether to include Dockerfile
        author: Author name (optional)
        email: Author email (optional)
        category: Toolkit category
        python_version: Required Python version

    Returns:
        Path to created toolkit directory

    Raises:
        FileExistsError: If toolkit directory already exists
        IOError: If unable to create files
    """
    # Ensure path is absolute
    toolkit_path = Path(path).resolve()

    # Check if directory already exists
    if toolkit_path.exists():
        raise FileExistsError(f"Directory already exists: {toolkit_path}")

    # Create main directory
    toolkit_path.mkdir(parents=True, exist_ok=False)

    # Create tools subdirectory
    tools_dir = toolkit_path / "tools"
    tools_dir.mkdir()

    # Create mcp subdirectory
    mcp_dir = toolkit_path / "mcp"
    mcp_dir.mkdir()

    # Prepare template substitutions
    # Tool imports and list for MCP registry
    tool_imports = "example_tool, text_processor"
    tool_list = "example_tool, text_processor"

    substitutions = {
        'name': name,
        'author': author or 'Your Name',
        'email': email or 'your.email@example.com',
        'category': category,
        'python_version': python_version,
        'version': '0.1.0',
        'description': 'A brief description of your toolkit',
        'tool_imports': tool_imports,
        'tool_list': tool_list,
    }

    # Create scitoolkit.yaml
    yaml_template = get_template_path("scitoolkit.yaml.template")
    yaml_content = render_template(yaml_template, substitutions)
    (toolkit_path / "scitoolkit.yaml").write_text(yaml_content)

    # Create tools/__init__.py
    init_template = get_template_path("__init__.py.template")
    init_content = render_template(init_template, substitutions)
    (tools_dir / "__init__.py").write_text(init_content)

    # Create tools/example_tool.py
    tool_template = get_template_path("tool_example.py")
    tool_content = render_template(tool_template, substitutions)
    (tools_dir / "example_tool.py").write_text(tool_content)

    # Create README.md
    readme_template = get_template_path("README.md.template")
    readme_content = render_template(readme_template, substitutions)
    (toolkit_path / "README.md").write_text(readme_content)

    # Create requirements.txt
    req_template = get_template_path("requirements.txt.template")
    req_content = render_template(req_template, substitutions)
    (toolkit_path / "requirements.txt").write_text(req_content)

    # Create mcp/__init__.py
    mcp_init_template = get_template_path("mcp/__init__.py.template")
    mcp_init_content = render_template(mcp_init_template, substitutions)
    (mcp_dir / "__init__.py").write_text(mcp_init_content)

    # Create mcp/toolkit_registry.py
    registry_template = get_template_path("mcp/toolkit_registry.py.template")
    registry_content = render_template(registry_template, substitutions)
    (mcp_dir / "toolkit_registry.py").write_text(registry_content)

    # Create mcp/server_stdio.py
    server_template = get_template_path("mcp/server_stdio.py.template")
    server_content = render_template(server_template, substitutions)
    (mcp_dir / "server_stdio.py").write_text(server_content)

    # Create Dockerfile if requested
    if with_docker:
        dockerfile_template = get_template_path("Dockerfile.template")
        dockerfile_content = render_template(dockerfile_template, substitutions)
        (toolkit_path / "Dockerfile").write_text(dockerfile_content)

    # Create .gitignore
    gitignore_content = """# Python
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
build/
develop-eggs/
dist/
downloads/
eggs/
.eggs/
lib/
lib64/
parts/
sdist/
var/
wheels/
*.egg-info/
.installed.cfg
*.egg

# Virtual environments
venv/
env/
ENV/

# IDE
.vscode/
.idea/
*.swp
*.swo
*~

# OS
.DS_Store
Thumbs.db

# Testing
.pytest_cache/
.coverage
htmlcov/

# SciToolkit
.scitoolkit/
"""
    (toolkit_path / ".gitignore").write_text(gitignore_content)

    return toolkit_path


def package_toolkit(toolkit_path: Path, output_path: Optional[Path] = None) -> Path:
    """
    Package a toolkit into a tar.gz file for distribution.

    Args:
        toolkit_path: Path to toolkit directory
        output_path: Where to save the tarball (default: current directory)

    Returns:
        Path to created tarball

    Raises:
        ValueError: If toolkit is invalid
        IOError: If unable to create tarball
    """
    from .validation import validate_toolkit

    # Validate toolkit first
    result = validate_toolkit(toolkit_path)
    if not result.is_valid:
        raise ValueError(f"Toolkit validation failed: {', '.join(result.errors)}")

    # Determine output path
    if output_path is None:
        output_path = Path.cwd()

    toolkit_name = result.metadata.name
    version = result.metadata.version
    tarball_name = f"{toolkit_name}-{version}.tar.gz"
    tarball_path = output_path / tarball_name

    # Create tarball
    import tarfile

    with tarfile.open(tarball_path, "w:gz") as tar:
        tar.add(toolkit_path, arcname=toolkit_name)

    return tarball_path
