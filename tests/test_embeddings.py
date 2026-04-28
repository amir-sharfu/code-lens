"""Tests for codelens.embeddings — backend protocol and factory."""
from __future__ import annotations
import os
import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

class _StubBackend:
    """Minimal EmbeddingBackend for protocol checks."""
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * 4 for _ in texts]

    @property
    def dimension(self) -> int:
        return 4


def _make_mock_model(dim: int = 384):
    """Return a mock SentenceTransformer-like model."""
    import numpy as np
    model = MagicMock()
    model.get_sentence_embedding_dimension.return_value = dim
    model.encode.side_effect = lambda texts, **_: np.zeros((len(texts), dim))
    return model


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------

class TestEmbeddingBackendProtocol:
    def test_stub_satisfies_protocol(self):
        from codelens.embeddings import EmbeddingBackend
        assert isinstance(_StubBackend(), EmbeddingBackend)

    def test_object_missing_embed_fails_protocol(self):
        from codelens.embeddings import EmbeddingBackend

        class Bad:
            @property
            def dimension(self):
                return 4

        assert not isinstance(Bad(), EmbeddingBackend)


# ---------------------------------------------------------------------------
# LocalEmbeddingBackend
# ---------------------------------------------------------------------------

class TestLocalEmbeddingBackend:
    def _make(self, dim=384):
        """Return a LocalEmbeddingBackend with a mocked SentenceTransformer."""
        mock_model = _make_mock_model(dim)
        with patch.dict("sys.modules", {"sentence_transformers": MagicMock(
            SentenceTransformer=MagicMock(return_value=mock_model)
        )}):
            from codelens import embeddings as emb_mod
            import importlib
            importlib.reload(emb_mod)
            backend = emb_mod.LocalEmbeddingBackend()
            backend._model = mock_model
            backend._dim = dim
        return backend, mock_model

    def test_dimension_property(self):
        backend, _ = self._make(dim=384)
        assert backend.dimension == 384

    def test_embed_returns_list_of_vectors(self):
        backend, mock_model = self._make(dim=4)
        result = backend.embed(["hello", "world"])
        assert len(result) == 2
        assert len(result[0]) == 4

    def test_embed_empty_returns_empty(self):
        backend, _ = self._make()
        assert backend.embed([]) == []

    def test_missing_sentence_transformers_raises(self):
        import sys
        with patch.dict("sys.modules", {"sentence_transformers": None}):
            # Force reimport to trigger the ImportError path
            from codelens import embeddings as emb_mod
            import importlib
            importlib.reload(emb_mod)
            with pytest.raises(ImportError, match="sentence-transformers"):
                emb_mod.LocalEmbeddingBackend()


# ---------------------------------------------------------------------------
# OpenAIEmbeddingBackend
# ---------------------------------------------------------------------------

class TestOpenAIEmbeddingBackend:
    def test_missing_api_key_raises(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        mock_openai = MagicMock()
        with patch.dict("sys.modules", {"openai": mock_openai}):
            from codelens import embeddings as emb_mod
            import importlib
            importlib.reload(emb_mod)
            with pytest.raises(ValueError, match="OPENAI_API_KEY"):
                emb_mod.OpenAIEmbeddingBackend()

    def test_embed_delegates_to_client(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        fake_vec = [0.1] * 1536

        mock_response = MagicMock()
        mock_response.data = [MagicMock(embedding=fake_vec)]

        mock_client_instance = MagicMock()
        mock_client_instance.embeddings.create.return_value = mock_response

        mock_openai = MagicMock()
        mock_openai.OpenAI.return_value = mock_client_instance

        with patch.dict("sys.modules", {"openai": mock_openai}):
            from codelens import embeddings as emb_mod
            import importlib
            importlib.reload(emb_mod)
            backend = emb_mod.OpenAIEmbeddingBackend()
            result = backend.embed(["query"])

        assert result == [fake_vec]

    def test_embed_empty_returns_empty(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        mock_openai = MagicMock()
        mock_openai.OpenAI.return_value = MagicMock()
        with patch.dict("sys.modules", {"openai": mock_openai}):
            from codelens import embeddings as emb_mod
            import importlib
            importlib.reload(emb_mod)
            backend = emb_mod.OpenAIEmbeddingBackend()
            assert backend.embed([]) == []

    def test_dimension_is_1536(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        mock_openai = MagicMock()
        mock_openai.OpenAI.return_value = MagicMock()
        with patch.dict("sys.modules", {"openai": mock_openai}):
            from codelens import embeddings as emb_mod
            import importlib
            importlib.reload(emb_mod)
            backend = emb_mod.OpenAIEmbeddingBackend()
            assert backend.dimension == 1536


# ---------------------------------------------------------------------------
# get_embedding_backend factory
# ---------------------------------------------------------------------------

class TestGetEmbeddingBackendFactory:
    def test_unknown_name_raises(self, monkeypatch):
        monkeypatch.setenv("CODELENS_EMBEDDING_BACKEND", "unknown")
        from codelens import embeddings as emb_mod
        import importlib
        importlib.reload(emb_mod)
        with pytest.raises(ValueError, match="Unknown embedding backend"):
            emb_mod.get_embedding_backend("unknown")

    def test_explicit_openai_arg_raises_without_key(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        mock_openai = MagicMock()
        with patch.dict("sys.modules", {"openai": mock_openai}):
            from codelens import embeddings as emb_mod
            import importlib
            importlib.reload(emb_mod)
            with pytest.raises(ValueError, match="OPENAI_API_KEY"):
                emb_mod.get_embedding_backend("openai")
