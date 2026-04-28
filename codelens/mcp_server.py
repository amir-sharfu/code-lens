"""
CodeLens MCP stdio server — exposes three tools for Claude Desktop / Cursor.

Tools:
  get_relevant_files(query, max_tokens?)    → packed context string
  get_file_skeleton(path)                   → FileSkeleton JSON
  get_dependency_subgraph(file, depth?)     → nodes + edges JSON

Start with:
  python -m codelens.mcp_server
or configure in claude_desktop_config.json / .cursor/mcp.json.

The repo path is read from CODELENS_REPO_PATH env var (defaults to cwd).
"""
from __future__ import annotations
import json
import os
import posixpath
from pathlib import Path

from codelens.config import CodeLensConfig


def _is_safe_repo_path(path: str) -> bool:
    """Return True only when path is a relative, non-traversing POSIX path."""
    if not path or posixpath.isabs(path):
        return False
    return not posixpath.normpath(path).startswith("..")
from codelens.db.repository import FileRepository, DependencyRepository
from codelens.db.schema import get_engine, create_tables, DependencyRecord
from codelens.models import FileSkeleton, RepoSkeleton


# ---------------------------------------------------------------------------
# Pure helper functions (testable without MCP protocol)
# ---------------------------------------------------------------------------

def _open_session(cfg: CodeLensConfig):
    from sqlalchemy.orm import Session
    engine = get_engine(cfg.db_path)
    return Session(engine)


def _skeleton_from_db(session, repo_path: Path) -> RepoSkeleton:
    records = FileRepository(session).get_all()
    files: list[FileSkeleton] = []
    for rec in records:
        try:
            files.append(FileSkeleton.model_validate(json.loads(rec.skeleton_json)))
        except Exception:
            continue
    return RepoSkeleton(
        repo_path=str(repo_path),
        files=files,
        total_files=len(files),
    )


def _graph_from_db(session) -> "nx.DiGraph":
    import networkx as nx
    g: nx.DiGraph = nx.DiGraph()
    for rec in FileRepository(session).get_all():
        g.add_node(rec.path, tier=rec.tier, importance=rec.importance_score)
    for dep in session.query(DependencyRecord).all():
        g.add_edge(dep.from_file, dep.to_file)
    return g


def _bfs_subgraph(graph, root: str, depth: int) -> tuple[list[str], list[list[str]]]:
    """Return (nodes, edges) for a BFS neighbourhood of radius `depth`."""
    visited: set[str] = {root}
    frontier: set[str] = {root}

    for _ in range(depth):
        next_frontier: set[str] = set()
        for node in frontier:
            if not graph.has_node(node):
                continue
            for nb in list(graph.predecessors(node)) + list(graph.successors(node)):
                if nb not in visited:
                    next_frontier.add(nb)
                    visited.add(nb)
        frontier = next_frontier

    edges = [
        [u, v]
        for u, v in graph.edges()
        if u in visited and v in visited
    ]
    return sorted(visited), edges


# ---------------------------------------------------------------------------
# Tool implementations (called by both MCP handler and tests)
# ---------------------------------------------------------------------------

def get_relevant_files_impl(
    query: str,
    cfg: CodeLensConfig,
    max_tokens: int = 4000,
) -> str:
    """
    Hybrid retrieval: semantic search (if vector store available) →
    re-rank by importance → neighbor expand → pack context.
    Falls back to structural compact_repr when phase3 deps are missing.
    """
    if not cfg.is_initialized:
        return "[codelens] Repository not initialised. Run `codelens init` first."

    with _open_session(cfg) as session:
        importance = {r.path: r.importance_score for r in FileRepository(session).get_all()}
        skeleton = _skeleton_from_db(session, cfg.repo_path)
        graph = _graph_from_db(session)

    try:
        from codelens.vector_store import VectorStore
        from codelens.embeddings import get_embedding_backend
        from codelens.retriever import retrieve

        backend = get_embedding_backend(cfg.embedding_backend)
        vs = VectorStore(persist_dir=cfg.chroma_dir, backend=backend)
        if vs.count == 0:
            raise RuntimeError("empty")
        return retrieve(query, vs, graph, importance, skeleton, max_tokens=max_tokens)
    except (ImportError, RuntimeError):
        from codelens.compact_repr import compact_repr
        return compact_repr(skeleton, importance=importance, token_budget=max_tokens)


