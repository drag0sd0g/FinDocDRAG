"""LLM backend protocol — interface implemented by Ollama and OpenAI.

References:
  - TDD: Section 5.2.3 LLM Backend Abstraction
  - TDD: FR-18 (two backends selectable via LLM_BACKEND env var)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class LLMResponse:
    """Response from an LLM backend."""

    text: str
    model: str
    prompt_tokens: int
    completion_tokens: int


class LLMBackend(Protocol):
    """Interface that both LLM backends implement."""

    async def generate(self, prompt: str, max_tokens: int = 1024) -> LLMResponse:
        """Generate a completion from the given prompt."""
        ...

    @property
    def model_name(self) -> str:
        """Return the name of the model being used."""
        ...
