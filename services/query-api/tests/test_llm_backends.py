"""Unit tests for the OllamaBackend and OpenAIBackend.

All HTTP/API calls are mocked — no real LLM servers needed.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.llm.backend import LLMResponse

# ── OllamaBackend ────────────────────────────────────────────────


class TestOllamaBackend:
    """Tests for the Ollama LLM backend."""

    def test_model_name_property(self) -> None:
        from src.llm.ollama_backend import OllamaBackend

        backend = OllamaBackend(base_url="http://localhost:11434", model="mistral:7b")
        assert backend.model_name == "mistral:7b"

    def test_strips_trailing_slash(self) -> None:
        from src.llm.ollama_backend import OllamaBackend

        backend = OllamaBackend(base_url="http://localhost:11434/", model="mistral:7b")
        assert backend._base_url == "http://localhost:11434"

    @pytest.mark.asyncio
    async def test_generate_success(self) -> None:
        from src.llm.ollama_backend import OllamaBackend

        ollama_response = {
            "response": "Apple's risk factors include...",
            "prompt_eval_count": 50,
            "eval_count": 30,
        }

        mock_resp = AsyncMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = AsyncMock(return_value=ollama_response)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_resp),
            __aexit__=AsyncMock(return_value=False),
        ))

        with patch("src.llm.ollama_backend.aiohttp.ClientSession") as mock_cs:
            mock_cs.return_value = AsyncMock(
                __aenter__=AsyncMock(return_value=mock_session),
                __aexit__=AsyncMock(return_value=False),
            )
            # Manually mock the nested context managers
            mock_session_ctx = AsyncMock()
            mock_post_ctx = AsyncMock()

            mock_post_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
            mock_post_ctx.__aexit__ = AsyncMock(return_value=False)

            mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_session.post.return_value = mock_post_ctx

            mock_cs.return_value = mock_session_ctx

            backend = OllamaBackend(base_url="http://localhost:11434", model="mistral:7b")
            result = await backend.generate("Tell me about risks")

        assert isinstance(result, LLMResponse)
        assert result.text == "Apple's risk factors include..."
        assert result.model == "mistral:7b"
        assert result.prompt_tokens == 50
        assert result.completion_tokens == 30

    @pytest.mark.asyncio
    async def test_generate_missing_optional_fields(self) -> None:
        """When Ollama doesn't return token counts, defaults to 0."""
        from src.llm.ollama_backend import OllamaBackend

        ollama_response = {"response": "Answer text"}

        mock_resp = AsyncMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = AsyncMock(return_value=ollama_response)

        mock_post_ctx = MagicMock()
        mock_post_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_post_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_post_ctx)

        mock_session_ctx = MagicMock()
        mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("src.llm.ollama_backend.aiohttp.ClientSession", return_value=mock_session_ctx):
            backend = OllamaBackend(base_url="http://localhost:11434", model="mistral:7b")
            result = await backend.generate("Question")

        assert result.prompt_tokens == 0
        assert result.completion_tokens == 0


# ── OpenAIBackend ────────────────────────────────────────────────


class TestOpenAIBackend:
    """Tests for the OpenAI LLM backend."""

    @patch("src.llm.openai_backend.AsyncOpenAI")
    def test_model_name_property(self, mock_openai: MagicMock) -> None:
        from src.llm.openai_backend import OpenAIBackend

        backend = OpenAIBackend(model="gpt-4o-mini", api_key="test-key")
        assert backend.model_name == "gpt-4o-mini"

    @pytest.mark.asyncio
    async def test_generate_success(self) -> None:
        from src.llm.openai_backend import OpenAIBackend

        mock_message = MagicMock()
        mock_message.content = "Revenue was $394B."

        mock_choice = MagicMock()
        mock_choice.message = mock_message

        mock_usage = MagicMock()
        mock_usage.prompt_tokens = 100
        mock_usage.completion_tokens = 25

        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.usage = mock_usage

        with patch("src.llm.openai_backend.AsyncOpenAI") as mock_openai:
            mock_client = AsyncMock()
            mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
            mock_openai.return_value = mock_client

            backend = OpenAIBackend(model="gpt-4o-mini", api_key="test-key")
            result = await backend.generate("What is revenue?")

        assert isinstance(result, LLMResponse)
        assert result.text == "Revenue was $394B."
        assert result.model == "gpt-4o-mini"
        assert result.prompt_tokens == 100
        assert result.completion_tokens == 25

    @pytest.mark.asyncio
    async def test_generate_no_content(self) -> None:
        """When message.content is None, text should be empty string."""
        from src.llm.openai_backend import OpenAIBackend

        mock_message = MagicMock()
        mock_message.content = None

        mock_choice = MagicMock()
        mock_choice.message = mock_message

        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.usage = None

        with patch("src.llm.openai_backend.AsyncOpenAI") as mock_openai:
            mock_client = AsyncMock()
            mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
            mock_openai.return_value = mock_client

            backend = OpenAIBackend(model="gpt-4o-mini", api_key="test-key")
            result = await backend.generate("Question")

        assert result.text == ""
        assert result.prompt_tokens == 0
        assert result.completion_tokens == 0
