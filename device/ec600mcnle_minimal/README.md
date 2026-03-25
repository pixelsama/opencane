# EC600MCNLE Minimal Device Client

This directory contains a minimal QuecPython-side device client aligned to OpenCane `generic_mqtt`.

## Files

- `main.py`: runnable minimal script for EC600MCNLE

## What It Implements

- MQTT uplink/downlink with OpenCane default topics
- Uplink control events:
  - `hello`
  - `heartbeat`
  - `listen_start`
  - `listen_stop`
  - `image_ready` (optional camera)
- Uplink framed audio packet (`16-byte header + audio payload`)
- GPIO button trigger for start/stop listen session
- Optional real-mic stream (`audio.Record`) with demo fallback

## Quick Start

1. Flash QuecPython firmware to EC600MCNLE (via QPYcom).
2. Upload `main.py` to the module filesystem.
3. Edit config constants in `main.py`:
   - `DEVICE_ID`
   - `MQTT_HOST`, `MQTT_PORT`
   - `MQTT_USERNAME`, `MQTT_PASSWORD` (if needed)
   - `BUTTON_PIN`, `BUTTON_ACTIVE_LOW`
   - `AUDIO_SOURCE_MODE` (`auto` / `real` / `demo`)
   - `AUDIO_RECORD_FORMAT`, `AUDIO_RECORD_SAMPLE_RATE`
   - `CAMERA_ENABLED` and related camera constants (if camera wiring+LCD init are ready)
4. Power cycle or run `main.py`.

## OpenCane Runtime Side

Use `generic_mqtt` with EC600 profile and default topics:

```bash
opencane hardware serve --adapter generic_mqtt --logs
```

Recommended config:

- `hardware.deviceProfile = "ec600mcnle_v1"`
- `hardware.mqtt.host = <your broker host>`
- keep topic defaults:
  - `device/{device_id}/up/control`
  - `device/{device_id}/up/audio`
  - `device/{device_id}/down/control`
  - `device/{device_id}/down/audio`

## Important Note About Audio

`main.py` now supports both:

- real mic stream via `audio.Record.stream_start/stream_read/stream_stop`
- demo fake stream fallback (`DemoAudioSource`)

Mode selection:

- `AUDIO_SOURCE_MODE = "auto"`: try real mic, fallback to demo
- `AUDIO_SOURCE_MODE = "real"`: force real mic, fail `listen_start` when unavailable
- `AUDIO_SOURCE_MODE = "demo"`: always use demo stream

Recommended for demo:

1. First run with `auto` and watch startup logs (`[init] audio_source=...`).
2. Press button to trigger `listen_start/listen_stop`.
3. Verify OpenCane receives `up/audio` packets.

## Optional Camera Snapshot

If `CAMERA_ENABLED = True`, script can handle downlink command:

- `capture_image` (or `debug_capture_image`)

It emits uplink control event `image_ready` with `{ok, path, size, trigger}`.

Notes:

- QuecPython camera docs require LCD initialized before camera capture.
- This sample assumes your board camera+LCD path is already initialized in firmware/env.

## Protocol Mapping (Current Defaults)

- `PACKET_MAGIC = 0xA1`
- header layout:
  - `byte[0]`: magic
  - `byte[1]`: protocol version (`1`)
  - `byte[4:8]`: seq (big-endian)
  - `byte[8:12]`: timestamp ms (big-endian)
  - `byte[12:16]`: payload length (big-endian)

This matches the current OpenCane EC600/generic MQTT adapter expectations.
