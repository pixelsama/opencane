"""
Minimal QuecPython device client for OpenCane generic_mqtt adapter.

This script targets EC600MCNLE and implements:
1. MQTT connect + subscribe downlink topics
2. Uplink control events: hello / heartbeat / listen_start / listen_stop
3. Uplink audio packet with 16-byte framed header
4. GPIO button trigger for listen start/stop

Notes:
- Audio source is a demo stub. Replace DemoAudioSource.read() with real mic data.
- Keep PACKET_MAGIC/topic names aligned with OpenCane runtime config.
"""

try:
    import ujson as json
except ImportError:
    import json

try:
    import ustruct as struct
except ImportError:
    import struct

import utime
from machine import Pin

try:
    from umqtt import MQTTClient
except ImportError:
    import umqtt

    MQTTClient = umqtt.MQTTClient


# ===== Device + broker config (edit these first) =====
DEVICE_ID = "ec600mcnle-dev01"
MQTT_HOST = "127.0.0.1"
MQTT_PORT = 1883
MQTT_USERNAME = ""
MQTT_PASSWORD = ""
MQTT_CLIENT_ID = "ec600mcnle-opencane-demo"

# Topic layout should match OpenCane defaults:
# device/{device_id}/up/control
# device/{device_id}/up/audio
# device/{device_id}/down/control
# device/{device_id}/down/audio
TOPIC_UP_CONTROL = "device/{}/up/control".format(DEVICE_ID)
TOPIC_UP_AUDIO = "device/{}/up/audio".format(DEVICE_ID)
TOPIC_DOWN_CONTROL = "device/{}/down/control".format(DEVICE_ID)
TOPIC_DOWN_AUDIO = "device/{}/down/audio".format(DEVICE_ID)

CONTROL_QOS = 1
AUDIO_QOS = 0
KEEPALIVE_SECONDS = 45
HEARTBEAT_INTERVAL_MS = 30000
RECONNECT_DELAY_MS = 3000

# OpenCane framed packet defaults
PACKET_MAGIC = 0xA1

# Button config (adjust pin for your board)
BUTTON_PIN = 12
BUTTON_ACTIVE_LOW = True
BUTTON_POLL_MS = 20


class DemoAudioSource:
    """Simple fake audio source for smoke tests."""

    def __init__(self, frame_bytes=320, frame_interval_ms=40):
        self.frame_bytes = int(frame_bytes)
        self.frame_interval_ms = int(frame_interval_ms)
        self._last_sent_ms = 0
        self._tone = 0

    def read(self):
        now = utime.ticks_ms()
        if utime.ticks_diff(now, self._last_sent_ms) < self.frame_interval_ms:
            return None
        self._last_sent_ms = now
        self._tone = (self._tone + 1) & 0xFF
        return bytes([self._tone]) * self.frame_bytes


