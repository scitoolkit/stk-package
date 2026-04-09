"""
Configuration management for SciToolkit.

Handles storing and retrieving user configuration including API keys,
user settings, and installed toolkit information.
"""

import json
from pathlib import Path
from typing import Optional, Dict, Any
import os


# Configuration directory
CONFIG_DIR = Path.home() / ".scitoolkit"
CONFIG_FILE = CONFIG_DIR / "config.json"
TOOLKITS_DIR = CONFIG_DIR / "toolkits"


def ensure_config_dir():
    """Create configuration directory if it doesn't exist."""
    CONFIG_DIR.mkdir(exist_ok=True, parents=True)
    TOOLKITS_DIR.mkdir(exist_ok=True, parents=True)


def load_config() -> Dict[str, Any]:
    """
    Load configuration from disk.

    Returns:
        Dictionary containing configuration settings
    """
    ensure_config_dir()

    if not CONFIG_FILE.exists():
        return {}

    try:
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def save_config(config: Dict[str, Any]):
    """
    Save configuration to disk.

    Args:
        config: Dictionary containing configuration settings
    """
    ensure_config_dir()

    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)

    # Set appropriate permissions (user read/write only)
    os.chmod(CONFIG_FILE, 0o600)


def get_api_key() -> Optional[str]:
    """
    Retrieve stored API key.

    Returns:
        API key string or None if not set
    """
    config = load_config()
    return config.get('api_key')


def save_api_key(api_key: str):
    """
    Save API key to configuration.

    Args:
        api_key: API key string to store
    """
    config = load_config()
    config['api_key'] = api_key
    save_config(config)


def validate_api_key_format(api_key: str) -> bool:
    """
    Validate API key format.

    API keys should start with 'stk_' and be at least 20 characters long.

    Args:
        api_key: API key to validate

    Returns:
        True if valid format, False otherwise
    """
    if not api_key:
        return False

    return api_key.startswith('stk_') and len(api_key) >= 20


def get_toolkits_dir() -> Path:
    """
    Get the directory where toolkits are installed.

    Returns:
        Path to toolkits directory
    """
    ensure_config_dir()
    return TOOLKITS_DIR


def list_installed_toolkits() -> list:
    """
    List all installed toolkits.

    Returns:
        List of toolkit names
    """
    toolkits_dir = get_toolkits_dir()

    if not toolkits_dir.exists():
        return []

    # Return directories that contain a toolkit.yaml file
    toolkits = []
    for item in toolkits_dir.iterdir():
        if item.is_dir() and (item / "scitoolkit.yaml").exists():
            toolkits.append(item.name)

    return sorted(toolkits)


def get_toolkit_path(name: str) -> Optional[Path]:
    """
    Get the installation path for a toolkit.

    Args:
        name: Toolkit name

    Returns:
        Path to toolkit directory or None if not installed
    """
    toolkit_path = TOOLKITS_DIR / name

    if toolkit_path.exists() and (toolkit_path / "scitoolkit.yaml").exists():
        return toolkit_path

    return None


def get_setting(key: str, default: Any = None) -> Any:
    """
    Get a configuration setting.

    Args:
        key: Setting key
        default: Default value if setting not found

    Returns:
        Setting value or default
    """
    config = load_config()
    return config.get(key, default)


def set_setting(key: str, value: Any):
    """
    Set a configuration setting.

    Args:
        key: Setting key
        value: Setting value
    """
    config = load_config()
    config[key] = value
    save_config(config)