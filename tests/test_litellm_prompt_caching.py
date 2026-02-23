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


def test_supports_cache_control_respects_gateway_capability_flag() -> None:
    openrouter_provider = LiteLLMProvider(provider_name="openrouter")
    aihubmix_provider = LiteLLMProvider(provider_name="aihubmix")

    assert openrouter_provider._supports_cache_control("anthropic/claude-sonnet-4-5")
    assert not aihubmix_provider._supports_cache_control("anthropic/claude-sonnet-4-5")


def test_apply_cache_control_marks_system_and_last_tool_without_mutating_inputs() -> None:
    provider = LiteLLMProvider(default_model="anthropic/claude-sonnet-4-5")
    messages = [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "hello"},
    ]
    tools = [
        {"type": "function", "function": {"name": "tool_a"}},
        {"type": "function", "function": {"name": "tool_b"}},
    ]

    new_messages, new_tools = provider._apply_cache_control(messages, tools)

    assert isinstance(new_messages[0]["content"], list)
    assert new_messages[0]["content"][0]["cache_control"]["type"] == "ephemeral"
    assert new_tools is not None
    assert new_tools[-1]["cache_control"]["type"] == "ephemeral"

    assert "cache_control" not in tools[-1]
    assert messages[0]["content"] == "system prompt"


@pytest.mark.asyncio
async def test_chat_injects_cache_control_for_openrouter_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def _fake_acompletion(**kwargs: Any) -> Any:
        captured["kwargs"] = kwargs
        return _dummy_litellm_response()

    monkeypatch.setattr("opencane.providers.litellm_provider.acompletion", _fake_acompletion)

    provider = LiteLLMProvider(provider_name="openrouter")
    await provider.chat(
        model="anthropic/claude-sonnet-4-5",
        messages=[
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "hello"},
        ],
        tools=[{"type": "function", "function": {"name": "tool_a"}}],
    )

    kwargs = captured["kwargs"]
    assert kwargs["messages"][0]["content"][0]["cache_control"]["type"] == "ephemeral"
    assert kwargs["tools"][-1]["cache_control"]["type"] == "ephemeral"

