"""``scitoolkit ingest`` — generate toolkit.yaml from an existing codebase.

Walks a directory tree, AST-parses every .py file, detects tools via
``@define_tool`` decorators and ``BaseTool`` subclasses, and emits a
``toolkit.yaml`` skeleton with explicit ``module:``/``name:`` import
paths. The author keeps their code where it is. The yaml is the manifest.

Pure static analysis — never imports the modules it scans.

Tier 1 only for v1: decorated functions and BaseTool subclasses. Tier 2
(heuristic detection of undecorated functions) and Tier 3 (cross-format
conversion) deferred. See docs/INGEST_DESIGN.md.
"""
from __future__ import annotations

import ast
import fnmatch
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, List, Literal, Optional, Sequence, Set, Tuple

# Names that, when imported from these modules, should be treated as the
# define_tool decorator. Any local alias bound from these resolves to it.
_DEFINE_TOOL_SOURCES: Tuple[Tuple[str, str], ...] = (
    ("orchestral", "define_tool"),
    ("orchestral.tools", "define_tool"),
    ("orchestral.tools.decorator.define_tool", "define_tool"),
)

# Same shape for BaseTool — modules and the attribute name.
_BASETOOL_SOURCES: Tuple[Tuple[str, str], ...] = (
    ("orchestral.tools", "BaseTool"),
    ("orchestral.tools.base.tool", "BaseTool"),
)

# Top-level module attribute access patterns we recognize for the
# decorator (``@orchestral.define_tool``, ``@orchestral.tools.define_tool``).
_DEFINE_TOOL_ATTR_CHAINS: Tuple[Tuple[str, ...], ...] = (
    ("orchestral", "define_tool"),
    ("orchestral", "tools", "define_tool"),
)
_BASETOOL_ATTR_CHAINS: Tuple[Tuple[str, ...], ...] = (
    ("orchestral", "tools", "BaseTool"),
)

# Directories we never descend into when walking a repo for ingest.
_DEFAULT_SKIP_DIRS: frozenset[str] = frozenset({
    "__pycache__",
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "dist",
    "build",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
})

# Test-file patterns. Files matching these are not considered tool sources.
_TEST_FILE_PATTERNS: Tuple[str, ...] = ("test_*.py", "*_test.py")


# ─────────────────────────────────────────────────────────────────────
# ToolDescriptor
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ToolDescriptor:
    """A single tool discovered in a source file."""

    module: str
    name: str
    description: str
    source_path: Path
    source_line: int
    kind: Literal["function", "class"]


@dataclass(frozen=True)
class DroppedFile:
    """A .py file that contained tool-shaped definitions but was skipped.

    Emitted when ``_module_path_for_file`` returns ``None`` for a file
    that an AST pre-scan flagged as containing ``@define_tool`` or a
    ``BaseTool`` subclass. The CLI surfaces these as a warning so the
    author can fix the underlying problem (usually a missing
    ``__init__.py``) rather than silently shipping an incomplete
    toolkit.yaml.
    """

    source_path: Path
    reason: str


# ─────────────────────────────────────────────────────────────────────
# Walker
# ─────────────────────────────────────────────────────────────────────


def _is_test_file(path: Path) -> bool:
    return any(fnmatch.fnmatch(path.name, p) for p in _TEST_FILE_PATTERNS)


def _is_inside_tests_dir(path: Path, root: Path) -> bool:
    """True iff any path component between root and path is a tests dir."""
    try:
        rel = path.relative_to(root)
    except ValueError:
        return False
    for part in rel.parts[:-1]:
        if part in {"tests", "test"}:
            return True
    return False


