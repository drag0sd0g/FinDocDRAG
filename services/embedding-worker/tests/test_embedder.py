"""Unit tests for the embedder.

The actual sentence-transformers model is NOT loaded in tests —
we mock it to keep tests fast and CPU-light.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np

from src.embedder import Embedder


class TestEmbedder:
    """Tests for the Embedder wrapper."""

    @patch("src.embedder.SentenceTransformer")
    def test_dimension_property(self, mock_st_class: MagicMock) -> None:
        mock_model = MagicMock()
        mock_model.get_sentence_embedding_dimension.return_value = 384
        mock_st_class.return_value = mock_model

        embedder = Embedder(model_name="test-model")
        assert embedder.dimension == 384

    @patch("src.embedder.SentenceTransformer")
    def test_embed_returns_list_of_lists(self, mock_st_class: MagicMock) -> None:
        mock_model = MagicMock()
        mock_model.get_sentence_embedding_dimension.return_value = 384
        # encode returns numpy arrays
        mock_model.encode.return_value = np.array([
            [0.1] * 384,
            [0.2] * 384,
        ])
        mock_st_class.return_value = mock_model

        embedder = Embedder(model_name="test-model")
        result = embedder.embed(["hello", "world"])

        assert len(result) == 2
        assert len(result[0]) == 384
        assert isinstance(result[0], list)
        assert isinstance(result[0][0], float)

    @patch("src.embedder.SentenceTransformer")
    def test_embed_passes_batch_size(self, mock_st_class: MagicMock) -> None:
        mock_model = MagicMock()
        mock_model.get_sentence_embedding_dimension.return_value = 384
        mock_model.encode.return_value = np.array([[0.1] * 384])
        mock_st_class.return_value = mock_model

        embedder = Embedder(model_name="test-model")
        embedder.embed(["hello"], batch_size=32)

        mock_model.encode.assert_called_once_with(
            ["hello"],
            batch_size=32,
            show_progress_bar=False,
            normalize_embeddings=True,
        )