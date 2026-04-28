"""
Incremental index updater for CodeLens Phase 4.

Uses SHA-256 per file to detect changes and only re-parses what changed.
PageRank is recomputed when >= RECOMPUTE_THRESHOLD files change, or on full init.
"""
from __future__ import annotations
import hashlib
import json
import time
from pathlib import Path

from sqlalchemy.orm import Session

from codelens.db.schema import FileRecord, get_engine, create_tables
from codelens.db.repository import FileRepository, DependencyRepository
from codelens.models import FileSkeleton, RepoSkeleton

RECOMPUTE_THRESHOLD = 10
_DEFAULT_DB_SUBPATH = ".codelens/index.db"


def compute_file_hash(path: Path) -> str:
    """Return the SHA-256 hex digest of a file's bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def scan_hashes(repo_path: str | Path) -> dict[str, str]:
    """
    Walk the repo and return {posix_rel_path: sha256} for every source file.
    Files that cannot be read are silently skipped.
    """
    from codelens.walker import walk_repo

    root = Path(repo_path)
    hashes: dict[str, str] = {}
    for abs_path, rel_path in walk_repo(root):
        try:
            hashes[rel_path] = compute_file_hash(abs_path)
        except OSError:
            pass
    return hashes


class IncrementalUpdater:
    """
    Manages incremental re-parsing and graph recomputation for a repository.

    Usage::

        updater = IncrementalUpdater("/path/to/repo")
        summary = updater.init(full=True)   # first run
        summary = updater.update()           # subsequent runs
        scores  = updater.get_importance()
        tiers   = updater.get_tiers()
    """

    def __init__(
        self,
        repo_path: str | Path,
        db_path: str | Path | None = None,
    ) -> None:
        self._repo_path = Path(repo_path).resolve()

        if db_path is None:
            db_dir = self._repo_path / ".codelens"
            db_dir.mkdir(exist_ok=True)
            db_path = db_dir / "index.db"

        self._db_path = str(db_path)
        # Ensure the parent directory exists regardless of how db_path was supplied
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._engine = get_engine(self._db_path)
        create_tables(self._engine)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def init(self, full: bool = False) -> dict:
        """
        Index the repository.

        Args:
            full: Re-parse every file, ignoring cached hashes.

        Returns a summary dict:
            parsed    — files re-parsed this run
            skipped   — files whose hash matched (skipped)
            deleted   — files removed from DB (no longer in repo)
            recomputed — whether PageRank was recomputed
        """
        with Session(self._engine) as session:
            file_repo = FileRepository(session)
            dep_repo = DependencyRepository(session)

            current_hashes = scan_hashes(self._repo_path)
            current_paths = set(current_hashes.keys())

            stale = list(current_hashes.keys()) if full else file_repo.get_stale_paths(current_hashes)
            deleted = file_repo.get_deleted_paths(current_paths)

            # Remove files that no longer exist
            for path in deleted:
                dep_repo.delete_for_file(path)
                file_repo.delete(path)

            # Re-parse stale files
            parsed = 0
            for rel_path in stale:
                abs_path = self._repo_path / rel_path
                skeleton = self._parse_single(abs_path, rel_path)
                if skeleton is None:
                    continue

                try:
                    mtime = abs_path.stat().st_mtime
                except OSError:
                    mtime = 0.0

                file_repo.upsert(FileRecord(
                    path=rel_path,
                    skeleton_json=json.dumps(skeleton.to_dict()),
                    importance_score=0.0,
                    tier="peripheral",
                    last_modified=mtime,
                    content_hash=current_hashes[rel_path],
                ))
                parsed += 1

            # Recompute graph if enough changed or on full init
            changes = parsed + len(deleted)
            recomputed = False
            if full or changes >= RECOMPUTE_THRESHOLD:
                self._recompute_graph(session)
                recomputed = True

        return {
            "parsed": parsed,
            "skipped": len(current_hashes) - len(stale),
            "deleted": len(deleted),
            "recomputed": recomputed,
        }

    def update(self) -> dict:
        """Incremental update: re-parse only what changed since last run."""
        return self.init(full=False)

    def get_importance(self) -> dict[str, float]:
        """Return {path: importance_score} for all indexed files."""
        with Session(self._engine) as session:
            return {r.path: r.importance_score for r in FileRepository(session).get_all()}

    def get_tiers(self) -> dict[str, str]:
        """Return {path: tier} for all indexed files."""
        with Session(self._engine) as session:
            return {r.path: r.tier for r in FileRepository(session).get_all()}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _parse_single(self, abs_path: Path, rel_path: str) -> FileSkeleton | None:
        """Parse one file. Returns None if unsupported or unreadable."""
        from codelens.parsers import get_parser_for

        parser = get_parser_for(abs_path)
        if parser is None:
            return None
        try:
            source = abs_path.read_bytes()
            return parser.parse(source, rel_path)
        except OSError:
            return None

    def _recompute_graph(self, session: Session) -> None:
        """
        Reconstruct the full skeleton from DB records, run build_and_score,
        then persist updated importance scores, tiers, and dependency edges.
        """
        from codelens.graph import build_and_score

        file_repo = FileRepository(session)
        dep_repo = DependencyRepository(session)

        records = file_repo.get_all()
        if not records:
            return

        # Deserialise skeletons stored as JSON
        files: list[FileSkeleton] = []
        for rec in records:
            try:
                f = FileSkeleton.model_validate(json.loads(rec.skeleton_json))
                files.append(f)
            except Exception:
                continue

        skeleton = RepoSkeleton(
            repo_path=str(self._repo_path),
            files=files,
            total_files=len(files),
        )

        graph, importance, tiers = build_and_score(skeleton)

        # Update importance + tier in every FileRecord
        rec_map = {r.path: r for r in records}
        for path, score in importance.items():
            if path in rec_map:
                rec_map[path].importance_score = score
                rec_map[path].tier = tiers.get(path, "peripheral")

        # Persist dependency edges derived from the graph
        for f in files:
            edges = [dst for _, dst in graph.out_edges(f.path)]
            dep_repo.upsert_for_file(f.path, edges)

        session.commit()
