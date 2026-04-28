"""Tests for codelens.db.incremental — hash utilities and IncrementalUpdater."""
from __future__ import annotations
import json
import pytest
from pathlib import Path
from sqlalchemy.orm import Session

from codelens.db.schema import get_engine, create_tables, FileRecord
from codelens.db.repository import FileRepository, DependencyRepository
from codelens.db.incremental import (
    compute_file_hash, scan_hashes, IncrementalUpdater, RECOMPUTE_THRESHOLD,
)


# ---------------------------------------------------------------------------
# compute_file_hash
# ---------------------------------------------------------------------------

class TestComputeFileHash:
    def test_returns_64_char_hex(self, tmp_path):
        f = tmp_path / "a.py"
        f.write_bytes(b"hello")
        h = compute_file_hash(f)
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_deterministic(self, tmp_path):
        f = tmp_path / "a.py"
        f.write_bytes(b"content")
        assert compute_file_hash(f) == compute_file_hash(f)

    def test_different_content_different_hash(self, tmp_path):
        a = tmp_path / "a.py"
        b = tmp_path / "b.py"
        a.write_bytes(b"aaa")
        b.write_bytes(b"bbb")
        assert compute_file_hash(a) != compute_file_hash(b)

    def test_same_content_same_hash(self, tmp_path):
        a = tmp_path / "a.py"
        b = tmp_path / "b.py"
        a.write_bytes(b"same")
        b.write_bytes(b"same")
        assert compute_file_hash(a) == compute_file_hash(b)


# ---------------------------------------------------------------------------
# scan_hashes
# ---------------------------------------------------------------------------

class TestScanHashes:
    def test_finds_python_files(self, tmp_path):
        (tmp_path / "app.py").write_text("x = 1")
        hashes = scan_hashes(tmp_path)
        assert "app.py" in hashes

    def test_uses_posix_paths(self, tmp_path):
        sub = tmp_path / "src"
        sub.mkdir()
        (sub / "utils.py").write_text("")
        hashes = scan_hashes(tmp_path)
        keys = list(hashes.keys())
        assert all("/" in k or "\\" not in k for k in keys)
        assert any("utils.py" in k for k in keys)

    def test_skips_non_source_files(self, tmp_path):
        (tmp_path / "readme.md").write_text("docs")
        (tmp_path / "app.py").write_text("")
        hashes = scan_hashes(tmp_path)
        assert not any(".md" in k for k in hashes)
        assert any(".py" in k for k in hashes)

    def test_empty_repo_returns_empty(self, tmp_path):
        assert scan_hashes(tmp_path) == {}


# ---------------------------------------------------------------------------
# IncrementalUpdater helpers
# ---------------------------------------------------------------------------

def _make_py_file(path: Path, content: str = "x = 1") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


# ---------------------------------------------------------------------------
# IncrementalUpdater
# ---------------------------------------------------------------------------

class TestIncrementalUpdater:
    def test_creates_db_file(self, tmp_path):
        _make_py_file(tmp_path / "a.py")
        updater = IncrementalUpdater(tmp_path, db_path=tmp_path / "test.db")
        updater.init(full=True)
        assert (tmp_path / "test.db").exists()

    def test_full_init_parses_all_files(self, tmp_path):
        _make_py_file(tmp_path / "a.py", "def foo(): pass")
        _make_py_file(tmp_path / "b.py", "def bar(): pass")
        updater = IncrementalUpdater(tmp_path, db_path=":memory:")
        summary = updater.init(full=True)
        assert summary["parsed"] == 2
        assert summary["deleted"] == 0

    def test_incremental_skips_unchanged(self, tmp_path):
        _make_py_file(tmp_path / "a.py", "def foo(): pass")
        updater = IncrementalUpdater(tmp_path, db_path=tmp_path / "test.db")
        updater.init(full=True)
        # Second run — same file, same hash
        summary = updater.update()
        assert summary["parsed"] == 0
        assert summary["skipped"] == 1

    def test_incremental_detects_changed_file(self, tmp_path):
        f = _make_py_file(tmp_path / "a.py", "x = 1")
        updater = IncrementalUpdater(tmp_path, db_path=tmp_path / "test.db")
        updater.init(full=True)
        f.write_text("x = 2")  # change content → different hash
        summary = updater.update()
        assert summary["parsed"] == 1

    def test_detects_deleted_files(self, tmp_path):
        f = _make_py_file(tmp_path / "a.py")
        _make_py_file(tmp_path / "b.py")
        updater = IncrementalUpdater(tmp_path, db_path=tmp_path / "test.db")
        updater.init(full=True)
        f.unlink()  # delete a.py
        summary = updater.update()
        assert summary["deleted"] == 1

    def test_new_file_detected_on_update(self, tmp_path):
        _make_py_file(tmp_path / "a.py")
        updater = IncrementalUpdater(tmp_path, db_path=tmp_path / "test.db")
        updater.init(full=True)
        _make_py_file(tmp_path / "b.py")  # new file added
        summary = updater.update()
        assert summary["parsed"] == 1

    def test_full_init_triggers_recompute(self, tmp_path):
        _make_py_file(tmp_path / "a.py")
        updater = IncrementalUpdater(tmp_path, db_path=tmp_path / "test.db")
        summary = updater.init(full=True)
        assert summary["recomputed"] is True

    def test_small_update_does_not_recompute(self, tmp_path):
        _make_py_file(tmp_path / "a.py", "x = 1")
        updater = IncrementalUpdater(tmp_path, db_path=tmp_path / "test.db")
        updater.init(full=True)
        # Modify the file — 1 change, below threshold of 10
        (tmp_path / "a.py").write_text("x = 2")
        summary = updater.update()
        assert summary["recomputed"] is False

    def test_recompute_triggered_at_threshold(self, tmp_path):
        # Create RECOMPUTE_THRESHOLD files
        for i in range(RECOMPUTE_THRESHOLD):
            _make_py_file(tmp_path / f"m{i}.py")
        updater = IncrementalUpdater(tmp_path, db_path=tmp_path / "test.db")
        updater.init(full=True)
        # Change all of them (use a suffix that guarantees content differs from "x = 1")
        for i in range(RECOMPUTE_THRESHOLD):
            (tmp_path / f"m{i}.py").write_text(f"x = {i}_changed")
        summary = updater.update()
        assert summary["parsed"] == RECOMPUTE_THRESHOLD
        assert summary["recomputed"] is True

    def test_get_importance_returns_dict(self, tmp_path):
        _make_py_file(tmp_path / "a.py", "def fn(): pass")
        updater = IncrementalUpdater(tmp_path, db_path=tmp_path / "test.db")
        updater.init(full=True)
        scores = updater.get_importance()
        assert isinstance(scores, dict)
        assert "a.py" in scores

    def test_get_tiers_returns_valid_tiers(self, tmp_path):
        _make_py_file(tmp_path / "a.py")
        updater = IncrementalUpdater(tmp_path, db_path=tmp_path / "test.db")
        updater.init(full=True)
        tiers = updater.get_tiers()
        valid = {"core", "important", "supporting", "peripheral"}
        assert all(t in valid for t in tiers.values())

    def test_db_path_none_creates_dot_codelens(self, tmp_path):
        _make_py_file(tmp_path / "a.py")
        updater = IncrementalUpdater(tmp_path)  # db_path=None → auto
        updater.init(full=True)
        assert (tmp_path / ".codelens" / "index.db").exists()
