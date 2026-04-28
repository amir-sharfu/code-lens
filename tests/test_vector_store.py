"""Tests for codelens.vector_store — chunking and ChromaDB integration."""
from __future__ import annotations
import pytest
from unittest.mock import MagicMock, call
from codelens.models import FileSkeleton, SymbolEntry, ImportEntry
from codelens.vector_store import chunks_for_file, _doc_id, VectorStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sym(name: str, kind: str = "function", sig: str | None = None, doc: str | None = None):
    return SymbolEntry(
        kind=kind,
        name=name,
        signature=sig or f"def {name}()",
        doc=doc,
        line=1,
    )


def _file(
    path: str = "src/utils.py",
    language: str = "python",
    exports: list[str] | None = None,
    symbols: list[SymbolEntry] | None = None,
    loc: int = 50,
) -> FileSkeleton:
    return FileSkeleton(
        path=path,
        language=language,
        imports=[],
        exports=exports or [],
        symbols=symbols or [],
        loc=loc,
    )


class _MockBackend:
    """Deterministic stub: each text gets a [0.1, 0.2, 0.3] vector."""
    DIM = 3

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3] for _ in texts]

    @property
    def dimension(self) -> int:
        return self.DIM


def _mock_chroma_client(count: int = 0):
    collection = MagicMock()
    collection.count.return_value = count
    collection.query.return_value = {
        "metadatas": [[]],
        "documents": [[]],
        "distances": [[]],
    }
    client = MagicMock()
    client.get_or_create_collection.return_value = collection
    return client, collection


# ---------------------------------------------------------------------------
# _doc_id
# ---------------------------------------------------------------------------

class TestDocId:
    def test_short_path_uses_raw_form(self):
        did = _doc_id("src/utils.py", "helper")
        assert "src/utils.py" in did
        assert "helper" in did

    def test_long_path_hashes(self):
        # 195 chars + "::" + "suffix" = 203 > 200 → triggers SHA-256 path
        long_path = "a" * 195
        did = _doc_id(long_path, "suffix")
        assert len(did) == 64  # SHA-256 hex

    def test_stable_across_calls(self):
        assert _doc_id("a.py", "fn") == _doc_id("a.py", "fn")

    def test_different_inputs_differ(self):
        assert _doc_id("a.py", "fn1") != _doc_id("a.py", "fn2")


# ---------------------------------------------------------------------------
# chunks_for_file
# ---------------------------------------------------------------------------

class TestChunksForFile:
    def test_empty_file_no_chunks(self):
        f = _file(symbols=[], exports=[])
        assert chunks_for_file(f) == []

    def test_private_symbol_skipped(self):
        f = _file(symbols=[_sym("_private"), _sym("public")])
        ids = [c[0] for c in chunks_for_file(f)]
        assert not any("_private" in i for i in ids)

    def test_public_symbol_produces_chunk(self):
        f = _file(symbols=[_sym("my_func", sig="def my_func(x: int) -> str")])
        chunks = chunks_for_file(f)
        assert len(chunks) == 1
        _, text, meta = chunks[0]
        assert "my_func" in text
        assert meta["symbol"] == "my_func"
        assert meta["chunk_type"] == "symbol"
        assert meta["path"] == f.path

    def test_symbol_doc_included_in_text(self):
        f = _file(symbols=[_sym("fn", doc="Does something important.")])
        _, text, _ = chunks_for_file(f)[0]
        assert "Does something important." in text

    def test_exports_produce_summary_chunk(self):
        f = _file(exports=["foo", "bar"], symbols=[])
        chunks = chunks_for_file(f)
        assert len(chunks) == 1
        _, text, meta = chunks[0]
        assert "foo" in text
        assert meta["chunk_type"] == "file_summary"
        assert meta["kind"] == "file"

    def test_symbol_plus_exports_produces_two_chunks(self):
        f = _file(
            symbols=[_sym("public_fn")],
            exports=["public_fn"],
        )
        assert len(chunks_for_file(f)) == 2

    def test_multiple_public_symbols(self):
        syms = [_sym(f"fn{i}") for i in range(5)]
        f = _file(symbols=syms)
        assert len(chunks_for_file(f)) == 5

    def test_exports_capped_at_30_in_text(self):
        f = _file(exports=[f"e{i}" for i in range(50)], symbols=[])
        _, text, _ = chunks_for_file(f)[0]
        # Should not list all 50
        assert "e30" not in text

    def test_metadata_has_language(self):
        f = _file(language="typescript", symbols=[_sym("fn")])
        _, _, meta = chunks_for_file(f)[0]
        assert meta["language"] == "typescript"


