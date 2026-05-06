# Changelog

All notable changes to `scitoolkit` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

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