@dataclass
class _GitignoreSpec:
    """Minimal .gitignore matcher.

    Supports the common subset:
    - Blank lines and ``# comment`` lines are skipped.
    - ``pattern`` matches files or directories anywhere in the tree.
    - ``pattern/`` matches directories only.
    - ``/pattern`` is anchored to the gitignore's directory.
    - ``!pattern`` negates an earlier match.
    - ``*`` and ``?`` glob wildcards via fnmatch.

    Does NOT support: ``**`` segment matching beyond what fnmatch already
    handles, character classes beyond fnmatch, advanced anchoring rules.
    Sufficient for ingest's "skip the obvious build artifacts and
    generated files" use case.
    """

    base: Path
    patterns: List[Tuple[str, bool, bool]] = field(default_factory=list)
    # Each entry: (pattern, is_directory_only, is_negation)

    @classmethod
    def from_file(cls, gitignore_path: Path) -> "_GitignoreSpec":
        spec = cls(base=gitignore_path.parent)
        try:
            content = gitignore_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return spec
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            negate = line.startswith("!")
            if negate:
                line = line[1:]
            dir_only = line.endswith("/")
            if dir_only:
                line = line[:-1]
            spec.patterns.append((line, dir_only, negate))
        return spec

    def matches(self, path: Path, is_dir: bool) -> bool:
        try:
            rel = path.relative_to(self.base)
        except ValueError:
            return False
        rel_str = str(rel).replace("\\", "/")
        name = path.name
        matched = False
        for pattern, dir_only, negate in self.patterns:
            if dir_only and not is_dir:
                continue
            anchored = pattern.startswith("/")
            pat = pattern.lstrip("/")
            if anchored:
                hit = fnmatch.fnmatch(rel_str, pat) or fnmatch.fnmatch(
                    rel_str, pat + "/*"
                )
            else:
                hit = (
                    fnmatch.fnmatch(name, pat)
                    or fnmatch.fnmatch(rel_str, pat)
                    or fnmatch.fnmatch(rel_str, "*/" + pat)
                    or fnmatch.fnmatch(rel_str, "*/" + pat + "/*")
                    or fnmatch.fnmatch(rel_str, pat + "/*")
                )
            if hit:
                matched = not negate
        return matched


def walk_python_files(root: Path) -> Iterator[Path]:
    """Yield every .py file under ``root`` worth scanning for tools.

    Filters:
    - Hardcoded skip dirs (`__pycache__`, `.git`, `.venv`, etc.).
    - ``.gitignore`` rules (root-level only; nested ``.gitignore`` files
      are not honored — sufficient for v1).
    - ``tests/`` and ``test/`` directories at any depth.
    - ``test_*.py`` and ``*_test.py`` files.
    - Anything starting with ``.`` (hidden files/dirs).
    """
    root = root.resolve()
    gitignore = _GitignoreSpec.from_file(root / ".gitignore")

    def _walk(current: Path) -> Iterator[Path]:
        try:
            entries = sorted(current.iterdir())
        except OSError:
            return
        for entry in entries:
            if entry.name.startswith("."):
                # Skip hidden files/dirs except .gitignore (already read).
                continue
            if entry.is_dir():
                if entry.name in _DEFAULT_SKIP_DIRS:
                    continue
                if entry.name in {"tests", "test"}:
                    continue
                if entry.name.endswith(".egg-info"):
                    continue
                if gitignore.matches(entry, is_dir=True):
                    continue
                yield from _walk(entry)
            elif entry.is_file():
                if entry.suffix != ".py":
                    continue
                if _is_test_file(entry):
                    continue
                if gitignore.matches(entry, is_dir=False):
                    continue
                yield entry

    yield from _walk(root)


# ─────────────────────────────────────────────────────────────────────
# Module path resolution
# ─────────────────────────────────────────────────────────────────────


def _module_path_for_file(path: Path, root: Path) -> Optional[str]:
    """Compute the dotted module path for ``path`` relative to ``root``.

    Walks up from the file's parent directory; each level is part of the
    dotted name only if it contains an ``__init__.py``. Top-level files
    that aren't part of a package use the bare filename stem (and will
    only resolve at serve time if the toolkit root is on sys.path or
    the file is a sibling at root level).

    Returns None if the file is outside ``root``.

    Special cases:
    - If the file is ``__init__.py``, the module path is the package
      itself (e.g. ``mypkg/__init__.py`` → ``mypkg``).
    - Top-level scripts at the root with no ``__init__.py`` use the
      bare stem.
    """
    module_path, _reason = _module_path_for_file_with_reason(path, root)
    return module_path


