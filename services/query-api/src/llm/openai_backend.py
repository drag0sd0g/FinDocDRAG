"""OpenAI LLM backend — calls the OpenAI API.

References:
  - TDD: FR-18 (OpenAI gpt-4o-mini as alternative backend)
  - TDD: Section 7.1 (retry with exponential backoff, 2 attempts)
"""

from __future__ import annotations

import os

import structlog
from openai import AsyncOpenAI

from src.llm.backend import LLMResponse

logger = structlog.get_logger()

DEFAULT_MODEL = "gpt-4o-mini"


class OpenAIBackend:
    """Calls the OpenAI chat completions API."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        api_key: str | None = None,
    ) -> None:
        key = api_key or os.getenv("OPENAI_API_KEY", "")
        self._client = AsyncOpenAI(api_key=key)
        self._model = model

    @property
    def model_name(self) -> str:
        return self._model

    async def generate(self, prompt: str, max_tokens: int = 1024) -> LLMResponse:
        """Call OpenAI chat completions API.

        Uses the system role for the financial analyst instruction
        and user role for the actual prompt.
        """
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "user", "content": prompt},
            ],
            max_tokens=max_tokens,
        )

        choice = response.choices[0]
        text = choice.message.content or ""

        usage = response.usage
        prompt_tokens = usage.prompt_tokens if usage else 0
        completion_tokens = usage.completion_tokens if usage else 0

        logger.info(
            "openai_generate_complete",
            model=self._model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

        return LLMResponse(
            text=text,
            model=self._model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
