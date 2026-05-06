"""Southbound adapters that map raw device protocols to canonical envelopes."""

from opencane.hardware.adapter.base import GatewayAdapter
from opencane.hardware.adapter.device_profiles import (
    GenericMQTTDeviceProfile,
    build_generic_mqtt_runtime,
    list_generic_mqtt_profiles,
    resolve_generic_mqtt_profile,
)
from opencane.hardware.adapter.ec600_adapter import EC600Adapter, EC600MQTTAdapter
from opencane.hardware.adapter.generic_mqtt_adapter import GenericMQTTAdapter
from opencane.hardware.adapter.mock_adapter import MockAdapter
from opencane.hardware.adapter.websocket_adapter import WebSocketAdapter
from opencane.hardware.adapter.legacy_websocket_adapter import LegacyWebSocketAdapter

__all__ = [
    "GatewayAdapter",
    "MockAdapter",
    "EC600Adapter",
    "EC600MQTTAdapter",
    "GenericMQTTAdapter",
    "GenericMQTTDeviceProfile",
    "resolve_generic_mqtt_profile",
    "list_generic_mqtt_profiles",
    "build_generic_mqtt_runtime",
    "WebSocketAdapter",
    "LegacyWebSocketAdapter",
]