def _module_path_for_file_with_reason(
    path: Path, root: Path
) -> Tuple[Optional[str], Optional[str]]:
    """Like :func:`_module_path_for_file` but also returns a reason for ``None``.

    When the dotted module path can't be resolved, the second element
    is a short human-readable string suitable for inclusion in a CLI
    warning (e.g. ``"missing __init__.py in tools/analysis"``).

    On success the reason is ``None``.
    """
    try:
        rel = path.resolve().relative_to(root.resolve())
    except ValueError:
        return None, "file is outside the ingest root"

    parts: List[str] = []
    # Build the package prefix by walking parents up to (but not
    # including) root, collecting each directory iff it has an
    # __init__.py. If we hit a directory without one, capture which
    # directory was missing the marker so the caller can point the
    # user at exactly the right fix.
    cur = path.parent.resolve()
    root_resolved = root.resolve()
    while cur != root_resolved:
        if (cur / "__init__.py").is_file():
            parts.append(cur.name)
            cur = cur.parent
        else:
            try:
                missing_rel = cur.relative_to(root_resolved)
                missing_str = str(missing_rel).replace("\\", "/")
            except ValueError:
                missing_str = cur.name
            reason = (
                f"missing __init__.py in {missing_str}"
                if missing_str and missing_str != "."
                else "missing __init__.py in toolkit root"
            )
            return None, reason
    parts.reverse()

    if path.name == "__init__.py":
        return (".".join(parts) if parts else ""), None
    parts.append(path.stem)
    return ".".join(parts), None


def _file_contains_tool_patterns(path: Path) -> bool:
    """Cheap AST pre-scan: does ``path`` define any tool-shaped symbol?

    Used to decide whether silently skipping a file (because its module
    path can't be resolved) is "this is a plain Python file, no warning
    needed" or "this file would have contributed tools — warn the user."

    Mirrors the detection logic in :func:`extract_tools_from_file` but
    doesn't need the module path: it only checks shape (a top-level
    ``@define_tool`` decorated function or a top-level ``BaseTool``
    subclass).
    """
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return False

    define_tool_aliases, basetool_aliases = _resolve_decorator_aliases(tree)

    for node in tree.body:
        if _is_in_type_checking_block(node, tree):
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for deco in node.decorator_list:
                if _decorator_is_define_tool(deco, define_tool_aliases):
                    return True
        elif isinstance(node, ast.ClassDef):
            for base in node.bases:
                if _base_is_basetool(base, basetool_aliases):
                    return True
    return False


# ─────────────────────────────────────────────────────────────────────
# AST detection
# ─────────────────────────────────────────────────────────────────────


def _resolve_decorator_aliases(tree: ast.Module) -> Tuple[Set[str], Set[str]]:
    """Return (define_tool aliases, BaseTool aliases) bound by this module's imports.

    Walks top-level imports only. A ``from orchestral import define_tool
    as dt`` produces ``"dt"`` in the first set. ``from orchestral.tools
    import BaseTool`` produces ``"BaseTool"`` in the second.

    ``import orchestral`` doesn't add to either set; attribute-access
    decorators (``@orchestral.define_tool``) are detected separately
    against the known chains in ``_DEFINE_TOOL_ATTR_CHAINS``.
    """
    define_tool_aliases: Set[str] = set()
    basetool_aliases: Set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                bound = alias.asname or alias.name
                if (module, alias.name) in _DEFINE_TOOL_SOURCES:
                    define_tool_aliases.add(bound)
                if (module, alias.name) in _BASETOOL_SOURCES:
                    basetool_aliases.add(bound)
    return define_tool_aliases, basetool_aliases


def _attribute_chain(node: ast.AST) -> Optional[Tuple[str, ...]]:
    """Reduce an ``ast.Attribute`` chain to a tuple of names.

    ``orchestral.define_tool`` → ``("orchestral", "define_tool")``.
    Returns None if the chain isn't a pure attribute-access chain.
    """
    parts: List[str] = []
    cur: ast.AST = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if not isinstance(cur, ast.Name):
        return None
    parts.append(cur.id)
    parts.reverse()
    return tuple(parts)


def _decorator_is_define_tool(
    decorator: ast.expr, aliases: Set[str]
) -> bool:
    """True if this decorator expression resolves to ``@define_tool``."""
    # Strip Call() to get to the decorator name itself; @define_tool() and
    # @define_tool both look the same in semantics.
    inner: ast.AST = decorator
    if isinstance(inner, ast.Call):
        inner = inner.func
    if isinstance(inner, ast.Name):
        return inner.id in aliases
    if isinstance(inner, ast.Attribute):
        chain = _attribute_chain(inner)
        if chain is None:
            return False
        return chain in _DEFINE_TOOL_ATTR_CHAINS
    return False


