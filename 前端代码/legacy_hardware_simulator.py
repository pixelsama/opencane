#!/usr/bin/env python3
"""
Legacy hardware simulator for OpenCane demo mode.

This script mimics the old blind cane hardware behavior:
1. Sends session.update (handshake)
2. Streams audio chunks (AMR or WAV)
3. Sends input_audio_buffer.commit (finish audio)
4. Waits for response.create
5. Simulates audio playback

Used locally to test the legacy_websocket_adapter before real hardware integration.
"""

import asyncio
import json
import base64
import os
import sys
import time
import argparse
from pathlib import Path
import logging

try:
    import websockets
    from websockets.client import WebSocketClientProtocol
except ImportError:
    print("ERROR: websockets package required. Install: pip install websockets")
    sys.exit(1)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


class LegacyHardwareSimulator:
    """Simulates old blind cane hardware protocol."""

    def __init__(self, ws_url: str, device_id: str = "legacy-device-001", session_id: str = None):
        self.ws_url = ws_url
        self.device_id = device_id
        self.session_id = session_id or f"sess-{int(time.time())}"
        self.ws: WebSocketClientProtocol = None
        self.seq_counter = 0
        self.trace_id = f"trace-{int(time.time() * 1000)}"

    async def connect(self):
        """Establish WebSocket connection."""
        log.info(f"Connecting to {self.ws_url}")
        try:
            self.ws = await websockets.connect(self.ws_url)
            log.info("✓ WebSocket connected")
        except Exception as e:
            log.error(f"Connection failed: {e}")
            raise

    async def disconnect(self):
        """Close WebSocket connection."""
        if self.ws:
            await self.ws.close()
            log.info("✓ Disconnected")

    def _next_seq(self) -> int:
        """Get next sequence number."""
        self.seq_counter += 1
        return self.seq_counter

    async def send_session_update(self):
        """Send old protocol: session.update."""
        msg = {
            "type": "session.update",
            "seq": self._next_seq(),
            "device_id": self.device_id,
            "session_id": self.session_id,
            "trace_id": self.trace_id,
            "payload": {
                "device_token": "demo-token-123",
                "device_model": "EC600U",
                "capabilities": ["asr", "tts", "vision"],
            },
        }
        log.info(f"→ Sending session.update: seq={msg['seq']}")
        await self.ws.send(json.dumps(msg))

    async def send_audio_chunk(self, audio_data: bytes, encoding: str = "amr", is_first: bool = False):
        """Send old protocol: audio chunk."""
        b64_data = base64.b64encode(audio_data).decode("utf-8")
        msg = {
            "type": "audio_data",
            "seq": self._next_seq(),
            "device_id": self.device_id,
            "session_id": self.session_id,
            "trace_id": self.trace_id,
            "payload": {
                "audio_b64": b64_data,
                "encoding": encoding,
                "sample_rate": 8000 if encoding == "amr" else 16000,
            },
        }
        log.info(f"→ Sending audio_chunk: seq={msg['seq']}, size={len(audio_data)} bytes, encoding={encoding}")
        await self.ws.send(json.dumps(msg))
        await asyncio.sleep(0.1)  # Small delay between chunks

    async def send_input_audio_buffer_commit(self):
        """Send old protocol: input_audio_buffer.commit."""
        msg = {
            "type": "input_audio_buffer.commit",
            "seq": self._next_seq(),
            "device_id": self.device_id,
            "session_id": self.session_id,
            "trace_id": self.trace_id,
            "payload": {
                "total_duration_ms": 3000,
            },
        }
        log.info(f"→ Sending input_audio_buffer.commit: seq={msg['seq']}")
        await self.ws.send(json.dumps(msg))

    async def send_response_create(self):
        """Send old protocol: response.create."""
        msg = {
            "type": "response.create",
            "seq": self._next_seq(),
            "device_id": self.device_id,
            "session_id": self.session_id,
            "trace_id": self.trace_id,
            "payload": {},
        }
        log.info(f"→ Sending response.create: seq={msg['seq']}")
        await self.ws.send(json.dumps(msg))

    async def listen_for_responses(self, timeout_sec: int = 10):
        """Listen for server responses."""
        log.info(f"Listening for responses (timeout={timeout_sec}s)...")
        start_time = time.time()
        responses = []

        while time.time() - start_time < timeout_sec:
            try:
                # Non-blocking receive with timeout
                msg_text = await asyncio.wait_for(self.ws.recv(), timeout=1.0)
                msg = json.loads(msg_text)
                responses.append(msg)
                log.info(f"← Received: type={msg.get('type')}, seq={msg.get('seq')}")

                # Print payload if present
                if "payload" in msg:
                    log.info(f"  Payload: {msg['payload']}")
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                log.warning(f"Receive error: {e}")
                break

        return responses

    async def run_session(self, audio_file: str = None):
        """Run a complete legacy hardware session."""
        try:
            await self.connect()

            # 1. Send session.update (handshake)
            await self.send_session_update()
            await asyncio.sleep(0.5)

            # 2. Send audio data (or dummy data if no file provided)
            if audio_file and os.path.exists(audio_file):
                with open(audio_file, "rb") as f:
                    audio_data = f.read()
                # Guess encoding from file extension
                encoding = "amr" if audio_file.endswith(".amr") else "wav"
                await self.send_audio_chunk(audio_data, encoding=encoding)
            else:
                # Send dummy audio data
                dummy_audio = b"\x00" * 1000
                await self.send_audio_chunk(dummy_audio, encoding="amr")

            await asyncio.sleep(0.5)

            # 3. Send input_audio_buffer.commit
            await self.send_input_audio_buffer_commit()
            await asyncio.sleep(0.5)

            # 4. Send response.create
            await self.send_response_create()

            # 5. Listen for server responses
            responses = await self.listen_for_responses(timeout_sec=10)

            log.info(f"\n✓ Session complete. Received {len(responses)} response(s)")
            return responses

        except Exception as e:
            log.error(f"Session error: {e}")
            raise
        finally:
            await self.disconnect()


async def main():
    parser = argparse.ArgumentParser(description="Legacy hardware simulator for OpenCane")
    parser.add_argument("--ws-url", default="ws://localhost:8000/v1/device/ws", help="WebSocket URL")
    parser.add_argument("--device-id", default="legacy-device-001", help="Device ID")
    parser.add_argument("--session-id", default=None, help="Session ID (auto-generated if not provided)")
    parser.add_argument("--audio-file", default=None, help="Path to audio file (AMR or WAV)")
    parser.add_argument("--rounds", type=int, default=1, help="Number of rounds to run")
    args = parser.parse_args()

    for round_num in range(1, args.rounds + 1):
        log.info(f"\n{'='*60}")
        log.info(f"Round {round_num}/{args.rounds}")
        log.info(f"{'='*60}")

        simulator = LegacyHardwareSimulator(
            ws_url=args.ws_url,
            device_id=args.device_id,
            session_id=args.session_id,
        )

        try:
            await simulator.run_session(audio_file=args.audio_file)
        except Exception as e:
            log.error(f"Round {round_num} failed: {e}")
            if args.rounds == 1:
                raise
            # Continue to next round if multiple rounds requested

        if round_num < args.rounds:
            log.info(f"Waiting 2s before next round...")
            await asyncio.sleep(2)


if __name__ == "__main__":
    asyncio.run(main())
