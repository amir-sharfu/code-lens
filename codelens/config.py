"""
Central configuration for CodeLens.

Reads defaults from environment variables; all paths are resolved
relative to the repository root.
"""
from __future__ import annotations
import os
from dataclasses import dataclass
from pathlib import Path


_CODELENS_DIR = ".codelens"


@dataclass
class CodeLensConfig:
    """Runtime configuration derived from a repo root and env vars."""

    repo_path: Path
    db_path: Path
    chroma_dir: Path
    embedding_backend: str
    embed_model: str

    @classmethod
    def for_repo(cls, repo_path: str | Path | None = None) -> "CodeLensConfig":
        """
        Build a config for the given repo root (defaults to cwd).
        All values can be overridden by environment variables.
        """
        root = Path(repo_path or os.getcwd()).resolve()
        codelens_dir = root / _CODELENS_DIR

        return cls(
            repo_path=root,
            db_path=Path(os.getenv("CODELENS_DB_PATH", str(codelens_dir / "index.db"))),
            chroma_dir=Path(os.getenv("CODELENS_CHROMA_DIR", str(codelens_dir / "chroma"))),
            embedding_backend=os.getenv("CODELENS_EMBEDDING_BACKEND", "local"),
            embed_model=os.getenv("CODELENS_EMBED_MODEL", "BAAI/bge-small-en-v1.5"),
        )

    @property
    def is_initialized(self) -> bool:
        """True when the SQLite index already exists."""
        return self.db_path.exists()
