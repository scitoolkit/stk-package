# scitoolkit

A package manager and runtime for scientific AI tools. Lets researchers
publish toolkits to the [SciToolkit registry](https://scitoolkit.org)
and use them in coding agents (Claude Code, Codex) or in scripts via
the [Model Context Protocol](https://modelcontextprotocol.io).

Each toolkit installs into its own isolated Python environment, so
dependency conflicts between toolkits are never a problem.

---

## Quickstart

```bash
pip install scitoolkit

# Install a toolkit from the registry
stk install arxiv-search

# See what you have
stk list

# Serve installed toolkits over MCP stdio
stk serve
```

`stk` is a shorter alias for `scitoolkit`; both ship with the package
and behave identically.

To use the served toolkits in [Claude Code](https://claude.ai/code), add
this to its MCP config:

```json
{
  "mcpServers": {
    "scitoolkit": {
      "command": "scitoolkit",
      "args": ["serve"]
    }
  }
}
```

Claude Code will spawn its own `scitoolkit serve` subprocess and
discover all installed toolkits' tools. To watch tool calls fire in
real time, run `stk logs` in another terminal.

---

## Authoring a toolkit

```bash
stk init my-toolkit              # scaffold from template
# stk init my-toolkit --with-setup   # if your toolkit needs a setup.py
cd my-toolkit
# write your tools in tools/ ; write skills in skills/
stk validate                     # check structure
stk login                        # one-time, browser-flow auth (per-user)
stk publish                      # ship it
```

`stk login` (no toolkit name) does a browser-flow that gives you a
per-user token good for any toolkit you own or collaborate on. Legacy
per-toolkit tokens are still accepted (`stk login my-toolkit`) but
deprecated.

For the agent-assisted authoring flow (recommended for first toolkits),
see <https://scitoolkit.org/docs/scaffold-with-an-agent>.

For the full author guide — toolkit layout, tool conventions, skills,
groups, expected_toolkits, configuration — see
<https://scitoolkit.org/docs/authoring> and
<https://scitoolkit.org/docs/configuration>.

---

## What's in scitoolkit 0.3.0

**Commands:**

- `init`, `validate`, `login`, `whoami`, `logout`, `publish` — author
  and ship toolkits.
- `search`, `install`, `uninstall`, `list` — manage installed toolkits.
- `serve` — run installed toolkits as an MCP stdio server. Supports
  positional toolkit names, `--group`, `--enable-tool`,
  `--disable-tool`, `--dry-run`, `--call-timeout`.
- `setup <toolkit>` — run a toolkit's `setup.py` (`--reset`, `--check`).
- `config <show|edit|path|set|unset|validate>` — manage per-toolkit
  config files at `~/.scitoolkit/config/<toolkit>.yaml`.
- `logs` — tail the serve log with Rich coloring.
- `groups` — manage named tool subsets that span toolkits.

**Features:**

- **Configuration system.** Toolkits declare a `config:` block in
  `toolkit.yaml` (seven types: `string`, `secret`, `path`, `integer`,
  `float`, `boolean`, `choice`); users fill it at install time or by
  editing `~/.scitoolkit/config/<toolkit>.yaml`. Toolkits with more
  involved setup ship a `setup.py` with full prompts, downloads
  (resumable, SHA256-verified, auto-extracting tar/zip with zip-slip
  defense), and derived-state writes via `ctx.set_config(...)`.
- **Per-user auth.** `scitoolkit login` does a browser-flow that
  stores a per-user token good for any toolkit you own or
  collaborate on. Legacy per-toolkit tokens still work but are
  deprecated.
- **Multi-tier execution:** same-Python toolkits run in venv,
  different-Python toolkits run under conda (auto-detected). Docker
  mode coming in 3B.
- **Per-tool selection:** enable or disable individual tools per
  serve session or persistently in `~/.scitoolkit/serve.yaml`.
- **Skills surfacing:** a toolkit's `skills/*.md` files are
  auto-mirrored to `~/.claude/skills/` so Claude Code discovers
  them. Symlinked on POSIX for live edits, copied on Windows.
- **Agent-friendly flags:** every state-modifying command supports
  `--yes`, `--no`, `--no-input`. Non-TTY stdin auto-applies
  non-interactive behavior.
- **Versioning safeguards:** `publish` blocks "version already
  exists" and "version decrease" with helpful suggestions before
  upload.
- **Crash resilience:** per-toolkit subprocess auto-restart with
  exponential backoff. A crashed toolkit doesn't take the
  orchestrator down.
- Python 3.12+ required.

See [CHANGELOG.md](CHANGELOG.md) for the full release history.

---

## Architecture

The package has three pieces:

- **CLI** (this package) — installed locally, manages toolkit
  environments and serves tools.
- **Backend** ([api.scitoolkit.org](https://api.scitoolkit.org)) —
  registry, auth, tarball storage.
- **Website** ([scitoolkit.org](https://scitoolkit.org)) — discover and
  manage published toolkits.

Each installed toolkit runs in its own subprocess in its own Python
environment. The `scitoolkit serve` orchestrator aggregates them and
exposes the union as a single MCP server upstream. Failures in one
toolkit don't affect others.

---

## Contributing

Issues and PRs are welcome at
<https://github.com/adroman/scitoolkit>.

---

## License

MIT. See [LICENSE](LICENSE).

## Links

- Website: <https://scitoolkit.org>
- Backend API: <https://api.scitoolkit.org>
- GitHub: <https://github.com/adroman/scitoolkit>
- Issues: <https://github.com/adroman/scitoolkit/issues>
