"""Integration tests for the ``scitoolkit config`` command group (3C-1).

Drives every subcommand (``show / edit / path / set / unset /
validate``) via Click's ``CliRunner`` against a tmp config dir.
``edit`` is exercised by mocking ``subprocess.call`` since launching
$EDITOR for real is not test-friendly.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml as _yaml
from click.testing import CliRunner

from scitoolkit import config as scitoolkit_config
from scitoolkit import cli
from scitoolkit.setup import (
    NEEDS_VALUE_SENTINEL,
    config_path,
    load_config,
    save_config,
)


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect both CONFIG_DIR and TOOLKITS_DIR to tmp_path subtree.

    The ``config`` group's commands resolve the toolkit's source
    ``toolkit.yaml`` from TOOLKITS_DIR. We seed both directories so
    every test gets an isolated environment.
    """
    fake = tmp_path / "scitoolkit"
    fake.mkdir()
    (fake / "toolkits").mkdir()
    monkeypatch.setattr(scitoolkit_config, "CONFIG_DIR", fake)
    monkeypatch.setattr(scitoolkit_config, "TOOLKITS_DIR", fake / "toolkits")
    return fake


def _install_synthetic(
    base: Path,
    name: str = "demo",
    config_block=None,
) -> Path:
    """Drop a minimal toolkit.yaml + .stk_meta.json under TOOLKITS_DIR/<name>/."""
    tk = base / "toolkits" / name
    tk.mkdir(parents=True)
    yaml_data = {
        "name": name,
        "version": "0.1.0",
        "description": "x",
        "author": "test",
        "category": "other",
        "tools": [{"name": "t", "function": "tools.t", "description": "d"}],
    }
    if config_block is not None:
        yaml_data["config"] = config_block
    (tk / "toolkit.yaml").write_text(_yaml.safe_dump(yaml_data))
    (tk / ".stk_meta.json").write_text(json.dumps({
        "name": name, "version": "0.1.0", "environment": "venv",
        "python_path": "/usr/bin/python", "python_version": "3.12",
    }))
    return tk


# ── path ────────────────────────────────────────────────────────────


def test_config_path_prints_absolute(isolated: Path):
    _install_synthetic(isolated)
    r = CliRunner().invoke(cli.main, ["config", "path", "demo"])
    assert r.exit_code == 0
    assert "demo.yaml" in r.output
    assert str(isolated) in r.output


def test_config_path_unknown_toolkit_errors(isolated: Path):
    r = CliRunner().invoke(cli.main, ["config", "path", "ghost"])
    assert r.exit_code == 1
    assert "not installed" in r.output.lower()


# ── show ────────────────────────────────────────────────────────────


def test_config_show_no_file_prints_hint(isolated: Path):
    _install_synthetic(isolated, config_block=[
        {"name": "api_key", "type": "secret"},
    ])
    r = CliRunner().invoke(cli.main, ["config", "show", "demo"])
    assert r.exit_code == 0
    assert "no config file yet" in r.output.lower()


def test_config_show_renders_fields(isolated: Path):
    _install_synthetic(isolated, config_block=[
        {"name": "host", "type": "string"},
        {"name": "port", "type": "integer"},
    ])
    save_config("demo", {"host": "localhost", "port": 8080})
    r = CliRunner().invoke(cli.main, ["config", "show", "demo"])
    assert r.exit_code == 0
    assert "host" in r.output
    assert "localhost" in r.output
    assert "port" in r.output
    assert "8080" in r.output


def test_config_show_masks_secrets(isolated: Path):
    _install_synthetic(isolated, config_block=[
        {"name": "api_key", "type": "secret"},
    ])
    save_config("demo", {"api_key": "supersecret"})
    r = CliRunner().invoke(cli.main, ["config", "show", "demo"])
    assert "supersecret" not in r.output
    assert "<set>" in r.output


def test_config_show_marks_needs_value_sentinel(isolated: Path):
    _install_synthetic(isolated, config_block=[
        {"name": "api_key", "type": "secret", "required": True},
    ])
    save_config("demo", {"api_key": NEEDS_VALUE_SENTINEL})
    r = CliRunner().invoke(cli.main, ["config", "show", "demo"])
    assert NEEDS_VALUE_SENTINEL in r.output


# ── set ─────────────────────────────────────────────────────────────


def test_config_set_writes_value(isolated: Path):
    _install_synthetic(isolated, config_block=[
        {"name": "host", "type": "string"},
    ])
    r = CliRunner().invoke(cli.main, ["config", "set", "demo", "host", "myhost"])
    assert r.exit_code == 0
    assert load_config("demo")["host"] == "myhost"


