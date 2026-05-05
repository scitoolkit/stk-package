"""
Orchestrator for ``scitoolkit serve`` (MVP, ``--no-tui`` mode only).

Responsibilities:

1. Discover installed toolkits via ``~/.scitoolkit/toolkits/*/.stk_meta.json``.
2. Classify each as ready / skipped (docker, needs_setup, broken).
3. Print a scannable startup banner.
4. Spawn one subprocess per ready toolkit, using that toolkit's interpreter.
5. Read each subprocess's handshake (port + tool list) from its stdout.
6. Connect an MCPClient (HTTP) to each subprocess.
7. Build proxy tools (namespaced ``<toolkit>__<tool>``) and aggregate them.
8. Start ``orchestral.mcp.MCPServer`` on stdio (Claude Code talks to it).
9. On shutdown, send graceful → SIGTERM → SIGKILL to children.

Out of scope for the MVP (deferred per direction):

- Restart-on-crash with exponential backoff (only basic detection here).
- Per-call timeout enforcement (relies on MCPClient's default).
- TUI-facing event subscription API.
- Hot reload.

See ``stk-package/docs/SERVE_ARCHITECTURE.md`` for the full design.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from rich.console import Console

from ..config import LOGS_DIR, TOOLKITS_DIR
from ..logging.logger import ToolLogger, get_logger


HOST_HANDSHAKE_TIMEOUT_S = 15.0  # generous; conda startup can be slow
# The MCPClient's `timeout` parameter governs *both* the initial HTTP
# connect and each subsequent call. We default it to 60 s so long-running
# scientific calls don't fail prematurely.
#
# History (2026-05-06): the original code passed a `MCP_CONNECT_TIMEOUT_S
# = 10.0` constant here, which Orchestral applied as the per-call timeout
# too — silently capping every tool at 10 s, *worse* than Orchestral's
# 30 s default. Renamed and bumped to 60 s. Override per-invocation with
# `scitoolkit serve --call-timeout SECONDS`.
DEFAULT_CALL_TIMEOUT_S = 60.0
SHUTDOWN_GRACEFUL_S = 5.0


# ── data classes ────────────────────────────────────────────────────────


@dataclass
class ToolkitDiscovery:
    """A single toolkit discovered in TOOLKITS_DIR. Pre-launch state."""
    name: str
    path: Path
    meta: Dict[str, Any]
    skip_reason: Optional[str] = None  # human-readable; None = ready

    @property
    def env_type(self) -> str:
        return self.meta.get("environment", "unknown")

    @property
    def python_version(self) -> str:
        return self.meta.get("python_version", "?")


@dataclass
class ToolkitRuntime:
    """A successfully spawned toolkit, post-handshake."""
    name: str
    path: Path
    proc: subprocess.Popen
    port: int
    upstream_tool_names: List[str]
    mcp_client: Any  # orchestral.mcp.MCPClient
    stderr_thread: Optional[threading.Thread] = None
    stderr_logfile_handle: Optional[Any] = None


# ── discovery ───────────────────────────────────────────────────────────


def discover_toolkits(toolkits_dir: Path = TOOLKITS_DIR) -> List[ToolkitDiscovery]:
    """Walk ``toolkits_dir`` and classify each entry. Sorted by name."""
    found: List[ToolkitDiscovery] = []
    if not toolkits_dir.exists():
        return found

    for entry in sorted(toolkits_dir.iterdir()):
        if not entry.is_dir():
            continue
        meta_file = entry / ".stk_meta.json"
        if not meta_file.exists():
            found.append(ToolkitDiscovery(
                name=entry.name, path=entry, meta={},
                skip_reason="missing .stk_meta.json (broken install)",
            ))
            continue
        try:
            meta = json.loads(meta_file.read_text())
        except Exception:
            found.append(ToolkitDiscovery(
                name=entry.name, path=entry, meta={},
                skip_reason="unreadable .stk_meta.json",
            ))
            continue

        skip: Optional[str] = None
        env = meta.get("environment")
        if env == "docker":
            skip = "Docker mode (Phase 3B not yet supported)"
        elif meta.get("needs_setup"):
            skip = "setup not yet run (Phase 3C)"
        elif env not in ("venv", "conda"):
            skip = f"unknown environment type: {env!r}"

        found.append(ToolkitDiscovery(
            name=entry.name, path=entry, meta=meta, skip_reason=skip,
        ))
    return found


# ── subprocess launch ───────────────────────────────────────────────────


def _build_host_command(disc: ToolkitDiscovery) -> List[str]:
    """Return the argv for spawning the per-toolkit host subprocess."""
    base_args = [
        "-m", "scitoolkit._toolkit_host",
        "--toolkit-dir", str(disc.path),
        "--name", disc.name,
        # state-config is empty until Phase 3C
        "--state-config", "",
    ]
    if disc.env_type == "venv":
        python_exe = disc.meta.get("python_path")
        if not python_exe:
            raise RuntimeError(
                f"venv toolkit {disc.name!r} has no python_path in metadata"
            )
        return [python_exe] + base_args
    if disc.env_type == "conda":
        env_name = disc.meta.get("env_name")
        if not env_name:
            raise RuntimeError(
                f"conda toolkit {disc.name!r} has no env_name in metadata"
            )
        # --no-capture-output so stderr (and our stdout handshake) flow through.
        return [
            "conda", "run", "--no-capture-output", "-n", env_name,
            "python",
        ] + base_args
    raise RuntimeError(f"unsupported env_type {disc.env_type!r}")


def _build_host_env(toolkit_path: Path) -> Dict[str, str]:
    """Compose the subprocess environment.

    The toolkit's interpreter doesn't have ``scitoolkit`` installed, only
    ``orchestral-ai`` and ``mcp``. We need ``scitoolkit._toolkit_host`` to
    be importable, so we point ``PYTHONPATH`` at the parent package
    location of the running orchestrator.
    """
    env = os.environ.copy()
    # Find the directory that contains the ``scitoolkit`` package.
    import scitoolkit as _sk
    pkg_parent = str(Path(_sk.__file__).resolve().parent.parent)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        pkg_parent + (os.pathsep + existing if existing else "")
    )
    # Ensure unbuffered stdout so the handshake line reaches us promptly.
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _spawn_host(disc: ToolkitDiscovery, logger: ToolLogger) -> subprocess.Popen:
    """Launch the host subprocess. Returns the Popen handle."""
    cmd = _build_host_command(disc)
    env = _build_host_env(disc.path)
    # stdin is a pipe so we can later send a graceful shutdown JSON line
    # and so closing the pipe also terminates the host.
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
        bufsize=1,  # line-buffered
    )
    logger.log_event(
        "subprocess_spawned",
        toolkit=disc.name,
        message=f"interpreter={disc.env_type}",
        pid=proc.pid,
    )
    return proc


def _read_handshake(
    proc: subprocess.Popen,
    timeout_s: float,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Wait for the host's first stdout line and parse it.

    Returns:
        (handshake_dict, error_message). Exactly one is non-None.
    """
    deadline = time.monotonic() + timeout_s
    line_holder: List[str] = []
    err_holder: List[str] = []

    def reader():
        try:
            line = proc.stdout.readline() if proc.stdout else ""
            line_holder.append(line)
        except Exception as e:  # pragma: no cover
            err_holder.append(str(e))

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    t.join(timeout=max(0.0, deadline - time.monotonic()))

    if t.is_alive():
        return None, f"no handshake within {timeout_s:.0f}s"
    if err_holder:
        return None, f"stdout read error: {err_holder[0]}"
    if not line_holder or not line_holder[0].strip():
        # Subprocess exited before writing anything.
        rc = proc.poll()
        return None, f"host exited before handshake (returncode={rc})"

    try:
        payload = json.loads(line_holder[0])
    except json.JSONDecodeError as e:
        return None, f"invalid handshake JSON: {e}: {line_holder[0]!r}"
    if "error" in payload:
        return None, f"host startup error: {payload['error']}"
    if "port" not in payload or "tools" not in payload:
        return None, f"handshake missing keys: {payload!r}"
    return payload, None


