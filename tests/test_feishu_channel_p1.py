from __future__ import annotations

import json

import pytest

from opencane.bus.events import OutboundMessage
from opencane.bus.queue import MessageBus
from opencane.channels.feishu import FeishuChannel, _extract_share_card_content
from opencane.config.schema import FeishuConfig


def test_extract_share_card_content_interactive_card() -> None:
    content = {
        "header": {"title": {"content": "Daily Summary"}},
        "elements": [
            {"tag": "markdown", "content": "hello"},
            {"tag": "button", "url": "https://example.com", "text": {"content": "Open"}},
        ],
    }
    text = _extract_share_card_content(content, "interactive")
    assert "title: Daily Summary" in text
    assert "hello" in text
    assert "link: https://example.com" in text


@pytest.mark.asyncio
async def test_feishu_send_uploads_media_with_expected_types(monkeypatch: pytest.MonkeyPatch) -> None:
    channel = FeishuChannel(config=FeishuConfig(enabled=True), bus=MessageBus())
    channel._client = object()

    monkeypatch.setattr("opencane.channels.feishu.os.path.isfile", lambda _path: True)
    monkeypatch.setattr(channel, "_upload_image_sync", lambda _path: "img-key")
    monkeypatch.setattr(channel, "_upload_file_sync", lambda _path: "file-key")

    sent: list[tuple[str, str]] = []

    def _fake_send(receive_id_type: str, receive_id: str, msg_type: str, content: str) -> bool:
        del receive_id_type, receive_id
        sent.append((msg_type, content))
        return True

    monkeypatch.setattr(channel, "_send_message_sync", _fake_send)

    await channel.send(
        OutboundMessage(
            channel="feishu",
            chat_id="oc_chat",
            content="hello world",
            media=["/tmp/a.png", "/tmp/b.opus", "/tmp/c.pdf"],
        )
    )

    assert [item[0] for item in sent] == ["image", "audio", "file", "interactive"]
    assert json.loads(sent[0][1])["image_key"] == "img-key"
    assert json.loads(sent[1][1])["file_key"] == "file-key"
    assert json.loads(sent[2][1])["file_key"] == "file-key"
    assert "elements" in json.loads(sent[3][1])


@pytest.mark.asyncio
async def test_feishu_on_message_for_image_attaches_media(monkeypatch: pytest.MonkeyPatch) -> None:
    channel = FeishuChannel(config=FeishuConfig(enabled=True), bus=MessageBus())
    channel._client = object()

    async def _fake_add_reaction(_message_id: str, _emoji: str = "THUMBSUP") -> None:
        return None

    async def _fake_download(msg_type: str, content_json: dict, message_id: str | None = None):  # type: ignore[no-untyped-def]
        del content_json, message_id
        assert msg_type == "image"
        return "/tmp/pic.jpg", "[image: pic.jpg]"

    captured: dict = {}

    async def _fake_handle_message(**kwargs):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        return None

    monkeypatch.setattr(channel, "_add_reaction", _fake_add_reaction)
    monkeypatch.setattr(channel, "_download_and_save_media", _fake_download)
    monkeypatch.setattr(channel, "_handle_message", _fake_handle_message)

    class _SenderID:
        open_id = "ou_user"

    class _Sender:
        sender_type = "user"
        sender_id = _SenderID()

    class _Message:
        message_id = "m-1"
        chat_id = "oc-group"
        chat_type = "group"
        message_type = "image"
        content = '{"image_key":"img_key"}'

    class _Event:
        sender = _Sender()
        message = _Message()

    class _Data:
        event = _Event()

    await channel._on_message(_Data())

    assert captured["chat_id"] == "oc-group"
    assert captured["media"] == ["/tmp/pic.jpg"]
    assert captured["content"] == "[image: pic.jpg]"
    assert captured["metadata"]["msg_type"] == "image"

