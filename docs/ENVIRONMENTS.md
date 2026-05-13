# Environments and Scoping in SciToolkit

**Status:** Shipped in 0.5.0.
**Audience:** Toolkit authors and toolkit users coming from 0.4.x, plus anyone using SciToolkit across more than one project.
**Last revised:** 2026-05-13.

---

## TL;DR

0.5.0 splits "where toolkits live on disk" from "what this project uses":

- A **cache** at `~/.scitoolkit/cache/<name>/<version>/` holds installed toolkit binaries. Multi-version side-by-side. Regenerable, like `~/.cache/pip/`.
- A **project manifest** at `<project>/.scitoolkit/manifest.yaml` pins which version of each toolkit *this* project uses. Small, checked into git.
- **Per-toolkit config** lives in two layers: user-level at `~/.scitoolkit/config/<toolkit>.yaml` (defaults for all your work) and project-level at `<project>/.scitoolkit/config/<toolkit>.yaml` (overrides for one project). Project wins key-by-key, like `.env`.

The same toolkit can be installed at multiple versions for different projects without conflict. Sharing a project just means committing `.scitoolkit/`; collaborators run `stk install` and the cache rebuilds.

If you're upgrading from 0.4.x: see **[Migrating from 0.4.x](#migrating-from-04x)** at the bottom. You'll run `stk reset` once, then `stk install` for the toolkits you actually need.

---

## The mental model

Three pieces, three responsibilities:

| Piece | Path | Owner | Source of truth for |
|---|---|---|---|
| **Cache** | `~/.scitoolkit/cache/<name>/<version>/` | SciToolkit | The bytes that get executed. Regenerable. |
| **Project manifest** | `<project>/.scitoolkit/manifest.yaml` | You (checked into git) | Which version of each toolkit *this project* uses. |
| **Config (two layers)** | `~/.scitoolkit/config/<toolkit>.yaml` + `<project>/.scitoolkit/config/<toolkit>.yaml` | You | Per-toolkit settings: secrets, paths, options. |

The mental shortcut:

- **The cache is like `~/.cache/pip/`.** It's a download cache. If you delete it, things still work — they just have to be rebuilt.
- **The manifest is like `requirements.txt`.** It's small, checked into git, describes what this project needs. If you delete it, the project loses its identity.
- **The two config layers are like `.env`.** User-level is your global defaults; project-level overrides on a per-key basis when you're in that project.

---

## File layout

```
~/.scitoolkit/                       # user-scope root
├── cache/                           # installed toolkit binaries (regenerable)
│   └── <name>/
│       └── <version>/               # multi-version slots side-by-side
│           ├── venv/                # or conda env, or docker ref
│           ├── tools/               # toolkit content
│           ├── toolkit.yaml
│           ├── requirements.txt
│           ├── .install_meta.yaml   # schema_version, install_method, installed_at
│           ├── .last_used           # ISO-8601 timestamp; touched by serve
│           └── .disk_size           # cached byte count (for stk list)
├── config/                          # user-level toolkit config (defaults)
│   └── <toolkit>.yaml               # schema_version: 1
├── default-project/                 # implicit project when cwd has no .scitoolkit/
│   ├── manifest.yaml
│   └── config/
│       └── <toolkit>.yaml
├── logs/
│   └── serve.log
├── serve.yaml                       # user-level serve config (groups, etc.)
└── config.json                      # login state (never touched by reset)

<project>/.scitoolkit/               # project-scope root (commit to git)
├── manifest.yaml                    # pinned toolkit list
└── config/                          # project-level config overrides
    └── <toolkit>.yaml
```

You'll rarely need to look inside `cache/`. The two paths you'll edit by hand are the user-level `config/<toolkit>.yaml` and your project's `manifest.yaml` / `config/<toolkit>.yaml`.

### Project discovery

When you run a command from inside a project (or any subdir of one), SciToolkit walks upward looking for `.scitoolkit/manifest.yaml` and uses the first hit as the project root. If no manifest is found, it falls back to `~/.scitoolkit/default-project/` — that way `stk install` outside a project still works (it pins to the implicit global project).

Override with `--project-dir <path>` on any command if you need to (CI, debugging, scripting).

---

## The five common workflows

### 1. Install in a new project

```
$ cd ~/research/exoplanet-paper
$ stk project init
✓ Initialized scitoolkit project at /home/alex/research/exoplanet-paper
  Manifest: /home/alex/research/exoplanet-paper/.scitoolkit/manifest.yaml

$ stk install arxiv-search
[fetches metadata, downloads, builds venv, writes cache slot, pins manifest]

$ cat .scitoolkit/manifest.yaml
schema_version: 1
toolkits:
  - name: arxiv-search
    version: 0.2.0
    pinned_at: '2026-05-13T10:24:31'
```

`stk install` (no version) pins the latest. Commit `.scitoolkit/` to git so your collaborators get the same versions.