def _base_is_basetool(base: ast.expr, aliases: Set[str]) -> bool:
    """True if a class-base expression resolves to ``BaseTool``."""
    if isinstance(base, ast.Name):
        return base.id in aliases
    if isinstance(base, ast.Attribute):
        chain = _attribute_chain(base)
        if chain is None:
            return False
        return chain in _BASETOOL_ATTR_CHAINS
    return False


def _docstring_first_line(node: ast.AST) -> str:
    raw = ast.get_docstring(node)
    if not raw:
        return "(no description)"
    first = raw.strip().splitlines()[0].strip()
    return first or "(no description)"


def _is_in_type_checking_block(
    target: ast.AST, tree: ast.Module
) -> bool:
    """True if ``target`` is nested inside an ``if TYPE_CHECKING:`` block.

    AST-only check; we walk the tree and if we find ``target`` as a
    descendant of any ``If(test=...)`` whose test is ``TYPE_CHECKING``
    or ``typing.TYPE_CHECKING``, we exclude it.
    """
    for if_node in ast.walk(tree):
        if not isinstance(if_node, ast.If):
            continue
        test = if_node.test
        is_tc = False
        if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
            is_tc = True
        elif isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING":
            is_tc = True
        if not is_tc:
            continue
        for descendant in ast.walk(if_node):
            if descendant is target:
                return True
    return False


def extract_tools_from_file(
    path: Path, root: Path
) -> List[ToolDescriptor]:
    """AST-parse one file and return its tool descriptors.

    Pure static analysis — never imports the file. Returns an empty
    list if the file can't be parsed or has no tools.
    """
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []

    module_path = _module_path_for_file(path, root)
    if module_path is None or module_path == "":
        return []

    define_tool_aliases, basetool_aliases = _resolve_decorator_aliases(tree)

    descriptors: List[ToolDescriptor] = []

    # Only top-level definitions count. Metaprogramming and nested
    # definitions are out of scope for v1.
    for node in tree.body:
        if _is_in_type_checking_block(node, tree):
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for deco in node.decorator_list:
                if _decorator_is_define_tool(deco, define_tool_aliases):
                    descriptors.append(
                        ToolDescriptor(
                            module=module_path,
                            name=node.name,
                            description=_docstring_first_line(node),
                            source_path=path,
                            source_line=node.lineno,
                            kind="function",
                        )
                    )
                    break  # one descriptor per definition
        elif isinstance(node, ast.ClassDef):
            for base in node.bases:
                if _base_is_basetool(base, basetool_aliases):
                    descriptors.append(
                        ToolDescriptor(
                            module=module_path,
                            name=node.name,
                            description=_docstring_first_line(node),
                            source_path=path,
                            source_line=node.lineno,
                            kind="class",
                        )
                    )
                    break

    return descriptors


# ─────────────────────────────────────────────────────────────────────
# Discovery driver
# ─────────────────────────────────────────────────────────────────────


def discover_tools(root: Path) -> List[ToolDescriptor]:
    """Walk ``root`` and return every tool descriptor found.

    Sorted by (module, name) for deterministic output.
    """
    tools, _dropped = discover_tools_and_drops(root)
    return tools


def discover_tools_and_drops(
    root: Path,
) -> Tuple[List[ToolDescriptor], List[DroppedFile]]:
    """Walk ``root`` and return tool descriptors plus dropped files.

    A "dropped" file is one that an AST pre-scan flagged as containing
    tool-shaped definitions but whose dotted module path could not be
    resolved (typically because an intermediate directory is missing
    ``__init__.py``). Reporting these to the user prevents the
    silent-drop bug where ingest emits fewer tools than the codebase
    actually defines.

    Both lists are sorted for deterministic output.
    """
    root = root.resolve()
    found: List[ToolDescriptor] = []
    dropped: List[DroppedFile] = []
    for py in walk_python_files(root):
        module_path, reason = _module_path_for_file_with_reason(py, root)
        if module_path is None or module_path == "":
            if reason is not None and _file_contains_tool_patterns(py):
                dropped.append(DroppedFile(source_path=py, reason=reason))
            continue
        found.extend(extract_tools_from_file(py, root))
    found.sort(key=lambda t: (t.module, t.name))
    dropped.sort(key=lambda d: str(d.source_path))
    return found, dropped


