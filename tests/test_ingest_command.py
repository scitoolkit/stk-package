"""Unit tests for the ``scitoolkit ingest`` Click command.

Exercises the CLI surface: flag handling, prompt-mode integration,
existing-yaml behavior, dry-run, output-path override, requirements.txt
warning. The actual discovery + emission is unit-tested elsewhere; here
we only care about the command wiring.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from scitoolkit.cli import main


def _make_repo_with_one_tool(root: Path) -> None:
    pkg = root / "pkg"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "mod.py").write_text(
        "from orchestral import define_tool\n"
        "@define_tool\n"
        "def my_tool():\n"
        "    \"\"\"Does the thing.\"\"\"\n"
        "    pass\n",
        encoding="utf-8",
    )


class TestBareInvocation:
    def test_writes_toolkit_yaml(self, tmp_path):
        _make_repo_with_one_tool(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            main, ["ingest", str(tmp_path), "--no-input"]
        )
        assert result.exit_code == 0, result.output
        assert (tmp_path / "toolkit.yaml").exists()
        assert "Found 1 tools" in result.output
        assert "pkg.mod.my_tool" in result.output

    def test_warns_on_missing_requirements(self, tmp_path):
        _make_repo_with_one_tool(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            main, ["ingest", str(tmp_path), "--no-input"]
        )
        assert "requirements.txt not found" in result.output.lower() or \
               "WARNING" in result.output

    def test_no_warning_when_requirements_exists(self, tmp_path):
        _make_repo_with_one_tool(tmp_path)
        (tmp_path / "requirements.txt").write_text(
            "orchestral-ai\n", encoding="utf-8"
        )
        runner = CliRunner()
        result = runner.invoke(
            main, ["ingest", str(tmp_path), "--no-input"]
        )
        assert "requirements.txt not found" not in result.output.lower()


class TestDryRun:
    def test_dry_run_does_not_write(self, tmp_path):
        _make_repo_with_one_tool(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            main, ["ingest", str(tmp_path), "--dry-run"]
        )
        assert result.exit_code == 0
        assert not (tmp_path / "toolkit.yaml").exists()
        assert "dry-run" in result.output.lower()


class TestExistingYamlBehavior:
    def test_blocks_in_no_input_mode(self, tmp_path):
        _make_repo_with_one_tool(tmp_path)
        (tmp_path / "toolkit.yaml").write_text(
            "name: existing\nversion: 1.0.0\n", encoding="utf-8"
        )
        runner = CliRunner()
        result = runner.invoke(
            main, ["ingest", str(tmp_path), "--no-input"]
        )
        # Consequential prompt + skip mode = abort.
        assert result.exit_code != 0
        # File preserved unchanged.
        assert "existing" in (tmp_path / "toolkit.yaml").read_text()

    def test_force_overwrites(self, tmp_path):
        _make_repo_with_one_tool(tmp_path)
        (tmp_path / "toolkit.yaml").write_text(
            "name: existing\nversion: 9.9.9\n", encoding="utf-8"
        )
        runner = CliRunner()
        result = runner.invoke(
            main, ["ingest", str(tmp_path), "--force", "--no-input"]
        )
        assert result.exit_code == 0
        content = (tmp_path / "toolkit.yaml").read_text()
        # Original metadata preserved, but tools block now present.
        assert "existing" in content
        assert "tools:" in content
        assert "my_tool" in content

    def test_yes_flag_overwrites(self, tmp_path):
        _make_repo_with_one_tool(tmp_path)
        (tmp_path / "toolkit.yaml").write_text(
            "name: existing\n", encoding="utf-8"
        )
        runner = CliRunner()
        result = runner.invoke(
            main, ["ingest", str(tmp_path), "--yes"]
        )
        assert result.exit_code == 0
        assert "my_tool" in (tmp_path / "toolkit.yaml").read_text()


class TestOutputOption:
    def test_custom_output_path(self, tmp_path):
        _make_repo_with_one_tool(tmp_path)
        out = tmp_path / "elsewhere" / "manifest.yaml"
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "ingest", str(tmp_path),
                "--output", str(out),
                "--no-input",
            ],
        )
        assert result.exit_code == 0, result.output
        assert out.exists()
        assert not (tmp_path / "toolkit.yaml").exists()


class TestEmptyRepo:
    def test_no_tools_still_writes(self, tmp_path):
        # No tools at all — ingest still emits a yaml skeleton (with
        # empty tools list) so the author has somewhere to start.
        runner = CliRunner()
        result = runner.invoke(
            main, ["ingest", str(tmp_path), "--no-input"]
        )
        assert result.exit_code == 0
        assert (tmp_path / "toolkit.yaml").exists()
        assert "Found 0 tools" in result.output


class TestSummaryOutput:
    def test_lists_decorated_functions_separately(self, tmp_path):
        _make_repo_with_one_tool(tmp_path)
        # Add a class-based tool too.
        (tmp_path / "pkg" / "klass.py").write_text(
            "from orchestral.tools import BaseTool\n"
            "class MyClassTool(BaseTool):\n"
            "    pass\n",
            encoding="utf-8",
        )
        runner = CliRunner()
        result = runner.invoke(
            main, ["ingest", str(tmp_path), "--no-input"]
        )
        assert result.exit_code == 0
        assert "Decorated functions" in result.output
        assert "BaseTool subclasses" in result.output
        assert "my_tool" in result.output
        assert "MyClassTool" in result.output