### 2. Install in an existing project (cloned from a teammate)

```
$ git clone https://github.com/alex/exoplanet-paper.git
$ cd exoplanet-paper
$ cat .scitoolkit/manifest.yaml
schema_version: 1
toolkits:
  - name: arxiv-search
    version: 0.2.0

$ stk install arxiv-search
[reads the manifest pin, installs exactly 0.2.0 into the cache]
```

You can also install everything pinned in the manifest at once (planned in 0.5.x; for now install one at a time).

### 3. Switch a project from one version to another

```
$ stk install arxiv-search@0.3.0
[adds a second cache slot at ~/.scitoolkit/cache/arxiv-search/0.3.0/]
[updates manifest pin to 0.3.0]

$ stk list
arxiv-search
  - 0.3.0 *   (used 2 seconds ago, 182 MB)
  - 0.2.0     (used 3 days ago, 180 MB)

* = pinned in this project
```

Both versions stay in the cache. Switch back any time with `stk install arxiv-search@0.2.0` — no re-download, no re-build; the slot is already there. The pin in your manifest is the only thing that moves.

### 4. Share a project (manifest in git, cache rebuilds)

```
$ git add .scitoolkit/manifest.yaml
$ git commit -m "Pin arxiv-search 0.3.0"
$ git push

# On your collaborator's machine:
$ git pull
$ stk install arxiv-search   # picks up the manifest pin
[fresh cache build for arxiv-search@0.3.0]
```

What's in git: the manifest, and optionally `project/.scitoolkit/config/<toolkit>.yaml` if it has shareable settings.

What's NOT in git: the cache (it's a build artifact), `config/<toolkit>.yaml` files that contain secrets (gitignore them, or only store secrets at the user layer).

### 5. One machine, many projects

This is the killer feature. Each project pins what it needs; the cache holds every version anyone has installed.

```
~/research/paper-a/.scitoolkit/manifest.yaml   # pins arxiv-search 0.2.0
~/research/paper-b/.scitoolkit/manifest.yaml   # pins arxiv-search 0.3.0
~/.scitoolkit/cache/arxiv-search/0.2.0/        # one shared slot
~/.scitoolkit/cache/arxiv-search/0.3.0/        # one shared slot
```

`cd paper-a && stk serve` runs 0.2.0. `cd paper-b && stk serve` runs 0.3.0. No environment switching, no reinstalls.

---

## The two-layer config story

Per-toolkit configuration lives in two layers:

- **User layer:** `~/.scitoolkit/config/<toolkit>.yaml` — your global defaults.
- **Project layer:** `<project>/.scitoolkit/config/<toolkit>.yaml` — overrides for this project only.

Resolution: the project layer wins key-by-key. Keys absent from the project layer fall through to the user layer.

### Worked example: ASTER

ASTER (the exoplanet toolkit) has two config fields:

- `opacity_path` — where the ~2.3 GB opacity files live on this machine.
- `api_key` — for the NASA Exoplanet Archive.

`opacity_path` is a machine-wide thing — every project on this laptop uses the same opacity files. Set it once at the user layer:

```
$ stk config set aster opacity_path /scratch/alex/aster/opacities --user
```

`api_key` is *probably* shared too. But you might have a separate key for your high-volume scratch project (rate-limited differently, billed differently). Override it per-project:

```
$ cd ~/research/high-volume-paper
$ stk config set aster api_key sct_npe_HIGHVOLUME_KEY
   (no --user flag → writes to project layer in this dir)
```

Now `stk serve` from `high-volume-paper/` uses the high-volume key. From any other dir, it falls back to the user-layer key (if set).

### `stk config show` reads the merged view

```
$ stk config show aster
opacity_path: /scratch/alex/aster/opacities  # from user
api_key:      <set>                          # from project
```

`--layer user` or `--layer project` shows just that one layer. `--layer` is the way to see the actual stored file, not the merged view.

### Where the project layer file lives

```
<project>/.scitoolkit/config/<toolkit>.yaml
```

It's a regular YAML file. You can edit it by hand:

```yaml
# .scitoolkit/config/aster.yaml
schema_version: 1
api_key: sct_npe_HIGHVOLUME_KEY
```

Note the sparse shape — only the keys that override appear. Keys absent here fall through to the user layer.

### Don't commit secrets

If your project layer holds secrets (`api_key`, etc.), gitignore the file:

```
# .gitignore
.scitoolkit/config/aster.yaml
```

The manifest itself (`.scitoolkit/manifest.yaml`) is safe to commit — it only has names and versions.

---

## `stk list` reading guide

```
$ stk list
arxiv-search
  - 0.2.0 *   (used 2 hours ago, 180 MB)
heptapod
  - 0.3.0 *   (used yesterday, 8.4 GB)
  - 0.1.0     (used 3 days ago, 8.2 GB)

* = pinned in this project (./.scitoolkit/manifest.yaml)
```

Reading row by row:

