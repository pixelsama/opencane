from __future__ import annotations

import pytest

from opencane.bus.events import OutboundMessage
from opencane.bus.queue import MessageBus
from opencane.channels.slack import SlackChannel
from opencane.config.schema import SlackConfig


class _FakeSlackWebClient:
    def __init__(self) -> None:
        self.chat_calls: list[dict] = []
        self.file_calls: list[dict] = []

    async def chat_postMessage(self, **kwargs):  # type: ignore[no-untyped-def]  # noqa: N802
        self.chat_calls.append(kwargs)
        return None

    async def files_upload_v2(self, **kwargs):  # type: ignore[no-untyped-def]
        self.file_calls.append(kwargs)
        return None


@pytest.mark.asyncio
async def test_slack_send_uploads_media_and_uses_thread_for_channels() -> None:
    channel = SlackChannel(config=SlackConfig(enabled=True), bus=MessageBus())
    fake_client = _FakeSlackWebClient()
    channel._web_client = fake_client  # type: ignore[assignment]

    await channel.send(
        OutboundMessage(
            channel="slack",
            chat_id="C123",
            content="hello",
            media=["/tmp/a.png", "/tmp/b.pdf"],
            metadata={"slack": {"thread_ts": "111.222", "channel_type": "channel"}},
        )
    )

    assert len(fake_client.chat_calls) == 1
    assert fake_client.chat_calls[0]["thread_ts"] == "111.222"
    assert [c["file"] for c in fake_client.file_calls] == ["/tmp/a.png", "/tmp/b.pdf"]
    assert all(c["thread_ts"] == "111.222" for c in fake_client.file_calls)


@pytest.mark.asyncio
async def test_slack_send_does_not_thread_in_dm_and_skips_empty_text() -> None:
    channel = SlackChannel(config=SlackConfig(enabled=True), bus=MessageBus())
    fake_client = _FakeSlackWebClient()
    channel._web_client = fake_client  # type: ignore[assignment]

    await channel.send(
        OutboundMessage(
            channel="slack",
            chat_id="D123",
            content="",
            media=["/tmp/a.png"],
            metadata={"slack": {"thread_ts": "333.444", "channel_type": "im"}},
        )
    )

    assert fake_client.chat_calls == []
    assert len(fake_client.file_calls) == 1
    assert fake_client.file_calls[0]["thread_ts"] is None
