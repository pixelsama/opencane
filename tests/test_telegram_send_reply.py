from __future__ import annotations

import pytest

from opencane.bus.events import OutboundMessage
from opencane.bus.queue import MessageBus
from opencane.channels.telegram import TelegramChannel
from opencane.config.schema import TelegramConfig


class _FakeBot:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def send_message(self, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(kwargs)
        return None


class _FakeApp:
    def __init__(self, bot: _FakeBot) -> None:
        self.bot = bot


@pytest.mark.asyncio
async def test_telegram_send_uses_reply_when_enabled() -> None:
    bot = _FakeBot()
    channel = TelegramChannel(
        config=TelegramConfig(enabled=True, reply_to_message=True),
        bus=MessageBus(),
    )
    channel._app = _FakeApp(bot)  # type: ignore[assignment]

    await channel.send(
        OutboundMessage(
            channel="telegram",
            chat_id="123",
            content="hello",
            metadata={"message_id": 456},
        )
    )

    assert len(bot.calls) == 1
    assert bot.calls[0]["reply_to_message_id"] == 456
    assert bot.calls[0]["allow_sending_without_reply"] is True


@pytest.mark.asyncio
async def test_telegram_send_skips_reply_when_disabled() -> None:
    bot = _FakeBot()
    channel = TelegramChannel(
        config=TelegramConfig(enabled=True, reply_to_message=False),
        bus=MessageBus(),
    )
    channel._app = _FakeApp(bot)  # type: ignore[assignment]

    await channel.send(
        OutboundMessage(
            channel="telegram",
            chat_id="123",
            content="hello",
            metadata={"message_id": 456},
        )
    )

    assert len(bot.calls) == 1
    assert "reply_to_message_id" not in bot.calls[0]
    assert "allow_sending_without_reply" not in bot.calls[0]

