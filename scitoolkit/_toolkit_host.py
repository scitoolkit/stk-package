"""
Per-toolkit subprocess host for ``scitoolkit serve``.

This module is the entrypoint that runs *inside* a toolkit's own Python
interpreter (its venv or conda env). The orchestrator process spawns one of
these per active toolkit and talks to it over MCP-over-HTTP on a loopback
port.

Why this lives in the scitoolkit package: every installed toolkit has
``orchestral-ai`` and ``mcp`` in its environment (we install them at toolkit
install time), but it does NOT have the scitoolkit package installed there.
So the orchestrator launches us via ``python -m scitoolkit._toolkit_host``
*using the orchestrator's interpreter* — wait, no: the orchestrator launches
us using the *toolkit's* interpreter, which means scitoolkit must be
importable inside the toolkit env too.

The simplest way to make that work without polluting the toolkit env: this
module is intentionally self-contained — it imports only stdlib +
``orchestral`` + ``mcp``, all of which are guaranteed to be in the toolkit
env. The orchestrator passes us the toolkit directory; we add it to
``sys.path``, import its ``tools`` package, and serve.

To make ``python -m scitoolkit._toolkit_host`` work inside the toolkit env,
we don't actually need the full scitoolkit package installed there — we just
need this single file plus a stub package init. That's handled by the
orchestrator at spawn time: it copies this file (and a tiny ``__init__.py``)
into a known cache location inside the toolkit's env (e.g.
``<toolkit_dir>/.stk_host/scitoolkit/_toolkit_host.py``) and launches with
``PYTHONPATH=<toolkit_dir>/.stk_host``. See orchestrator.py for that wiring.

---

A note on stateful tools (Orchestral 1.3.0):

The shipped Orchestral 1.3.0 supports stateful tools by subclassing
``BaseTool`` and declaring fields with ``StateField(...)``. The ``_setup()``
method runs at instance construction and can use those fields. Earlier
documentation referred to a ``@define_tool(state=[...])`` decorator and
``Agent(tool_config=...)`` injection API — those are NOT in the shipped
1.3.0 release. They may land later; if and when they do, this module
continues to work without changes.

For now, scitoolkit performs state injection itself: after constructing the
tool instances exposed by ``tools/__init__.py``, we look at each tool's
declared state fields, set values from the (currently empty) state config
passed in, and re-run ``_setup()`` so the tool sees the injected values.
When Orchestral ships the convenience layer, this manual injection is still
correct; if/when toolkit authors switch to the new decorator form, the
``_get_state_fields`` mechanism remains the discovery API and our injection
loop continues to work.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import socket
import sys
import traceback
from pathlib import Path
from typing import Any, Iterable


def _find_free_loopback_port() -> int:
    """Bind to 127.0.0.1:0, get the kernel-allocated port, release it.

    Tiny race between releasing here and re-binding inside FastMCP, but on
    loopback in practice this is fine.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    return port


def _import_tools_package(toolkit_dir: Path) -> Any:
    """Import the toolkit's ``tools`` package without polluting sys.path.

    DO NOT change this to add ``toolkit_dir`` to ``sys.path``. The naive
    approach (sys.path.insert(0, str(toolkit_dir))) makes EVERY top-level
    directory the toolkit ships compete with installed packages of the same
    name. Real failures we've seen and theoretical ones to keep in mind:

    - **Confirmed: ``mcp/``** — the toolkit template generates an ``mcp/``
      directory (see ``scitoolkit/templates/mcp/``). With ``toolkit_dir``
      on sys.path, ``import mcp`` resolves to *the toolkit's* ``mcp/``
      package, which then can't satisfy ``from mcp.server import Server``.
      Orchestral's ``_check_mcp_installed()`` raises a misleading
      "MCP integration requires the 'mcp' package" error. This is the
      bug that motivated the spec_from_file_location approach.

    - **Plausible: ``data/``, ``tests/``, ``scripts/``, ``docs/``** —
      common toolkit-author directory names. All have same-named packages
      on PyPI (``data`` is a real package). If the toolkit's venv installs
      one of those as a transitive dep and the toolkit also has a
      same-named directory, you get the same shadowing class of bug.

    - **Plausible: any dependency of the toolkit's own deps.** The toolkit
      env contains all of its requirements and their transitive deps. Any
      of those names colliding with a toolkit's top-level directory =
      same bug.

    The fix (this function) builds an explicit module spec for the
    ``tools/__init__.py`` path and registers the loaded module under the
    name ``"tools"``. ``submodule_search_locations`` tells the loader
    where to find ``arxiv_tools.py`` etc. for relative imports. Nothing
    goes on ``sys.path``, so no shadowing is possible.

    Side effect for toolkit authors: code inside ``tools/`` cannot do
    absolute imports of sibling top-level dirs (``import data``, etc.).
    If a toolkit needs a ``data/`` directory of pure-data files, they
    should access it by path (``Path(__file__).parent.parent / "data"``),
    not by import. If they need a sibling Python *package*, they should
    nest it under ``tools/`` (e.g. ``tools/helpers/__init__.py``) so
    relative imports work.

    See also: ``_collect_tool_instances`` which walks the loaded module
    for ``BaseTool`` instances or a ``TOOLS`` list.
    """
    tools_dir = toolkit_dir / "tools"
    init_file = tools_dir / "__init__.py"
    if not init_file.exists():
        raise ImportError(f"missing tools/__init__.py in {toolkit_dir}")

    # Drop any cached import from a previous invocation (paranoid; one host
    # process only ever imports one toolkit, but safer for tests).
    for k in list(sys.modules):
        if k == "tools" or k.startswith("tools."):
            del sys.modules[k]

    spec = importlib.util.spec_from_file_location(
        "tools",
        str(init_file),
        submodule_search_locations=[str(tools_dir)],
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"could not build module spec for {init_file}")
    module = importlib.util.module_from_spec(spec)
    # Register before exec so that relative imports inside the package
    # find their parent in sys.modules.
    sys.modules["tools"] = module
    spec.loader.exec_module(module)
    return module