def get_file_skeleton_impl(path: str, cfg: CodeLensConfig) -> str:
    """Return the stored FileSkeleton JSON for a repo-relative path."""
    if not cfg.is_initialized:
        return json.dumps({"error": "not initialised"})

    with _open_session(cfg) as session:
        rec = FileRepository(session).get(path)

    if rec is None:
        return json.dumps({"error": f"file not found: {path}"})
    return rec.skeleton_json


def get_dependency_subgraph_impl(
    file_path: str,
    cfg: CodeLensConfig,
    depth: int = 2,
) -> str:
    """Return JSON with nodes and edges for the dependency neighbourhood of a file."""
    if not cfg.is_initialized:
        return json.dumps({"error": "not initialised"})

    with _open_session(cfg) as session:
        graph = _graph_from_db(session)

    if not graph.has_node(file_path):
        return json.dumps({"error": f"file not in graph: {file_path}"})

    nodes, edges = _bfs_subgraph(graph, file_path, depth)
    return json.dumps({"nodes": nodes, "edges": edges})


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------

_TOOL_SCHEMAS = [
    {
        "name": "get_relevant_files",
        "description": (
            "Retrieve the most structurally and semantically relevant files "
            "for a query. Returns a packed context string ready for LLM use."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural-language query."},
                "max_tokens": {
                    "type": "integer",
                    "default": 4000,
                    "minimum": 100,
                    "maximum": 32000,
                    "description": "Approximate token budget for the response.",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_file_skeleton",
        "description": "Return the stored FileSkeleton JSON for a repo-relative file path.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Repo-relative POSIX path."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "get_dependency_subgraph",
        "description": (
            "Return the dependency sub-graph (nodes + edges) within `depth` hops "
            "of the given file."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "file": {"type": "string", "description": "Repo-relative POSIX path."},
                "depth": {"type": "integer", "default": 2, "minimum": 1, "maximum": 5},
            },
            "required": ["file"],
        },
    },
]


async def _serve(cfg: CodeLensConfig) -> None:
    try:
        from mcp.server import Server
        from mcp.server.stdio import stdio_server
        from mcp import types
    except ImportError as exc:
        raise ImportError(
            "mcp package is required: pip install mcp>=1.0"
        ) from exc

    server = Server("codelens")

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name=t["name"],
                description=t["description"],
                inputSchema=t["inputSchema"],
            )
            for t in _TOOL_SCHEMAS
        ]

    @server.call_tool()
    async def call_tool(
        name: str, arguments: dict
    ) -> list[types.TextContent]:
        if name == "get_relevant_files":
            query = str(arguments["query"])[:2000]
            max_tokens = min(int(arguments.get("max_tokens", 4000)), 32000)
            result = get_relevant_files_impl(query, cfg, max_tokens=max_tokens)
        elif name == "get_file_skeleton":
            path_arg = str(arguments["path"])[:500]
            if not _is_safe_repo_path(path_arg):
                result = json.dumps({"error": f"invalid path: {path_arg!r}"})
            else:
                result = get_file_skeleton_impl(path_arg, cfg)
        elif name == "get_dependency_subgraph":
            file_arg = str(arguments["file"])[:500]
            if not _is_safe_repo_path(file_arg):
                result = json.dumps({"error": f"invalid path: {file_arg!r}"})
            else:
                depth = min(int(arguments.get("depth", 2)), 5)
                result = get_dependency_subgraph_impl(file_arg, cfg, depth=depth)
        else:
            result = json.dumps({"error": f"unknown tool: {name}"})

        return [types.TextContent(type="text", text=result)]

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def main() -> None:
    import asyncio
    repo_path = os.getenv("CODELENS_REPO_PATH", os.getcwd())
    cfg = CodeLensConfig.for_repo(repo_path)
    asyncio.run(_serve(cfg))


if __name__ == "__main__":
    main()
