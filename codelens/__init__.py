"""
CodeLens - Architectural orientation for LLMs.

Phase 1 public API:
    extract_repo(path)              -> RepoSkeleton
    extract_file(path, repo_root)   -> FileSkeleton | None
    to_json(skeleton)               -> str

Phase 2 public API:
    build_graph(skeleton)           -> nx.DiGraph
    compute_importance(graph, skel) -> dict[str, float]
    assign_tiers(importance)        -> dict[str, str]
    build_and_score(skeleton)       -> (graph, importance, tiers)

Phase 3 public API (requires: pip install codelens[phase3]):
    get_embedding_backend(name?)    -> EmbeddingBackend
    VectorStore(persist_dir?, ...)  — embed + semantic search
    retrieve(query, vs, graph, ...) -> str  — packed context
    pack_context(ranked, file_map)  -> str

Phase 4 public API (requires: pip install codelens[phase4]):
    IncrementalUpdater(repo_path)   — init/update/get_importance/get_tiers
    FileRepository, DependencyRepository, ObservationRepository

Phase 5 public API (requires: pip install codelens[phase5]):
    CodeLensConfig.for_repo(path?)  — config dataclass
    CLI: codelens init/query/watch/map/stats
    MCP: python -m codelens.mcp_server  (get_relevant_files, get_file_skeleton, get_dependency_subgraph)
"""
from codelens.extractor import extract_repo, extract_file, to_json
from codelens.models import RepoSkeleton, FileSkeleton, ImportEntry, SymbolEntry
from codelens.graph import build_graph, compute_importance, assign_tiers, build_and_score
from codelens.embeddings import get_embedding_backend, EmbeddingBackend
from codelens.vector_store import VectorStore
from codelens.retriever import retrieve, pack_context
from codelens.db.incremental import IncrementalUpdater
from codelens.db.repository import FileRepository, DependencyRepository, ObservationRepository
from codelens.config import CodeLensConfig

__version__ = "0.5.0"
__all__ = [
    # Phase 1
    "extract_repo", "extract_file", "to_json",
    "RepoSkeleton", "FileSkeleton", "ImportEntry", "SymbolEntry",
    # Phase 2
    "build_graph", "compute_importance", "assign_tiers", "build_and_score",
    # Phase 3
    "get_embedding_backend", "EmbeddingBackend", "VectorStore",
    "retrieve", "pack_context",
    # Phase 4
    "IncrementalUpdater",
    "FileRepository", "DependencyRepository", "ObservationRepository",
    # Phase 5
    "CodeLensConfig",
]
