"""
SQLAlchemy ORM schema for CodeLens Phase 4 persistence.

Three tables:
  files        — one row per tracked source file
  dependencies — import edges between files
  observations — LLM session notes attached to files
"""
from __future__ import annotations
from pathlib import Path

try:
    from sqlalchemy import Column, String, Float, Text, Integer, create_engine
    from sqlalchemy.orm import DeclarativeBase
except ImportError as exc:
    raise ImportError(
        "sqlalchemy is required for Phase 4: pip install sqlalchemy>=2.0"
    ) from exc


class Base(DeclarativeBase):
    pass


class FileRecord(Base):
    """One row per source file tracked in the index."""
    __tablename__ = "files"

    path = Column(String, primary_key=True)
    skeleton_json = Column(Text, nullable=False)
    importance_score = Column(Float, default=0.0, nullable=False)
    tier = Column(String, default="peripheral", nullable=False)
    last_modified = Column(Float, default=0.0, nullable=False)
    content_hash = Column(String, nullable=False)

    def __repr__(self) -> str:
        return f"<FileRecord path={self.path!r} tier={self.tier!r}>"


class DependencyRecord(Base):
    """Import edge: from_file imports from to_file."""
    __tablename__ = "dependencies"

    from_file = Column(String, primary_key=True)
    to_file = Column(String, primary_key=True)
    import_type = Column(String, default="static", nullable=False)

    def __repr__(self) -> str:
        return f"<DependencyRecord {self.from_file!r} -> {self.to_file!r}>"


class ObservationRecord(Base):
    """
    An LLM session observation attached to a file.

    importance is populated at insert time from the file's current importance_score,
    so observations remain useful even after files are re-ranked.
    """
    __tablename__ = "observations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    file_path = Column(String, nullable=False)
    session_id = Column(String, nullable=False)
    observation_type = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    importance = Column(Float, default=0.0, nullable=False)
    created_at = Column(Float, nullable=False)

    def __repr__(self) -> str:
        return (
            f"<ObservationRecord file={self.file_path!r} "
            f"type={self.observation_type!r}>"
        )


def get_engine(db_path: str | Path):
    """Create a SQLAlchemy engine for a SQLite database at db_path."""
    return create_engine(f"sqlite:///{db_path}")


def create_tables(engine) -> None:
    """Create all tables if they don't already exist."""
    Base.metadata.create_all(engine)
