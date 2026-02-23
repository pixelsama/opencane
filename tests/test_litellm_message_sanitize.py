from __future__ import annotations

from typing import Any

import pytest

from opencane.providers.litellm_provider import LiteLLMProvider


def _dummy_litellm_response() -> Any:
    class _Message:
        content = "ok"
        tool_calls = None

    class _Choice:
        message = _Message()
        finish_reason = "stop"

    class _Usage:
        prompt_tokens = 1
        completion_tokens = 1
        total_tokens = 2

    class _Response:
        choices = [_Choice()]
        usage = _Usage()

    return _Response()


def test_sanitize_messages_strips_extra_keys_and_keeps_assistant_content_key() -> None:
    provider = LiteLLMProvider(default_model="openai/gpt-4.1")
    messages = [
        {"role": "user", "content": "hello", "reasoning_content": "x", "timestamp": "t"},
        {"role": "assistant", "tool_calls": [{"id": "tool-1"}], "reasoning_content": "y"},
    ]

    sanitized = provider._sanitize_messages(messages)

    assert sanitized[0] == {"role": "user", "content": "hello"}
    assert sanitized[1]["role"] == "assistant"
    assert sanitized[1]["tool_calls"] == [{"id": "tool-1"}]
    assert sanitized[1]["content"] is None
    assert "reasoning_content" not in sanitized[1]
    assert "timestamp" not in sanitized[0]

    # Source messages should remain unchanged.
    assert "reasoning_content" in messages[0]


@pytest.mark.asyncio
async def test_chat_passes_sanitized_messages_to_litellm(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def _fake_acompletion(**kwargs: Any) -> Any:
        captured["kwargs"] = kwargs
        return _dummy_litellm_response()

    monkeypatch.setattr("opencane.providers.litellm_provider.acompletion", _fake_acompletion)

    provider = LiteLLMProvider(default_model="openai/gpt-4.1")
    await provider.chat(
        model="openai/gpt-4.1",
        messages=[
            {"role": "assistant", "tool_calls": [{"id": "tool-1"}], "reasoning_content": "ignore-me"},
        ],
    )

    sent_messages = captured["kwargs"]["messages"]
    assert sent_messages == [{"role": "assistant", "tool_calls": [{"id": "tool-1"}], "content": None}]