# ---------------------------------------------------------------------------
# VectorStore
# ---------------------------------------------------------------------------

class TestVectorStore:
    def _make_store(self, count: int = 0) -> tuple[VectorStore, MagicMock]:
        client, collection = _mock_chroma_client(count)
        store = VectorStore(backend=_MockBackend(), _client=client)
        return store, collection

    def test_init_calls_get_or_create_collection(self):
        client, _ = _mock_chroma_client()
        VectorStore(backend=_MockBackend(), _client=client)
        client.get_or_create_collection.assert_called_once()

    def test_upsert_file_with_no_chunks_returns_zero(self):
        store, collection = self._make_store()
        n = store.upsert_file(_file(symbols=[], exports=[]))
        assert n == 0
        collection.upsert.assert_not_called()

    def test_upsert_file_returns_chunk_count(self):
        store, collection = self._make_store()
        f = _file(symbols=[_sym("fn")], exports=["fn"])
        n = store.upsert_file(f)
        assert n == 2  # 1 symbol + 1 file summary
        collection.upsert.assert_called_once()

    def test_upsert_passes_correct_ids_and_embeddings(self):
        store, collection = self._make_store()
        f = _file(symbols=[_sym("fn")], exports=[])
        store.upsert_file(f)
        kwargs = collection.upsert.call_args.kwargs
        assert len(kwargs["ids"]) == 1
        assert len(kwargs["embeddings"]) == 1
        assert kwargs["embeddings"][0] == [0.1, 0.2, 0.3]

    def test_delete_file_calls_collection_delete(self):
        store, collection = self._make_store()
        store.delete_file("src/utils.py")
        collection.delete.assert_called_once_with(where={"path": "src/utils.py"})

    def test_query_empty_collection_returns_empty_list(self):
        store, _ = self._make_store(count=0)
        assert store.query("anything") == []

    def test_query_returns_merged_metadata(self):
        client, collection = _mock_chroma_client(count=1)
        collection.query.return_value = {
            "metadatas": [[{"path": "a.py", "language": "python",
                            "symbol": "fn", "kind": "function",
                            "chunk_type": "symbol"}]],
            "documents": [["def fn()"]],
            "distances": [[0.1]],
        }
        store = VectorStore(backend=_MockBackend(), _client=client)
        results = store.query("fn", k=1)
        assert len(results) == 1
        assert results[0]["path"] == "a.py"
        assert results[0]["score"] == pytest.approx(0.9)
        assert results[0]["document"] == "def fn()"

    def test_query_score_clamped_at_zero(self):
        client, collection = _mock_chroma_client(count=1)
        collection.query.return_value = {
            "metadatas": [[{"path": "a.py", "language": "python",
                            "symbol": "", "kind": "file",
                            "chunk_type": "file_summary"}]],
            "documents": [["a.py\nexports: fn"]],
            "distances": [[1.5]],  # distance > 1 is theoretically possible with some spaces
        }
        store = VectorStore(backend=_MockBackend(), _client=client)
        results = store.query("fn")
        assert results[0]["score"] >= 0.0

    def test_count_delegates_to_collection(self):
        store, collection = self._make_store(count=42)
        assert store.count == 42
