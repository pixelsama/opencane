"""
Minimal QuecPython device client for OpenCane generic_mqtt adapter.

This script targets EC600MCNLE and implements:
1. MQTT connect + subscribe downlink topics
2. Uplink control events: hello / heartbeat / listen_start / listen_stop / image_ready
3. Uplink audio packet with 16-byte framed header
4. GPIO button trigger for listen start/stop
5. Optional real-media integration:
   - Mic stream via audio.Record.stream_start/stream_read/stream_stop
   - Camera snapshot via camera.camCapture (requires LCD init per QuecPython docs)
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

try:
    import uos as os_mod
except ImportError:
    import os as os_mod


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

# Audio source mode:
# - "auto": prefer real mic stream, fallback to demo stream
# - "real": require real mic stream, fail listen_start if unavailable
# - "demo": always use fake audio stream
AUDIO_SOURCE_MODE = "auto"
AUDIO_RECORD_DEVICE = 0
AUDIO_RECORD_FORMAT = "AMRNB"  # PCM / WAV / AMRNB / AMRWB / OGGOPUS
AUDIO_RECORD_SAMPLE_RATE = 8000
AUDIO_RECORD_READ_BYTES = 320

# Optional camera snapshot support.
# Notes:
# - QuecPython camera docs require LCD initialization before camera usage.
# - Leave CAMERA_ENABLED=False unless your board wiring/init is ready.
CAMERA_ENABLED = False
CAMERA_MODEL = 0
CAMERA_SENSOR_WIDTH = 320
CAMERA_SENSOR_HEIGHT = 240
CAMERA_PREVIEW_LEVEL = 1
CAMERA_LCD_WIDTH = 240
CAMERA_LCD_HEIGHT = 320
CAMERA_CAPTURE_WIDTH = 320
CAMERA_CAPTURE_HEIGHT = 240
CAMERA_CAPTURE_TIMEOUT_MS = 3000
CAMERA_PICTURE_PREFIX = "/usr/opencane_img"


class DemoAudioSource:
    """Simple fake audio source for smoke tests."""

    name = "demo"
    codec_name = "pcm_u8_demo"

    def __init__(self, frame_bytes=320, frame_interval_ms=40):
        self.frame_bytes = int(frame_bytes)
        self.frame_interval_ms = int(frame_interval_ms)
        self._last_sent_ms = 0
        self._tone = 0

    def start(self):
        return 0

    def stop(self):
        return 0

    def read(self):
        now = utime.ticks_ms()
        if utime.ticks_diff(now, self._last_sent_ms) < self.frame_interval_ms:
            return None
        self._last_sent_ms = now
        self._tone = (self._tone + 1) & 0xFF
        return bytes([self._tone]) * self.frame_bytes


class RecordAudioSource:
    """Real mic stream source based on QuecPython audio.Record API."""

    name = "record_stream"

    def __init__(
        self,
        device=0,
        format_name="AMRNB",
        sample_rate=8000,
        read_bytes=320,
    ):
        import audio

        self._audio = audio
        self._rec = self._audio.Record(int(device))
        self._format_name = str(format_name or "AMRNB").strip().upper()
        self._format_code = self._resolve_format_code()
        self.sample_rate = int(sample_rate)
        self.read_bytes = max(64, int(read_bytes))
        self._read_buf = bytearray(self.read_bytes)
        self._running = False
        self.codec_name = self._format_name.lower()

    def _resolve_format_code(self):
        if hasattr(self._rec, self._format_name):
            return int(getattr(self._rec, self._format_name))
        if hasattr(self._rec, "AMRNB"):
            self._format_name = "AMRNB"
            return int(getattr(self._rec, "AMRNB"))
        raise ValueError("audio format not supported: {}".format(self._format_name))

    def start(self):
        if self._running:
            return 0
        # time=0 means continuous stream until stream_stop.
        rc = self._rec.stream_start(self._format_code, self.sample_rate, 0)
        if int(rc) != 0:
            raise RuntimeError("record stream_start failed rc={}".format(rc))
        self._running = True
        return 0

    def stop(self):
        if not self._running:
            return 0
        rc = self._rec.stream_stop()
        self._running = False
        return int(rc)

    def read(self):
        if not self._running:
            return None
        n = self._rec.stream_read(self._read_buf, len(self._read_buf))
        if isinstance(n, int) and n > 0:
            return bytes(self._read_buf[: int(n)])
        return None


class CameraCaptureSource:
    """Optional camera capture helper via camera.camCapture."""

    name = "cam_capture"

    def __init__(self):
        import camera

        self._camera = camera
        self._capture = self._camera.camCapture(
            int(CAMERA_MODEL),
            int(CAMERA_SENSOR_WIDTH),
            int(CAMERA_SENSOR_HEIGHT),
            int(CAMERA_PREVIEW_LEVEL),
            int(CAMERA_LCD_WIDTH),
            int(CAMERA_LCD_HEIGHT),
        )
        self._capture_result = None
        self._opened = False
        self._capture.callback(self._on_capture)

    def _on_capture(self, result_list):
        self._capture_result = result_list

    def open(self):
        if self._opened:
            return 0
        rc = self._capture.open()
        if int(rc) == 0:
            self._opened = True
        return int(rc)

    def close(self):
        if not self._opened:
            return 0
        rc = self._capture.close()
        self._opened = False
        return int(rc)

    def capture(self, picture_name_no_ext):
        if not self._opened:
            rc = self.open()
            if int(rc) != 0:
                raise RuntimeError("camera open failed rc={}".format(rc))
        self._capture_result = None
        rc = self._capture.start(int(CAMERA_CAPTURE_WIDTH), int(CAMERA_CAPTURE_HEIGHT), picture_name_no_ext)
        if int(rc) != 0:
            raise RuntimeError("camera start failed rc={}".format(rc))

        start_ms = utime.ticks_ms()
        while utime.ticks_diff(utime.ticks_ms(), start_ms) < int(CAMERA_CAPTURE_TIMEOUT_MS):
            if self._capture_result is not None:
                result = self._capture_result
                state = int(result[0]) if isinstance(result, (list, tuple)) and result else -1
                if state == 0 and len(result) > 1:
                    return str(result[1])
                break
            utime.sleep_ms(50)
        return "{}.jpeg".format(picture_name_no_ext)


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
        self.audio_source = self._build_audio_source()
        self.camera_source = self._build_camera_source()
        print(
            "[init] audio_source={} codec={} camera={}".format(
                getattr(self.audio_source, "name", "unknown"),
                getattr(self.audio_source, "codec_name", "unknown"),
                "on" if self.camera_source is not None else "off",
            )
        )

    def _build_audio_source(self):
        mode = str(AUDIO_SOURCE_MODE or "auto").strip().lower()
        if mode == "demo":
            return DemoAudioSource()
        try:
            return RecordAudioSource(
                device=AUDIO_RECORD_DEVICE,
                format_name=AUDIO_RECORD_FORMAT,
                sample_rate=AUDIO_RECORD_SAMPLE_RATE,
                read_bytes=AUDIO_RECORD_READ_BYTES,
            )
        except Exception as exc:
            if mode == "real":
                raise
            print("[audio] fallback to demo source:", exc)
            return DemoAudioSource()

    def _build_camera_source(self):
        if not CAMERA_ENABLED:
            return None
        try:
            return CameraCaptureSource()
        except Exception as exc:
            print("[camera] init failed, camera disabled:", exc)
            return None

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

    def _safe_file_size(self, path):
        try:
            return int(os_mod.stat(path)[6])
        except Exception:
            return -1

    def _capture_image_and_report(self, trigger):
        if self.camera_source is None:
            self._publish_control("image_ready", {"ok": False, "error": "camera_disabled", "trigger": trigger})
            print("[up/control] image_ready camera_disabled")
            return
        stem = "{}_{}".format(CAMERA_PICTURE_PREFIX, self._now_ms())
        try:
            pic_path = self.camera_source.capture(stem)
        except Exception as exc:
            self._publish_control(
                "image_ready",
                {"ok": False, "error": str(exc), "trigger": trigger, "path": "{}.jpeg".format(stem)},
            )
            print("[up/control] image_ready failed:", exc)
            return
        payload = {
            "ok": True,
            "path": pic_path,
            "size": self._safe_file_size(pic_path),
            "trigger": trigger,
        }
        self._publish_control("image_ready", payload)
        print("[up/control] image_ready path={} size={}".format(pic_path, payload["size"]))

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
            return
        if cmd_type in ("debug_listen_start", "listen_start"):
            self._send_listen_start(trigger="remote")
            return
        if cmd_type in ("debug_listen_stop", "listen_stop"):
            self._send_listen_stop(reason="remote")
            return
        if cmd_type in ("capture_image", "debug_capture_image"):
            self._capture_image_and_report(trigger="remote")
            return

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
        try:
            self.audio_source.start()
        except Exception as exc:
            self._publish_control(
                "error",
                {"stage": "listen_start", "error": str(exc), "trigger": trigger},
            )
            print("[audio] start failed:", exc)
            return
        self._new_session_id()
        self.listen_chunk_count = 0
        self.listening = True
        payload = {"trigger": trigger, "codec": getattr(self.audio_source, "codec_name", "unknown")}
        self._publish_control("listen_start", payload)
        print("[up/control] listen_start sid={}".format(self.session_id))

    def _send_listen_stop(self, reason):
        if not self.listening:
            return
        try:
            self.audio_source.stop()
        except Exception as exc:
            print("[audio] stop failed:", exc)
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
                try:
                    self.audio_source.stop()
                except Exception:
                    pass
                if self.camera_source is not None:
                    try:
                        self.camera_source.close()
                    except Exception:
                        pass
                self.disconnect()
                utime.sleep_ms(RECONNECT_DELAY_MS)


def main():
    client = OpenCaneEC600Client()
    client.loop_forever()


if __name__ == "__main__":
    main()
