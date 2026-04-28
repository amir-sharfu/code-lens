"""
Pluggable embedding backends for CodeLens Phase 3.

Select backend via CODELENS_EMBEDDING_BACKEND env var (default: "local"):
  local  — sentence-transformers, BAAI/bge-small-en-v1.5 (free, CPU-friendly)
  openai — OpenAI text-embedding-3-small (requires OPENAI_API_KEY)
"""
from __future__ import annotations
import os
from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbeddingBackend(Protocol):
    """Protocol satisfied by any object that can embed a list of texts."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one float vector per input text."""
        ...

    @property
    def dimension(self) -> int:
        """Dimensionality of the output vectors."""
        ...


class LocalEmbeddingBackend:
    """
    sentence-transformers backend.

    Downloads the model on first use (~33 MB for bge-small-en-v1.5).
    Override model with CODELENS_EMBED_MODEL env var or the model_name arg.
    """

    def __init__(self, model_name: str | None = None) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise ImportError(
                "sentence-transformers is required for LocalEmbeddingBackend: "
                "pip install sentence-transformers>=2.7"
            ) from exc

        self._model_name = model_name or os.getenv(
            "CODELENS_EMBED_MODEL", "BAAI/bge-small-en-v1.5"
        )
        self._model = SentenceTransformer(self._model_name)
        self._dim: int = self._model.get_sentence_embedding_dimension()

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vecs = self._model.encode(texts, normalize_embeddings=True)
        return vecs.tolist()

    @property
    def dimension(self) -> int:
        return self._dim


class OpenAIEmbeddingBackend:
    """
    OpenAI text-embedding-3-small backend.
    Requires OPENAI_API_KEY environment variable and the openai package.
    """

    _DIMENSION = 1536

    def __init__(self, model: str = "text-embedding-3-small") -> None:
        try:
            import openai
        except ImportError as exc:
            raise ImportError(
                "openai package is required for OpenAIEmbeddingBackend: "
                "pip install openai>=1.0"
            ) from exc

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY environment variable is not set")

        self._client = openai.OpenAI(api_key=api_key)
        self._model = model

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        response = self._client.embeddings.create(input=texts, model=self._model)
        return [item.embedding for item in response.data]

    @property
    def dimension(self) -> int:
        return self._DIMENSION


def get_embedding_backend(backend: str | None = None) -> EmbeddingBackend:
    """
    Factory. Reads CODELENS_EMBEDDING_BACKEND env var when backend arg is None.
    Valid values: "local" (default), "openai".
    """
    name = backend or os.getenv("CODELENS_EMBEDDING_BACKEND", "local")
    if name == "local":
        return LocalEmbeddingBackend()
    if name == "openai":
        return OpenAIEmbeddingBackend()
    raise ValueError(
        f"Unknown embedding backend {name!r}. Valid values: 'local', 'openai'."
    )
