"""Unit tests for the embedding drift detector.

All external dependencies (psycopg2, push_to_gateway, sys.exit) are
mocked so tests run without a real database or Prometheus Pushgateway.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

import src.drift_detector as dd

# ── _cosine_distance ─────────────────────────────────────────────

class TestCosineDistance:
    def test_identical_vectors_return_zero(self) -> None:
        v = np.array([1.0, 0.0, 0.0])
        assert dd._cosine_distance(v, v) == pytest.approx(0.0, abs=1e-6)

    def test_orthogonal_vectors_return_one(self) -> None:
        a = np.array([1.0, 0.0])
        b = np.array([0.0, 1.0])
        assert dd._cosine_distance(a, b) == pytest.approx(1.0, abs=1e-6)

    def test_opposite_vectors_return_two_clamped_to_positive(self) -> None:
        a = np.array([1.0, 0.0])
        b = np.array([-1.0, 0.0])
        result = dd._cosine_distance(a, b)
        assert result == pytest.approx(2.0, abs=1e-6)

    def test_zero_norm_vector_returns_one(self) -> None:
        zero = np.array([0.0, 0.0])
        v = np.array([1.0, 0.0])
        assert dd._cosine_distance(zero, v) == 1.0
        assert dd._cosine_distance(v, zero) == 1.0

    def test_similar_vectors_return_small_distance(self) -> None:
        a = np.array([1.0, 0.1, 0.0])
        b = np.array([1.0, 0.2, 0.0])
        result = dd._cosine_distance(a, b)
        assert 0.0 <= result < 0.1


# ── main() — DB connection failure ───────────────────────────────

class TestMainDbConnectFailure:
    @patch("src.drift_detector.psycopg2.connect", side_effect=Exception("conn refused"))
    @patch("src.drift_detector.sys.exit")
    def test_exits_on_connect_failure(
        self, mock_exit: MagicMock, mock_connect: MagicMock
    ) -> None:
        dd.main()
        mock_exit.assert_called_once_with(1)


# ── main() — DB query failure ─────────────────────────────────────

class TestMainDbQueryFailure:
    def _make_conn(self, side_effect: Exception) -> MagicMock:
        mock_cur = MagicMock()
        mock_cur.__enter__ = MagicMock(return_value=mock_cur)
        mock_cur.__exit__ = MagicMock(return_value=False)
        mock_cur.execute.side_effect = side_effect
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        return mock_conn

    @patch("src.drift_detector.register_vector")
    @patch("src.drift_detector.psycopg2.connect")
    @patch("src.drift_detector.sys.exit")
    def test_exits_on_query_failure(
        self,
        mock_exit: MagicMock,
        mock_connect: MagicMock,
        mock_register: MagicMock,
    ) -> None:
        mock_connect.return_value = self._make_conn(RuntimeError("query error"))
        dd.main()
        mock_exit.assert_called_once_with(1)


# ── main() — empty corpus ────────────────────────────────────────

class TestMainEmptyCorpus:
    def _make_conn(self, corpus_mean: object, recent_mean: object) -> MagicMock:
        """Return a mock connection whose cursor returns the given means in order."""
        results = iter([(corpus_mean,), (recent_mean,)])

        mock_cur = MagicMock()
        mock_cur.__enter__ = MagicMock(return_value=mock_cur)
        mock_cur.__exit__ = MagicMock(return_value=False)
        mock_cur.fetchone.side_effect = lambda: next(results)

        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        return mock_conn

    @patch("src.drift_detector.register_vector")
    @patch("src.drift_detector.psycopg2.connect")
    def test_returns_early_when_corpus_is_empty(
        self, mock_connect: MagicMock, mock_register: MagicMock
    ) -> None:
        mock_connect.return_value = self._make_conn(None, None)
        # Should return without raising or calling sys.exit
        dd.main()

    @patch("src.drift_detector.register_vector")
    @patch("src.drift_detector.psycopg2.connect")
    def test_returns_early_when_no_recent_chunks(
        self, mock_connect: MagicMock, mock_register: MagicMock
    ) -> None:
        corpus = np.array([0.1, 0.2, 0.3])
        mock_connect.return_value = self._make_conn(corpus, None)
        dd.main()


# ── main() — normal run (no alert) ───────────────────────────────

class TestMainNormalRun:
    def _make_conn(
        self, corpus_mean: np.ndarray, recent_mean: np.ndarray
    ) -> MagicMock:
        results = iter([(corpus_mean,), (recent_mean,)])

        mock_cur = MagicMock()
        mock_cur.__enter__ = MagicMock(return_value=mock_cur)
        mock_cur.__exit__ = MagicMock(return_value=False)
        mock_cur.fetchone.side_effect = lambda: next(results)

        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        return mock_conn

    @patch("src.drift_detector.push_to_gateway")
    @patch("src.drift_detector.register_vector")
    @patch("src.drift_detector.psycopg2.connect")
    def test_no_pushgateway_when_url_not_set(
        self,
        mock_connect: MagicMock,
        mock_register: MagicMock,
        mock_push: MagicMock,
    ) -> None:
        a = np.array([1.0, 0.0, 0.0])
        mock_connect.return_value = self._make_conn(a, a)
        with patch.object(dd, "PUSHGATEWAY_URL", None):
            dd.main()
        mock_push.assert_not_called()

    @patch("src.drift_detector.push_to_gateway")
    @patch("src.drift_detector.register_vector")
    @patch("src.drift_detector.psycopg2.connect")
    def test_pushes_to_gateway_when_url_set(
        self,
        mock_connect: MagicMock,
        mock_register: MagicMock,
        mock_push: MagicMock,
    ) -> None:
        a = np.array([1.0, 0.0, 0.0])
        mock_connect.return_value = self._make_conn(a, a)
        with patch.object(dd, "PUSHGATEWAY_URL", "http://pushgateway:9091"):
            dd.main()
        mock_push.assert_called_once()
        _, kwargs = mock_push.call_args
        assert kwargs["job"] == "findoc-embedding-drift-detection"

    @patch("src.drift_detector.push_to_gateway")
    @patch("src.drift_detector.register_vector")
    @patch("src.drift_detector.psycopg2.connect")
    def test_pushgateway_failure_does_not_raise(
        self,
        mock_connect: MagicMock,
        mock_register: MagicMock,
        mock_push: MagicMock,
    ) -> None:
        mock_push.side_effect = Exception("gateway down")
        a = np.array([1.0, 0.0, 0.0])
        mock_connect.return_value = self._make_conn(a, a)
        with patch.object(dd, "PUSHGATEWAY_URL", "http://pushgateway:9091"):
            dd.main()  # must not raise


# ── main() — alert threshold ─────────────────────────────────────

class TestMainDriftAlert:
    def _make_conn(
        self, corpus_mean: np.ndarray, recent_mean: np.ndarray
    ) -> MagicMock:
        results = iter([(corpus_mean,), (recent_mean,)])

        mock_cur = MagicMock()
        mock_cur.__enter__ = MagicMock(return_value=mock_cur)
        mock_cur.__exit__ = MagicMock(return_value=False)
        mock_cur.fetchone.side_effect = lambda: next(results)

        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        return mock_conn

    @patch("src.drift_detector.push_to_gateway")
    @patch("src.drift_detector.register_vector")
    @patch("src.drift_detector.psycopg2.connect")
    def test_alert_fires_when_drift_exceeds_threshold(
        self,
        mock_connect: MagicMock,
        mock_register: MagicMock,
        mock_push: MagicMock,
    ) -> None:
        # Orthogonal vectors → cosine distance = 1.0, well above any threshold
        corpus = np.array([1.0, 0.0])
        recent = np.array([0.0, 1.0])
        mock_connect.return_value = self._make_conn(corpus, recent)
        with patch.object(dd, "PUSHGATEWAY_URL", None), patch.object(dd, "DRIFT_THRESHOLD", 0.15):
            dd.main()  # should log warning but not raise

    @patch("src.drift_detector.push_to_gateway")
    @patch("src.drift_detector.register_vector")
    @patch("src.drift_detector.psycopg2.connect")
    def test_no_alert_when_drift_below_threshold(
        self,
        mock_connect: MagicMock,
        mock_register: MagicMock,
        mock_push: MagicMock,
    ) -> None:
        # Identical vectors → cosine distance ≈ 0.0
        a = np.array([1.0, 0.0])
        mock_connect.return_value = self._make_conn(a, a)
        with patch.object(dd, "PUSHGATEWAY_URL", None), patch.object(dd, "DRIFT_THRESHOLD", 0.15):
            dd.main()  # should complete without alert
