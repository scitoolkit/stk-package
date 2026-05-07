"""End-to-end test for ``scitoolkit ingest``.

Drives the ingest → validate → host-import round trip against a
synthetic existing-repo fixture (``test-existing-repo-fixture/``).

Steps:

1. Copy the fixture into a tmpdir (treat it as the author's repo).
2. Run ``scitoolkit ingest`` against it.
3. Verify the emitted ``toolkit.yaml`` has the expected tools and skips
   the .gitignored file, the tests/ dir, and the non-tool helper.
4. Patch in metadata that ``validate`` will accept (name, version,
   description, author, requirements.txt with orchestral-ai +
   the heptapod_synth package as a "dep" — even though it's
   actually inside the toolkit root, the validate path-residence
   check should accept it via filesystem resolution).
5. Run ``scitoolkit validate`` and assert it passes.
6. Use the host's ``_import_explicit_tools`` directly to confirm the
   tools actually load and register as BaseTool instances.

Network-free, pytest-independent. Mirrors the shape of
run_install_e2e.py and run_setup_script_e2e.py.

Run from the repo root with the test venv:

    python tests/e2e/run_ingest_e2e.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "test-existing-repo-fixture"


def _ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def _fail(msg: str) -> int:
    print(f"  ✗ {msg}")
    return 1


def main() -> int:
    if not FIXTURE.is_dir():
        return _fail(f"fixture missing at {FIXTURE}")

    print(f"=== ingest e2e ===\nFixture: {FIXTURE}")

    with tempfile.TemporaryDirectory(prefix="ingest-e2e-") as tmp:
        repo = Path(tmp) / "synth-repo"
        shutil.copytree(FIXTURE, repo)
        print(f"Repo:    {repo}")

        # Step 1: invoke ingest as a CLI subprocess.
        scitoolkit_bin = shutil.which("scitoolkit")
        if scitoolkit_bin is None:
            return _fail(
                "could not find `scitoolkit` on PATH. "
                "Activate the dev venv first."
            )
        result = subprocess.run(
            [scitoolkit_bin, "ingest", str(repo), "--no-input"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"--- ingest stdout ---\n{result.stdout}")
            print(f"--- ingest stderr ---\n{result.stderr}")
            return _fail(f"`scitoolkit ingest` exited {result.returncode}")
        _ok("scitoolkit ingest exit 0")

        yaml_path = repo / "toolkit.yaml"
        if not yaml_path.is_file():
            return _fail("toolkit.yaml was not written")
        _ok("toolkit.yaml written")

        # Step 2: parse the yaml and confirm what we expected to find.
        import yaml as pyyaml
        data = pyyaml.safe_load(yaml_path.read_text(encoding="utf-8"))

        names = sorted({entry["name"] for entry in data["tools"]})
        expected = sorted([
            "compute_amplitude",
            "cross_section",
            "TwoPointFunction",
        ])
        if names != expected:
            print(f"  expected tools: {expected}")
            print(f"  actual tools:   {names}")
            return _fail(f"unexpected tool list")
        _ok(f"discovered {len(names)} tools, none extras")

        # Verify exclusions: no fake_tool_should_not_appear from tests/,
        # no gitignored_tool from generated_*.py, no helper_function
        # from internal_helpers.py.
        if "fake_tool_should_not_appear" in names:
            return _fail("tests/ dir was NOT skipped (fake_tool emitted)")
        _ok("tests/ dir skipped")
        if "gitignored_tool" in names:
            return _fail(".gitignore pattern NOT honored (gitignored_tool emitted)")
        _ok(".gitignore generated_*.py honored")
        if "helper_function" in names:
            return _fail("non-decorated helper picked up as a tool")
        _ok("undecorated helpers not picked up")

        # Confirm both kinds (function + class) made it.
        modules = {entry["module"] for entry in data["tools"]}
        if "heptapod_synth.scattering.amplitudes" not in modules:
            return _fail("decorated functions module not in tools list")
        if "heptapod_synth.observables.two_point" not in modules:
            return _fail("BaseTool subclass module not in tools list")
        _ok("both decorated-function and class-based tools detected")

        # Step 3: patch in real metadata so validate passes.
        # Tweak yaml directly via ruamel for round-trip safety.
        from ruamel.yaml import YAML
        yaml_rt = YAML()
        with yaml_path.open("r", encoding="utf-8") as f:
            doc = yaml_rt.load(f)
        doc["name"] = "synth-heptapod"
        doc["version"] = "0.1.0"
        doc["description"] = "Synthetic HEPTAPOD-shaped toolkit."
        doc["author"] = "ingest e2e fixture"
        doc["category"] = "hep"
        with yaml_path.open("w", encoding="utf-8") as f:
            yaml_rt.dump(doc, f)
        _ok("metadata patched")

        (repo / "requirements.txt").write_text(
            "orchestral-ai>=1.0.0\n", encoding="utf-8"
        )
        (repo / "README.md").write_text(
            "# Synth HEPTAPOD\n", encoding="utf-8"
        )
        # mcp/ dir scaffolded to satisfy the existing validation check.
        (repo / "mcp").mkdir(exist_ok=True)
        (repo / "mcp" / "__init__.py").write_text("", encoding="utf-8")
        (repo / "mcp" / "server_stdio.py").write_text("", encoding="utf-8")
        _ok("requirements + README + mcp/ scaffolded")

        # Step 4: invoke validate as a CLI subprocess.
        result = subprocess.run(
            [scitoolkit_bin, "validate", str(repo)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"--- validate stdout ---\n{result.stdout}")
            print(f"--- validate stderr ---\n{result.stderr}")
            return _fail(f"`scitoolkit validate` exited {result.returncode}")
        _ok("scitoolkit validate exit 0 against ingested yaml")

        # Step 5: load each tool via the host's explicit-form importer
        # and confirm it returns BaseTool instances.
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        try:
            from scitoolkit._toolkit_host import _import_explicit_tools
            from orchestral.tools.base.tool import BaseTool

            spec = [
                {"name": entry["name"], "module": entry["module"]}
                for entry in doc["tools"]
            ]
            instances = _import_explicit_tools(spec, repo)
            if len(instances) != len(spec):
                return _fail(
                    f"expected {len(spec)} instances, got {len(instances)}"
                )
            for inst in instances:
                if not isinstance(inst, BaseTool):
                    return _fail(
                        f"instance not a BaseTool: {type(inst).__name__}"
                    )
            _ok(f"all {len(instances)} tools instantiated as BaseTool")
        finally:
            # Clean up: drop the test-fixture modules from sys.modules so
            # repeated runs don't see stale state.
            for mod in list(sys.modules):
                if mod.startswith("heptapod_synth"):
                    del sys.modules[mod]

    print("\n=== ingest e2e: PASS ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
