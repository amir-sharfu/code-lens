"""Tests for codelens.db.schema — ORM models and table creation."""
from __future__ import annotations
import pytest
from sqlalchemy.orm import Session
from codelens.db.schema import (
    get_engine, create_tables,
    FileRecord, DependencyRecord, ObservationRecord,
)


@pytest.fixture()
def engine():
    """In-memory SQLite engine with all tables created."""
    eng = get_engine(":memory:")
    create_tables(eng)
    return eng


@pytest.fixture()
def session(engine):
    with Session(engine) as s:
        yield s


# ---------------------------------------------------------------------------
# Table creation
# ---------------------------------------------------------------------------

class TestCreateTables:
    def test_idempotent(self, engine):
        """Calling create_tables twice must not raise."""
        create_tables(engine)

    def test_files_table_exists(self, session):
        assert session.query(FileRecord).count() == 0

    def test_dependencies_table_exists(self, session):
        assert session.query(DependencyRecord).count() == 0

    def test_observations_table_exists(self, session):
        assert session.query(ObservationRecord).count() == 0


# ---------------------------------------------------------------------------
# FileRecord
# ---------------------------------------------------------------------------

class TestFileRecord:
    def _rec(self, path: str = "a.py", hash_: str = "abc") -> FileRecord:
        return FileRecord(
            path=path,
            skeleton_json='{"path": "a.py"}',
            importance_score=0.5,
            tier="core",
            last_modified=1000.0,
            content_hash=hash_,
        )

    def test_insert_and_retrieve(self, session):
        rec = self._rec()
        session.add(rec)
        session.commit()
        fetched = session.get(FileRecord, "a.py")
        assert fetched is not None
        assert fetched.tier == "core"
        assert fetched.importance_score == pytest.approx(0.5)

    def test_primary_key_is_path(self, session):
        session.add(self._rec("x.py"))
        session.commit()
        assert session.get(FileRecord, "x.py") is not None
        assert session.get(FileRecord, "y.py") is None

    def test_merge_updates_existing(self, session):
        session.add(self._rec("a.py", hash_="old"))
        session.commit()
        session.merge(FileRecord(
            path="a.py",
            skeleton_json="{}",
            importance_score=0.9,
            tier="important",
            last_modified=2000.0,
            content_hash="new",
        ))
        session.commit()
        rec = session.get(FileRecord, "a.py")
        assert rec.content_hash == "new"
        assert rec.tier == "important"

    def test_repr_contains_path(self):
        assert "a.py" in repr(self._rec("a.py"))


# ---------------------------------------------------------------------------
# DependencyRecord
# ---------------------------------------------------------------------------

class TestDependencyRecord:
    def test_insert_and_retrieve(self, session):
        session.add(DependencyRecord(from_file="main.py", to_file="utils.py"))
        session.commit()
        rows = session.query(DependencyRecord).all()
        assert len(rows) == 1
        assert rows[0].from_file == "main.py"
        assert rows[0].to_file == "utils.py"

    def test_default_import_type_is_static(self, session):
        session.add(DependencyRecord(from_file="a.py", to_file="b.py"))
        session.commit()
        rec = session.query(DependencyRecord).first()
        assert rec.import_type == "static"

    def test_composite_primary_key(self, session):
        session.add(DependencyRecord(from_file="a.py", to_file="b.py"))
        session.add(DependencyRecord(from_file="a.py", to_file="c.py"))
        session.commit()
        assert session.query(DependencyRecord).count() == 2


# ---------------------------------------------------------------------------
# ObservationRecord
# ---------------------------------------------------------------------------

class TestObservationRecord:
    def _obs(self, **kw) -> ObservationRecord:
        defaults = dict(
            file_path="a.py",
            session_id="sess-1",
            observation_type="note",
            content="interesting function",
            importance=0.7,
            created_at=1000.0,
        )
        defaults.update(kw)
        return ObservationRecord(**defaults)

    def test_autoincrement_id(self, session):
        session.add(self._obs())
        session.add(self._obs())
        session.commit()
        ids = [r.id for r in session.query(ObservationRecord).all()]
        assert ids[0] != ids[1]

    def test_fields_stored(self, session):
        session.add(self._obs(content="test content"))
        session.commit()
        obs = session.query(ObservationRecord).first()
        assert obs.content == "test content"
        assert obs.session_id == "sess-1"

    def test_repr_contains_file_path(self):
        assert "a.py" in repr(self._obs())