class OpenCaneEC600Client:
    def __init__(self):
        self.client = None
        self.seq = 0
        self.session_id = ""
        self.listening = False
        self.listen_chunk_count = 0
        self.last_heartbeat_ms = 0
        self.button = self._init_button(BUTTON_PIN)
        self.prev_pressed = self._is_button_pressed()
        self.audio_source = DemoAudioSource()

    def _init_button(self, pin_num):
        mode_in = getattr(Pin, "IN", 0)
        pull_up = getattr(Pin, "PULL_PU", None)
        if pull_up is None:
            pull_up = getattr(Pin, "PULL_UP", None)
        if pull_up is None:
            return Pin(pin_num, mode_in)
        return Pin(pin_num, mode_in, pull_up)

    def _is_button_pressed(self):
        value = int(self.button.value())
        if BUTTON_ACTIVE_LOW:
            return value == 0
        return value == 1

    def _next_seq(self):
        self.seq += 1
        return self.seq

    def _now_ms(self):
        try:
            return int(utime.time() * 1000)
        except Exception:
            return int(utime.ticks_ms() & 0x7FFFFFFF)

    def _new_session_id(self):
        tail = self._now_ms() & 0xFFFFFFFF
        self.session_id = "{}-{:08x}".format(DEVICE_ID, tail)

    def _build_msg_id(self, seq):
        return "{:08x}{:08x}".format(self._now_ms() & 0xFFFFFFFF, int(seq) & 0xFFFFFFFF)

    def _build_control_envelope(self, event_type, payload):
        if not self.session_id:
            self._new_session_id()
        seq = self._next_seq()
        return {
            "version": "0.1",
            "msg_id": self._build_msg_id(seq),
            "device_id": DEVICE_ID,
            "session_id": self.session_id,
            "seq": seq,
            "ts": self._now_ms(),
            "type": str(event_type),
            "payload": payload if isinstance(payload, dict) else {},
        }

    def _publish_control(self, event_type, payload):
        env = self._build_control_envelope(event_type, payload)
        body = json.dumps(env)
        self.client.publish(TOPIC_UP_CONTROL, body, qos=CONTROL_QOS)
        return env

    def _build_audio_packet(self, seq, timestamp_ms, audio_bytes):
        data = audio_bytes if isinstance(audio_bytes, (bytes, bytearray)) else b""
        header = bytearray(16)
        header[0] = PACKET_MAGIC
        header[1] = 1
        header[4:8] = struct.pack(">I", int(seq) & 0xFFFFFFFF)
        header[8:12] = struct.pack(">I", int(timestamp_ms) & 0xFFFFFFFF)
        header[12:16] = struct.pack(">I", len(data) & 0xFFFFFFFF)
        return bytes(header) + data

    def _publish_audio_chunk(self, raw_audio):
        if not self.session_id:
            return
        seq = self._next_seq()
        ts_ms = self._now_ms()
        packet = self._build_audio_packet(seq, ts_ms, raw_audio)
        self.client.publish(TOPIC_UP_AUDIO, packet, qos=AUDIO_QOS)
        self.listen_chunk_count += 1

    def _on_message(self, topic, payload):
        topic_text = topic.decode() if isinstance(topic, bytes) else str(topic)
        if topic_text == TOPIC_DOWN_CONTROL:
            self._handle_down_control(payload)
            return
        if topic_text == TOPIC_DOWN_AUDIO:
            print("[down/audio] bytes={}".format(len(payload)))
            return

    def _handle_down_control(self, payload):
        try:
            raw = payload.decode() if isinstance(payload, bytes) else str(payload)
            data = json.loads(raw)
        except Exception as exc:
            print("[down/control] invalid json:", exc)
            return
        cmd_type = str(data.get("type") or "").strip()
        print("[down/control] type={}".format(cmd_type))
        if cmd_type == "close" and self.listening:
            self._send_listen_stop(reason="server_close")

    def connect(self):
        username = MQTT_USERNAME or None
        password = MQTT_PASSWORD or None
        self.client = MQTTClient(
            MQTT_CLIENT_ID,
            MQTT_HOST,
            MQTT_PORT,
            username,
            password,
            KEEPALIVE_SECONDS,
        )
        self.client.set_callback(self._on_message)
        self.client.connect()
        self.client.subscribe(TOPIC_DOWN_CONTROL, qos=CONTROL_QOS)
        self.client.subscribe(TOPIC_DOWN_AUDIO, qos=AUDIO_QOS)
        print("[mqtt] connected host={} port={}".format(MQTT_HOST, MQTT_PORT))

    def disconnect(self):
        if self.client is None:
            return
        try:
            self.client.disconnect()
        except Exception:
            pass
        self.client = None

    def send_hello(self):
        payload = {
            "firmware": "quecpython-demo",
            "capabilities": {
                "audio_up_mode": "framed_packet",
                "button_trigger": True,
            },
        }
        self._publish_control("hello", payload)
        print("[up/control] hello")

    def send_heartbeat(self):
        payload = {"signal": "ok", "uptime_ms": self._now_ms()}
        self._publish_control("heartbeat", payload)
        self.last_heartbeat_ms = utime.ticks_ms()

    def _send_listen_start(self, trigger):
        if self.listening:
            return
        self._new_session_id()
        self.listen_chunk_count = 0
        self.listening = True
        payload = {"trigger": trigger, "codec": "opus"}
        self._publish_control("listen_start", payload)
        print("[up/control] listen_start sid={}".format(self.session_id))

    def _send_listen_stop(self, reason):
        if not self.listening:
            return
        payload = {"reason": reason, "chunks": self.listen_chunk_count}
        self._publish_control("listen_stop", payload)
        print("[up/control] listen_stop sid={} chunks={}".format(self.session_id, self.listen_chunk_count))
        self.listening = False
        self.listen_chunk_count = 0

    def _poll_button(self):
        pressed = self._is_button_pressed()
        if pressed and not self.prev_pressed:
            self._send_listen_start(trigger="button")
        if (not pressed) and self.prev_pressed:
            self._send_listen_stop(reason="button_release")
        self.prev_pressed = pressed

    def _maybe_stream_audio(self):
        if not self.listening:
            return
        frame = self.audio_source.read()
        if frame is None:
            return
        self._publish_audio_chunk(frame)

    def loop_forever(self):
        while True:
            try:
                self.connect()
                self.send_hello()
                self.last_heartbeat_ms = utime.ticks_ms()
                while True:
                    self.client.check_msg()
                    now = utime.ticks_ms()
                    if utime.ticks_diff(now, self.last_heartbeat_ms) >= HEARTBEAT_INTERVAL_MS:
                        self.send_heartbeat()
                    self._poll_button()
                    self._maybe_stream_audio()
                    utime.sleep_ms(BUTTON_POLL_MS)
            except Exception as exc:
                print("[runtime] error:", exc)
                self.disconnect()
                utime.sleep_ms(RECONNECT_DELAY_MS)


def main():
    client = OpenCaneEC600Client()
    client.loop_forever()


if __name__ == "__main__":
    main()
