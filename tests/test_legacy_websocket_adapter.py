#!/usr/bin/env python3
"""
Test script for legacy_websocket_adapter protocol translation.

Tests that old protocol messages are correctly translated to canonical format.
"""

import asyncio
import base64
import pytest

from opencane.hardware.adapter.legacy_websocket_adapter import LegacyWebSocketAdapter
from opencane.hardware.protocol import DeviceEventType

# Use unique ports for each test
PORT_BASE = 18800


@pytest.mark.asyncio
async def test_session_update():
    """Test session.update → hello translation."""
    adapter = LegacyWebSocketAdapter(config={"device_id": "test-device", "port": PORT_BASE + 1})
    await adapter.start()

    try:
        # Create old protocol message
        old_msg = {
            "type": "session.update",
            "seq": 1,
            "device_id": "test-device",
            "session_id": "sess-001",
            "trace_id": "trace-001",
            "payload": {
                "device_token": "token-123",
                "device_model": "EC600U",
                "capabilities": ["asr", "tts", "vision"],
            },
        }

        # Translate
        canonical = adapter.inject_event(old_msg)
        assert canonical is not None, "Expected canonical event"
        assert canonical.type == "hello", f"Expected hello, got {canonical.type}"
        assert canonical.device_id == "test-device"
        assert canonical.session_id == "sess-001"
        assert canonical.payload["device_model"] == "EC600U"
        print("✓ session.update → hello")
    finally:
        await adapter.stop()


@pytest.mark.asyncio
async def test_audio_chunk():
    """Test audio_data → audio_chunk translation."""
    adapter = LegacyWebSocketAdapter(config={"device_id": "test-device", "port": PORT_BASE + 2})
    await adapter.start()

    try:
        # Create dummy audio
        dummy_audio = b"\x00\x01\x02\x03" * 250  # 1000 bytes
        audio_b64 = base64.b64encode(dummy_audio).decode("utf-8")

        old_msg = {
            "type": "audio_data",
            "seq": 2,
            "device_id": "test-device",
            "session_id": "sess-001",
            "trace_id": "trace-001",
            "payload": {
                "audio_b64": audio_b64,
                "encoding": "amr",
                "sample_rate": 8000,
            },
        }

        canonical = adapter.inject_event(old_msg)
        assert canonical is not None, "Expected canonical event"
        assert canonical.type == "audio_chunk", f"Expected audio_chunk, got {canonical.type}"
        assert canonical.payload["encoding"] == "amr"
        assert canonical.payload["size_bytes"] == 1000
        print("✓ audio_data → audio_chunk")
    finally:
        await adapter.stop()


@pytest.mark.asyncio
async def test_listen_stop():
    """Test input_audio_buffer.commit → listen_stop translation."""
    adapter = LegacyWebSocketAdapter(config={"device_id": "test-device", "port": PORT_BASE + 3})
    await adapter.start()

    try:
        old_msg = {
            "type": "input_audio_buffer.commit",
            "seq": 3,
            "device_id": "test-device",
            "session_id": "sess-001",
            "trace_id": "trace-001",
            "payload": {
                "total_duration_ms": 3000,
            },
        }

        canonical = adapter.inject_event(old_msg)
        assert canonical is not None, "Expected canonical event"
        assert canonical.type == "listen_stop", f"Expected listen_stop, got {canonical.type}"
        print("✓ input_audio_buffer.commit → listen_stop")
    finally:
        await adapter.stop()


@pytest.mark.asyncio
async def test_response_create_consumed():
    """Test response.create is consumed (not forwarded)."""
    adapter = LegacyWebSocketAdapter(config={"device_id": "test-device", "port": PORT_BASE + 4})
    await adapter.start()

    try:
        old_msg = {
            "type": "response.create",
            "seq": 4,
            "device_id": "test-device",
            "session_id": "sess-001",
            "trace_id": "trace-001",
            "payload": {},
        }

        canonical = adapter.inject_event(old_msg)
        assert canonical is None, "Expected None (consumed)"
        assert "sess-001" in adapter.response_create_consumed
        print("✓ response.create consumed (not forwarded)")
    finally:
        await adapter.stop()


@pytest.mark.asyncio
async def test_unknown_type():
    """Test unknown message type is rejected."""
    adapter = LegacyWebSocketAdapter(config={"device_id": "test-device", "port": PORT_BASE + 5})
    await adapter.start()

    try:
        old_msg = {
            "type": "unknown.type",
            "seq": 5,
            "device_id": "test-device",
            "session_id": "sess-001",
            "trace_id": "trace-001",
        }

        canonical = adapter.inject_event(old_msg)
        assert canonical is None, "Expected None (unknown type)"
        print("✓ unknown type rejected")
    finally:
        await adapter.stop()


@pytest.mark.asyncio
async def test_full_session_flow():
    """Test complete session flow."""
    adapter = LegacyWebSocketAdapter(config={"device_id": "test-device", "port": PORT_BASE + 6})
    await adapter.start()

    try:
        # 1. Session start
        msg1 = {
            "type": "session.update",
            "seq": 1,
            "device_id": "test-device",
            "session_id": "sess-full",
            "trace_id": "trace-full",
            "payload": {"capabilities": ["asr", "tts"]},
        }
        evt1 = adapter.inject_event(msg1)
        assert evt1.type == "hello"

        # 2. Audio data
        audio_b64 = base64.b64encode(b"\x00" * 500).decode("utf-8")
        msg2 = {
            "type": "audio_data",
            "seq": 2,
            "device_id": "test-device",
            "session_id": "sess-full",
            "trace_id": "trace-full",
            "payload": {"audio_b64": audio_b64, "encoding": "wav"},
        }
        evt2 = adapter.inject_event(msg2)
        assert evt2.type == "audio_chunk"

        # 3. Finish audio
        msg3 = {
            "type": "input_audio_buffer.commit",
            "seq": 3,
            "device_id": "test-device",
            "session_id": "sess-full",
            "trace_id": "trace-full",
            "payload": {},
        }
        evt3 = adapter.inject_event(msg3)
        assert evt3.type == "listen_stop"

        # 4. Device acks (not forwarded)
        msg4 = {
            "type": "response.create",
            "seq": 4,
            "device_id": "test-device",
            "session_id": "sess-full",
            "trace_id": "trace-full",
            "payload": {},
        }
        evt4 = adapter.inject_event(msg4)
        assert evt4 is None  # Consumed

        print("✓ Full session flow (hello → audio_chunk → listen_stop → consumed)")
    finally:
        await adapter.stop()


async def main():
    """Run all tests (pytest will handle async via mark)."""
    print("\n🧪 Testing LegacyWebSocketAdapter\n")
    try:
        await test_session_update()
        await test_audio_chunk()
        await test_listen_stop()
        await test_response_create_consumed()
        await test_unknown_type()
        await test_full_session_flow()
        print("\n✅ All tests passed!\n")
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}\n")
        raise


if __name__ == "__main__":
    # For manual runs (pytest will use @pytest.mark.asyncio instead)
    asyncio.run(main())