def test_config_set_coerces_per_type(isolated: Path):
    _install_synthetic(isolated, config_block=[
        {"name": "port", "type": "integer", "min": 1, "max": 65535},
    ])
    r = CliRunner().invoke(cli.main, ["config", "set", "demo", "port", "8080"])
    assert r.exit_code == 0
    # Stored as int, not str.
    assert load_config("demo")["port"] == 8080


def test_config_set_rejects_invalid_per_type(isolated: Path):
    _install_synthetic(isolated, config_block=[
        {"name": "port", "type": "integer", "min": 1, "max": 100},
    ])
    r = CliRunner().invoke(cli.main, ["config", "set", "demo", "port", "9999"])
    assert r.exit_code == 1
    assert "above max" in r.output


def test_config_set_undeclared_field_warns_but_writes(isolated: Path):
    """A field not in the schema is still writable (with a warning)."""
    _install_synthetic(isolated, config_block=[
        {"name": "host", "type": "string"},
    ])
    r = CliRunner().invoke(cli.main, ["config", "set", "demo", "extra", "value"])
    assert r.exit_code == 0
    assert "not declared" in r.output.lower() or "warning" in r.output.lower()
    assert load_config("demo")["extra"] == "value"


def test_config_set_preserves_other_fields(isolated: Path):
    _install_synthetic(isolated, config_block=[
        {"name": "a", "type": "string"},
        {"name": "b", "type": "string"},
    ])
    save_config("demo", {"a": "1", "b": "2"})
    CliRunner().invoke(cli.main, ["config", "set", "demo", "a", "99"])
    data = load_config("demo")
    assert data["a"] == "99"
    assert data["b"] == "2"


# ── unset ───────────────────────────────────────────────────────────


def test_config_unset_removes_field(isolated: Path):
    _install_synthetic(isolated, config_block=[
        {"name": "a", "type": "string"},
    ])
    save_config("demo", {"a": "x", "extra": "y"})
    r = CliRunner().invoke(cli.main, ["config", "unset", "demo", "a"])
    assert r.exit_code == 0
    assert "a" not in load_config("demo")


def test_config_unset_missing_key_warns(isolated: Path):
    _install_synthetic(isolated)
    r = CliRunner().invoke(cli.main, ["config", "unset", "demo", "ghost"])
    assert r.exit_code == 0
    assert "no such field" in r.output.lower()


# ── validate ────────────────────────────────────────────────────────


def test_config_validate_clean_passes(isolated: Path):
    _install_synthetic(isolated, config_block=[
        {"name": "host", "type": "string", "required": True},
    ])
    save_config("demo", {"host": "myhost"})
    r = CliRunner().invoke(cli.main, ["config", "validate", "demo"])
    assert r.exit_code == 0
    assert "valid" in r.output.lower()


def test_config_validate_missing_required_fails(isolated: Path):
    _install_synthetic(isolated, config_block=[
        {"name": "host", "type": "string", "required": True},
        {"name": "port", "type": "integer", "default": 8080},
    ])
    r = CliRunner().invoke(cli.main, ["config", "validate", "demo"])
    assert r.exit_code == 1
    assert "missing required" in r.output.lower()
    assert "host" in r.output


def test_config_validate_no_schema_says_so(isolated: Path):
    _install_synthetic(isolated)  # no config: block
    r = CliRunner().invoke(cli.main, ["config", "validate", "demo"])
    assert r.exit_code == 0
    assert "no config" in r.output.lower() or "nothing to validate" in r.output.lower()


# ── edit ────────────────────────────────────────────────────────────


def test_config_edit_invokes_editor(isolated: Path, monkeypatch):
    _install_synthetic(isolated)
    called = {}

    def fake_call(argv):
        called["argv"] = argv
        return 0

    monkeypatch.setenv("EDITOR", "/usr/bin/nano")
    monkeypatch.setattr("scitoolkit.cli.subprocess.call", fake_call)
    r = CliRunner().invoke(cli.main, ["config", "edit", "demo"])
    assert r.exit_code == 0
    assert called["argv"][0] == "/usr/bin/nano"
    assert called["argv"][1].endswith("demo.yaml")


def test_config_edit_drops_template_for_schema(isolated: Path, monkeypatch):
    """Editing a never-edited toolkit's config drops a template populated
    with defaults and NEEDS_VALUE markers for required fields."""
    _install_synthetic(isolated, config_block=[
        {"name": "host", "type": "string", "default": "localhost"},
        {"name": "api_key", "type": "secret", "required": True},
    ])
    monkeypatch.setenv("EDITOR", "/usr/bin/nano")
    monkeypatch.setattr("scitoolkit.cli.subprocess.call", lambda argv: 0)

    r = CliRunner().invoke(cli.main, ["config", "edit", "demo"])
    assert r.exit_code == 0

    cfg = config_path("demo")
    assert cfg.exists()
    data = load_config("demo")
    assert data["host"] == "localhost"
    assert data["api_key"] == NEEDS_VALUE_SENTINEL