- **Toolkit name** at the top of each group.
- **Versions** indented underneath, one per line.
- **`*`** after a version means it's the pinned version in *this project*. The legend at the bottom tells you which manifest the `*` refers to.
- **`(used <delta>, <size>)`** — last time `stk serve` activated this version, and the cached disk size. `used never` means it's installed but hasn't been served.

`stk list --json` is the structured form:

```json
[
  {"name": "arxiv-search", "version": "0.2.0",
   "last_used_iso": "2026-05-13T08:41:23", "size_bytes": 188743680,
   "pinned_in_project": true},
  {"name": "heptapod", "version": "0.3.0", ...},
  {"name": "heptapod", "version": "0.1.0", ...}
]
```

Use the JSON form when scripting (`stk list --json | jq '.[] | select(.pinned_in_project)'`).

If you have nothing installed:

```
$ stk list
No toolkits installed. Try stk install arxiv-search
```

---

## Cache GC

Not in 0.5.0. The cache grows monotonically (each `stk install` adds; `stk uninstall` is the only thing that prunes). The thinking: visibility first via `stk list` + `.disk_size`, eviction policies later when bloat actually bites.

If your cache is too big right now, find the offenders with `stk list`, then `stk uninstall <name>` or `stk uninstall <name>@<version>` to prune. `stk reset --all` is the scorched-earth option (see below).

---

## Migrating from 0.4.x

0.4.x installed toolkits under `~/.scitoolkit/toolkits/<name>/` — flat, one slot per toolkit, no multi-version. 0.5.0 moved to `~/.scitoolkit/cache/<name>/<version>/`. **There is no auto-migration.** Existing 0.4.x installs surface a one-line heads-up and `stk` keeps working (the cache just looks empty until you reinstall).

### The cutover, in three commands

```
# 1. Detect that you have legacy installs.
$ stk list
[heads-up to stderr:]
Heads up: 0.5.0 changed the install layout. Toolkits installed under
~/.scitoolkit/toolkits/ are no longer used. Run `stk reset` to remove
them and reinstall the ones you need.

# 2. Clear the legacy directory (default mode — preserves cache/, config/,
#    default-project/, serve.yaml, logs/, config.json).
$ stk reset --dry-run
Dry-run: the following would be removed
  toolkits/ (legacy 0.4.x layout)
    /home/alex/.scitoolkit/toolkits

$ stk reset
This will remove the legacy 0.4.x layout:
  toolkits/ (legacy 0.4.x layout)
    /home/alex/.scitoolkit/toolkits
Proceed? [y/N] y
✓ Removed /home/alex/.scitoolkit/toolkits
✓ Legacy layout removed.
Reinstall toolkits with stk install <name> to populate the new cache layout.

# 3. Reinstall the toolkits you actually need.
$ stk install arxiv-search
$ stk install aster
```

That's it. Your `~/.scitoolkit/config/<toolkit>.yaml` files survive (the format is forward-compatible — they just get a `schema_version: 1` line added on the next write). Your login state at `~/.scitoolkit/config.json` survives. Your `serve.yaml` (groups, selective serve) survives.

### `stk reset` has three modes

| Mode | Removes | Preserves |
|---|---|---|
| `stk reset` (default) | `toolkits/` (legacy 0.4.x) | Everything else |
| `stk reset --all` | `cache/`, `toolkits/`, `downloads/`, `default-project/` | `config.json`, `logs/`, `config/` |
| `stk reset --all --include-config` | All of the above + `config/` | `config.json`, `logs/` |

`--dry-run` works with every mode. `--yes` / `-y` skips confirmations (for CI).

`config.json` (your login state) and `logs/` are **always** preserved. There's no flag to delete them; that's deliberate.

### What about per-project state from 0.4.x?

There was none. 0.4.x had no project concept — everything was user-global. After the cutover, set up your projects fresh:

```
$ cd ~/research/my-paper
$ stk project init
$ stk install <name>          # this writes a pin into the new manifest
```

If you have a `~/.scitoolkit/config/<toolkit>.yaml` file with values you want, those carry over untouched — they're still your user-layer defaults. Override per-project only if you actually need to.

---

## Pointers

- **Frontend documentation site:** [scitoolkit.org/docs/environments](https://scitoolkit.org/docs/environments) (will mirror this file with extra walkthroughs).
- **Design doc with rationale:** `docs/ENVIRONMENTS_DESIGN.md` (in the main scitoolkit project repo).
- **Setup system (Tier-1 declarative + Tier-2 `setup.py`):** `docs/SETUP_SYSTEM_SPEC.md`.
- **Serve architecture (per-toolkit subprocess + MCP):** `docs/SERVE_ARCHITECTURE.md`.
- **CLI reference:** `stk --help` and `stk <command> --help` for everything.

If something here is unclear or wrong, open an issue at [github.com/scitoolkit/scitoolkit](https://github.com/scitoolkit/scitoolkit).
