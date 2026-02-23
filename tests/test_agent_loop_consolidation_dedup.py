from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from opencane.agent.loop import AgentLoop
from opencane.bus.events import InboundMessage
from opencane.bus.queue import MessageBus
from opencane.providers.base import LLMProvider, LLMResponse


class _Provider(LLMProvider):
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
async def test_consolidation_task_is_deduplicated_per_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop = AgentLoop(
        bus=MessageBus(),
        provider=_Provider(),
        workspace=tmp_path,
        memory_window=2,
    )

    session = loop.sessions.get_or_create("cli:chat-dedup")
    for i in range(3):
        session.add_message("user", f"user-{i}")
    loop.sessions.save(session)

    started = 0
    release = asyncio.Event()

    async def _fake_consolidate(target_session, archive_all: bool = False):  # type: ignore[no-untyped-def]
        del target_session, archive_all
        nonlocal started
        started += 1
        await release.wait()

    monkeypatch.setattr(loop, "_consolidate_memory", _fake_consolidate)

    created_tasks: list[asyncio.Task[Any]] = []
    real_create_task = asyncio.create_task

    def _track_create_task(coro):  # type: ignore[no-untyped-def]
        task = real_create_task(coro)
        created_tasks.append(task)
        return task

    monkeypatch.setattr("opencane.agent.loop.asyncio.create_task", _track_create_task)

    await loop._process_message(
        InboundMessage(channel="cli", sender_id="u1", chat_id="chat-dedup", content="first")
    )
    await asyncio.sleep(0)
    assert "cli:chat-dedup" in loop._consolidating

    await loop._process_message(
        InboundMessage(channel="cli", sender_id="u1", chat_id="chat-dedup", content="second")
    )
    await asyncio.sleep(0)

    assert started == 1
    assert len(created_tasks) == 1

    release.set()
    await asyncio.gather(*created_tasks)
    assert "cli:chat-dedup" not in loop._consolidating

