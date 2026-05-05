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
stk init my-toolkit         # scaffold from template
cd my-toolkit
# write your tools in tools/ ; write skills in skills/
stk validate                # check structure
stk login my-toolkit        # one-time, stores publish token
stk publish                 # ship it
```

For the agent-assisted authoring flow (recommended for first toolkits),
see <https://scitoolkit.org/docs/scaffold-with-an-agent>.

For the full author guide — toolkit layout, tool conventions, skills,
groups, expected_toolkits — see <https://scitoolkit.org/docs/authoring>.

---

## What's in scitoolkit 0.2.0

**Commands:**

- `init`, `validate`, `login`, `publish` — author and ship toolkits.
- `search`, `install`, `uninstall`, `list` — manage installed toolkits.
- `serve` — run installed toolkits as an MCP stdio server. Supports
  positional toolkit names, `--group`, `--enable-tool`,
  `--disable-tool`, `--dry-run`, `--call-timeout`.
- `logs` — tail the serve log with Rich coloring.
- `groups` — manage named tool subsets that span toolkits.

**Features:**

- Multi-tier execution: same-Python toolkits run in venv, different-Python
  toolkits run under conda (auto-detected). Docker mode coming in 3B.
- Per-tool selection: enable or disable individual tools per serve session
  or persistently in `~/.scitoolkit/serve.yaml`.
- Skills surfacing: a toolkit's `skills/*.md` files are auto-mirrored to
  `~/.claude/skills/` so Claude Code discovers them. Symlinked on POSIX
  for live edits, copied on Windows.
- Agent-friendly flags: every state-modifying command supports
  `--yes`, `--no`, `--no-input`. Non-TTY stdin auto-applies non-interactive
  behavior.
- Versioning safeguards: `publish` blocks "version already exists" and
  "version decrease" with helpful suggestions before upload.
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
