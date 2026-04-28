"""Tests for codelens.graph — dependency graph and importance scoring."""
from __future__ import annotations
import pytest
from pathlib import Path
from codelens.models import RepoSkeleton, FileSkeleton, ImportEntry, SymbolEntry
from codelens.graph import build_graph, compute_importance, assign_tiers, build_and_score


def _make_file(path: str, imports: list[str] | None = None, loc: int = 100,
               is_entrypoint: bool = False) -> FileSkeleton:
    imp_entries = [
        ImportEntry(**{"from": p, "symbols": []}) for p in (imports or [])
    ]
    return FileSkeleton(
        path=path,
        language="python",
        imports=imp_entries,
        loc=loc,
        is_entrypoint=is_entrypoint,
    )


def _make_skeleton(repo_path: str, files: list[FileSkeleton]) -> RepoSkeleton:
    return RepoSkeleton(
        repo_path=repo_path,
        files=files,
        total_files=len(files),
    )


class TestBuildGraph:
    def test_nodes_equal_files(self, tmp_path):
        (tmp_path / "a.py").write_text("")
        (tmp_path / "b.py").write_text("")
        skeleton = _make_skeleton(str(tmp_path), [
            _make_file("a.py"),
            _make_file("b.py"),
        ])
        g = build_graph(skeleton)
        assert set(g.nodes) == {"a.py", "b.py"}

    def test_edge_from_import(self, tmp_path):
        (tmp_path / "main.py").write_text("")
        (tmp_path / "utils.py").write_text("")
        skeleton = _make_skeleton(str(tmp_path), [
            _make_file("main.py", imports=["./utils"]),
            _make_file("utils.py"),
        ])
        g = build_graph(skeleton)
        # main.py imports utils.py → edge main.py -> utils.py
        assert g.has_edge("main.py", "utils.py")

    def test_external_import_not_an_edge(self, tmp_path):
        (tmp_path / "main.py").write_text("")
        skeleton = _make_skeleton(str(tmp_path), [
            _make_file("main.py", imports=["os", "numpy"]),
        ])
        g = build_graph(skeleton)
        assert g.number_of_edges() == 0

    def test_unresolved_import_not_an_edge(self, tmp_path):
        (tmp_path / "main.py").write_text("")
        skeleton = _make_skeleton(str(tmp_path), [
            _make_file("main.py", imports=["./nonexistent"]),
        ])
        g = build_graph(skeleton)
        assert g.number_of_edges() == 0

    def test_node_metadata(self, tmp_path):
        (tmp_path / "app.py").write_text("")
        skeleton = _make_skeleton(str(tmp_path), [
            _make_file("app.py", loc=500, is_entrypoint=True),
        ])
        g = build_graph(skeleton)
        assert g.nodes["app.py"]["loc"] == 500
        assert g.nodes["app.py"]["is_entrypoint"] is True


class TestComputeImportance:
    def test_hub_file_scores_higher(self, tmp_path):
        """A file imported by many should score higher than one imported by none."""
        for name in ["a.py", "b.py", "c.py", "shared.py"]:
            (tmp_path / name).write_text("")

        skeleton = _make_skeleton(str(tmp_path), [
            _make_file("a.py", imports=["./shared"]),
            _make_file("b.py", imports=["./shared"]),
            _make_file("c.py", imports=["./shared"]),
            _make_file("shared.py"),
        ])
        g = build_graph(skeleton)
        scores = compute_importance(g, skeleton)

        assert scores["shared.py"] > scores["a.py"]
        assert scores["shared.py"] > scores["b.py"]
        assert scores["shared.py"] > scores["c.py"]

    def test_scores_in_unit_range(self, tmp_path):
        for name in ["x.py", "y.py"]:
            (tmp_path / name).write_text("")
        skeleton = _make_skeleton(str(tmp_path), [
            _make_file("x.py", imports=["./y"]),
            _make_file("y.py"),
        ])
        g = build_graph(skeleton)
        scores = compute_importance(g, skeleton)
        for s in scores.values():
            assert 0.0 <= s <= 1.0

    def test_empty_graph_returns_empty(self, tmp_path):
        skeleton = _make_skeleton(str(tmp_path), [])
        g = build_graph(skeleton)
        scores = compute_importance(g, skeleton)
        assert scores == {}

    def test_entrypoint_gets_bonus(self, tmp_path):
        for name in ["entry.py", "lib.py"]:
            (tmp_path / name).write_text("")
        skeleton = _make_skeleton(str(tmp_path), [
            _make_file("entry.py", is_entrypoint=True, loc=50),
            _make_file("lib.py", is_entrypoint=False, loc=50),
        ])
        g = build_graph(skeleton)
        scores = compute_importance(g, skeleton)
        # entry.py has no imports pointing to it but has entrypoint bonus
        assert scores["entry.py"] >= scores["lib.py"]


class TestAssignTiers:
    def test_tier_names_valid(self, tmp_path):
        importance = {"a.py": 1.0, "b.py": 0.7, "c.py": 0.4, "d.py": 0.1}
        tiers = assign_tiers(importance)
        valid = {"core", "important", "supporting", "peripheral"}
        assert all(t in valid for t in tiers.values())

    def test_top_file_is_core(self):
        importance = {f"f{i}.py": i / 10 for i in range(11)}
        tiers = assign_tiers(importance)
        assert tiers["f10.py"] == "core"

    def test_bottom_file_is_peripheral(self):
        importance = {f"f{i}.py": i / 10 for i in range(11)}
        tiers = assign_tiers(importance)
        assert tiers["f0.py"] == "peripheral"

    def test_empty_returns_empty(self):
        assert assign_tiers({}) == {}


class TestBuildAndScore:
    def test_returns_three_tuple(self, tmp_path):
        (tmp_path / "a.py").write_text("")
        skeleton = _make_skeleton(str(tmp_path), [_make_file("a.py")])
        graph, importance, tiers = build_and_score(skeleton)
        assert "a.py" in graph.nodes
        assert "a.py" in importance
        assert "a.py" in tiers
