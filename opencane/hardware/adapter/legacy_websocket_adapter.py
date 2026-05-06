"""
Legacy WebSocket adapter for old blind cane hardware (demo mode).

Maps old protocol (session.update/audio_data/input_audio_buffer.commit) to canonical protocol.
This is a compatibility shim to allow reuse of legacy hardware without modification.

Protocol mapping:
- session.update → hello
- audio_data (AMR/WAV) → audio_chunk (with transcoding if needed)
- input_audio_buffer.commit → listen_stop
- response.create → (consumed by adapter, not forwarded to runtime)

Reverse mapping:
- hello_ack → session.updated
- ack → input_audio_buffer.committed
- tts_start → response.created
- tts_chunk → audio/text response
- tts_stop → response.audio.done
"""

from __future__ import annotations

import asyncio
import base64
import logging
from typing import Any

from opencane.hardware.adapter.base import GatewayAdapter
from opencane.hardware.protocol import (
    CanonicalEnvelope,
    DeviceEventType,
    make_event,
)

log = logging.getLogger(__name__)

_SENTINEL = object()


class LegacyWebSocketAdapter(GatewayAdapter):
    """
    Adapter for legacy hardware using old WebSocket protocol.

    Inherits from GatewayAdapter and implements protocol translation.
    """

    name = "legacy_demo"
    transport = "ws_legacy"

    def __init__(self, config: dict[str, Any]):
        self.device_id = config.get("device_id", "legacy-device-001")
        self.session_map = {}  # Maps old session_id -> canonical session_id
        self.response_create_consumed = set()  # Track response.create messages
        self._running = False
        self._queue: asyncio.Queue[CanonicalEnvelope | object] = asyncio.Queue()

        # Event type aliases: old name -> canonical name
        self._event_type_aliases = {
            "session.update": "hello",
            "audio_data": "audio_chunk",
            "input_audio_buffer.commit": "listen_stop",
            # response.create is consumed by adapter, not forwarded
        }

        # Command type aliases: canonical name -> old name
        self._command_type_aliases = {
            "hello_ack": "session.updated",
            "ack": "input_audio_buffer.committed",
            "tts_start": "response.created",
            "tts_chunk": "response.audio.chunk",
            "tts_stop": "response.audio.done",
        }

    async def start(self):
        """Initialize adapter."""
        self._running = True
        log.info(f"[{self.name}] Adapter started")

    async def stop(self):
        """Shutdown adapter."""
        self._running = False
        await self._queue.put(_SENTINEL)
        log.info(f"[{self.name}] Adapter stopped")

    async def recv_events(self):
        """Receive events from queue and yield to runtime."""
        while self._running:
            try:
                item = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                if item is _SENTINEL:
                    break
                yield item
            except asyncio.TimeoutError:
                continue

    async def send_command(self, cmd: CanonicalEnvelope) -> None:
        """
        Send command to hardware (translate from canonical to old protocol).

        For demo purposes, we log the command.
        In a real scenario, this would send via WebSocket.
        """
        old_type = self._command_type_aliases.get(cmd.type, cmd.type)

        # Build old protocol message
        old_msg = {
            "type": old_type,
            "seq": cmd.seq,
            "device_id": cmd.device_id,
            "session_id": cmd.session_id,
            "trace_id": cmd.msg_id,
            "payload": cmd.payload or {},
        }

        log.info(f"[{self.name}] send_command: canonical={cmd.type} → old={old_type}")
        log.debug(f"[{self.name}] Message: {old_msg}")

    def _translate_session_update(self, old_msg: dict[str, Any]) -> CanonicalEnvelope:
        """Translate session.update → hello."""
        old_session_id = old_msg.get("session_id")
        device_id = old_msg.get("device_id", self.device_id)

        # Map old session to canonical (1:1 for demo)
        canonical_session_id = old_session_id
        self.session_map[old_session_id] = canonical_session_id

        # Extract payload
        old_payload = old_msg.get("payload", {})

        # Build canonical hello event
        canonical_event = make_event(
            DeviceEventType.HELLO,
            device_id=device_id,
            session_id=canonical_session_id,
            seq=old_msg.get("seq", 0),
            payload={
                "device_token": old_payload.get("device_token"),
                "device_model": old_payload.get("device_model"),
                "capabilities": old_payload.get("capabilities", ["asr", "tts"]),
            },
        )
        canonical_event.msg_id = old_msg.get("trace_id", canonical_event.msg_id)

        log.info(f"[{self.name}] Translated: session.update → hello (session={canonical_session_id})")
        return canonical_event

    def _translate_audio_data(self, old_msg: dict[str, Any]) -> CanonicalEnvelope | None:
        """
        Translate audio_data (AMR/WAV) → audio_chunk.

        Note: In a real scenario, if encoding is AMR, we would transcode to WAV.
        For demo, we pass through as-is and let transcription handle it.
        """
        old_payload = old_msg.get("payload", {})
        audio_b64 = old_payload.get("audio_b64", "")
        encoding = old_payload.get("encoding", "amr")

        # Decode audio to get size
        try:
            audio_bytes = base64.b64decode(audio_b64)
        except Exception as e:
            log.error(f"[{self.name}] Failed to decode audio: {e}")
            return None

        # Build canonical audio_chunk event
        canonical_event = make_event(
            DeviceEventType.AUDIO_CHUNK,
            device_id=old_msg.get("device_id", self.device_id),
            session_id=old_msg.get("session_id"),
            seq=old_msg.get("seq", 0),
            payload={
                "audio_b64": audio_b64,  # Keep base64
                "encoding": encoding,
                "sample_rate": old_payload.get("sample_rate", 8000 if encoding == "amr" else 16000),
                "size_bytes": len(audio_bytes),
            },
        )
        canonical_event.msg_id = old_msg.get("trace_id", canonical_event.msg_id)

        log.info(f"[{self.name}] Translated: audio_data → audio_chunk (encoding={encoding}, size={len(audio_bytes)})")
        return canonical_event

    def _translate_input_audio_buffer_commit(self, old_msg: dict[str, Any]) -> CanonicalEnvelope:
        """Translate input_audio_buffer.commit → listen_stop."""
        canonical_event = make_event(
            DeviceEventType.LISTEN_STOP,
            device_id=old_msg.get("device_id", self.device_id),
            session_id=old_msg.get("session_id"),
            seq=old_msg.get("seq", 0),
            payload=old_msg.get("payload", {}),
        )
        canonical_event.msg_id = old_msg.get("trace_id", canonical_event.msg_id)

        log.info(f"[{self.name}] Translated: input_audio_buffer.commit → listen_stop")
        return canonical_event

    def _consume_response_create(self, old_msg: dict[str, Any]) -> bool:
        """
        Consume response.create message (don't forward to runtime).

        This message triggers old device state transitions but doesn't enter runtime.
        """
        session_id = old_msg.get("session_id")
        self.response_create_consumed.add(session_id)

        log.info(f"[{self.name}] Consumed: response.create (not forwarded to runtime, session={session_id})")
        return True

    def inject_event(self, old_msg: dict[str, Any]) -> CanonicalEnvelope | None:
        """
        Inject raw message from WebSocket and translate to canonical format.

        Called by the WebSocket handler when a message arrives from the device.
        Returns the canonical event to be queued for runtime.
        """
        old_type = old_msg.get("type")

        if old_type not in self._event_type_aliases and old_type != "response.create":
            log.warning(f"[{self.name}] Unknown message type: {old_type}")
            return None

        # Special handling for response.create
        if old_type == "response.create":
            self._consume_response_create(old_msg)
            return None

        # Translate old protocol to canonical
        canonical_type = self._event_type_aliases.get(old_type)

        if old_type == "session.update":
            canonical_event = self._translate_session_update(old_msg)
        elif old_type == "audio_data":
            canonical_event = self._translate_audio_data(old_msg)
        elif old_type == "input_audio_buffer.commit":
            canonical_event = self._translate_input_audio_buffer_commit(old_msg)
        else:
            log.error(f"[{self.name}] Unhandled translation for type: {old_type}")
            return None

        if canonical_event:
            # Queue the event for runtime
            asyncio.create_task(self._queue.put(canonical_event))
            log.debug(f"[{self.name}] Queued: {canonical_type} (seq={old_msg.get('seq')})")

        return canonical_event