# ─────────────────────────────────────────────────────────────────────
# Yaml emission
# ─────────────────────────────────────────────────────────────────────


_PLACEHOLDER_METADATA = {
    "name": "TODO_set_toolkit_name",
    "version": "0.1.0",
    "description": "TODO_describe_your_toolkit",
    "author": "TODO_your_name",
    "license": "MIT",
    "category": "other",
    "python_version": "3.12",
    "keywords": [],
}


def _build_yaml_data(
    tools: Sequence[ToolDescriptor],
    existing: Optional[dict],
):
    """Build the ruamel.yaml-roundtrippable mapping for emission.

    If ``existing`` is provided, all top-level keys except ``tools`` are
    preserved (an author who started filling in metadata before realizing
    they needed ingest doesn't lose their work).
    """
    from ruamel.yaml.comments import CommentedMap, CommentedSeq

    data = CommentedMap()
    if existing:
        for key, value in existing.items():
            if key == "tools":
                continue
            data[key] = value
    else:
        for key, value in _PLACEHOLDER_METADATA.items():
            data[key] = value

    tool_seq = CommentedSeq()
    for t in tools:
        entry = CommentedMap()
        entry["module"] = t.module
        entry["name"] = t.name
        entry["description"] = t.description
        tool_seq.append(entry)
    data["tools"] = tool_seq

    return data


def emit_toolkit_yaml(
    tools: Sequence[ToolDescriptor],
    target: Path,
    existing: Optional[dict] = None,
) -> None:
    """Write ``toolkit.yaml`` at ``target`` using ruamel.yaml."""
    from ruamel.yaml import YAML

    yaml = YAML()
    yaml.indent(mapping=2, sequence=4, offset=2)
    yaml.preserve_quotes = True

    data = _build_yaml_data(tools, existing)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as f:
        yaml.dump(data, f)


def load_existing_yaml(target: Path) -> Optional[dict]:
    """Load an existing toolkit.yaml as a roundtrippable mapping, or None."""
    if not target.is_file():
        return None
    from ruamel.yaml import YAML

    yaml = YAML()
    yaml.preserve_quotes = True
    try:
        with target.open("r", encoding="utf-8") as f:
            data = yaml.load(f)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return data


# ─────────────────────────────────────────────────────────────────────
# Entry point used by the CLI command
# ─────────────────────────────────────────────────────────────────────


@dataclass
class IngestResult:
    """Outcome of an ingest run.

    ``wrote`` is False for dry-run or when the user declined overwrite.
    ``requirements_present`` carries forward to the CLI summary.
    ``dropped`` lists files that the walker flagged as containing
    tool-shaped definitions but whose module path could not be
    resolved (e.g. missing ``__init__.py``); the CLI surfaces these
    as a stderr warning so the silent-drop case becomes visible.
    """

    tools: List[ToolDescriptor]
    target: Path
    wrote: bool
    requirements_present: bool
    overwrite_blocked: bool = False
    dropped: List[DroppedFile] = field(default_factory=list)


def ingest(
    root: Path,
    output: Optional[Path],
    *,
    overwrite: bool,
    dry_run: bool,
) -> IngestResult:
    """Discover tools and write toolkit.yaml.

    ``overwrite`` controls whether an existing ``toolkit.yaml`` at
    ``output`` should be replaced. The CLI layer is responsible for
    deriving this from ``--force``/prompt-mode/TTY status; this function
    treats it as a binary input.

    On ``dry_run`` no file is written; descriptors and target path are
    returned for the CLI to print.
    """
    root = root.resolve()
    target = (output or (root / "toolkit.yaml")).resolve()
    tools, dropped = discover_tools_and_drops(root)
    requirements_present = (root / "requirements.txt").is_file()

    if dry_run:
        return IngestResult(
            tools=tools,
            target=target,
            wrote=False,
            requirements_present=requirements_present,
            dropped=dropped,
        )

    existing = load_existing_yaml(target)
    if existing is not None and not overwrite:
        return IngestResult(
            tools=tools,
            target=target,
            wrote=False,
            requirements_present=requirements_present,
            overwrite_blocked=True,
            dropped=dropped,
        )

    emit_toolkit_yaml(tools, target, existing=existing)
    return IngestResult(
        tools=tools,
        target=target,
        wrote=True,
        requirements_present=requirements_present,
        dropped=dropped,
    )
