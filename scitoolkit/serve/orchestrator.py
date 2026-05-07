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

- Per-call timeout enforcement (relies on MCPClient's default).
- TUI-facing event subscription API.
- Hot reload.

Implemented post-MVP:

- Auto-restart of crashed per-toolkit subprocesses with exponential
  backoff (1s, 4s, 16s; budget 3). See §3.3 of SERVE_ARCHITECTURE.md.

See ``stk-package/docs/SERVE_ARCHITECTURE.md`` for the full design.
"""

from __future__ import annotations

import enum
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
# After the host emits its handshake JSON, FastMCP still has to finish
# binding the port and accepting connections — there's a tiny window
# where the port is reported but not yet listening. A subsequent
# ``MCPClient.connect()`` can race that window and fail with
# ``httpx.ConnectError``. We poll the port for accept-readiness with this
# total budget before giving up. In practice it resolves in <100 ms.
HOST_PORT_READY_TIMEOUT_S = 5.0
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

# Restart policy for a per-toolkit subprocess that crashes *after* a
# successful initial connect (i.e. CRASHED in the lifecycle state machine).
#
# Initial-launch failures do NOT consume this budget — if `start()` can't
# bring a toolkit up (spawn failed, handshake timed out, MCPClient connect
# failed), the orchestrator skips that toolkit immediately and keeps
# serving the rest. Restart budget is reserved for *runtime* crashes
# (subprocess died mid-call, OOM-killed between calls). Configuration
# bugs don't get fixed by restarting three times in 21 seconds; flakes do.
RESTART_BUDGET = 3
RESTART_BACKOFF_S = (1.0, 4.0, 16.0)


class ToolkitState(enum.Enum):
    """Per-toolkit lifecycle state. See SERVE_ARCHITECTURE.md §3.7."""
    DISCOVERED = "discovered"  # walked, classified, not yet spawned
    STARTING = "starting"      # spawn → handshake → connect in flight
    READY = "ready"            # MCPClient connected; calls succeeding
    CRASHED = "crashed"        # detected dead; restart pending or running
    FAILED = "failed"          # restart budget exhausted; terminal
    STOPPED = "stopped"        # user toggled off (TUI hook; unused now)


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
    """A successfully spawned toolkit, post-handshake.

    Carries the data needed to talk to the subprocess (proc, port, client)
    plus the lifecycle state used by the restart machinery. ``discovery``
    is held so a restart can re-build the spawn argv without re-walking
    TOOLKITS_DIR or re-reading metadata.
    """
    name: str
    path: Path
    proc: subprocess.Popen
    port: int
    upstream_tool_names: List[str]
    mcp_client: Any  # orchestral.mcp.MCPClient
    stderr_thread: Optional[threading.Thread] = None
    stderr_logfile_handle: Optional[Any] = None
    # Restart machinery. See RESTART_BUDGET / RESTART_BACKOFF_S.
    state: ToolkitState = ToolkitState.READY
    restart_attempts: int = 0
    # Serializes restart kickoff so parallel tool calls on the same
    # crashed toolkit don't double-spawn the restart thread.
    restart_lock: threading.Lock = field(default_factory=threading.Lock)
    # Original discovery record; needed to re-spawn on restart.
    discovery: Optional[ToolkitDiscovery] = None
    # Last error seen on a permanently-failed toolkit (for the agent-facing
    # message and the `toolkit_permanently_failed` telemetry).
    last_error: str = ""


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
        elif env not in ("venv", "conda"):
            skip = f"unknown environment type: {env!r}"
        # NB: ``meta.get("needs_setup")`` (Tier-2 setup.py present)
        # used to skip here in 3C-1 with "Phase 3C-2 not yet runnable."
        # That gate is now lifted; ``_resolve_state_config`` calls
        # ``validate_setup_script_cached`` for setup_script toolkits
        # and surfaces the validate result as the skip reason if it
        # fails.

        found.append(ToolkitDiscovery(
            name=entry.name, path=entry, meta=meta, skip_reason=skip,
        ))
    return found


def _resolve_state_config(
    disc: ToolkitDiscovery,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Validate the toolkit's stored config against its declared schema.

    Returns ``(state_config_dict, skip_reason)``. Exactly one is non-None:

    - ``({...}, None)`` — config is valid (or the toolkit has no
      ``config:`` block, in which case ``state_config_dict`` is empty).
      The orchestrator passes the dict to the host subprocess via
      ``--state-config <json>``.
    - ``(None, reason)`` — the toolkit declared required fields the
      user hasn't filled in, or stored values fail validation. The
      orchestrator skips this toolkit with ``reason`` in the banner.

    Imports the setup module lazily so a malformed ``config:`` block
    in some other toolkit can't take down the whole orchestrator
    startup. Per-toolkit failures stay per-toolkit.
    """
    # Read the toolkit's published `config:` block from its toolkit.yaml.
    # The metadata file (.stk_meta.json) doesn't carry it because the
    # block is the toolkit author's published schema, not user data.
    yaml_path = disc.path / "toolkit.yaml"
    if not yaml_path.exists():
        # Broken install at this point shouldn't happen — discover
        # already filtered missing metadata — but be defensive.
        return None, "toolkit.yaml missing (broken install)"

    try:
        import yaml as _yaml
        with open(yaml_path, "r") as f:
            tk_data = _yaml.safe_load(f) or {}
    except Exception as e:
        return None, f"unreadable toolkit.yaml: {e}"

    raw_block = tk_data.get("config") or []
    has_setup_py = (disc.path / "setup.py").exists()
    declares_setup = bool(tk_data.get("setup_script"))

    # Tier-1 declarative validation (config: block).
    state_config: Dict[str, Any] = {}
    if raw_block:
        try:
            from ..setup import parse_config_block, load_state_config
        except Exception as e:
            return None, f"setup module unavailable: {e}"

        try:
            schema = parse_config_block(raw_block)
        except Exception as e:
            return None, f"invalid config: schema in toolkit.yaml: {e}"

        resolution = load_state_config(disc.name, schema)
        if not resolution.ok:
            return None, "config incomplete — " + (resolution.skip_reason() or "unknown")
        state_config = dict(resolution.state_config)

    # Tier-2 validate(ctx) — only if the toolkit declares setup_script
    # AND has a setup.py at root. Both checks are needed because a
    # toolkit could ship one without the other (broken state we surface
    # explicitly at validate / publish time, but be defensive here).
    if declares_setup and has_setup_py:
        try:
            from ..setup import validate_setup_script_cached
        except Exception as e:
            return None, f"setup module unavailable: {e}"
        try:
            v_result = validate_setup_script_cached(disc.name)
        except Exception as e:
            return None, f"validate(ctx) failed to run: {e}"
        if not v_result.ok:
            msg = v_result.message or "validate(ctx) returned False"
            return None, f"validate(ctx) failed — {msg}"

    return state_config, None


# ── subprocess launch ───────────────────────────────────────────────────


def _read_tools_spec(toolkit_path: Path) -> List[Dict[str, Any]]:
    """Extract the ``tools:`` list from the toolkit's yaml, if present.

    Returns ``[]`` when the yaml is missing, malformed, or carries no
    ``tools:`` field — the host treats that as "fall back to implicit
    tools/__init__.py discovery", which is the legacy path. Each
    returned entry is a dict with at least ``name`` and either
    ``module`` (explicit form) or ``function`` (implicit form).
    """
    yaml_path = toolkit_path / "toolkit.yaml"
    if not yaml_path.is_file():
        return []
    try:
        import yaml as pyyaml  # PyYAML; bundled with scitoolkit's deps
        data = pyyaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    tools = data.get("tools")
    if not isinstance(tools, list):
        return []
    out: List[Dict[str, Any]] = []
    for entry in tools:
        if not isinstance(entry, dict):
            continue
        # Pass through only the fields the host consumes.
        cleaned: Dict[str, Any] = {}
        for key in ("name", "module", "function", "description"):
            if key in entry:
                cleaned[key] = entry[key]
        if "name" in cleaned and ("module" in cleaned or "function" in cleaned):
            out.append(cleaned)
    return out


def _build_host_command(
    disc: ToolkitDiscovery,
    *,
    state_config: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """Return the argv for spawning the per-toolkit host subprocess.

    ``state_config`` is the validated Phase 3C-1 config dict (flat
    ``{state_field: value}``) for the toolkit. ``None`` or empty dict
    is serialized to ``""`` for the host's empty-input fast path.
    """
    state_arg = ""
    if state_config:
        state_arg = json.dumps(state_config, ensure_ascii=False)
    tools_spec = _read_tools_spec(disc.path)
    tools_spec_arg = ""
    if tools_spec:
        tools_spec_arg = json.dumps(tools_spec, ensure_ascii=False)
    base_args = [
        "-m", "scitoolkit._toolkit_host",
        "--toolkit-dir", str(disc.path),
        "--name", disc.name,
        "--state-config", state_arg,
        "--tools-spec", tools_spec_arg,
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


def _spawn_host(
    disc: ToolkitDiscovery,
    logger: ToolLogger,
    *,
    state_config: Optional[Dict[str, Any]] = None,
) -> subprocess.Popen:
    """Launch the host subprocess. Returns the Popen handle.

    ``state_config`` is forwarded to the host via ``--state-config``
    JSON so ``_inject_state_into_tools`` can populate ``@define_tool(
    state=[...])`` fields before any tool call lands.
    """
    cmd = _build_host_command(disc, state_config=state_config)
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


def _wait_for_port_ready(
    port: int, timeout_s: float = HOST_PORT_READY_TIMEOUT_S,
) -> bool:
    """Block until 127.0.0.1:port accepts TCP connections, or timeout.

    The host emits its handshake before ``mcp.run()`` actually binds the
    listen socket — there's a small window where the port number is known
    but not yet listening. Without this poll, ``MCPClient.connect()`` can
    lose the race and fail with ``httpx.ConnectError``. The race is
    invisible at first launch (the orchestrator does enough other work
    between handshake and connect to mask it) but reliable on restart
    when Python is warm and the loop runs faster.

    Returns True if the port came up, False on timeout. Caller decides
    whether to proceed (we still try to connect either way; the connect
    error will be more informative than a timeout from here).
    """
    import socket
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            try:
                s.connect(("127.0.0.1", port))
                return True
            except (ConnectionRefusedError, OSError):
                time.sleep(0.05)
    return False


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

    @dataclass
    class _SpawnResult:
        """Internal: artifacts of a successful spawn → connect sequence."""
        proc: subprocess.Popen
        stderr_thread: threading.Thread
        stderr_fh: Any
        port: int
        upstream_tools: List[str]
        client: Any  # orchestral.mcp.MCPClient

    def _spawn_and_connect(
        self,
        disc: ToolkitDiscovery,
        *,
        state_config: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Optional["Orchestrator._SpawnResult"], Optional[str]]:
        """Spawn the host, read handshake, connect MCPClient.

        Returns ``(SpawnResult, None)`` on success or ``(None, error)`` on
        failure. On failure, any subprocess that did get spawned is killed
        before returning.

        Used both by initial launch (``_launch_one``) and by restart
        (``_attempt_restart``). Does not touch ``self._runtimes`` or the
        proxy-tools list.

        ``state_config`` is forwarded to ``_spawn_host`` so the toolkit
        host can inject Phase 3C-1 declarative config values onto its
        tool instances before they're called.
        """
        try:
            proc = _spawn_host(disc, self.logger, state_config=state_config)
        except Exception as e:
            return None, f"spawn failed: {e}"

        # Pump stderr to <toolkit>.log immediately (so import failures land there).
        stderr_thread, stderr_fh = _start_stderr_pump(proc, disc.name)

        hs, err = _read_handshake(proc, HOST_HANDSHAKE_TIMEOUT_S)
        if err:
            self._kill(proc)
            return None, err

        port = hs["port"]
        upstream_tools = hs["tools"]

        # Wait for the port to actually accept connections. The host
        # emits its handshake before mcp.run() binds the socket; without
        # this poll, MCPClient.connect() can race the bind and fail with
        # the opaque "TaskGroup (1 sub-exception)" / httpx.ConnectError
        # combo. See HOST_PORT_READY_TIMEOUT_S.
        _wait_for_port_ready(port)

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
            self._kill(proc)
            return None, f"mcp connect failed: {e}"

        return Orchestrator._SpawnResult(
            proc=proc,
            stderr_thread=stderr_thread,
            stderr_fh=stderr_fh,
            port=port,
            upstream_tools=upstream_tools,
            client=client,
        ), None

    def _launch_one(self, disc: ToolkitDiscovery) -> None:
        """Spawn host, read handshake, connect MCPClient, build proxies.

        On any failure: log clearly, skip this toolkit, keep going with
        the rest. Initial-launch failures do NOT consume the per-toolkit
        restart budget — see the comment on ``RESTART_BUDGET``.

        Phase 3C-1: before spawning, resolve the toolkit's stored
        config against its declared schema. Missing required fields or
        invalid values short-circuit to a skip with a clear pointer to
        ``scitoolkit config edit <toolkit>``.
        """
        # Resolve declarative state-config first. A missing-required-
        # field condition means we never spawn.
        state_config, config_err = _resolve_state_config(disc)
        if config_err is not None:
            self.console.print(
                f"  [red]✗[/red] [dim]{disc.name:<18}[/dim] "
                f"[red]{config_err}[/red]"
            )
            self.console.print(
                f"     [dim]Edit:[/dim] "
                f"~/.scitoolkit/config/{disc.name}.yaml"
            )
            self.console.print(
                f"     [dim]Or:[/dim] "
                f"scitoolkit config edit {disc.name}"
            )
            self.logger.log_event(
                "toolkit_skipped", toolkit=disc.name,
                message=config_err, level="warn",
            )
            return

        spawn, err = self._spawn_and_connect(disc, state_config=state_config)
        if err is not None:
            self.console.print(
                f"  [red]✗[/red] [dim]{disc.name:<18}[/dim] [red]{err}[/red]"
            )
            self.logger.log_event(
                "toolkit_skipped", toolkit=disc.name,
                message=err, level="error",
            )
            return
        assert spawn is not None  # for type checkers

        self.logger.log_event(
            "mcp_client_connected", toolkit=disc.name,
            port=spawn.port, tool_count=len(spawn.upstream_tools),
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

        # Forwarder is bound to the toolkit *name*, not the client. The
        # forwarder looks up the live MCPClient on every call so a restart
        # that swaps the client is picked up transparently.
        exposed_tools: List[str] = []
        forward = self._make_forwarder(disc.name)
        for defn in spawn.client.get_tool_definitions():
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
            proc=spawn.proc,
            port=spawn.port,
            upstream_tool_names=exposed_tools,
            mcp_client=spawn.client,
            stderr_thread=spawn.stderr_thread,
            stderr_logfile_handle=spawn.stderr_fh,
            state=ToolkitState.READY,
            discovery=disc,
        )
        self.logger.log_event(
            "toolkit_loaded", toolkit=disc.name,
            tool_count=len(exposed_tools),
        )

    # ── restart machinery (subprocess crash recovery) ──────────────────

    # See SERVE_ARCHITECTURE.md §3.3 / §3.7 and RESTART_BUDGET above.

    @staticmethod
    def _is_crash_exception(exc: BaseException) -> bool:
        """Decide whether an exception from ``client.call_tool`` indicates
        the subprocess died (vs. the tool itself raising).

        Crash signals: connection-class errors from httpx or stdlib. Tool
        exceptions (RuntimeError, ValueError, etc. raised inside the
        tool body) are *not* crashes — Orchestral catches those upstream
        and turns them into ``isError=True`` MCP results that come back
        through the wire normally; we only see them when something more
        fundamental is wrong.

        ``proc.poll() is not None`` is the load-bearing check (handled
        by the caller via ``_classify_call_failure``); this function is
        the exception-shape heuristic that runs first.
        """
        # ConnectionError covers most stdlib-level cases (connection
        # refused, reset, etc.). httpx errors don't subclass it, so we
        # match by name to avoid a hard import dependency on httpx (it
        # comes in transitively via the MCP client).
        if isinstance(exc, ConnectionError):
            return True
        cls_name = type(exc).__name__
        if cls_name in (
            "ConnectError",         # httpx: TCP connect failed
            "RemoteProtocolError",  # httpx: server closed connection mid-stream
            "ReadError",            # httpx: socket read failed
        ):
            return True
        # ExceptionGroup / TaskGroup wrapped errors (anyio): unwrap one level.
        inner = getattr(exc, "exceptions", None)
        if inner:
            return any(Orchestrator._is_crash_exception(e) for e in inner)
        return False

    def _classify_call_failure(
        self, rt: ToolkitRuntime, exc: BaseException
    ) -> bool:
        """Return True iff the failure represents a subprocess crash.

        Combines the exception shape with a ``proc.poll()`` check — even
        if the exception type doesn't look connection-y, a dead subprocess
        is a crash.
        """
        if self._is_crash_exception(exc):
            return True
        try:
            return rt.proc.poll() is not None
        except Exception:
            return False

    def _schedule_restart(self, rt: ToolkitRuntime) -> None:
        """If no restart is in flight, start one on a background thread.

        The lock guarantees exactly one restart attempt is queued per
        crash event, even when parallel tool calls all detect the same
        crashed subprocess.
        """
        with rt.restart_lock:
            if rt.state == ToolkitState.STARTING:
                # A restart is already running; nothing to do.
                return
            if rt.state == ToolkitState.FAILED:
                # Permanently failed; no further restart attempts.
                return
            if rt.restart_attempts >= RESTART_BUDGET:
                rt.state = ToolkitState.FAILED
                self.logger.log_event(
                    "toolkit_permanently_failed", toolkit=rt.name,
                    message=rt.last_error or "restart budget exhausted",
                    level="error",
                    attempts=rt.restart_attempts,
                    final_error=rt.last_error or "",
                )
                return
            attempt = rt.restart_attempts + 1
            backoff = RESTART_BACKOFF_S[
                min(attempt - 1, len(RESTART_BACKOFF_S) - 1)
            ]
            rt.state = ToolkitState.STARTING
            self.logger.log_event(
                "restart_scheduled", toolkit=rt.name,
                attempt=attempt, backoff_s=backoff,
            )
            t = threading.Thread(
                target=self._attempt_restart,
                args=(rt, attempt, backoff),
                name=f"restart-{rt.name}-{attempt}",
                daemon=True,
            )
            t.start()

    def _attempt_restart(
        self, rt: ToolkitRuntime, attempt: int, backoff_s: float
    ) -> None:
        """Wait ``backoff_s``, then try to spawn-and-connect again.

        Runs on a daemon thread. On success, swaps in the new subprocess
        and client and returns to READY. On failure, increments the
        attempt counter; if budget remains, schedules the next attempt;
        otherwise marks the toolkit FAILED.
        """
        if self._shutdown_initiated:
            return
        time.sleep(backoff_s)
        if self._shutdown_initiated:
            return

        self.logger.log_event(
            "restart_attempt", toolkit=rt.name, attempt=attempt,
        )

        if rt.discovery is None:
            # Defensive: shouldn't happen for a runtime we built.
            rt.state = ToolkitState.FAILED
            rt.last_error = "missing discovery record"
            self.logger.log_event(
                "toolkit_permanently_failed", toolkit=rt.name,
                message="missing discovery record", level="error",
                attempts=attempt, final_error="missing discovery record",
            )
            return

        # Best-effort cleanup of the prior MCPClient. Each MCPClient
        # owns a daemon thread running its own asyncio loop; abandoning
        # them across many restarts would leak threads and event-loop
        # state. Disconnect failures are non-fatal (the client may
        # already be in a broken state from the connection drop).
        try:
            rt.mcp_client.disconnect()
        except Exception:
            pass

        # Best-effort cleanup of the prior dead subprocess. The proc may
        # already be reaped, but if it died by exception (not exit) the
        # zombie sticks around until we wait on it.
        try:
            if rt.proc.poll() is None:
                self._kill(rt.proc, name=rt.name)
        except Exception:
            pass

        # Re-resolve state-config on restart in case the user edited the
        # config file between sessions (the file is canonical; we always
        # read fresh). Same shape as initial launch: a config error here
        # marks the toolkit failed for this restart attempt.
        state_config, config_err = _resolve_state_config(rt.discovery)
        if config_err is not None:
            rt.restart_attempts = attempt
            rt.last_error = config_err
            self.logger.log_event(
                "restart_failed", toolkit=rt.name,
                attempt=attempt, message=config_err, level="warn",
            )
            rt.state = ToolkitState.FAILED
            self.logger.log_event(
                "toolkit_permanently_failed", toolkit=rt.name,
                message=config_err, level="error",
                attempts=attempt, final_error=config_err,
            )
            return

        spawn, err = self._spawn_and_connect(
            rt.discovery, state_config=state_config,
        )
        if err is not None:
            rt.restart_attempts = attempt
            rt.last_error = err
            self.logger.log_event(
                "restart_failed", toolkit=rt.name,
                attempt=attempt, message=err, level="warn",
            )
            if attempt >= RESTART_BUDGET:
                rt.state = ToolkitState.FAILED
                self.logger.log_event(
                    "toolkit_permanently_failed", toolkit=rt.name,
                    message=err, level="error",
                    attempts=attempt, final_error=err,
                )
                # Note: we deliberately do NOT send an MCP
                # `tools/list_changed` notification here. Orchestral's
                # MCPServer exposes no public surface for arbitrary
                # notifications; logging is the best we can do today.
                # See HANDOFF.md "upstream-blocked" entry.
            else:
                # State stays STARTING via _schedule_restart's transition;
                # reset to CRASHED so the next call's _schedule_restart
                # treats it as a fresh schedule (not a reentry). Then
                # schedule the next attempt with the longer backoff.
                rt.state = ToolkitState.CRASHED
                self._schedule_restart(rt)
            return

        # Success. Swap in the new subprocess and client; keep the same
        # ToolkitRuntime object so the proxy's forwarder (which looks up
        # by name) sees the new client on its next call.
        assert spawn is not None
        rt.proc = spawn.proc
        rt.port = spawn.port
        rt.mcp_client = spawn.client
        rt.stderr_thread = spawn.stderr_thread
        rt.stderr_logfile_handle = spawn.stderr_fh
        rt.state = ToolkitState.READY
        rt.restart_attempts = attempt
        rt.last_error = ""
        # Per SERVE_ARCHITECTURE.md §3.3: "at most 3 restarts per toolkit
        # per orchestrator session." We count *attempts* (success or
        # failure), not failures. A toolkit that crashes 3 separate times
        # in one session is suspect — silently restarting forever masks
        # the bug. The 4th crash transitions to FAILED via _schedule_restart.
        self.logger.log_event(
            "restart_succeeded", toolkit=rt.name,
            attempt=attempt, port=spawn.port,
            tool_count=len(spawn.upstream_tools),
        )

    def _make_forwarder(self, toolkit_name: str):
        """Return a closure that the proxy uses to invoke an upstream tool.

        Bound to the toolkit *name*, not its MCPClient. The forwarder
        resolves the live runtime on each call so a restart that swaps
        in a fresh client is picked up transparently. Crash detection
        and restart scheduling happen here.
        """
        logger = self.logger

        def forward(upstream_name: str, kwargs: Dict[str, Any]) -> str:
            rt = self._runtimes.get(toolkit_name)
            if rt is None:
                # Should not happen — runtime is created before any proxy
                # tool that references it. Defensive.
                return (
                    f"Tool unavailable: {toolkit_name} runtime not registered."
                )

            # If the toolkit is in a non-ready state, return guidance
            # without attempting the call. This covers two cases:
            #   - CRASHED: a prior call detected a dead subprocess; a
            #     restart is in flight or will be scheduled by this call.
            #   - FAILED: budget exhausted; no point trying.
            if rt.state == ToolkitState.FAILED:
                return (
                    f"Tool unavailable: subprocess crashed "
                    f"{rt.restart_attempts} times. Marked failed for this "
                    f"serve session. Run scitoolkit logs for details."
                )
            if rt.state in (ToolkitState.CRASHED, ToolkitState.STARTING):
                # Make sure a restart is queued (idempotent thanks to lock).
                self._schedule_restart(rt)
                return (
                    f"Tool unavailable: restart in progress "
                    f"(attempt {rt.restart_attempts + 1} of {RESTART_BUDGET}). "
                    f"Retry shortly."
                )

            tid = logger.log_tool_start(toolkit_name, upstream_name, kwargs)
            t0 = time.monotonic()
            try:
                result = rt.mcp_client.call_tool(upstream_name, kwargs)
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

                if self._classify_call_failure(rt, e):
                    # Subprocess died. Transition to CRASHED, schedule
                    # restart, return guidance.
                    pid = rt.proc.pid
                    rt.state = ToolkitState.CRASHED
                    rt.last_error = detail
                    self.logger.log_event(
                        "subprocess_crashed", toolkit=toolkit_name,
                        message=detail, level="warn",
                        pid=pid, state_before="ready",
                    )
                    self._schedule_restart(rt)
                    next_attempt = min(
                        rt.restart_attempts + 1, RESTART_BUDGET
                    )
                    return (
                        f"Tool unavailable: subprocess crashed. Automatic "
                        f"restart scheduled (attempt {next_attempt} of "
                        f"{RESTART_BUDGET}). Retry in a few seconds."
                    )

                # Tool error (or transient non-crash failure). Surface
                # the failure to the upstream MCP client (Claude Code)
                # as an error string. Don't touch toolkit state.
                return f"{upstream_name} failed after {duration:.1f}s: {detail}"

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
