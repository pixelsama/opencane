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

import base64
import json
import logging
from typing import Any
from urllib.parse import parse_qs, urlparse

from websockets.legacy.server import WebSocketServerProtocol

from opencane.hardware.adapter.websocket_adapter import WebSocketAdapter
from opencane.hardware.protocol import CanonicalEnvelope, DeviceEventType, make_event

log = logging.getLogger(__name__)


class LegacyWebSocketAdapter(WebSocketAdapter):
    """
    Adapter for legacy hardware using old WebSocket protocol.

    Extends WebSocketAdapter with protocol translation for old blind cane hardware.
    Implements message conversion between old and canonical protocols.
    """

    name = "legacy_demo"
    transport = "ws_legacy"

    def __init__(self, config: dict[str, Any]):
        # Initialize parent WebSocket adapter
        super().__init__(
            host=config.get("host", "0.0.0.0"),
            port=config.get("port", 18791),
            require_token=config.get("require_token", False),
            token=config.get("token", ""),
            packet_magic=config.get("packet_magic", 0xA1),
        )
        
        self.device_id = config.get("device_id", "legacy-device-001")
        self.session_map = {}  # Maps old session_id -> canonical session_id
        self.response_create_consumed = set()  # Track response.create messages

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

    async def _handle_connection(self, websocket: WebSocketServerProtocol, path: str) -> None:
        """Override parent handler to add legacy protocol translation."""
        # Parse query parameters (may be empty for legacy clients)
        query = parse_qs(urlparse(path).query)
        device_id = (query.get("device_id") or query.get("device-id") or [""])[0]
        session_id = (query.get("session_id") or query.get("session-id") or [""])[0]

        # Use config device_id if not provided in query params
        if not device_id:
            device_id = self.device_id

        # Track if we've stored the socket
        stored_device = False
        stored_session = False

        try:
            async for message in websocket:
                if isinstance(message, bytes):
                    # Handle binary audio (pass through to parent)
                    continue

                # Parse JSON message
                try:
                    old_msg = json.loads(message)
                except json.JSONDecodeError:
                    log.warning(f"[{self.name}] Failed to parse JSON: {message}")
                    continue

                # Extract device_id and session_id from message if not yet known
                msg_device_id = old_msg.get("device_id", device_id)
                msg_session_id = old_msg.get("session_id", session_id)

                # Store socket on first message (now we know the device/session IDs)
                if not stored_device and msg_device_id:
                    self._device_sockets[msg_device_id] = websocket
                    stored_device = True
                    device_id = msg_device_id

                if not stored_session and msg_device_id and msg_session_id:
                    self._session_sockets[(msg_device_id, msg_session_id)] = websocket
                    stored_session = True
                    session_id = msg_session_id

                # Translate old protocol to canonical
                canonical = self.inject_event(old_msg)
                if canonical:
                    await self._queue.put(canonical)
        except Exception as e:
            log.error(f"[{self.name}] Connection error: {e}")
        finally:
            # Cleanup
            if stored_device and device_id in self._device_sockets:
                del self._device_sockets[device_id]
            if stored_session and (device_id, session_id) in self._session_sockets:
                del self._session_sockets[(device_id, session_id)]

    async def send_command(self, cmd: CanonicalEnvelope) -> None:
        """
        Send command to hardware (translate from canonical to old protocol).

        Overrides parent to add legacy protocol translation before sending.
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

        # Send via parent's WebSocket infrastructure
        ws = self._session_sockets.get((cmd.device_id, cmd.session_id))
        if ws is None:
            ws = self._device_sockets.get(cmd.device_id)
        if ws is None:
            log.warning(
                f"[{self.name}] Cannot find socket for {cmd.device_id}/{cmd.session_id}"
            )
            return
        try:
            await ws.send(json.dumps(old_msg, ensure_ascii=False))
        except Exception as e:
            log.warning(f"[{self.name}] Failed to send command: {e}")

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
            log.debug(f"[{self.name}] Queued: {canonical_type} (seq={old_msg.get('seq')})")

        return canonical_event