PER_TOOLKIT_LOG_MAX_BYTES = 5 * 1024 * 1024   # 5 MB
PER_TOOLKIT_LOG_TAIL_BYTES = 2 * 1024 * 1024  # keep ~last 2 MB on prune


def _prune_per_toolkit_log_if_oversized(log_path: Path) -> None:
    """Tail-prune a per-toolkit stderr log if it grew past the size cap.

    Mirrors the serve.log strategy: read the last ~tail bytes, drop any
    partial leading line, rewrite the file. Cheaper than a true rolling
    rotation and good enough for stderr-noise capture.
    """
    try:
        if not log_path.exists():
            return
        size = log_path.stat().st_size
        if size <= PER_TOOLKIT_LOG_MAX_BYTES:
            return
        import os as _os
        with open(log_path, "rb") as f:
            f.seek(-PER_TOOLKIT_LOG_TAIL_BYTES, _os.SEEK_END)
            tail = f.read()
        nl = tail.find(b"\n")
        if nl != -1:
            tail = tail[nl + 1:]
        with open(log_path, "wb") as f:
            f.write(b"# --- log pruned to last ~2 MB ---\n")
            f.write(tail)
    except Exception:
        # Pruning is best-effort; never block startup over it.
        pass


def _start_stderr_pump(
    proc: subprocess.Popen,
    toolkit_name: str,
) -> Tuple[threading.Thread, Any]:
    """Pipe child stderr to ``~/.scitoolkit/logs/<toolkit>.log``.

    Tail-prunes the file at session start if it has grown past the size
    cap so a long-lived install doesn't end up with multi-GB log files.
    """
    log_path = LOGS_DIR / f"{toolkit_name}.log"
    _prune_per_toolkit_log_if_oversized(log_path)
    fh = open(log_path, "a", encoding="utf-8")
    fh.write(f"\n--- session {time.strftime('%Y-%m-%d %H:%M:%S')} pid={proc.pid} ---\n")
    fh.flush()

    def pump():
        try:
            assert proc.stderr is not None
            for line in proc.stderr:
                fh.write(line)
                fh.flush()
        except Exception:
            pass
        finally:
            try:
                fh.close()
            except Exception:
                pass

    t = threading.Thread(target=pump, name=f"stderr-{toolkit_name}", daemon=True)
    t.start()
    return t, fh


