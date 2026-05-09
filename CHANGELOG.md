# Changelog

All notable changes to `scitoolkit` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

---

## [0.4.1] — TBD (in tree, not yet shipped)

Bundle of two coherent pieces of work, neither big enough to warrant its own release: CLI-driven toolkit creation (eliminating the website round-trip from the agent-onboarding flow), and Orchestral 1.4 stdio MCPClient cleanup (retiring the ~150-200 LOC HTTP-loopback machinery in `serve/orchestrator.py` now that Orchestral 1.4's persistent stdio client makes it unnecessary).

### Added

- **`scitoolkit create <name>` command.** Registers a new toolkit row at the registry from the CLI without a website round-trip. Required flags: `--category/-c`, `--description/-d`. Optional: `--organization` (passed through; reserved for future org support), `--version` (default `0.1.0`). Authenticates via the per-user CLI token in `~/.scitoolkit/token`; prints a clear "run scitoolkit login first" pointer if no token. Local validation runs before any network call (name format, length, category whitelist via `get_allowed_categories()`, description length). On success, prints next-step pointers for both `scitoolkit init <name>` (scaffold a fresh dir) and `scitoolkit ingest .` (onboard existing code). Lands in the "Authoring & publishing" section alongside `init`, `ingest`, `validate`, `publish`. The legacy per-toolkit token returned by the registry is intentionally not persisted — `auth.load_token_for_publish()` already resolves the per-user token first, so saving the legacy token would just create another credential to manage.

### Changed

- **Per-toolkit serve transport: HTTP loopback → MCP stdio.** The orchestrator now drives each per-toolkit subprocess via Orchestral 1.4's persistent stdio `MCPClient(server_command=[...])` instead of spawning a `Popen` and connecting an `MCPClient(url=...)` to a FastMCP HTTP loopback server. The wire is the subprocess's own stdin/stdout pipe; there is no port, no handshake JSON line, no port-bind race, no `/mcp` URL. Wire shift is invisible to toolkit authors and to the agent (Claude Code) — the upstream MCP stdio surface is unchanged. Internal-only architectural change.
- **Per-toolkit stderr capture: orchestrator `Popen(stderr=PIPE)` pump → host-side direct write.** Pre-0.4.1 the orchestrator captured the host's stderr via a pipe and pumped it to `~/.scitoolkit/logs/<toolkit>.log` from a daemon thread. With Orchestral 1.4's `MCPClient` owning the `Popen` lifecycle, the orchestrator no longer holds the stderr handle. The host now opens the same log path directly via the new `SCITOOLKIT_HOST_LOG` env var (passed by the orchestrator at spawn time) and redirects its own `sys.stderr` to it at startup. Same destination, same per-toolkit log file, simpler plumbing. Sentinel test (`test_host_stderr_capture.py`) pins the routing.
- **Crash detection: `proc.poll()` → `MCPClient._subprocess_died`.** Pre-0.4.1 the orchestrator polled the subprocess via its `Popen` handle to detect crashes; post-cleanup the canonical signal is `MCPSubprocessDiedError` raised by `MCPClient.call_tool` after the persistent-session loop sees the connection drop, with `client._subprocess_died` as the underlying flag. The restart state machine is otherwise unchanged.

### Internal

- Retired ~250 LOC of HTTP-loopback machinery from `serve/orchestrator.py` and `_toolkit_host.py`: `_find_free_loopback_port`, `_emit_handshake`, `_read_handshake`, `_wait_for_port_ready`, `_start_stderr_pump`, the `_kill` Popen-reaping helper, FastMCP server construction. `ToolkitRuntime` lost `proc`, `port`, `stderr_thread`, `stderr_logfile_handle` fields.
- Two HANDOFF gotchas retired: #1 (`/mcp` URL trailing slash) and #11 (host port-bind race). Both are gone with the HTTP loopback. Historical entries kept in HANDOFF.md so future revivers re-read the original incidents before reintroducing the URL.
- New module-level constant `SCITOOLKIT_HOST_LOG` is the env-var contract between orchestrator and host for log routing. Set by `_build_host_env` to `~/.scitoolkit/logs/<toolkit>.log`; consumed by `_redirect_stderr_to_log` at host startup.
- Sentinel test `test_host_stderr_capture.py` covers the redirect: env-set → file capture works, env-unset → stderr unchanged, unwritable path → graceful no-op.
- Deleted `tests/test_orchestrator_url_no_slash.py` (the URL is gone; no slash to sentinel).
- 698 unit tests green (Item 1 added 16, Item 2 added 3, sentinel deletion subtracted 2; baseline 681).

---

## [0.4.0] — 2026-05-07

The ingest release. `scitoolkit ingest` lets authors with existing scientific codebases generate a `toolkit.yaml` from their repo without restructuring their code or maintaining a hand-edited `tools/__init__.py` mirror. The yaml gains a new explicit form (`module:` import paths) alongside the existing implicit form (`function:` paths into `tools/`); both are supported forever. Driven by HEPTAPOD's first-real-world-toolkit porting case.

### Added

- **`scitoolkit ingest [PATH]` command.** Walks an existing repo, AST-parses every `.py` file, detects tools via `@define_tool` decorators and `BaseTool` subclasses, and emits a `toolkit.yaml` skeleton with explicit import paths. Pure static analysis — never imports the modules being scanned. Honors `.gitignore`, skips `tests/`, `__pycache__`, `.venv`, build dirs, and hidden files. Flags: `--output/-o`, `--force`, `--dry-run`, plus the standard `--yes/--no/--no-input` interactive set. Author keeps their code where it is; the yaml is the manifest.
- **Explicit `tools:` form in `toolkit.yaml`.** Each entry can declare `module: <dotted-path>` (resolved against the toolkit root) and `name: <attr>` instead of the existing `function: tools.foo.bar`. `description:` becomes optional in this form (falls back to the function/class docstring). Both forms coexist within the same yaml; in practice each toolkit picks one.
- **Validation rules for the new form.** Mutually-exclusive `function` xor `module`. Duplicate-entry detection by `(module, name)` or `(function, name)` pair. Path-residence check: explicit-form modules must resolve to a file under the toolkit root OR appear as a top-level dep in `requirements.txt` (the tarball wouldn't include them otherwise; loud-failure at validate time, not silent at install time).
- **Per-toolkit host explicit-form imports** (`scitoolkit/_toolkit_host.py::_import_module_no_syspath`). Imports each declared module by file resolution against the toolkit root using `importlib.util.spec_from_file_location` — no `sys.path` mutation. Same discipline as the existing `tools/__init__.py` import path, for the same reason (HANDOFF gotcha #2: top-level dirs in toolkits can shadow installed packages of the same name).

### Changed

- **`tools:` field schema is a discriminated union.** `ToolDefinition` accepts either `{name, function, description}` (implicit form, the existing shape) or `{name, module, description?}` (explicit form, new). Pydantic model validator enforces exactly one form per entry. Backward-compatible: every existing toolkit yaml still validates and serves unchanged.
- **`tools/` directory is no longer required for all-explicit-form toolkits.** Toolkits whose `tools:` list is entirely explicit-form (`module:` entries) skip the historical `tools/__init__.py` requirement. Mixed-form and all-implicit-form toolkits still require it.
- **Orchestrator passes `--tools-spec` to the per-toolkit host.** The orchestrator parses each toolkit's `toolkit.yaml` at spawn time and forwards the parsed `tools:` list to `_toolkit_host.py` as a JSON arg. Empty/absent triggers the implicit fallback path; non-empty drives explicit-form imports. No dependency added to the toolkit-env; the orchestrator's host already has PyYAML.

### Internal

- New module: `scitoolkit/ingest.py` (~440 LOC). `ToolDescriptor`, gitignore-aware walker, AST-only decorator/subclass detection (handles aliasing, attribute-access, `TYPE_CHECKING` exclusion, nested-scope exclusion), comment-preserving yaml emission via `ruamel.yaml`.
- New tests: `test_ingest_walker.py` (16 cases), `test_ingest_ast.py` (28 cases), `test_ingest_yaml_emit.py` (16 cases), `test_ingest_command.py` (10 cases), `test_validation_explicit_tools.py` (14 cases), `test_toolkit_host_explicit_tools.py` (14 cases). 98 new unit tests; 647 total green.
- New e2e: `tests/e2e/run_ingest_e2e.py` against `test-existing-repo-fixture/` — synthetic HEPTAPOD-shaped repo (decorated functions in 2 modules + a BaseTool subclass + non-tool helpers + a `.gitignore` pattern + a `tests/` dir to skip). Drives ingest → patch metadata → validate → host-import end-to-end.
- HANDOFF gotcha #2 unchanged but now also covers the explicit-form case: explicit imports use `spec_from_file_location` with a per-module `submodule_search_locations` argument, never `sys.path.insert`.

---

## [0.3.0] — 2026-05-06

The configuration system. Toolkits with API keys, downloadable data, and derived state now work end-to-end. The full create → publish → install → setup → serve loop is live for the broadest class of toolkits we've supported. ASTER-class workflows — multi-GB downloads, hardware detection, custom setup logic — are now wireable.

### Added

- **File-canonical YAML configuration** at `~/.scitoolkit/config/<toolkit>.yaml`. Mode 0600, hand-editable, comment-preserving (via `ruamel.yaml`). One file per toolkit, source-of-truth for all configured values. Users edit anytime; runtime always reads fresh.
- **Two-tier setup system.** Tier 1 — declarative `config:` block in `toolkit.yaml` for simple cases (the user-supplied values). Tier 2 — optional `setup.py` at the toolkit root for complex flows (downloads, hardware detection, multi-step setup). Both write to the same canonical YAML; both feed the same state-injection pipeline at serve time.
- **Seven config field types:** `string`, `secret`, `path`, `integer`, `float`, `boolean`, `choice`. Each gets per-type validation at parse time. Defaults validated against the declared type. `secret` fields are masked in `scitoolkit config show` and hidden during install prompts. `path` fields tilde-expand. `integer`/`float` support optional `min`/`max` bounds. `choice` requires an `options:` list (≥2 unique entries).
- **`scitoolkit setup <toolkit>` command** with `--reset` (delete config + re-run; consequential, default-N) and `--check` (run `validate(ctx)` only; useful to diagnose serve-startup skips). Honors `--yes`/`--no`/`--no-input` like the rest of the CLI.
- **`scitoolkit config` subcommand group:** `show`, `edit` (drops a populated template if no file yet, opens `$EDITOR`), `path`, `set`, `unset`, `validate`. Coerces values per the declared schema. Secrets masked in `show` output.
- **`scitoolkit init --with-setup` flag** drops a heavily-commented `setup.py.template` alongside the toolkit scaffold and flips `setup_script: true` in the generated `toolkit.yaml`. Mirrors `--with-docker`. Without the flag, `init` produces a Tier-1-only scaffold (no `setup.py`).
- **Sample `config:` block in the default `init` template** — commented-out by default, exercises 4 of 7 types (`secret`, `path`, `integer`, `choice`) with worked examples. Author uncomments what they need.
- **`SetupContext` API** for `setup.py` authors. Methods: `info`/`warn`/`error`/`hint`/`success` (Rich-styled output), `prompt`/`prompt_path`/`prompt_int`/`prompt_float`/`prompt_secret`/`confirm`/`choice` (TTY-aware, honor `--yes`/`--no`/`--no-input`), `get_config`/`set_config`/`config` (read/write canonical YAML), `download` (resumable + SHA256 + auto-extract), and standard paths (`toolkit_path`, `data_dir`, `cache_dir`, `config_path`).
- **Resumable, SHA256-verified downloads** via `ctx.download(url, destination, sha256=..., extract=True)`. Three retries with exponential backoff. Resume via HTTP `Range` headers across calls. Auto-extract for `.tar.gz`/`.tgz`/`.tar.bz2`/`.tbz2`/`.tar`/`.zip`. **Zip-slip defense** on every archive entry — paths that escape `destination` are rejected before any bytes touch disk.
- **Auto-cache for downloaded files** at `~/.scitoolkit/cache/<urlhash>-<filename>`. Cache key is URL + SHA256 (or URL + filename if no SHA). Cache hits skip the network entirely; SHA verification still runs on hits to catch cache corruption with the same loud failure as a corrupted download.
- **Mtime-keyed validate cache** at `~/.scitoolkit/cache/_setup_validate.json`. Both successful and failed validates cache (same key shape: `(toolkit_name, config_mtime, setup_py_mtime)`). Cache hits in <1ms vs ~150-170ms for misses (subprocess spawn). Auto-invalidates when either file changes.

### Changed

- **`config:` block in `toolkit.yaml` is list-of-objects** (not dict-keyed). The aspirational pre-3C docs showed `config: { name: { type: ... } }`; shipped form is `config: [{name: ..., type: ...}]`. Order-preserving, matches install-prompt order.
- **Orchestral-ai dependency bumped to >=1.4** (introduced the `state=[...]` decorator argument the setup system relies on, plus persistent stdio MCPClient).
- **Serve refusal messaging for setup-incomplete toolkits.** Skipped toolkits now include a clear pointer to `scitoolkit config edit <toolkit>` (Tier-1 missing config) or `scitoolkit setup <toolkit>` (Tier-2 validate failure). No silent fallbacks; no half-running tools.
- **`validate_toolkit` warning text** updated to reflect shipped Tier-2 behavior (was "Phase 3C-2 won't have anything to invoke" — now "the install pipeline will skip the Tier-2 setup runner").

### Fixed

- **The setup system supersedes the `needs_setup` skip placeholder.** Toolkits with declared config or `setup.py` now go through real validation at serve startup; the old "Phase 3C not yet runnable" skip is gone.
- **Init template no longer drifts from shipped CLI semantics.** The sample `config:` block uses the shipped list-of-objects shape; the `setup.py` template uses the parameter-passed `ctx` (no `from scitoolkit.setup import SetupContext` — that import would fail in toolkit envs by design).

### Internal

- **New modules:** `scitoolkit/setup/` (storage, schema, prompts, declarative, runner, context, downloads, validate_cache, _rpc — ~2,300 LOC); `scitoolkit/_setup_host.py` (per-toolkit setup-time subprocess entrypoint, ~200 LOC).
- **Setup-host JSON-RPC channel.** Line-mode JSON over stdin/stdout between the orchestrator/CLI parent and the toolkit's setup-host subprocess. Six methods: `log`, `prompt` (with seven kinds), `set_config`, `download` (with `progress` notifications during transfer), plus the `hello`/`go`/`done` handshake. Independent of the serve-time MCP channel; survives the future Orchestral 1.4 stdio cleanup unchanged.
- **Test count: 195 → 549 unit tests (+354), 3 → 6 e2e harnesses.** New harnesses: `run_setup_e2e.py` (Tier-1 declarative loop), `run_setup_script_e2e.py` (Tier-2 setup.py loop), `run_aster_synthetic_e2e.py` (ASTER-shaped install + download flow against a localhost mock).
- **Live-registry release-ritual checklist** at `tests/e2e/manual_arxiv_postship_check.md`. Manual, ~5 minutes, exercises install/serve/call against the production registry. Catches drift between dev work and live behavior.
- **HANDOFF gotchas** added: #12 (sentinel-default resolver pattern for test isolation; CONFIG_DIR resolves at call time, not import time), #13 (3C-1-era state-config wire format is flat `{state_field: value}`, not per-tool nested), #14 (RPC framing: `hello` always first; setup.py load errors travel via `hello.params.load_error` rather than a pre-hello `done`).

---

## [0.2.0] — 2026-05-06

The first release after MVP closure. Substantial polish pass focused on agent-friendliness, selective serve, and configuration ergonomics. Driven by user feedback from Tony Menzo (co-creator) and the first wave of real-world usage of the live `arxiv-search` toolkit.

### Added

- **Selective serve.** `scitoolkit serve` no longer indiscriminately launches every installed toolkit. Use `-t/--toolkit <name>` (repeatable) to scope to specific toolkits. New flags `--enable-tool <toolkit>__<tool>` (allowlist) and `--disable-tool <toolkit>__<tool>` (blocklist) for tool-level overrides. New `--dry-run` prints the resolved serve set without starting.
- **Tool groups.** Persistent named subsets of tools that span toolkits. `scitoolkit serve --group <name>` invokes one. `scitoolkit groups list/create/edit/delete` for management. Stored in `~/.scitoolkit/serve.yaml`.
- **Persistent serve config.** `~/.scitoolkit/serve.yaml` holds default-disabled toolkits, default-disabled tools, and group definitions. `scitoolkit serve enable/disable <toolkit>` and `enable-tool/disable-tool <toolkit>__<tool>` for persistent edits. `scitoolkit serve config show/edit/path` to view or edit directly.
- **Skills surfacing.** Skill markdown files shipped by toolkits (`<toolkit>/skills/*.md`) are now automatically surfaced at install time to `~/.claude/skills/<toolkit>__<skill>/SKILL.md` so Claude Code discovers them. Symlinks on POSIX (edits propagate live), copies on Windows. `--no-skills` opt-out on `install`. Frontmatter validated with helpful warnings.
- **`expected_toolkits:` field.** Toolkit authors can declare companion toolkits that work well alongside theirs. At install time, `scitoolkit install <name>` prompts to install missing companions (TTY) or prints the explicit command (non-TTY). No runtime coupling — companion tools are surfaced independently and the agent decides when to chain them.
- **Agent-friendly CLI conventions.** Every state-modifying command now carries `--yes/-y`, `--no`, `--no-input` flags. TTY detection auto-applies non-interactive behavior in CI and pipelines. Default-Y on benign prompts; default-N on consequential prompts. Drives compatibility with coding agents (Claude Code, Codex, etc.) without breaking the human flow.
- **`scitoolkit login --token <token>`.** Non-interactive login for agents and CI scripts.
- **`scitoolkit logs` command.** Tails `~/.scitoolkit/logs/serve.log` with Rich coloring. Flags: `--lines/-n`, `--follow/--no-follow`, `--all`, `--raw`. Suppresses noisy JSON mirror lines by default. Handles file rotation and Ctrl-C cleanly.
- **`--call-timeout SECONDS`** on `serve`. Bump for long-running scientific workflows. Default 60s (was effectively 10s due to the latent bug fixed below).
- **Per-toolkit stderr log rotation.** `~/.scitoolkit/logs/<toolkit>.log` files now self-prune at 5 MB, keeping the last 2 MB.
- **Pip progress streaming.** `scitoolkit install` no longer suppresses pip output behind `--quiet`; "Collecting", "Building wheel for", and "Installing collected packages" all surface into a Rich Status spinner so users see motion.
- **`stk` alias.** Every `pip install` now ships both `scitoolkit` and `stk` as console scripts. They are identical entry points. Use whichever you prefer.
- **`scitoolkit init` template improvements.** Templates now ship a useful skill example with realistic frontmatter, structure guidance, and "what to skip" notes so authors copy-paste-modify rather than build from scratch.
- **Versioning safeguards.** `scitoolkit publish` now performs a pre-flight version check against the registry: blocks "version already exists" and rejects version decreases. `--allow-version-decrease` escape hatch for emergencies; bypasses are recorded as a structured event in the local serve log for later auditing (no data leaves the user's machine). The error message now suggests the next version explicitly and points to the `version:` line in `toolkit.yaml`.

### Changed

- **CLI emoji removal.** All emoji output stripped from CLI commands. `✓` and `✗` are the only retained glyphs (status markers). Hard rule: `scitoolkit` should read as a polished CLI, not as AI-coded prose.
- **Categories now backend-driven.** `scitoolkit validate` fetches the category whitelist from `https://api.scitoolkit.org/api/categories` with a hardcoded fallback for offline use. Adding a category is now a backend DB insert; no CLI release required.
- **Default `--call-timeout` changed from 10s (latent) to 60s.** See bug fix below.

### Fixed

- **Latent MCPClient timeout bug.** `MCP_CONNECT_TIMEOUT_S=10.0` was being applied to per-call timeouts as well as connect timeouts (Orchestral 1.3.x conflates them). Effective per-call timeout was 10s — worse than Orchestral's documented 30s default. Now explicit, configurable, and defaults to 60s.
- **`scitoolkit serve` TTY message.** Reworded to avoid implying that running `scitoolkit serve` in one terminal serves Claude Code in another. Claude Code spawns its own subprocess; the TTY-bound `serve` is for diagnostics. Pointer to `scitoolkit logs` added.
- **e2e test harness leak.** Mocked-registry install tests were writing synthetic toolkit skills into the developer's real `~/.claude/skills/`. Fixed via tmp-path injection and a sentinel-default pattern in install/uninstall.
- **Validation error specificity.** Skill frontmatter warnings now name the file and link to the authoring docs.

### Internal

- New module: `scitoolkit/serve/config.py` — `serve.yaml` schema + heavily-tested resolver (`resolve_serve_set`).
- New module: `scitoolkit/skills.py` — frontmatter parser, install/uninstall helpers, ownership-marker mechanism (`.scitoolkit-managed`).
- New module: `scitoolkit/versioning.py` — `parse_version`, `is_strictly_greater`, `suggest_next_version`, `max_version` helpers.
- Test count: 103 fast unit tests plus 8 install-pipeline integration tests, all passing. Two e2e harnesses (mocked-registry install + full serve loop) also pass.
- `stk-package/CLAUDE.md` rewritten as a tight pointer file (replaces 440 lines of stale planning content).

---

## [0.1.0] — 2026-05-05

Initial public release. The MVP — full create → publish → install → serve → use loop closed against live services.

### Added

- `scitoolkit init` — scaffold a toolkit from template.
- `scitoolkit validate` — Pydantic-based pre-publish structural checks.
- `scitoolkit login <toolkit>` — store per-toolkit publish token.
- `scitoolkit publish [--dry-run]` — package and upload to the registry.
- `scitoolkit install <name> [--version V]` — download, extract, set up isolated environment (venv or conda, auto-detected).
- `scitoolkit list` — show installed toolkits.
- `scitoolkit uninstall <name>` — remove cleanly.
- `scitoolkit serve` — multi-toolkit MCP aggregator (stdio mode).
- Multi-tier execution: venv for same-Python pure-Python toolkits, conda for different-Python-version toolkits, Docker detection (refuses with clear "Phase 3B" message).
- HTTP-loopback architecture between orchestrator and per-toolkit subprocesses.

The first toolkit on the registry — `arxiv-search` — was published, installed, served, and used by Claude Code returning real arXiv data.

[0.2.0]: https://github.com/scitoolkit/scitoolkit/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/scitoolkit/scitoolkit/releases/tag/v0.1.0