def _collect_tool_instances(tools_module: Any) -> list:
    """Return tool instances exposed by the toolkit's ``tools/__init__.py``.

    Convention (per the package's existing template): the module either
    exports a ``TOOLS`` list, or every public attribute that's a BaseTool
    instance is treated as a tool. We honor both.
    """
    from orchestral.tools.base.tool import BaseTool

    if hasattr(tools_module, "TOOLS"):
        candidates: Iterable = tools_module.TOOLS
    else:
        candidates = (
            getattr(tools_module, name)
            for name in dir(tools_module)
            if not name.startswith("_")
        )

    tools = []
    seen_ids = set()
    for obj in candidates:
        if isinstance(obj, BaseTool) and id(obj) not in seen_ids:
            tools.append(obj)
            seen_ids.add(id(obj))
    return tools


def _inject_state_into_tools(tools: list, state_config: dict) -> None:
    """Set state-field values on each tool, then re-run ``_setup()``.

    See module docstring for the rationale (manual injection because
    Orchestral 1.3.0 doesn't ship Agent(tool_config=...) yet). This is a
    no-op when ``state_config`` is empty — which it is today, until Phase 3C
    delivers the setup system that produces it.
    """
    if not state_config:
        return

    for tool in tools:
        state_fields = tool.__class__._get_state_fields()
        touched = False
        for field_name in state_fields:
            if field_name in state_config:
                setattr(tool, field_name, state_config[field_name])
                touched = True
        if touched:
            tool._setup()


def _emit_handshake(port: int, tool_names: list[str]) -> None:
    """Write the handshake JSON line to stdout and flush.

    The orchestrator reads exactly one line from this subprocess's stdout
    before connecting its MCP client. After this line, stdout is unused —
    further diagnostics go to stderr (which the orchestrator captures into
    ``~/.scitoolkit/logs/<toolkit>.log``).
    """
    sys.stdout.write(json.dumps({"port": port, "tools": tool_names}) + "\n")
    sys.stdout.flush()


def _emit_error(message: str, **fields) -> None:
    """Write a startup-failure handshake and exit non-zero.

    The orchestrator reads this same single-line slot when waiting for the
    handshake — it distinguishes success (``port`` key present) from failure
    (``error`` key present) and surfaces the error to the user.
    """
    payload = {"error": message, **fields}
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scitoolkit._toolkit_host",
        description="Per-toolkit subprocess host for scitoolkit serve.",
    )
    parser.add_argument(
        "--toolkit-dir",
        required=True,
        type=Path,
        help="Path to the installed toolkit directory.",
    )
    parser.add_argument(
        "--name",
        required=True,
        help="Toolkit name (used in the FastMCP server name).",
    )
    parser.add_argument(
        "--state-config",
        default="",
        help=(
            "JSON object of state-field values to inject into tool instances. "
            "Empty for now; populated by Phase 3C's setup system."
        ),
    )
    args = parser.parse_args(argv)

    state_config: dict = {}
    if args.state_config:
        try:
            state_config = json.loads(args.state_config)
            if not isinstance(state_config, dict):
                raise ValueError("state-config must be a JSON object")
        except Exception as e:
            _emit_error(f"invalid --state-config: {e}")
            return 2

    # Import the toolkit's tools.
    try:
        tools_module = _import_tools_package(args.toolkit_dir)
    except Exception as e:
        _emit_error(
            f"failed to import tools from {args.toolkit_dir}: {e}",
            traceback=traceback.format_exc(),
        )
        return 3

    tool_instances = _collect_tool_instances(tools_module)
    if not tool_instances:
        _emit_error(
            f"no Orchestral tools found in {args.toolkit_dir}/tools/",
            hint=(
                "Either export a TOOLS list from tools/__init__.py, or "
                "ensure the package re-exports your @define_tool-decorated "
                "tools as module attributes."
            ),
        )
        return 4

    # Inject state-field values (no-op when state_config is empty).
    try:
        _inject_state_into_tools(tool_instances, state_config)
    except Exception as e:
        _emit_error(
            f"state-field injection failed: {e}",
            traceback=traceback.format_exc(),
        )
        return 5

    # Build the FastMCP HTTP server.
    try:
        from orchestral.mcp import create_fastmcp_server
    except ImportError as e:
        _emit_error(
            "orchestral.mcp not available — toolkit env missing 'mcp' dep",
            detail=str(e),
        )
        return 6

    port = _find_free_loopback_port()

    mcp = create_fastmcp_server(
        tool_instances,
        name=f"scitoolkit-{args.name}",
        host="127.0.0.1",
        port=port,
        # Use snake_case names; the orchestrator does its own namespacing
        # with double-underscore prefixes, so we don't want a separate
        # PascalCase rename layered on top.
        use_display_names=False,
    )

    tool_names = [t.get_name() for t in tool_instances]
    _emit_handshake(port, tool_names)

    # Block here. anyio.run inside FastMCP handles the event loop.
    try:
        mcp.run(transport="streamable-http")
    except KeyboardInterrupt:
        return 0
    except Exception:
        # Log the traceback to stderr so the orchestrator can pick it up
        # from the per-toolkit log file. We don't try to emit a JSON error
        # here because the handshake is already past.
        traceback.print_exc()
        return 7

    return 0


if __name__ == "__main__":
    sys.exit(main())
