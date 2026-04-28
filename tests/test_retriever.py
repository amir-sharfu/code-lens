"""Tests for codelens.retriever — hybrid retrieval pipeline."""
from __future__ import annotations
import pytest
from unittest.mock import MagicMock
import networkx as nx

from codelens.models import RepoSkeleton, FileSkeleton, SymbolEntry, ImportEntry
from codelens.retriever import retrieve, pack_context, _file_block


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sym(name: str, sig: str | None = None) -> SymbolEntry:
    return SymbolEntry(
        kind="function",
        name=name,
        signature=sig or f"def {name}()",
        line=1,
    )


def _file(path: str, loc: int = 100, symbols: list[SymbolEntry] | None = None) -> FileSkeleton:
    return FileSkeleton(
        path=path,
        language="python",
        imports=[],
        exports=[],
        symbols=symbols or [],
        loc=loc,
    )


def _skeleton(*files: FileSkeleton) -> RepoSkeleton:
    return RepoSkeleton(
        repo_path="/repo",
        files=list(files),
        total_files=len(files),
    )


def _mock_vs(hits: list[dict]) -> MagicMock:
    vs = MagicMock()
    vs.query.return_value = hits
    return vs


def _empty_graph() -> nx.DiGraph:
    return nx.DiGraph()


def _graph_with_edge(src: str, dst: str) -> nx.DiGraph:
    g = nx.DiGraph()
    g.add_edge(src, dst)
    return g


# ---------------------------------------------------------------------------
# _file_block
# ---------------------------------------------------------------------------

class TestFileBlock:
    def test_header_contains_path_and_loc(self):
        f = _file("a/b.py", loc=42)
        block = _file_block(f, score=0.75)
        assert "a/b.py" in block
        assert "42 loc" in block
        assert "0.750" in block

    def test_private_symbols_excluded(self):
        f = _file("x.py", symbols=[_sym("_private"), _sym("public")])
        block = _file_block(f, score=0.0)
        assert "_private" not in block
        assert "public" in block

    def test_overflow_shows_more_count(self):
        syms = [_sym(f"fn{i}") for i in range(15)]
        f = _file("x.py", symbols=syms)
        block = _file_block(f, score=0.0)
        assert "+5 more symbols" in block

    def test_no_symbols_only_header(self):
        f = _file("x.py", symbols=[])
        block = _file_block(f, score=0.5)
        assert "x.py" in block


# ---------------------------------------------------------------------------
# pack_context
# ---------------------------------------------------------------------------

class TestPackContext:
    def test_returns_string(self):
        fa = _file("a.py")
        file_map = {"a.py": fa}
        result = pack_context([("a.py", 1.0)], file_map)
        assert isinstance(result, str)

    def test_higher_score_appears_first(self):
        fa, fb = _file("a.py"), _file("b.py")
        file_map = {"a.py": fa, "b.py": fb}
        result = pack_context([("a.py", 0.9), ("b.py", 0.2)], file_map)
        assert result.index("a.py") < result.index("b.py")

    def test_budget_respected(self):
        # Make one very large file description by giving it many symbols
        syms = [_sym(f"function_with_long_name_{i}") for i in range(50)]
        fa = _file("big.py", loc=5000, symbols=syms)
        fb = _file("small.py", loc=10)
        file_map = {"big.py": fa, "small.py": fb}
        # Very tight budget: only enough for 1 file
        result = pack_context(
            [("big.py", 1.0), ("small.py", 0.5)],
            file_map,
            max_tokens=20,
        )
        assert "omitted" in result

    def test_skips_missing_paths(self):
        fa = _file("a.py")
        file_map = {"a.py": fa}
        result = pack_context([("a.py", 1.0), ("missing.py", 0.5)], file_map)
        assert "missing.py" not in result

    def test_empty_paths_returns_empty_string(self):
        assert pack_context([], {}) == ""


# ---------------------------------------------------------------------------
# retrieve
# ---------------------------------------------------------------------------

class TestRetrieve:
    def test_returns_string(self):
        fa = _file("a.py")
        skel = _skeleton(fa)
        vs = _mock_vs([{"path": "a.py", "score": 0.9}])
        result = retrieve("query", vs, _empty_graph(), {"a.py": 0.5}, skel)
        assert isinstance(result, str)

    def test_re_ranks_by_importance(self):
        """File b.py has same semantic score but higher importance → ranked first."""
        fa = _file("a.py", symbols=[_sym("fn_a")])
        fb = _file("b.py", symbols=[_sym("fn_b")])
        skel = _skeleton(fa, fb)
        vs = _mock_vs([
            {"path": "a.py", "score": 0.8},
            {"path": "b.py", "score": 0.8},
        ])
        importance = {"a.py": 0.1, "b.py": 0.9}
        result = retrieve("query", vs, _empty_graph(), importance, skel)
        assert result.index("b.py") < result.index("a.py")

    def test_expands_graph_neighbors(self):
        """A neighbor of a top hit should appear in the context."""
        fa = _file("a.py", symbols=[_sym("fn_a")])
        fc = _file("c.py", symbols=[_sym("fn_c")])  # neighbor, not in hits
        skel = _skeleton(fa, fc)
        g = _graph_with_edge("a.py", "c.py")
        vs = _mock_vs([{"path": "a.py", "score": 0.9}])
        result = retrieve("query", vs, g, {"a.py": 0.8, "c.py": 0.3}, skel,
                          top_expand=1)
        assert "c.py" in result

    def test_empty_hits_returns_string(self):
        """No vector hits → empty or near-empty context, no crash."""
        skel = _skeleton(_file("a.py"))
        vs = _mock_vs([])
        result = retrieve("query", vs, _empty_graph(), {}, skel)
        assert isinstance(result, str)

    def test_file_not_in_skeleton_ignored(self):
        """Vector hits pointing to unknown paths don't crash retrieve."""
        skel = _skeleton(_file("known.py", symbols=[_sym("fn")]))
        vs = _mock_vs([{"path": "unknown.py", "score": 0.9}])
        result = retrieve("query", vs, _empty_graph(), {}, skel)
        assert "unknown.py" not in result

    def test_neighbors_not_duplicated(self):
        """A file that's both a hit and a neighbor appears only once."""
        fa = _file("a.py", symbols=[_sym("fn_a")])
        fb = _file("b.py", symbols=[_sym("fn_b")])
        skel = _skeleton(fa, fb)
        g = _graph_with_edge("a.py", "b.py")
        vs = _mock_vs([
            {"path": "a.py", "score": 0.9},
            {"path": "b.py", "score": 0.7},
        ])
        result = retrieve("query", vs, g, {"a.py": 0.8, "b.py": 0.6}, skel)
        assert result.count("b.py") == 1

    def test_predecessor_neighbors_included(self):
        """Files that import a top hit (predecessors) are also expanded."""
        fa = _file("a.py", symbols=[_sym("fn_a")])
        fp = _file("parent.py", symbols=[_sym("fn_p")])
        skel = _skeleton(fa, fp)
        g = _graph_with_edge("parent.py", "a.py")  # parent imports a
        vs = _mock_vs([{"path": "a.py", "score": 0.9}])
        result = retrieve("query", vs, g, {"a.py": 0.8, "parent.py": 0.4}, skel,
                          top_expand=1)
        assert "parent.py" in result
