"""
Repository pattern data-access layer for CodeLens Phase 4.

Each repository takes an injected SQLAlchemy Session, making them
straightforward to use in tests with an in-memory SQLite engine.
"""
from __future__ import annotations
import time

from sqlalchemy.orm import Session

from codelens.db.schema import FileRecord, DependencyRecord, ObservationRecord


class FileRepository:
    """CRUD for FileRecord rows plus stale/deleted detection."""

    def __init__(self, session: Session) -> None:
        self._s = session

    def upsert(self, record: FileRecord) -> None:
        """Insert or replace a FileRecord (merge by primary key)."""
        self._s.merge(record)
        self._s.commit()

    def get(self, path: str) -> FileRecord | None:
        return self._s.get(FileRecord, path)

    def get_all(self) -> list[FileRecord]:
        return list(self._s.query(FileRecord).all())

    def delete(self, path: str) -> None:
        rec = self._s.get(FileRecord, path)
        if rec is not None:
            self._s.delete(rec)
            self._s.commit()

    def get_stale_paths(self, current_hashes: dict[str, str]) -> list[str]:
        """
        Return paths that are new or whose content hash differs from the DB.
        These are the files that need re-parsing.
        """
        stale: list[str] = []
        for path, current_hash in current_hashes.items():
            rec = self.get(path)
            if rec is None or rec.content_hash != current_hash:
                stale.append(path)
        return stale

    def get_deleted_paths(self, current_paths: set[str]) -> list[str]:
        """Return paths in the DB that no longer exist in the repo."""
        db_paths = {r.path for r in self.get_all()}
        return list(db_paths - current_paths)


class DependencyRepository:
    """CRUD for DependencyRecord rows, keyed by (from_file, to_file)."""

    def __init__(self, session: Session) -> None:
        self._s = session

    def upsert_for_file(
        self,
        from_file: str,
        to_files: list[str],
        import_type: str = "static",
    ) -> None:
        """
        Replace all outgoing dependencies of from_file.
        Deletes existing rows first, then inserts new ones.
        """
        self._s.query(DependencyRecord).filter_by(from_file=from_file).delete()
        for to_file in to_files:
            self._s.add(DependencyRecord(
                from_file=from_file,
                to_file=to_file,
                import_type=import_type,
            ))
        self._s.commit()

    def delete_for_file(self, from_file: str) -> None:
        """Remove all edges originating from from_file."""
        self._s.query(DependencyRecord).filter_by(from_file=from_file).delete()
        self._s.commit()

    def get_for_file(self, from_file: str) -> list[DependencyRecord]:
        """Return all outgoing dependency records for from_file."""
        return list(
            self._s.query(DependencyRecord).filter_by(from_file=from_file).all()
        )

    def get_dependents(self, to_file: str) -> list[DependencyRecord]:
        """Return all files that import to_file."""
        return list(
            self._s.query(DependencyRecord).filter_by(to_file=to_file).all()
        )


class ObservationRepository:
    """
    CRUD for ObservationRecord rows.

    Observations automatically inherit importance from the referenced file
    at insert time (if importance is not explicitly set).
    """

    def __init__(self, session: Session) -> None:
        self._s = session

    def add(self, obs: ObservationRecord) -> None:
        """
        Persist an observation.

        If obs.importance == 0.0, inherits the current importance_score
        of the referenced file (if it exists in the DB).
        """
        if obs.importance == 0.0:
            file_rec = self._s.get(FileRecord, obs.file_path)
            if file_rec is not None:
                obs.importance = file_rec.importance_score

        if obs.created_at == 0.0:
            obs.created_at = time.time()

        self._s.add(obs)
        self._s.commit()

    def get_for_file(self, file_path: str) -> list[ObservationRecord]:
        return list(
            self._s.query(ObservationRecord).filter_by(file_path=file_path).all()
        )

    def get_by_session(self, session_id: str) -> list[ObservationRecord]:
        return list(
            self._s.query(ObservationRecord).filter_by(session_id=session_id).all()
        )
