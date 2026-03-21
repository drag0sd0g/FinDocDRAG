"""Embedding generation using sentence-transformers.

References:
  - TDD: FR-9 (384-dim embeddings via all-MiniLM-L6-v2)
  - TDD: Section 5.2.2 Embedding (batch of 64, loaded once at startup)
"""

from __future__ import annotations

import structlog
from sentence_transformers import SentenceTransformer

logger = structlog.get_logger()

DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_BATCH_SIZE = 64


class Embedder:
    """Wraps a sentence-transformers model for batch embedding.

    The model is loaded once on construction and held in memory
    (TDD Section 5.2.2).
    """

    def __init__(self, model_name: str = DEFAULT_MODEL) -> None:
        logger.info("embedder_loading_model", model=model_name)
        self._model = SentenceTransformer(model_name)
        self._dimension = self._model.get_sentence_embedding_dimension()
        logger.info(
            "embedder_model_loaded",
            model=model_name,
            dimension=self._dimension,
        )

    @property
    def dimension(self) -> int:
        """Return the embedding vector dimension (384 for MiniLM-L6-v2)."""
        return int(self._dimension or 0)

    def embed(self, texts: list[str], batch_size: int = DEFAULT_BATCH_SIZE) -> list[list[float]]:
        """Generate embeddings for a list of texts.

        Returns a list of float vectors, one per input text.
        Uses batch_size=64 for throughput (TDD Section 5.2.2).
        """
        embeddings = self._model.encode(
            texts,
            batch_size=batch_size,
            show_progress_bar=False,
            normalize_embeddings=True,
        )
        return [vec.tolist() for vec in embeddings]
