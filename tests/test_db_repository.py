"""Tests for codelens.db.repository — FileRepository, DependencyRepository, ObservationRepository."""
from __future__ import annotations
import pytest
import time
from sqlalchemy.orm import Session
from codelens.db.schema import (
    get_engine, create_tables,
    FileRecord, DependencyRecord, ObservationRecord,
)
from codelens.db.repository import FileRepository, DependencyRepository, ObservationRepository


@pytest.fixture()
def session():
    eng = get_engine(":memory:")
    create_tables(eng)
    with Session(eng) as s:
        yield s


def _file_rec(path: str, hash_: str = "h1", importance: float = 0.0, tier: str = "peripheral") -> FileRecord:
    return FileRecord(
        path=path,
        skeleton_json="{}",
        importance_score=importance,
        tier=tier,
        last_modified=0.0,
        content_hash=hash_,
    )


# ---------------------------------------------------------------------------
# FileRepository
# ---------------------------------------------------------------------------

class TestFileRepository:
    def test_upsert_and_get(self, session):
        repo = FileRepository(session)
        repo.upsert(_file_rec("a.py"))
        rec = repo.get("a.py")
        assert rec is not None
        assert rec.path == "a.py"

    def test_get_nonexistent_returns_none(self, session):
        assert FileRepository(session).get("missing.py") is None

    def test_get_all_returns_all(self, session):
        repo = FileRepository(session)
        repo.upsert(_file_rec("a.py"))
        repo.upsert(_file_rec("b.py"))
        assert len(repo.get_all()) == 2

    def test_upsert_updates_existing(self, session):
        repo = FileRepository(session)
        repo.upsert(_file_rec("a.py", hash_="old", tier="peripheral"))
        repo.upsert(_file_rec("a.py", hash_="new", tier="core"))
        rec = repo.get("a.py")
        assert rec.content_hash == "new"
        assert rec.tier == "core"

    def test_delete_removes_record(self, session):
        repo = FileRepository(session)
        repo.upsert(_file_rec("a.py"))
        repo.delete("a.py")
        assert repo.get("a.py") is None

    def test_delete_nonexistent_does_not_raise(self, session):
        FileRepository(session).delete("missing.py")  # should not raise

    def test_get_stale_paths_new_file(self, session):
        repo = FileRepository(session)
        # "a.py" not in DB yet → stale
        stale = repo.get_stale_paths({"a.py": "hash1"})
        assert "a.py" in stale

    def test_get_stale_paths_unchanged_file(self, session):
        repo = FileRepository(session)
        repo.upsert(_file_rec("a.py", hash_="same"))
        stale = repo.get_stale_paths({"a.py": "same"})
        assert "a.py" not in stale

    def test_get_stale_paths_changed_hash(self, session):
        repo = FileRepository(session)
        repo.upsert(_file_rec("a.py", hash_="old"))
        stale = repo.get_stale_paths({"a.py": "new"})
        assert "a.py" in stale

    def test_get_deleted_paths(self, session):
        repo = FileRepository(session)
        repo.upsert(_file_rec("a.py"))
        repo.upsert(_file_rec("b.py"))
        deleted = repo.get_deleted_paths({"a.py"})  # b.py no longer in repo
        assert "b.py" in deleted
        assert "a.py" not in deleted

    def test_get_deleted_paths_empty_db(self, session):
        assert FileRepository(session).get_deleted_paths({"a.py"}) == []


# ---------------------------------------------------------------------------
# DependencyRepository
# ---------------------------------------------------------------------------

