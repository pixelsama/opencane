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
- Uplink framed audio packet (`16-byte header + audio payload`)
- GPIO button trigger for start/stop listen session

## Quick Start

1. Flash QuecPython firmware to EC600MCNLE (via QPYcom).
2. Upload `main.py` to the module filesystem.
3. Edit config constants in `main.py`:
   - `DEVICE_ID`
   - `MQTT_HOST`, `MQTT_PORT`
   - `MQTT_USERNAME`, `MQTT_PASSWORD` (if needed)
   - `BUTTON_PIN`, `BUTTON_ACTIVE_LOW`
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

`main.py` currently uses `DemoAudioSource` and sends fake frames for protocol smoke testing.

To connect real hardware audio:

1. Replace `DemoAudioSource.read()` with real captured bytes from your mic pipeline.
2. Keep packet format unchanged (`PACKET_MAGIC`, 16-byte header).
3. If you switch to non-opus payload, align the server pipeline accordingly.

## Protocol Mapping (Current Defaults)

- `PACKET_MAGIC = 0xA1`
- header layout:
  - `byte[0]`: magic
  - `byte[1]`: protocol version (`1`)
  - `byte[4:8]`: seq (big-endian)
  - `byte[8:12]`: timestamp ms (big-endian)
  - `byte[12:16]`: payload length (big-endian)

This matches the current OpenCane EC600/generic MQTT adapter expectations.
