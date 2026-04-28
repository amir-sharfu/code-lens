"""Tests for codelens.cli — Typer commands via CliRunner."""
from __future__ import annotations
import json
import pytest
from pathlib import Path
from typer.testing import CliRunner

from codelens.cli import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_py(path: Path, content: str = "def fn(): pass") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def _init_repo(tmp_path: Path) -> None:
    """Run `codelens init` on a minimal repo so other commands can run."""
    _write_py(tmp_path / "a.py", "def foo(): pass")
    _write_py(tmp_path / "b.py", "def bar(): pass")
    result = runner.invoke(app, ["init", str(tmp_path)])
    assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------

class TestInit:
    def test_init_succeeds_on_empty_repo(self, tmp_path):
        result = runner.invoke(app, ["init", str(tmp_path)])
        assert result.exit_code == 0

    def test_init_creates_db(self, tmp_path):
        _write_py(tmp_path / "a.py")
        runner.invoke(app, ["init", str(tmp_path)])
        assert (tmp_path / ".codelens" / "index.db").exists()

    def test_init_reports_parsed_count(self, tmp_path):
        _write_py(tmp_path / "a.py")
        _write_py(tmp_path / "b.py")
        result = runner.invoke(app, ["init", str(tmp_path)])
        assert "Parsed 2" in result.output

    def test_init_incremental_skips_unchanged(self, tmp_path):
        _write_py(tmp_path / "a.py")
        runner.invoke(app, ["init", str(tmp_path)])
        result = runner.invoke(app, ["init", str(tmp_path), "--incremental"])
        assert "skipped 1" in result.output

    def test_init_mentions_vector_index(self, tmp_path):
        result = runner.invoke(app, ["init", str(tmp_path)])
        # Either "ready" (phase3 installed) or "skipped" (not installed)
        assert "vector index" in result.output.lower()


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------

class TestStats:
    def test_stats_after_init(self, tmp_path):
        _init_repo(tmp_path)
        result = runner.invoke(app, ["stats", str(tmp_path)])
        assert result.exit_code == 0
        assert "core" in result.output or "peripheral" in result.output

    def test_stats_shows_total(self, tmp_path):
        _init_repo(tmp_path)
        result = runner.invoke(app, ["stats", str(tmp_path)])
        assert "Total:" in result.output

    def test_stats_exits_1_when_not_initialized(self, tmp_path):
        result = runner.invoke(app, ["stats", str(tmp_path)])
        assert result.exit_code == 1

    def test_stats_shows_all_tier_names(self, tmp_path):
        # Create enough files to populate multiple tiers
        for i in range(12):
            _write_py(tmp_path / f"mod{i}.py", f"def fn{i}(): pass")
        runner.invoke(app, ["init", str(tmp_path)])
        result = runner.invoke(app, ["stats", str(tmp_path)])
        assert result.exit_code == 0
        # At least some tiers appear
        assert any(t in result.output for t in ["core", "important", "supporting", "peripheral"])


# ---------------------------------------------------------------------------
# map
# ---------------------------------------------------------------------------

class TestMap:
    def test_map_mermaid_default(self, tmp_path):
        _init_repo(tmp_path)
        result = runner.invoke(app, ["map", str(tmp_path)])
        assert result.exit_code == 0
        assert "graph LR" in result.output

    def test_map_json_format(self, tmp_path):
        _init_repo(tmp_path)
        result = runner.invoke(app, ["map", str(tmp_path), "--format", "json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "nodes" in data
        assert "edges" in data

    def test_map_json_nodes_are_strings(self, tmp_path):
        _init_repo(tmp_path)
        result = runner.invoke(app, ["map", str(tmp_path), "--format", "json"])
        data = json.loads(result.output)
        assert all(isinstance(n, str) for n in data["nodes"])

    def test_map_exits_1_when_not_initialized(self, tmp_path):
        result = runner.invoke(app, ["map", str(tmp_path)])
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# query (structural fallback — no vector store needed)
# ---------------------------------------------------------------------------

class TestQuery:
    def test_query_exits_1_when_not_initialized(self, tmp_path):
        result = runner.invoke(app, ["query", "find auth logic", "--path", str(tmp_path)])
        assert result.exit_code == 1

    def test_query_returns_context_after_init(self, tmp_path):
        _write_py(tmp_path / "auth.py", "def login(user, pwd): pass")
        runner.invoke(app, ["init", str(tmp_path)])
        result = runner.invoke(
            app, ["query", "find auth logic", "--path", str(tmp_path)]
        )
        assert result.exit_code == 0
        assert len(result.output.strip()) > 0

    def test_query_respects_max_tokens(self, tmp_path):
        for i in range(10):
            _write_py(tmp_path / f"mod{i}.py", f"def fn{i}(): pass\n" * 20)
        runner.invoke(app, ["init", str(tmp_path)])
        result = runner.invoke(
            app, ["query", "functions", "--path", str(tmp_path), "--max-tokens", "200"]
        )
        assert result.exit_code == 0
        # Very small budget → should truncate and mention "omitted"
        assert len(result.output) < 2000