class TestDependencyRepository:
    def test_upsert_for_file(self, session):
        repo = DependencyRepository(session)
        repo.upsert_for_file("main.py", ["utils.py", "models.py"])
        deps = repo.get_for_file("main.py")
        targets = {d.to_file for d in deps}
        assert targets == {"utils.py", "models.py"}

    def test_upsert_replaces_existing(self, session):
        repo = DependencyRepository(session)
        repo.upsert_for_file("main.py", ["old.py"])
        repo.upsert_for_file("main.py", ["new.py"])
        deps = repo.get_for_file("main.py")
        assert len(deps) == 1
        assert deps[0].to_file == "new.py"

    def test_upsert_empty_list_removes_all(self, session):
        repo = DependencyRepository(session)
        repo.upsert_for_file("main.py", ["utils.py"])
        repo.upsert_for_file("main.py", [])
        assert repo.get_for_file("main.py") == []

    def test_delete_for_file(self, session):
        repo = DependencyRepository(session)
        repo.upsert_for_file("main.py", ["utils.py"])
        repo.delete_for_file("main.py")
        assert repo.get_for_file("main.py") == []

    def test_get_dependents(self, session):
        repo = DependencyRepository(session)
        repo.upsert_for_file("a.py", ["shared.py"])
        repo.upsert_for_file("b.py", ["shared.py"])
        dependents = repo.get_dependents("shared.py")
        sources = {d.from_file for d in dependents}
        assert sources == {"a.py", "b.py"}

    def test_default_import_type(self, session):
        repo = DependencyRepository(session)
        repo.upsert_for_file("a.py", ["b.py"])
        dep = repo.get_for_file("a.py")[0]
        assert dep.import_type == "static"


# ---------------------------------------------------------------------------
# ObservationRepository
# ---------------------------------------------------------------------------

class TestObservationRepository:
    def _obs(self, file_path: str = "a.py", session_id: str = "s1",
             obs_type: str = "note", importance: float = 0.0) -> ObservationRecord:
        return ObservationRecord(
            file_path=file_path,
            session_id=session_id,
            observation_type=obs_type,
            content="some observation",
            importance=importance,
            created_at=0.0,
        )

    def test_add_and_get_for_file(self, session):
        repo = ObservationRepository(session)
        repo.add(self._obs("a.py"))
        obs = repo.get_for_file("a.py")
        assert len(obs) == 1
        assert obs[0].file_path == "a.py"

    def test_get_for_file_returns_only_that_file(self, session):
        repo = ObservationRepository(session)
        repo.add(self._obs("a.py"))
        repo.add(self._obs("b.py"))
        assert len(repo.get_for_file("a.py")) == 1

    def test_get_by_session(self, session):
        repo = ObservationRepository(session)
        repo.add(self._obs(session_id="sess-A"))
        repo.add(self._obs(session_id="sess-B"))
        assert len(repo.get_by_session("sess-A")) == 1

    def test_inherits_importance_from_file(self, session):
        """Observation with importance=0.0 should inherit file's importance_score."""
        # Insert a file record with importance 0.8
        file_rec = _file_rec("a.py", importance=0.8, tier="core")
        session.add(file_rec)
        session.commit()

        repo = ObservationRepository(session)
        repo.add(self._obs("a.py", importance=0.0))
        obs = repo.get_for_file("a.py")[0]
        assert obs.importance == pytest.approx(0.8)

    def test_explicit_importance_not_overridden(self, session):
        """If importance is already set, it should not be overridden."""
        file_rec = _file_rec("a.py", importance=0.8)
        session.add(file_rec)
        session.commit()

        repo = ObservationRepository(session)
        repo.add(self._obs("a.py", importance=0.3))
        obs = repo.get_for_file("a.py")[0]
        assert obs.importance == pytest.approx(0.3)

    def test_created_at_set_automatically(self, session):
        """created_at==0.0 should be replaced with current time."""
        before = time.time()
        repo = ObservationRepository(session)
        repo.add(self._obs())
        after = time.time()
        obs = repo.get_for_file("a.py")[0]
        assert before <= obs.created_at <= after

    def test_explicit_created_at_preserved(self, session):
        obs = self._obs()
        obs.created_at = 12345.0
        ObservationRepository(session).add(obs)
        stored = ObservationRepository(session).get_for_file("a.py")[0]
        assert stored.created_at == pytest.approx(12345.0)
