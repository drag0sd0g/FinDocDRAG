"""Anthropic (Claude) LLM backend.

References:
  - TDD: FR-18 (pluggable LLM backend)
  - Anthropic Python SDK: https://github.com/anthropics/anthropic-sdk-python
"""

from __future__ import annotations

import os

import structlog
from anthropic import AsyncAnthropic

from src.llm.backend import LLMResponse

logger = structlog.get_logger()

DEFAULT_MODEL = "claude-opus-4-6"


class AnthropicBackend:
    """Calls the Anthropic Messages API (Claude models)."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        api_key: str | None = None,
    ) -> None:
        # max_retries=2: exponential backoff on network errors and 5xx (TDD Section 7.1)
        self._client = AsyncAnthropic(
            api_key=api_key or os.getenv("ANTHROPIC_API_KEY", ""),
            max_retries=2,
        )
        self._model = model

    @property
    def model_name(self) -> str:
        return self._model

    async def generate(self, prompt: str, max_tokens: int = 1024) -> LLMResponse:
        """Call the Anthropic Messages API with the full RAG prompt."""
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )

        text = next((b.text for b in response.content if b.type == "text"), "")
        usage = response.usage

        logger.info(
            "anthropic_generate_complete",
            model=self._model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        )

        return LLMResponse(
            text=text,
            model=self._model,
            prompt_tokens=usage.input_tokens,
            completion_tokens=usage.output_tokens,
        )