# ── orchestrator ────────────────────────────────────────────────────────


class Orchestrator:
    """Holds discovered toolkits, running subprocesses, MCP clients."""

    def __init__(
        self,
        *,
        console: Optional[Console] = None,
        toolkits_dir: Path = TOOLKITS_DIR,
        resolved: Optional[Any] = None,  # serve.config.ResolvedSet, optional
        call_timeout_s: float = DEFAULT_CALL_TIMEOUT_S,
    ):
        self.console = console or Console(stderr=True)
        # Stderr console because stdin/stdout are owned by MCP stdio in the
        # serve flow — anything we print to stdout would corrupt the
        # protocol stream.
        self.toolkits_dir = toolkits_dir
        self.logger = get_logger(serve_log=True)
        self._runtimes: Dict[str, ToolkitRuntime] = {}
        self._proxy_tools: List[Any] = []
        self._shutdown_initiated = False
        # When set, the resolver has decided which toolkits and (optionally)
        # which tools per toolkit to expose. None means "serve everything
        # discoverable" (legacy behavior).
        self._resolved = resolved
        self._call_timeout_s = call_timeout_s

    # ── startup ─────────────────────────────────────────────────────────

    def start(self) -> List[Any]:
        """Discover, spawn, connect, build proxy tools. Returns the list.

        Raises ``RuntimeError`` only if no toolkit is ready to serve at all
        (so the user gets a clear "nothing to do" instead of a silent stdio
        server with zero tools).
        """
        self.logger.log_event("serve_started", message="orchestrator booting")

        discoveries = discover_toolkits(self.toolkits_dir)
        if not discoveries:
            self.console.print(
                "[yellow]No toolkits installed.[/yellow] "
                "Install one with: [cyan]scitoolkit install <name>[/cyan]"
            )
            raise RuntimeError("no toolkits installed")

        # If the resolver has narrowed the set, filter discoveries to match.
        if self._resolved is not None:
            target_names = set(self._resolved.toolkits)
            for d in discoveries:
                if d.skip_reason is None and d.name not in target_names:
                    d.skip_reason = "not in this serve session"
            for w in self._resolved.warnings:
                self.console.print(f"  [yellow]warning:[/yellow] {w}")

        self._print_startup_banner_pre(discoveries)

        ready = [d for d in discoveries if d.skip_reason is None]
        for d in ready:
            self._launch_one(d)

        # Print second half of the banner (final status + tool count).
        self._print_startup_banner_post()

        if not self._runtimes:
            raise RuntimeError("no toolkits could be started")

        return self._proxy_tools

    def _print_startup_banner_pre(self, discoveries: List[ToolkitDiscovery]) -> None:
        self.console.print("\n[bold]Checking installed toolkits...[/bold]")
        for d in discoveries:
            if d.skip_reason is None:
                # We don't yet know tool count for ready ones — fill in
                # after launch. Show "loading" placeholder.
                env = d.env_type
                self.console.print(
                    f"  [dim]…[/dim] [cyan]{d.name:<18}[/cyan] loading ({env})"
                )
            else:
                self.console.print(
                    f"  [yellow]⊘[/yellow] [dim]{d.name:<18}[/dim] "
                    f"[dim]skipped — {d.skip_reason}[/dim]"
                )
                self.logger.log_event(
                    "toolkit_skipped",
                    toolkit=d.name,
                    message=d.skip_reason,
                )
        self.console.print()  # blank line

    def _print_startup_banner_post(self) -> None:
        """After launches complete, print the final per-toolkit verdict."""
        # We don't try to overwrite the "loading" lines (terminal-dependent
        # cursor games we don't want); we just print the final status.
        self.console.print("[bold]Toolkit launch results:[/bold]")
        for name, rt in self._runtimes.items():
            tcount = len(rt.upstream_tool_names)
            self.console.print(
                f"  [green]✓[/green] [cyan]{name:<18}[/cyan] "
                f"ready ({tcount} tool{'s' if tcount != 1 else ''})"
            )
        # Tools that failed to launch were already logged inline by _launch_one.
        total_tools = sum(
            len(rt.upstream_tool_names) for rt in self._runtimes.values()
        )
        self.console.print(
            f"\nStarting MCP server with [bold]{len(self._runtimes)}[/bold] "
            f"toolkit{'s' if len(self._runtimes) != 1 else ''} "
            f"([bold]{total_tools}[/bold] tool{'s' if total_tools != 1 else ''})..."
        )

    # ── per-toolkit launch ──────────────────────────────────────────────

    def _launch_one(self, disc: ToolkitDiscovery) -> None:
        """Spawn host, read handshake, connect MCPClient, build proxies.

        On any failure: log clearly, kill the subprocess, skip this toolkit,
        keep going with the rest.
        """
        try:
            proc = _spawn_host(disc, self.logger)
        except Exception as e:
            self.console.print(
                f"  [red]✗[/red] [dim]{disc.name:<18}[/dim] "
                f"[red]could not spawn: {e}[/red]"
            )
            self.logger.log_event(
                "toolkit_skipped", toolkit=disc.name,
                message=f"spawn failed: {e}", level="error",
            )
            return

        # Pump stderr to <toolkit>.log immediately (so import failures land there).
        stderr_thread, stderr_fh = _start_stderr_pump(proc, disc.name)

        # Read handshake.
        hs, err = _read_handshake(proc, HOST_HANDSHAKE_TIMEOUT_S)
        if err:
            self.console.print(
                f"  [red]✗[/red] [dim]{disc.name:<18}[/dim] [red]{err}[/red]"
            )
            self.logger.log_event(
                "toolkit_skipped", toolkit=disc.name,
                message=err, level="error",
            )
            self._kill(proc)
            return

        port = hs["port"]
        upstream_tools = hs["tools"]

        # Connect MCPClient (HTTP loopback).
        #
        # IMPORTANT — DO NOT add a trailing slash to "/mcp".
        #
        # FastMCP serves the streamable-http endpoint at "/mcp" (no slash).
        # If the URL is "/mcp/" instead, FastMCP returns a 307 redirect to
        # "/mcp", and the streamable-http client (httpx-based) then fails
        # the request with a HTTPStatusError before the MCP session can
        # initialize. The failure is opaque — you get a TaskGroup
        # exception with no obvious indication that a redirect was the
        # problem. Confirmed by debugging in the May 2026 e2e.
        #
        # The matching test is tests/test_orchestrator_url_no_slash.py.
        # Don't "fix" the slash. If you need to change the path itself
        # (e.g. FastMCP's default changes), update the test too.
        try:
            from orchestral.mcp import MCPClient
            client = MCPClient(
                url=f"http://127.0.0.1:{port}/mcp",
                timeout=self._call_timeout_s,
            )
            client.connect()
        except Exception as e:
            self.console.print(
                f"  [red]✗[/red] [dim]{disc.name:<18}[/dim] "
                f"[red]MCP connect failed: {e}[/red]"
            )
            self.logger.log_event(
                "toolkit_skipped", toolkit=disc.name,
                message=f"mcp connect failed: {e}", level="error",
            )
            self._kill(proc)
            return

        self.logger.log_event(
            "mcp_client_connected", toolkit=disc.name,
            port=port, tool_count=len(upstream_tools),
        )

        # Build proxies from the canonical MCP listing (richer schema info
        # than the bare tool name list in the handshake).
        from .proxy_tool import make_proxy_tool

        # Determine which tools from this toolkit to actually expose.
        # `tool_filter` is one of:
        #   None       — expose all tools (no per-tool filter active)
        #   List[str]  — expose only tools in this allowlist
        # Disabled tools (when tool_filter is None) are subtracted via
        # `tool_disable_set`.
        tool_filter: Optional[List[str]] = None
        tool_disable_set: set = set()
        if self._resolved is not None:
            allow = self._resolved.tools.get(disc.name)
            if allow is not None:
                tool_filter = list(allow)
            # Pull disabled-tools subtraction list (qualified names).
            for q in self._resolved.disable_qualified:
                if q.startswith(f"{disc.name}__"):
                    tool_disable_set.add(q.split("__", 1)[1])

        exposed_tools: List[str] = []
        forward = self._make_forwarder(disc.name, client)
        for defn in client.get_tool_definitions():
            upstream_name = defn["name"]
            if tool_filter is not None and upstream_name not in tool_filter:
                continue
            if upstream_name in tool_disable_set:
                continue
            namespaced = f"{disc.name}__{upstream_name}"
            self._proxy_tools.append(make_proxy_tool(
                upstream_name=upstream_name,
                namespaced_name=namespaced,
                description=defn.get("description") or "",
                input_schema=defn.get("inputSchema") or {
                    "type": "object", "properties": {}, "required": []
                },
                forward=forward,
            ))
            exposed_tools.append(upstream_name)

        self._runtimes[disc.name] = ToolkitRuntime(
            name=disc.name,
            path=disc.path,
            proc=proc,
            port=port,
            upstream_tool_names=exposed_tools,
            mcp_client=client,
            stderr_thread=stderr_thread,
            stderr_logfile_handle=stderr_fh,
        )
        self.logger.log_event(
            "toolkit_loaded", toolkit=disc.name,
            tool_count=len(exposed_tools),
        )

    def _make_forwarder(self, toolkit_name: str, client: Any):
        """Return a closure that the proxy uses to invoke an upstream tool.

        We log start/complete here rather than from ProxyTool so the proxy
        stays a dumb forwarder.
        """
        logger = self.logger

        def forward(upstream_name: str, kwargs: Dict[str, Any]) -> str:
            tid = logger.log_tool_start(toolkit_name, upstream_name, kwargs)
            t0 = time.monotonic()
            try:
                result = client.call_tool(upstream_name, kwargs)
                duration = time.monotonic() - t0
                logger.log_tool_complete(tid, duration=duration, success=True)
                return result
            except Exception as e:
                duration = time.monotonic() - t0
                # Some exceptions (httpx ReadTimeout, anyio cancellations,
                # etc.) stringify to empty. Fall back to the class name so
                # the user gets *something* useful rather than a bare colon.
                detail = str(e) or type(e).__name__
                logger.log_tool_complete(
                    tid, duration=duration, success=False, error=detail,
                )
                # Surface the failure to the upstream MCP client (Claude
                # Code) as an error string. MCPServer's handler turns
                # exceptions into MCP error replies, so re-raising would
                # also work — but returning the string is more legible.
                return f"[scitoolkit] {upstream_name} failed after {duration:.1f}s: {detail}"

        return forward

    # ── serve loop ──────────────────────────────────────────────────────

    def run_mcp_stdio(self) -> None:
        """Run the upstream MCP stdio server. Blocks until shutdown."""
        from orchestral.mcp import MCPServer
        server = MCPServer(
            tools=self._proxy_tools,
            name="scitoolkit",
            use_display_names=False,  # we already namespaced with __
        )
        try:
            server.run()
        except KeyboardInterrupt:
            pass

    # ── shutdown ────────────────────────────────────────────────────────

    def shutdown(self) -> None:
        """Disconnect MCP clients, terminate child subprocesses cleanly."""
        if self._shutdown_initiated:
            return
        self._shutdown_initiated = True
        self.logger.log_event("serve_shutting_down")

        for name, rt in list(self._runtimes.items()):
            # Disconnect MCPClient first so it stops trying to talk to a
            # subprocess we're about to kill.
            try:
                rt.mcp_client.disconnect()
                self.logger.log_event(
                    "mcp_client_disconnected", toolkit=name,
                )
            except Exception:
                pass
            self._kill(rt.proc, name=name)

    def _kill(self, proc: subprocess.Popen, name: Optional[str] = None) -> None:
        """Graceful → SIGTERM → SIGKILL.

        ``proc.stdin`` close signals the host to shut down (the host will
        eventually notice EOF on stdin if we add a watcher there; for now
        we go straight to terminate).
        """
        if proc.poll() is not None:
            return
        try:
            proc.terminate()
            try:
                proc.wait(timeout=SHUTDOWN_GRACEFUL_S)
                return
            except subprocess.TimeoutExpired:
                pass
            proc.kill()
            proc.wait(timeout=2.0)
        except Exception as e:
            if name:
                self.logger.log_event(
                    "subprocess_crashed", toolkit=name,
                    message=f"shutdown error: {e}", level="warn",
                )

    # ── context manager ────────────────────────────────────────────────

    def __enter__(self) -> "Orchestrator":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.shutdown()


