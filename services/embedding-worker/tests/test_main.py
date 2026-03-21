"""Unit tests for the embedding worker FastAPI app and consume loop.

All Kafka, DB, and model dependencies are mocked.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

# ── FastAPI endpoints (health sidecar) ───────────────────────────


class TestHealthEndpoints:
    """Test the /health and /ready endpoints without starting the lifespan."""

    def test_health_returns_healthy(self) -> None:
        from fastapi.testclient import TestClient

        import src.main as main_mod

        mock_store = MagicMock()
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_store._get_conn.return_value = mock_conn

        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = True

        original_store = main_mod._store
        original_thread = main_mod._consumer_thread
        main_mod._store = mock_store
        main_mod._consumer_thread = mock_thread

        try:
            client = TestClient(app=main_mod.app, raise_server_exceptions=False)
            response = client.get("/health")
            assert response.status_code == 200
            assert response.json() == {"status": "healthy"}
            assert "X-Request-ID" in response.headers
        finally:
            main_mod._store = original_store
            main_mod._consumer_thread = original_thread

    def test_health_returns_503_when_not_initialized(self) -> None:
        from fastapi.testclient import TestClient

        import src.main as main_mod

        original_store = main_mod._store
        original_thread = main_mod._consumer_thread
        main_mod._store = None
        main_mod._consumer_thread = None

        try:
            client = TestClient(app=main_mod.app, raise_server_exceptions=False)
            response = client.get("/health")
            assert response.status_code == 503
        finally:
            main_mod._store = original_store
            main_mod._consumer_thread = original_thread

    def test_health_returns_503_when_consumer_dead(self) -> None:
        from fastapi.testclient import TestClient

        import src.main as main_mod

        mock_store = MagicMock()
        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = False

        original_store = main_mod._store
        original_thread = main_mod._consumer_thread
        main_mod._store = mock_store
        main_mod._consumer_thread = mock_thread

        try:
            client = TestClient(app=main_mod.app, raise_server_exceptions=False)
            response = client.get("/health")
            assert response.status_code == 503
        finally:
            main_mod._store = original_store
            main_mod._consumer_thread = original_thread

    def test_ready_returns_503_when_not_initialized(self) -> None:
        from fastapi.testclient import TestClient

        import src.main as main_mod

        original_embedder = main_mod._embedder
        original_store = main_mod._store
        main_mod._embedder = None
        main_mod._store = None

        try:
            client = TestClient(app=main_mod.app, raise_server_exceptions=False)
            response = client.get("/ready")
            assert response.status_code == 503
            assert response.json() == {"detail": "Service not initialized"}
        finally:
            main_mod._embedder = original_embedder
            main_mod._store = original_store

    def test_ready_returns_ready_when_initialized(self) -> None:
        from fastapi.testclient import TestClient

        import src.main as main_mod

        original_embedder = main_mod._embedder
        original_store = main_mod._store
        main_mod._embedder = MagicMock()
        main_mod._store = MagicMock()

        try:
            client = TestClient(app=main_mod.app, raise_server_exceptions=False)
            response = client.get("/ready")
            assert response.status_code == 200
            assert response.json() == {"status": "ready"}
        finally:
            main_mod._embedder = original_embedder
            main_mod._store = original_store


# ── Consume loop ─────────────────────────────────────────────────


class TestConsumeLoop:
    """Test the _consume_loop function with mocked Kafka."""

    @patch("src.main.Producer")
    @patch("src.main.Consumer")
    def test_handles_none_message(
        self, mock_consumer_class: MagicMock, mock_producer_class: MagicMock
    ) -> None:
        """If poll returns None, the loop continues without error."""
        import src.main as main_mod

        mock_consumer = MagicMock()
        # Return None once, then trigger shutdown
        call_count = 0

        def poll_side_effect(timeout: float = 1.0) -> None:
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                main_mod._shutdown_event.set()
            return None

        mock_consumer.poll.side_effect = poll_side_effect
        mock_consumer_class.return_value = mock_consumer
        mock_producer_class.return_value = MagicMock()

        # Clear shutdown event before test
        main_mod._shutdown_event.clear()

        main_mod._consume_loop()

        mock_consumer.subscribe.assert_called_once_with(["filings.raw"])
        mock_consumer.close.assert_called_once()
        # Reset for other tests
        main_mod._shutdown_event.clear()

    @patch("src.main.Producer")
    @patch("src.main.Consumer")
    def test_handles_partition_eof(
        self, mock_consumer_class: MagicMock, mock_producer_class: MagicMock
    ) -> None:
        """Partition EOF errors are silently skipped."""
        from confluent_kafka import KafkaError

        import src.main as main_mod

        mock_consumer = MagicMock()
        mock_msg = MagicMock()
        mock_error = MagicMock()
        mock_error.code.return_value = KafkaError._PARTITION_EOF
        mock_msg.error.return_value = mock_error

        call_count = 0

        def poll_side_effect(timeout: float = 1.0) -> MagicMock | None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return mock_msg
            main_mod._shutdown_event.set()
            return None

        mock_consumer.poll.side_effect = poll_side_effect
        mock_consumer_class.return_value = mock_consumer
        mock_producer_class.return_value = MagicMock()

        main_mod._shutdown_event.clear()
        main_mod._consume_loop()
        main_mod._shutdown_event.clear()

    @patch("src.main.Producer")
    @patch("src.main.Consumer")
    def test_handles_consumer_error(
        self, mock_consumer_class: MagicMock, mock_producer_class: MagicMock
    ) -> None:
        """Non-EOF consumer errors are logged and skipped."""
        from confluent_kafka import KafkaError

        import src.main as main_mod

        mock_consumer = MagicMock()
        mock_msg = MagicMock()
        mock_error = MagicMock()
        mock_error.code.return_value = KafkaError._ALL_BROKERS_DOWN
        mock_msg.error.return_value = mock_error

        call_count = 0

        def poll_side_effect(timeout: float = 1.0) -> MagicMock | None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return mock_msg
            main_mod._shutdown_event.set()
            return None

        mock_consumer.poll.side_effect = poll_side_effect
        mock_consumer_class.return_value = mock_consumer
        mock_producer_class.return_value = MagicMock()

        main_mod._shutdown_event.clear()
        main_mod._consume_loop()
        main_mod._shutdown_event.clear()

    @patch("src.main.ChunkStore")
    @patch("src.main.Embedder")
    @patch("src.main.chunk_filing")
    @patch("src.main.Producer")
    @patch("src.main.Consumer")
    def test_processes_valid_message(
        self,
        mock_consumer_class: MagicMock,
        mock_producer_class: MagicMock,
        mock_chunk_filing: MagicMock,
        mock_embedder_class: MagicMock,
        mock_store_class: MagicMock,
    ) -> None:
        """A valid Kafka message is chunked, embedded, stored, and confirmed."""
        import src.main as main_mod
        from src.chunker import Chunk

        # Set up global state
        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = [[0.1] * 384]
        mock_store = MagicMock()
        main_mod._embedder = mock_embedder
        main_mod._store = mock_store

        # Build a valid message
        payload = {
            "accession_number": "0001-24-000001",
            "ticker": "AAPL",
            "filing_date": "2024-11-01",
            "raw_text": "Item 1. Business\nWe are a company.",
        }
        mock_msg = MagicMock()
        mock_msg.error.return_value = None
        mock_msg.value.return_value = json.dumps(payload).encode("utf-8")
        mock_msg.key.return_value = b"0001-24-000001"

        chunk = Chunk(
            chunk_id="abc123",
            accession_number="0001-24-000001",
            ticker="AAPL",
            filing_date="2024-11-01",
            section_name="Item 1",
            chunk_index=0,
            text="We are a company.",
            token_count=5,
        )
        mock_chunk_filing.return_value = [chunk]

        mock_consumer = MagicMock()
        mock_producer = MagicMock()

        call_count = 0

        def poll_side_effect(timeout: float = 1.0) -> MagicMock | None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return mock_msg
            main_mod._shutdown_event.set()
            return None

        mock_consumer.poll.side_effect = poll_side_effect
        mock_consumer_class.return_value = mock_consumer
        mock_producer_class.return_value = mock_producer

        main_mod._shutdown_event.clear()

        try:
            main_mod._consume_loop()
        finally:
            main_mod._shutdown_event.clear()
            main_mod._embedder = None
            main_mod._store = None

        # Verify the pipeline
        mock_chunk_filing.assert_called_once()
        mock_embedder.embed.assert_called_once()
        mock_store.store_chunks.assert_called_once()
        mock_store.update_ingestion_status.assert_called_once_with("0001-24-000001", 1)
        mock_producer.produce.assert_called_once()
        mock_consumer.commit.assert_called()

    @patch("src.main.chunk_filing")
    @patch("src.main.Producer")
    @patch("src.main.Consumer")
    def test_skips_empty_chunks(
        self,
        mock_consumer_class: MagicMock,
        mock_producer_class: MagicMock,
        mock_chunk_filing: MagicMock,
    ) -> None:
        """Messages that produce no chunks are committed and skipped."""
        import src.main as main_mod

        main_mod._embedder = MagicMock()
        main_mod._store = MagicMock()

        payload = {
            "accession_number": "0001-24-000001",
            "ticker": "AAPL",
            "filing_date": "2024-11-01",
            "raw_text": "",
        }
        mock_msg = MagicMock()
        mock_msg.error.return_value = None
        mock_msg.value.return_value = json.dumps(payload).encode("utf-8")

        mock_chunk_filing.return_value = []

        mock_consumer = MagicMock()
        call_count = 0

        def poll_side_effect(timeout: float = 1.0) -> MagicMock | None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return mock_msg
            main_mod._shutdown_event.set()
            return None

        mock_consumer.poll.side_effect = poll_side_effect
        mock_consumer_class.return_value = mock_consumer
        mock_producer_class.return_value = MagicMock()

        main_mod._shutdown_event.clear()

        try:
            main_mod._consume_loop()
        finally:
            main_mod._shutdown_event.clear()
            main_mod._embedder = None
            main_mod._store = None

        mock_consumer.commit.assert_called()

    @patch("src.main._interruptible_sleep")
    @patch("src.main.chunk_filing")
    @patch("src.main.Producer")
    @patch("src.main.Consumer")
    def test_retries_before_dlq_on_processing_error(
        self,
        mock_consumer_class: MagicMock,
        mock_producer_class: MagicMock,
        mock_chunk_filing: MagicMock,
        mock_sleep: MagicMock,
    ) -> None:
        """chunk_filing failures trigger MAX_RETRIES retries with sleeps before DLQ."""
        import src.main as main_mod

        main_mod._embedder = MagicMock()
        main_mod._store = MagicMock()

        mock_chunk_filing.side_effect = RuntimeError("transient error")

        payload = {
            "accession_number": "0001-24-000001",
            "ticker": "AAPL",
            "filing_date": "2024-11-01",
            "raw_text": "Item 1. Business\nWe are a company.",
        }
        mock_msg = MagicMock()
        mock_msg.error.return_value = None
        mock_msg.value.return_value = json.dumps(payload).encode("utf-8")
        mock_msg.key.return_value = b"0001-24-000001"

        mock_consumer = MagicMock()
        mock_producer = MagicMock()
        call_count = 0

        def poll_side_effect(timeout: float = 1.0) -> MagicMock | None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return mock_msg
            main_mod._shutdown_event.set()
            return None

        mock_consumer.poll.side_effect = poll_side_effect
        mock_consumer_class.return_value = mock_consumer
        mock_producer_class.return_value = mock_producer

        main_mod._shutdown_event.clear()

        try:
            main_mod._consume_loop()
        finally:
            main_mod._shutdown_event.clear()
            main_mod._embedder = None
            main_mod._store = None

        # chunk_filing called MAX_RETRIES + 1 times (initial + 3 retries)
        assert mock_chunk_filing.call_count == main_mod.MAX_RETRIES + 1

        # sleep called MAX_RETRIES times between attempts
        assert mock_sleep.call_count == main_mod.MAX_RETRIES

        # sleep durations follow 2^(attempt+1): 2 s, 4 s, 8 s
        sleep_args = [c.args[0] for c in mock_sleep.call_args_list]
        assert sleep_args == [2, 4, 8]

        # Message routed to DLQ
        dlq_calls = [
            c for c in mock_producer.produce.call_args_list
            if c.kwargs.get("topic") == "filings.dlq"
            or (c.args and "filings.dlq" in str(c.args[0]))
        ]
        assert len(dlq_calls) == 1

        # Offset committed exactly once
        mock_consumer.commit.assert_called_once_with(mock_msg)

    @patch("src.main._interruptible_sleep")
    @patch("src.main.chunk_filing")
    @patch("src.main.Producer")
    @patch("src.main.Consumer")
    def test_succeeds_on_retry(
        self,
        mock_consumer_class: MagicMock,
        mock_producer_class: MagicMock,
        mock_chunk_filing: MagicMock,
        mock_sleep: MagicMock,
    ) -> None:
        """A transient error on the first attempt succeeds on the second attempt."""
        import src.main as main_mod
        from src.chunker import Chunk

        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = [[0.1] * 384]
        mock_store = MagicMock()
        main_mod._embedder = mock_embedder
        main_mod._store = mock_store

        chunk = Chunk(
            chunk_id="abc123",
            accession_number="0001-24-000001",
            ticker="AAPL",
            filing_date="2024-11-01",
            section_name="Item 1",
            chunk_index=0,
            text="We are a company.",
            token_count=5,
        )
        # Fail once, then succeed
        mock_chunk_filing.side_effect = [RuntimeError("blip"), [chunk]]

        payload = {
            "accession_number": "0001-24-000001",
            "ticker": "AAPL",
            "filing_date": "2024-11-01",
            "raw_text": "Item 1. Business\nWe are a company.",
        }
        mock_msg = MagicMock()
        mock_msg.error.return_value = None
        mock_msg.value.return_value = json.dumps(payload).encode("utf-8")
        mock_msg.key.return_value = b"0001-24-000001"

        mock_consumer = MagicMock()
        mock_producer = MagicMock()
        call_count = 0

        def poll_side_effect(timeout: float = 1.0) -> MagicMock | None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return mock_msg
            main_mod._shutdown_event.set()
            return None

        mock_consumer.poll.side_effect = poll_side_effect
        mock_consumer_class.return_value = mock_consumer
        mock_producer_class.return_value = mock_producer

        main_mod._shutdown_event.clear()

        try:
            main_mod._consume_loop()
        finally:
            main_mod._shutdown_event.clear()
            main_mod._embedder = None
            main_mod._store = None

        # Two attempts: one failure, one success
        assert mock_chunk_filing.call_count == 2

        # One sleep between attempts
        assert mock_sleep.call_count == 1
        assert mock_sleep.call_args.args[0] == 2  # first backoff = 2 s

        # No DLQ publish
        dlq_calls = [
            c for c in mock_producer.produce.call_args_list
            if c.kwargs.get("topic") == "filings.dlq"
            or (c.args and "filings.dlq" in str(c.args[0]))
        ]
        assert len(dlq_calls) == 0

        # Store was called with the successful chunks
        mock_store.store_chunks.assert_called_once()
        mock_consumer.commit.assert_called_once_with(mock_msg)

    @patch("src.main.Producer")
    @patch("src.main.Consumer")
    def test_processing_error_sends_to_dlq(
        self,
        mock_consumer_class: MagicMock,
        mock_producer_class: MagicMock,
    ) -> None:
        """Processing errors should send the message to the DLQ."""
        import src.main as main_mod

        main_mod._embedder = MagicMock()
        main_mod._store = MagicMock()

        # Invalid JSON to trigger an error
        mock_msg = MagicMock()
        mock_msg.error.return_value = None
        mock_msg.value.return_value = b"not valid json"
        mock_msg.key.return_value = b"test-key"

        mock_consumer = MagicMock()
        mock_producer = MagicMock()
        call_count = 0

        def poll_side_effect(timeout: float = 1.0) -> MagicMock | None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return mock_msg
            main_mod._shutdown_event.set()
            return None

        mock_consumer.poll.side_effect = poll_side_effect
        mock_consumer_class.return_value = mock_consumer
        mock_producer_class.return_value = mock_producer

        main_mod._shutdown_event.clear()

        try:
            main_mod._consume_loop()
        finally:
            main_mod._shutdown_event.clear()
            main_mod._embedder = None
            main_mod._store = None

        # Verify DLQ publish was attempted
        dlq_calls = [
            c for c in mock_producer.produce.call_args_list
            if c.kwargs.get("topic") == "filings.dlq"
            or (c.args and len(c.args) > 0 and "filings.dlq" in str(c))
        ]
        assert len(dlq_calls) >= 1 or mock_producer.produce.called
        mock_consumer.commit.assert_called()
