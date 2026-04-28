"""
Import path resolver: maps import strings to repo-relative POSIX paths.

Handles:
- Relative imports (starting with "." or "/")
- Python relative imports (starting with "..")
- Index file resolution (imports pointing to a directory)
- External packages → None
"""
from __future__ import annotations
import posixpath
from pathlib import Path, PurePosixPath


_PYTHON_EXTENSIONS = (".py", ".pyi")
_TS_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
_ALL_EXTENSIONS = _PYTHON_EXTENSIONS + _TS_EXTENSIONS

_INDEX_STEMS = ("index", "__init__")


def resolve(import_from: str, importing_file: str, repo_root: str | Path) -> str | None:
    """
    Resolve an import string to a repo-relative POSIX path.

    Args:
        import_from:     The import source string (e.g. "./utils", "../config", "os")
        importing_file:  Repo-relative POSIX path of the file doing the import
        repo_root:       Absolute path to the repository root

    Returns:
        Repo-relative POSIX path of the resolved file, or None if unresolvable
        (external package, dynamic import, ambiguous, or file not found).
    """
    root = Path(repo_root).resolve()

    # External packages: no relative prefix and no internal path
    if not _is_relative(import_from):
        return None

    importer_dir = PurePosixPath(importing_file).parent
    target = _normalize_target(import_from, importer_dir)
    if target is None:
        return None

    # Try candidate extensions
    candidates = _candidate_paths(target)
    for candidate in candidates:
        abs_candidate = root / candidate
        if abs_candidate.exists():
            return candidate

    return None


def _is_relative(import_from: str) -> bool:
    return import_from.startswith(".") or import_from.startswith("/")


def _normalize_target(import_from: str, importer_dir: PurePosixPath) -> str | None:
    """Resolve the import string relative to the importer's directory."""
    try:
        if import_from.startswith("/"):
            # Absolute-from-root path (rare in TS projects)
            return import_from.lstrip("/")

        # Relative path: join with importer directory and normalize ".."
        joined = str(importer_dir / import_from).replace("\\", "/")
        # posixpath.normpath collapses ".." and "." segments
        result = posixpath.normpath(joined)
        # Strip leading slash if present
        result = result.lstrip("/")
        return result or None
    except Exception:
        return None


def _candidate_paths(base: str) -> list[str]:
    """
    Return candidate file paths for a given base path (no extension).
    Tries exact match first, then adds extensions, then index files.
    """
    base_path = PurePosixPath(base)
    candidates: list[str] = []

    # 1. Exact match (import already has extension)
    if base_path.suffix in _ALL_EXTENSIONS:
        candidates.append(base)
        return candidates

    # 2. Try adding known extensions
    for ext in _ALL_EXTENSIONS:
        candidates.append(base + ext)

    # 3. Try as directory with index files
    for stem in _INDEX_STEMS:
        for ext in _ALL_EXTENSIONS:
            candidates.append(f"{base}/{stem}{ext}")

    return candidates
