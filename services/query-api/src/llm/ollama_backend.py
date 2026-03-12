"""Ollama LLM backend — calls local Ollama REST API.

References:
  - TDD: Section 5.2.3 (Ollama backend, mistral:7b default)
  - TDD: Section 7.1 (timeout after 30s, return 504 with partial response)
"""

from __future__ import annotations

import aiohttp
import structlog

from src.llm.backend import LLMResponse

logger = structlog.get_logger()

DEFAULT_OLLAMA_URL = "http://ollama:11434"
DEFAULT_MODEL = "mistral:7b"


class OllamaBackend:
    """Calls the local Ollama server's /api/generate endpoint."""

    def __init__(
        self,
        base_url: str = DEFAULT_OLLAMA_URL,
        model: str = DEFAULT_MODEL,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)

    @property
    def model_name(self) -> str:
        return self._model

    async def generate(self, prompt: str, max_tokens: int = 1024) -> LLMResponse:
        """Call Ollama /api/generate and return the response.

        Raises aiohttp.ClientError or asyncio.TimeoutError on failure
        (caller handles graceful degradation).
        """
        payload = {
            "model": self._model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "num_predict": max_tokens,
            },
        }

        async with aiohttp.ClientSession(timeout=self._timeout) as session:
            async with session.post(
                f"{self._base_url}/api/generate",
                json=payload,
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()

        text = data.get("response", "")
        # Ollama provides token counts in some versions
        prompt_tokens = data.get("prompt_eval_count", 0)
        completion_tokens = data.get("eval_count", 0)

        logger.info(
            "ollama_generate_complete",
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