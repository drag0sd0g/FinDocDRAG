"""Kafka producer wrapper for publishing filing messages.

References:
  - TDD: FR-3 (publish to filings.raw topic with metadata)
  - TDD: Section 5.2.1 Kafka message schema
  - TDD: Section 5.3 Kafka topics (filings.raw)
  - TDD: Section 8.1.1 (kafka publish counter)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog
from confluent_kafka import KafkaError, Producer

from src.config import settings
from src.metrics import KAFKA_PUBLISH_TOTAL

if TYPE_CHECKING:
    from src.edgar_client import Filing

logger = structlog.get_logger()

TOPIC_FILINGS_RAW = "filings.raw"


def _delivery_callback(err: KafkaError | None, msg: Any) -> None:
    """Callback invoked on message delivery (or failure)."""
    if err is not None:
        KAFKA_PUBLISH_TOTAL.labels(topic=msg.topic(), status="error").inc()
        logger.error(
            "kafka_delivery_failed",
            topic=msg.topic(),
            error=str(err),
        )
    else:
        KAFKA_PUBLISH_TOTAL.labels(topic=msg.topic(), status="success").inc()
        logger.debug(
            "kafka_delivery_success",
            topic=msg.topic(),
            partition=msg.partition(),
            offset=msg.offset(),
        )


class FilingProducer:
    """Publishes Filing objects to the filings.raw Kafka topic.

    Message schema matches TDD Section 5.2.1:
    {accession_number, ticker, filing_date, filing_type,
     company_name, raw_text, source_url, published_at}
    """

    def __init__(self, bootstrap_servers: str | None = None) -> None:
        servers = bootstrap_servers or settings.kafka_bootstrap_servers
        self._producer = Producer(
            {
                "bootstrap.servers": servers,
                "client.id": "ingestion-service",
                "acks": "all",
                "message.max.bytes": 20971520,
            }
        )

    def publish_filing(self, filing: Filing) -> None:
        """Serialize a Filing and publish it to Kafka (FR-3)."""
        message = {
            "accession_number": filing.accession_number,
            "ticker": filing.ticker,
            "filing_date": filing.filing_date,
            "filing_type": filing.filing_type,
            "company_name": filing.company_name,
            "raw_text": filing.raw_text,
            "source_url": filing.source_url,
            "published_at": datetime.now(UTC).isoformat(),
        }

        self._producer.produce(
            topic=TOPIC_FILINGS_RAW,
            key=filing.accession_number,
            value=json.dumps(message),
            callback=_delivery_callback,
        )
        self._producer.poll(0)  # Trigger any pending delivery callbacks

    def flush(self, timeout: float = 10.0) -> int:
        """Wait for all in-flight messages to be delivered.

        Returns the number of messages still in the queue
        (0 means all delivered).
        """
        remaining: int = self._producer.flush(timeout)
        return remaining