# ── module-level entrypoint used by CLI ─────────────────────────────────


def serve(
    *,
    no_tui: bool = True,
    resolved: Optional[Any] = None,
    call_timeout_s: float = DEFAULT_CALL_TIMEOUT_S,
) -> int:
    """Top-level entry point for ``scitoolkit serve``.

    For now ``no_tui=False`` is rejected (TUI not implemented yet).

    ``resolved`` is an optional ``serve.config.ResolvedSet`` that narrows
    which toolkits and tools to serve. When None, the legacy "everything"
    behavior is used.

    ``call_timeout_s`` is the upper bound on each upstream tool call as
    enforced by Orchestral's MCPClient. Default 60 s.
    """
    if not no_tui:
        raise NotImplementedError("TUI mode not yet implemented; use --no-tui")

    # Console must write to stderr so it doesn't corrupt the MCP stdio
    # stream we're handing to Claude Code.
    console = Console(stderr=True)
    orch = Orchestrator(
        console=console, resolved=resolved, call_timeout_s=call_timeout_s,
    )

    try:
        orch.start()
    except RuntimeError as e:
        console.print(f"[red]Cannot start serve: {e}[/red]")
        return 1
    except Exception as e:
        console.print(f"[red]Startup failed: {e}[/red]")
        import traceback
        console.print(f"[dim]{traceback.format_exc()}[/dim]")
        orch.shutdown()
        return 2

    # Install signal handlers so a Ctrl-C tears subprocesses down cleanly.
    def _sigterm(*_):
        orch.shutdown()
        sys.exit(0)
    signal.signal(signal.SIGTERM, _sigterm)
    signal.signal(signal.SIGINT, _sigterm)

    # If stdin is a TTY, a human ran `scitoolkit serve` directly. The next
    # thing to happen would be silence forever (this process waits on stdin
    # for MCP JSON-RPC), which looks identical to a hang and is misleading:
    # Claude Code does NOT connect to a running serve, it spawns its own
    # subprocess. So make the framing honest.
    if sys.stdin.isatty():
        console.print(
            "\n[dim]This is a standalone serve process. It will idle until "
            "an MCP client writes JSON-RPC to its stdin.[/dim]"
        )
        console.print(
            "[dim]Note: Claude Code spawns its own `scitoolkit serve` "
            "subprocess; it does not connect to this one.[/dim]"
        )
        console.print(
            "[dim]To watch tool calls Claude Code makes, run "
            "`scitoolkit logs` in another terminal. Press Ctrl-C to stop.[/dim]"
        )

    try:
        orch.run_mcp_stdio()
        return 0
    finally:
        orch.shutdown()
