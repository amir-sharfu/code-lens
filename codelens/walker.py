from __future__ import annotations
import os
from pathlib import Path
from typing import Iterator
import pathspec


_ALWAYS_SKIP_DIRS = frozenset({
    "node_modules", "venv", ".venv", "env", ".env",
    "dist", "build", ".next", ".nuxt", ".output",
    "__pycache__", ".pytest_cache", ".mypy_cache",
    ".git", ".svn", ".hg",
    "coverage", ".coverage", "htmlcov",
    ".codelens",
})

_SOURCE_EXTENSIONS = frozenset({
    ".py", ".pyi",
    ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
})

_AUTO_GEN_PATTERNS = (
    b"// AUTO-GENERATED",
    b"// Code generated",
    b"# AUTO-GENERATED",
    b"# This file is auto-generated",
    b"/* eslint-disable */",
    b"// @generated",
    b"DO NOT EDIT",
)

_MAX_LOC = 5000


def _load_gitignore(repo_path: Path) -> pathspec.PathSpec | None:
    gitignore = repo_path / ".gitignore"
    if not gitignore.exists():
        return None
    patterns = gitignore.read_text(encoding="utf-8", errors="replace").splitlines()
    return pathspec.PathSpec.from_lines("gitignore", patterns)


def _is_auto_generated(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            header = f.read(512)
            for pattern in _AUTO_GEN_PATTERNS:
                if pattern in header:
                    return True
            rest = f.read()
        # Count newlines directly: a file with 2000 lines has 2000 newlines
        total_newlines = (header + rest).count(b"\n")
        return total_newlines > _MAX_LOC
    except OSError:
        return False


def walk_repo(repo_path: str | Path) -> Iterator[tuple[Path, str]]:
    """
    Yield (absolute_path, repo_relative_posix_path) for every parseable
    source file in the repository.
    """
    root = Path(repo_path).resolve()
    spec = _load_gitignore(root)

    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)

        dirnames[:] = [
            d for d in dirnames
            if d not in _ALWAYS_SKIP_DIRS
            and not d.startswith(".")
            and (spec is None or not spec.match_file(
                str((current / d).relative_to(root)).replace("\\", "/") + "/"
            ))
        ]

        for filename in filenames:
            file_path = current / filename
            if file_path.suffix.lower() not in _SOURCE_EXTENSIONS:
                continue

            try:
                rel = file_path.relative_to(root)
            except ValueError:
                continue

            rel_posix = rel.as_posix()

            if spec and spec.match_file(rel_posix):
                continue

            if _is_auto_generated(file_path):
                continue

            yield file_path, rel_posix
