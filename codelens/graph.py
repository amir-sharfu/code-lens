"""
Dependency graph construction and importance scoring via PageRank.

Replaces the proxy _score_file() heuristic from compact_repr with a
structural importance score derived from actual import relationships.
"""
from __future__ import annotations
from codelens.models import RepoSkeleton, FileSkeleton
from codelens.resolver import resolve

try:
    import networkx as nx
except ImportError as e:
    raise ImportError("networkx is required for Phase 2: pip install networkx>=3.2") from e


_TIER_THRESHOLDS = {
    "core": 0.90,
    "important": 0.70,
    "supporting": 0.40,
}


def build_graph(skeleton: RepoSkeleton) -> "nx.DiGraph":
    """
    Build a directed import graph from a RepoSkeleton.

    Nodes are repo-relative POSIX paths (one per file in the skeleton).
    An edge A -> B means file A imports from file B.
    """
    graph: nx.DiGraph = nx.DiGraph()

    # Add all files as nodes with metadata
    for f in skeleton.files:
        graph.add_node(f.path, language=f.language, loc=f.loc, is_entrypoint=f.is_entrypoint)

    # Add edges from imports
    repo_root = skeleton.repo_path
    file_set = {f.path for f in skeleton.files}

    for f in skeleton.files:
        for imp in f.imports:
            resolved = resolve(imp.from_, f.path, repo_root)
            if resolved and resolved in file_set:
                graph.add_edge(f.path, resolved)

    return graph


def _pagerank_python(
    graph: "nx.DiGraph",
    alpha: float = 0.85,
    max_iter: int = 100,
    tol: float = 1e-4,
) -> dict[str, float]:
    """Pure-Python power-iteration PageRank. No scipy/numpy required."""
    n = len(graph)
    if n == 0:
        return {}

    nodes = list(graph.nodes)
    rank = {node: 1.0 / n for node in nodes}

    # Precompute out-degrees for dangling node handling
    out_degree = {node: graph.out_degree(node) for node in nodes}
    dangling_nodes = [node for node in nodes if out_degree[node] == 0]

    for _ in range(max_iter):
        prev = rank.copy()
        dangling_sum = alpha * sum(prev[node] for node in dangling_nodes) / n

        new_rank: dict[str, float] = {}
        for node in nodes:
            # Sum contributions from all predecessors
            incoming = sum(
                prev[pred] / out_degree[pred]
                for pred in graph.predecessors(node)
                if out_degree[pred] > 0
            )
            new_rank[node] = alpha * incoming + dangling_sum + (1.0 - alpha) / n

        # Normalize
        total = sum(new_rank.values())
        if total > 0:
            new_rank = {k: v / total for k, v in new_rank.items()}

        # Check convergence
        err = sum(abs(new_rank[n] - prev[n]) for n in nodes)
        rank = new_rank
        if err < tol:
            break

    return rank


def compute_importance(graph: "nx.DiGraph", skeleton: RepoSkeleton) -> dict[str, float]:
    """
    Compute a composite importance score for each file.

    Formula:
        0.5 * pagerank
      + 0.2 * normalized_in_degree
      + 0.2 * normalized_loc
      + 0.1 * is_entrypoint

    All components are normalized to [0, 1].

    Returns a dict mapping file path -> importance score in [0, 1].
    """
    if len(graph) == 0:
        return {}

    # PageRank on the reversed graph: files that are imported by many get high rank.
    # Edge convention: A->B means "A imports B". Files that ARE imported (in-demand)
    # should score high, so reverse the graph before running PageRank.
    reversed_graph = graph.reverse(copy=False)
    pagerank: dict[str, float] = _pagerank_python(reversed_graph, alpha=0.85)

    # Normalize in-degree (number of files that import this file)
    in_degrees = dict(graph.in_degree())
    max_in = max(in_degrees.values(), default=1) or 1

    # Normalize LOC
    loc_map = {f.path: f.loc for f in skeleton.files}
    max_loc = max(loc_map.values(), default=1) or 1

    # Entrypoint flag
    entrypoint_set = {f.path for f in skeleton.files if f.is_entrypoint}

    scores: dict[str, float] = {}
    for path in graph.nodes:
        pr = pagerank.get(path, 0.0)
        in_deg_norm = in_degrees.get(path, 0) / max_in
        loc_norm = loc_map.get(path, 0) / max_loc
        ep = 1.0 if path in entrypoint_set else 0.0

        scores[path] = (
            0.5 * pr * len(graph)  # scale PageRank from (0,1/N) range toward (0,1)
            + 0.2 * in_deg_norm
            + 0.2 * loc_norm
            + 0.1 * ep
        )

    # Clamp to [0, 1]
    max_score = max(scores.values(), default=1.0) or 1.0
    return {path: min(score / max_score, 1.0) for path, score in scores.items()}


def assign_tiers(importance: dict[str, float]) -> dict[str, str]:
    """
    Assign a qualitative tier to each file based on its importance percentile.

    Tiers:
        core        >= 90th percentile
        important   >= 70th percentile
        supporting  >= 40th percentile
        peripheral  < 40th percentile
    """
    if not importance:
        return {}

    sorted_scores = sorted(importance.values())
    n = len(sorted_scores)

    def percentile_threshold(pct: float) -> float:
        idx = int(pct * n)
        idx = min(idx, n - 1)
        return sorted_scores[idx]

    p90 = percentile_threshold(0.90)
    p70 = percentile_threshold(0.70)
    p40 = percentile_threshold(0.40)

    tiers: dict[str, str] = {}
    for path, score in importance.items():
        if score >= p90:
            tiers[path] = "core"
        elif score >= p70:
            tiers[path] = "important"
        elif score >= p40:
            tiers[path] = "supporting"
        else:
            tiers[path] = "peripheral"

    return tiers


def build_and_score(skeleton: RepoSkeleton) -> tuple["nx.DiGraph", dict[str, float], dict[str, str]]:
    """
    Convenience wrapper: build graph, compute importance, assign tiers.

    Returns:
        (graph, importance_scores, tier_map)
    """
    graph = build_graph(skeleton)
    importance = compute_importance(graph, skeleton)
    tiers = assign_tiers(importance)
    return graph, importance, tiers
