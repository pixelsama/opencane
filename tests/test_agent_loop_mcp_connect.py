from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from opencane.agent.loop import AgentLoop
from opencane.bus.queue import MessageBus
from opencane.providers.base import LLMProvider, LLMResponse


class _DummyProvider(LLMProvider):
    async def chat(  # type: ignore[override]
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        del messages, tools, model, max_tokens, temperature
        return LLMResponse(content="ok")

    def get_default_model(self) -> str:
        return "fake-model"


@pytest.mark.asyncio
async def test_mcp_connect_failure_can_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bus = MessageBus()
    loop = AgentLoop(
        bus=bus,
        provider=_DummyProvider(),
        workspace=tmp_path,
        mcp_servers={"demo": object()},
    )

    calls = 0

    async def _fake_connect(mcp_servers, registry, stack) -> None:  # type: ignore[no-untyped-def]
        del mcp_servers, registry, stack
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary failure")

    monkeypatch.setattr("opencane.agent.tools.mcp.connect_mcp_servers", _fake_connect)

    await loop._connect_mcp()
    assert calls == 1
    assert loop._mcp_connected is False
    assert loop._mcp_connecting is False
    assert loop._mcp_stack is None

    await loop._connect_mcp()
    assert calls == 2
    assert loop._mcp_connected is True
    assert loop._mcp_connecting is False

    await loop.close_mcp()


@pytest.mark.asyncio
async def test_mcp_connect_concurrent_calls_are_serialized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bus = MessageBus()
    loop = AgentLoop(
        bus=bus,
        provider=_DummyProvider(),
        workspace=tmp_path,
        mcp_servers={"demo": object()},
    )

    calls = 0

    async def _fake_connect(mcp_servers, registry, stack) -> None:  # type: ignore[no-untyped-def]
        del mcp_servers, registry, stack
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)

    monkeypatch.setattr("opencane.agent.tools.mcp.connect_mcp_servers", _fake_connect)

    await asyncio.gather(loop._connect_mcp(), loop._connect_mcp(), loop._connect_mcp())

    assert calls == 1
    assert loop._mcp_connected is True
    assert loop._mcp_connecting is False

    await loop.close_mcp()

