# Changelog

All notable changes to `scitoolkit` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

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
