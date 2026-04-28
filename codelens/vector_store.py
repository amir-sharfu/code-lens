"""
ChromaDB-backed vector store for CodeLens Phase 3.

One chunk is stored per public symbol (signature + docstring) and one summary
chunk per file (path + exports list).  All chunks carry a "path" metadata field
so a file's chunks can be bulk-deleted on incremental update.

Persist directory defaults to .codelens/chroma (override: CODELENS_CHROMA_DIR).
"""
from __future__ import annotations
import hashlib
import os
from pathlib import Path

from codelens.models import FileSkeleton
from codelens.embeddings import EmbeddingBackend


# ---------------------------------------------------------------------------
# Chunk helpers (module-level so they are independently testable)
# ---------------------------------------------------------------------------

def _doc_id(path: str, suffix: str) -> str:
    """
    Stable, ChromaDB-safe document ID for a (path, suffix) pair.
    IDs longer than 200 chars are SHA-256 hashed to stay within limits.
    """
    raw = f"{path}::{suffix}"
    if len(raw) <= 200:
        return raw.replace(" ", "_")
    return hashlib.sha256(raw.encode()).hexdigest()


def chunks_for_file(f: FileSkeleton) -> list[tuple[str, str, dict]]:
    """
    Return (doc_id, text, metadata) triples for a FileSkeleton.

    Produces:
      - One chunk per public symbol  (signature + optional docstring)
      - One file-summary chunk       (path + exports), only when exports exist
    """
    result: list[tuple[str, str, dict]] = []

    for sym in f.symbols:
        if sym.name.startswith("_"):
            continue
        parts = [sym.signature]
        if sym.doc:
            parts.append(sym.doc)
        text = "\n".join(parts)
        meta: dict = {
            "path": f.path,
            "language": f.language,
            "symbol": sym.name,
            "kind": sym.kind,
            "chunk_type": "symbol",
        }
        result.append((_doc_id(f.path, sym.name), text, meta))

    if f.exports:
        text = f"{f.path}\nexports: {', '.join(f.exports[:30])}"
        meta = {
            "path": f.path,
            "language": f.language,
            "symbol": "",
            "kind": "file",
            "chunk_type": "file_summary",
        }
        result.append((_doc_id(f.path, "__file_summary__"), text, meta))

    return result


# ---------------------------------------------------------------------------
# VectorStore
# ---------------------------------------------------------------------------

class VectorStore:
    """
    Wraps a ChromaDB collection.

    Pass ``_client`` to inject a mock ChromaDB client in tests; otherwise a
    PersistentClient is created from persist_dir.
    """

    _COLLECTION_NAME = "codelens_symbols"

    def __init__(
        self,
        persist_dir: str | Path | None = None,
        backend: EmbeddingBackend | None = None,
        _client=None,
    ) -> None:
        self._persist_dir = str(
            persist_dir or os.getenv("CODELENS_CHROMA_DIR", ".codelens/chroma")
        )
        self._backend = backend

        if _client is None:
            try:
                import chromadb
            except ImportError as exc:
                raise ImportError(
                    "chromadb is required for VectorStore: pip install chromadb>=0.4"
                ) from exc
            _client = chromadb.PersistentClient(path=self._persist_dir)

        self._collection = _client.get_or_create_collection(
            name=self._COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @property
    def _embedding_backend(self) -> EmbeddingBackend:
        if self._backend is None:
            from codelens.embeddings import get_embedding_backend
            self._backend = get_embedding_backend()
        return self._backend

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def upsert_file(self, f: FileSkeleton) -> int:
        """
        Embed and upsert all chunks for a file skeleton.
        Returns the number of chunks written.
        """
        chunks = chunks_for_file(f)
        if not chunks:
            return 0

        ids, texts, metadatas = zip(*chunks)
        embeddings = self._embedding_backend.embed(list(texts))

        self._collection.upsert(
            ids=list(ids),
            embeddings=embeddings,
            documents=list(texts),
            metadatas=list(metadatas),
        )
        return len(chunks)

    def delete_file(self, path: str) -> None:
        """Remove all chunks whose metadata path equals the given path."""
        self._collection.delete(where={"path": path})

    def query(self, text: str, k: int = 20) -> list[dict]:
        """
        Semantic nearest-neighbour search.

        Returns up to k result dicts, each with keys:
          path, language, symbol, kind, chunk_type, document, score
        Score is cosine similarity in [0, 1] (1 = identical).
        """
        total = self._collection.count()
        if total == 0:
            return []

        embedding = self._embedding_backend.embed([text])[0]
        results = self._collection.query(
            query_embeddings=[embedding],
            n_results=min(k, total),
            include=["metadatas", "documents", "distances"],
        )

        output: list[dict] = []
        for meta, doc, dist in zip(
            results["metadatas"][0],
            results["documents"][0],
            results["distances"][0],
        ):
            output.append({
                **meta,
                "document": doc,
                "score": max(0.0, 1.0 - dist),  # cosine distance → similarity
            })
        return output

    @property
    def count(self) -> int:
        """Total number of chunks stored."""
        return self._collection.count()
