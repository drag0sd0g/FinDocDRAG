"""Application configuration — loads from environment variables and tickers.yml.

References:
  - TDD: FR-2 (ticker configuration file)
  - TDD: Section 5.2.1 (service description)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Central configuration sourced from environment variables.

    Variable names match .env.example exactly (case-insensitive).
    """

    # --- PostgreSQL ---
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_db: str = "findocdrag"
    postgres_user: str = "findocdrag"
    postgres_password: str = "changeme"

    # --- Kafka ---
    kafka_bootstrap_servers: str = "kafka:9092"

    # --- SEC EDGAR ---
    edgar_user_agent: str = "FinDocDRAG findocdrag@example.com"
    edgar_rate_limit_rps: int = 10

    # --- Logging ---
    log_level: str = "INFO"

    # --- Paths ---
    tickers_config_path: str = Field(default="config/tickers.yml")

    @property
    def postgres_dsn(self) -> str:
        """Build a PostgreSQL connection string from individual components."""
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    model_config = {"env_prefix": "", "case_sensitive": False}


def load_tickers(path: str | Path) -> list[dict[str, str]]:
    """Load ticker list from a YAML config file.

    Returns a list of dicts with 'symbol' and 'name' keys.
    Returns an empty list if the file does not exist.
    """
    config_path = Path(path)
    if not config_path.exists():
        return []
    with open(config_path) as f:
        data: dict[str, Any] = yaml.safe_load(f)
    return list(data.get("tickers", []))


# Module-level singleton — import this from other modules.
settings = Settings()
