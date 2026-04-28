"""Tests for codelens.mcp_server — tool helper functions (no MCP protocol needed)."""
from __future__ import annotations
import json
import pytest
from pathlib import Path
from sqlalchemy.orm import Session

from codelens.config import CodeLensConfig
from codelens.db.schema import get_engine, create_tables, FileRecord, DependencyRecord
from codelens.db.repository import FileRepository
from codelens.mcp_server import (
    _bfs_subgraph,
    _skeleton_from_db,
    _graph_from_db,
    get_file_skeleton_impl,
    get_dependency_subgraph_impl,
    get_relevant_files_impl,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _write_py(path: Path, content: str = "def fn(): pass") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


@pytest.fixture()
def initialized_repo(tmp_path):
    """A repo with 3 .py files that have been indexed via IncrementalUpdater."""
    _write_py(tmp_path / "main.py", "from . import utils\ndef main(): pass")
    _write_py(tmp_path / "utils.py", "def helper(): pass")
    _write_py(tmp_path / "models.py", "class User: pass")
    from codelens.db.incremental import IncrementalUpdater
    updater = IncrementalUpdater(tmp_path, db_path=tmp_path / "test.db")
    updater.init(full=True)
    return tmp_path, tmp_path / "test.db"


@pytest.fixture()
def cfg(initialized_repo) -> CodeLensConfig:
    repo_path, db_path = initialized_repo
    c = CodeLensConfig.for_repo(repo_path)
    c.db_path.__class__  # just ensure it's a Path
    # Override db_path to point to our test DB
    from dataclasses import replace
    return CodeLensConfig(
        repo_path=repo_path,
        db_path=db_path,
        chroma_dir=repo_path / ".codelens" / "chroma",
        embedding_backend="local",
        embed_model="BAAI/bge-small-en-v1.5",
    )


# ---------------------------------------------------------------------------
# _bfs_subgraph
# ---------------------------------------------------------------------------

class TestBfsSubgraph:
    def _make_graph(self):
        import networkx as nx
        g = nx.DiGraph()
        g.add_edge("a.py", "b.py")
        g.add_edge("b.py", "c.py")
        g.add_edge("x.py", "y.py")  # disconnected component
        return g

    def test_depth_0_returns_only_root(self):
        g = self._make_graph()
        nodes, edges = _bfs_subgraph(g, "a.py", depth=0)
        assert nodes == ["a.py"]
        assert edges == []

    def test_depth_1_includes_direct_neighbours(self):
        g = self._make_graph()
        nodes, _ = _bfs_subgraph(g, "a.py", depth=1)
        assert "b.py" in nodes
        assert "a.py" in nodes
        assert "c.py" not in nodes

    def test_depth_2_includes_two_hops(self):
        g = self._make_graph()
        nodes, _ = _bfs_subgraph(g, "a.py", depth=2)
        assert "c.py" in nodes

    def test_disconnected_component_excluded(self):
        g = self._make_graph()
        nodes, _ = _bfs_subgraph(g, "a.py", depth=5)
        assert "x.py" not in nodes
        assert "y.py" not in nodes

    def test_edges_only_within_visited_nodes(self):
        g = self._make_graph()
        nodes, edges = _bfs_subgraph(g, "a.py", depth=1)
        for u, v in edges:
            assert u in nodes
            assert v in nodes

    def test_nodes_sorted(self):
        g = self._make_graph()
        nodes, _ = _bfs_subgraph(g, "a.py", depth=2)
        assert nodes == sorted(nodes)


# ---------------------------------------------------------------------------
# get_file_skeleton_impl
# ---------------------------------------------------------------------------

class TestGetFileSkeleton:
    def test_returns_json_string(self, cfg):
        with Session(get_engine(cfg.db_path)) as session:
            paths = [r.path for r in FileRepository(session).get_all()]
        assert paths, "No files indexed"
        result = get_file_skeleton_impl(paths[0], cfg)
        data = json.loads(result)
        assert "path" in data

    def test_unknown_path_returns_error_json(self, cfg):
        result = get_file_skeleton_impl("nonexistent.py", cfg)
        data = json.loads(result)
        assert "error" in data

    def test_not_initialized_returns_error(self, tmp_path):
        cfg_uninit = CodeLensConfig.for_repo(tmp_path)
        result = get_file_skeleton_impl("a.py", cfg_uninit)
        data = json.loads(result)
        assert "error" in data


# ---------------------------------------------------------------------------
# get_dependency_subgraph_impl
# ---------------------------------------------------------------------------

class TestGetDependencySubgraph:
    def test_returns_json_with_nodes_and_edges(self, cfg):
        with Session(get_engine(cfg.db_path)) as session:
            paths = [r.path for r in FileRepository(session).get_all()]
        result = get_dependency_subgraph_impl(paths[0], cfg, depth=1)
        data = json.loads(result)
        assert "nodes" in data
        assert "edges" in data

    def test_root_always_in_nodes(self, cfg):
        with Session(get_engine(cfg.db_path)) as session:
            paths = [r.path for r in FileRepository(session).get_all()]
        result = get_dependency_subgraph_impl(paths[0], cfg, depth=0)
        data = json.loads(result)
        assert paths[0] in data["nodes"]

    def test_unknown_file_returns_error(self, cfg):
        result = get_dependency_subgraph_impl("ghost.py", cfg, depth=1)
        data = json.loads(result)
        assert "error" in data

    def test_not_initialized_returns_error(self, tmp_path):
        cfg_uninit = CodeLensConfig.for_repo(tmp_path)
        result = get_dependency_subgraph_impl("a.py", cfg_uninit)
        data = json.loads(result)
        assert "error" in data


# ---------------------------------------------------------------------------
# get_relevant_files_impl
# ---------------------------------------------------------------------------

class TestGetRelevantFiles:
    def test_returns_string(self, cfg):
        result = get_relevant_files_impl("find utility functions", cfg)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_not_initialized_returns_message(self, tmp_path):
        cfg_uninit = CodeLensConfig.for_repo(tmp_path)
        result = get_relevant_files_impl("query", cfg_uninit)
        assert "not initialised" in result.lower() or "init" in result.lower()

    def test_respects_max_tokens(self, cfg):
        result = get_relevant_files_impl("query", cfg, max_tokens=100)
        # 100 tokens * 4 chars = 400 char budget → output should be small
        assert len(result) < 2000

    def test_output_contains_file_path(self, cfg):
        result = get_relevant_files_impl("utility helper", cfg, max_tokens=4000)
        # At least one .py path should appear somewhere in the output
        assert ".py" in result


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------

class TestCodeLensConfig:
    def test_for_repo_defaults_to_cwd(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        from codelens.config import CodeLensConfig
        cfg = CodeLensConfig.for_repo()
        assert cfg.repo_path == tmp_path.resolve()

    def test_is_initialized_false_before_init(self, tmp_path):
        cfg = CodeLensConfig.for_repo(tmp_path)
        assert not cfg.is_initialized

    def test_is_initialized_true_after_init(self, tmp_path):
        _write_py(tmp_path / "a.py")
        from codelens.db.incremental import IncrementalUpdater
        IncrementalUpdater(tmp_path).init(full=True)
        cfg = CodeLensConfig.for_repo(tmp_path)
        assert cfg.is_initialized

    def test_env_var_overrides_embedding_backend(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CODELENS_EMBEDDING_BACKEND", "openai")
        cfg = CodeLensConfig.for_repo(tmp_path)
        assert cfg.embedding_backend == "openai"
