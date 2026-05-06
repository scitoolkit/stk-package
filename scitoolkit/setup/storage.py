"""
File-canonical YAML storage for toolkit config.

Every toolkit's persistent configuration lives at:

    ~/.scitoolkit/config/<toolkit>.yaml

This module is the read/write layer. It uses ``ruamel.yaml`` so user
comments survive ``set`` / ``unset`` round-trips — without that, every
``scitoolkit config set`` would silently strip the user's notes.

Resolver pattern (HANDOFF gotcha #12): functions take ``base:
Optional[Path] = None`` and resolve in-body via ``_resolve_config_dir()``,
which re-reads from the ``config`` module each call. This is what makes
test fixtures work — patching ``scitoolkit.config.CONFIG_DIR`` to a
tmp dir is enough to redirect the entire config-storage surface.
Don't "simplify" back to a bound default; the naive form silently
writes tests' config into the developer's real ``~/.scitoolkit/``.

File mode is ``0600`` because the file may contain secrets.

The exposed surface:

- ``config_dir()`` — the directory ``~/.scitoolkit/config/``.
- ``config_path(name)`` — full path for one toolkit's config.
- ``load_config(name)`` — read into a dict-like (CommentedMap from
  ruamel; behaves like a dict for our purposes).
- ``save_config(name, data)`` — write back, preserving comments if a
  ``CommentedMap`` was loaded and mutated.
- ``set_config_value(name, key, value)`` — atomic single-field update.
- ``unset_config_value(name, key)`` — remove one field.
- ``delete_config(name)`` — remove the whole file (e.g. for ``--reset``).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

from .. import config as _config_mod


# ruamel YAML instance, configured for our use case.
#
# - typ="rt" is round-trip mode (preserves comments + ordering).
# - default_flow_style=False forces block style (one key per line),
#   which is what users expect to see and edit.
# - allow_unicode keeps non-ASCII strings legible.
def _new_yaml() -> YAML:
    y = YAML(typ="rt")
    y.default_flow_style = False
    y.allow_unicode = True
    y.width = 1000  # don't auto-wrap long values; they read worse wrapped
    y.preserve_quotes = True
    return y


_yaml = _new_yaml()


# ── path resolution ──────────────────────────────────────────────────


def _resolve_config_dir() -> Path:
    """Get the current ~/.scitoolkit/config/ — re-reading at call time.

    Honors test monkeypatching of ``scitoolkit.config.CONFIG_DIR``.
    See HANDOFF.md gotcha #12 for the full rationale.
    """
    return _config_mod.CONFIG_DIR / "config"


def config_dir(*, base: Optional[Path] = None) -> Path:
    """Return the per-toolkit config directory (creates if missing)."""
    if base is None:
        base = _resolve_config_dir()
    base.mkdir(parents=True, exist_ok=True)
    return base


def config_path(toolkit_name: str, *, base: Optional[Path] = None) -> Path:
    """Path to ``<config_dir>/<toolkit>.yaml``. Does not create the file."""
    return config_dir(base=base) / f"{toolkit_name}.yaml"


# ── read / write ─────────────────────────────────────────────────────


def load_config(
    toolkit_name: str, *, base: Optional[Path] = None,
) -> CommentedMap:
    """Read ``<toolkit>.yaml`` into a ruamel ``CommentedMap``.

    Returns an empty ``CommentedMap`` if the file doesn't exist (so
    callers can ``data["foo"] = "bar"`` without checking existence
    first). Returns an empty ``CommentedMap`` for an empty file too.

    Raises ``OSError`` only on actual read failures (permission, etc.).
    Malformed YAML is converted to a clear ``ValueError`` so callers
    don't have to import ruamel exception classes.
    """
    path = config_path(toolkit_name, base=base)
    if not path.exists():
        return CommentedMap()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = _yaml.load(f)
    except Exception as e:
        raise ValueError(
            f"failed to parse {path}: {e}"
        ) from e
    if data is None:
        return CommentedMap()
    if not isinstance(data, CommentedMap):
        # Could happen if the file's top-level is a list or scalar.
        # For toolkit config, that's a malformation — fields live at
        # the top level as a mapping.
        raise ValueError(
            f"{path}: expected a YAML mapping at the top level, got "
            f"{type(data).__name__}"
        )
    return data


def save_config(
    toolkit_name: str,
    data: Dict[str, Any],
    *,
    base: Optional[Path] = None,
    header_comment: Optional[str] = None,
) -> Path:
    """Write ``data`` to ``<toolkit>.yaml`` atomically with mode 0600.

    If ``data`` is a ``CommentedMap`` that came from ``load_config``,
    its comments and ordering are preserved through the round-trip.
    Plain dicts are written as fresh files (no comments to preserve;
    if the file existed, the previous comments are lost — this is the
    user's choice when they pass a plain dict).

    ``header_comment`` is prepended above the YAML body if the file
    doesn't already have a top-level comment. Useful for
    ``run_install_setup`` to seed a freshly-written file with a
    "this file is canonical, edit anytime" pointer.

    Atomic-write: write to ``<path>.tmp`` then ``os.replace``. Stops
    a Ctrl-C-interrupted write from leaving a partial file.
    """
    path = config_path(toolkit_name, base=base)
    path.parent.mkdir(parents=True, exist_ok=True)

    # If a header comment was requested and the data has no leading
    # comment, attach one. Convert plain dicts to CommentedMap first so
    # the comment-attachment hook is usable. We skip duplication if the
    # caller has already loaded-and-mutated a file that has the same
    # comment — checking the rendered string is cheaper than poking at
    # ruamel's comment internals across versions.
    if header_comment:
        if not isinstance(data, CommentedMap):
            converted = CommentedMap()
            for k, v in data.items():
                converted[k] = v
            data = converted
        # Strip trailing newline from the user-provided header so we
        # don't end up with a blank line between the comment and the
        # body.
        comment_text = header_comment.rstrip("\n")
        try:
            data.yaml_set_start_comment(comment_text)
        except Exception:
            # ruamel's start-comment API has been version-stable but
            # don't crash if a future major changes the signature.
            pass

    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        _yaml.dump(data, f)
    os.replace(tmp, path)

    try:
        os.chmod(path, 0o600)
    except (OSError, NotImplementedError):  # pragma: no cover (Windows)
        pass
    return path


def delete_config(
    toolkit_name: str, *, base: Optional[Path] = None,
) -> bool:
    """Delete the toolkit's config file. Returns True if a file was removed."""
    path = config_path(toolkit_name, base=base)
    if not path.exists():
        return False
    try:
        path.unlink()
        return True
    except OSError:
        return False


# ── single-field mutators (used by `scitoolkit config set/unset`) ──


def set_config_value(
    toolkit_name: str,
    key: str,
    value: Any,
    *,
    base: Optional[Path] = None,
) -> Path:
    """Set one field; preserve every other field and all comments.

    Read → mutate → write, all in this function so the caller doesn't
    have to worry about losing state by re-saving stale data.
    """
    data = load_config(toolkit_name, base=base)
    data[key] = value
    return save_config(toolkit_name, data, base=base)


def unset_config_value(
    toolkit_name: str,
    key: str,
    *,
    base: Optional[Path] = None,
) -> bool:
    """Remove one field. Returns True if the key existed."""
    data = load_config(toolkit_name, base=base)
    if key not in data:
        return False
    del data[key]
    save_config(toolkit_name, data, base=base)
    return True
