"""
Hybrid retrieval pipeline for CodeLens Phase 3.

Algorithm:
  1. Semantic recall   — vector_store.query(query, k=20)
  2. Re-rank           — score *= (1 + importance[file])
  3. Neighbor expand   — add 1-hop graph neighbors for top-N files
  4. Pack context      — importance-descending, respects token budget
"""
from __future__ import annotations
from codelens.models import RepoSkeleton, FileSkeleton


def retrieve(
    query: str,
    vector_store,
    graph,
    importance: dict[str, float],
    skeleton: RepoSkeleton,
    k: int = 20,
    top_expand: int = 5,
    max_tokens: int = 4000,
) -> str:
    """
    Run the full hybrid retrieval pipeline and return a packed context string.

    Args:
        query:        Natural-language query from the user/agent.
        vector_store: VectorStore instance (already populated).
        graph:        nx.DiGraph from build_graph().
        importance:   dict[path, float] from compute_importance().
        skeleton:     RepoSkeleton from extract_repo().
        k:            Number of semantic candidates to recall.
        top_expand:   Number of top-ranked files whose graph neighbors to add.
        max_tokens:   Approximate token budget for the output string.

    Returns:
        Packed context string ready for LLM consumption.
    """
    file_set = {f.path for f in skeleton.files}

    # Step 1: Semantic recall
    hits = vector_store.query(query, k=k)

    # Step 2: Re-rank — combine semantic similarity with structural importance
    file_scores: dict[str, float] = {}
    for hit in hits:
        path = hit["path"]
        semantic = float(hit.get("score", 0.0))
        structural = importance.get(path, 0.0)
        combined = semantic * (1.0 + structural)
        if path not in file_scores or combined > file_scores[path]:
            file_scores[path] = combined

    ranked = sorted(file_scores.items(), key=lambda x: -x[1])

    # Step 3: Expand top-N files with 1-hop graph neighbors
    top_paths = [p for p, _ in ranked[:top_expand]]
    expanded: set[str] = set()
    for path in top_paths:
        if graph.has_node(path):
            for neighbor in list(graph.predecessors(path)) + list(graph.successors(path)):
                if neighbor in file_set and neighbor not in file_scores:
                    expanded.add(neighbor)

    # Merge: ranked files first, then expanded neighbors ordered by importance
    all_paths: list[tuple[str, float]] = list(ranked)
    already = {p for p, _ in all_paths}
    for path in sorted(expanded, key=lambda p: -importance.get(p, 0.0)):
        if path not in already:
            all_paths.append((path, importance.get(path, 0.0)))

    # Step 4: Pack context
    file_map = {f.path: f for f in skeleton.files}
    return pack_context(
        [(p, s) for p, s in all_paths if p in file_map],
        file_map,
        max_tokens=max_tokens,
    )


def pack_context(
    ranked_paths: list[tuple[str, float]],
    file_map: dict[str, FileSkeleton],
    max_tokens: int = 4000,
) -> str:
    """
    Pack file skeletons into a context string within the token budget.

    Files are included in descending score order. Each file gets a compact
    block showing its public symbols.  Stops when the budget is exhausted.
    """
    char_budget = max_tokens * 4
    blocks: list[str] = []
    used = 0

    for path, score in ranked_paths:
        f = file_map.get(path)
        if f is None:
            continue
        block = _file_block(f, score)
        cost = len(block) + 2  # +2 for the blank line separator
        if used + cost > char_budget:
            remaining = len(ranked_paths) - len(blocks)
            blocks.append(f"# … {remaining} more files omitted (budget reached)")
            break
        blocks.append(block)
        used += cost

    return "\n\n".join(blocks)


def _file_block(f: FileSkeleton, score: float) -> str:
    """Single-file compact block: header + public symbol signatures."""
    header = f"## {f.path}  [{f.loc} loc, score={score:.3f}]"
    pub = [s for s in f.symbols if not s.name.startswith("_")]
    sym_lines = [f"  {s.signature}" for s in pub[:10]]
    if len(pub) > 10:
        sym_lines.append(f"  … +{len(pub) - 10} more symbols")
    return "\n".join([header] + sym_lines)
