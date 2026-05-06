"""Hardware runtime bootstrap and debug HTTP API."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import threading
import time
from concurrent.futures import TimeoutError as FutureTimeoutError
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from loguru import logger

from opencane.api.control_security import (
    RequestRateLimiter,
    RequestReplayProtector,
    parse_timestamp_ms,
)
from opencane.api.digital_task_service import DigitalTaskService
from opencane.api.lifelog_service import LifelogService
from opencane.api.observability import (
    build_observability_history_payload,
    runtime_observability_payload,
)
from opencane.api.vision_server import VisionService, json_response
from opencane.config.schema import HardwareConfig
from opencane.hardware.adapter import (
    EC600MQTTAdapter,
    GatewayAdapter,
    GenericMQTTAdapter,
    MockAdapter,
    WebSocketAdapter,
    build_generic_mqtt_runtime,
)
from opencane.hardware.protocol import CanonicalEnvelope
from opencane.hardware.runtime import DeviceRuntimeCore


def _first_query_value(params: dict[str, list[str]], *keys: str) -> str | None:
    for key in keys:
        values = params.get(key, [])
        if values:
            return str(values[0])
    return None


def _to_float_query(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _to_int_query(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _to_float_value(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _to_bool_query(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return bool(default)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _to_int_value(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


class _ControlRequestHandler(BaseHTTPRequestHandler):
    """Synchronous HTTP handler that proxies into asyncio runtime."""

    runtime: DeviceRuntimeCore | None = None
    vision: VisionService | None = None
    lifelog: LifelogService | None = None
    digital_task: DigitalTaskService | None = None
    adapter: GatewayAdapter | None = None
    loop: asyncio.AbstractEventLoop | None = None
    auth_enabled: bool = False
    auth_token: str = ""
    observability_history: list[dict[str, Any]] = []
    observability_lock = threading.Lock()
    observability_max_samples: int = 4000
    observability_store: Any | None = None
    max_request_body_bytes: int = 12 * 1024 * 1024
    control_api_rate_limit_enabled: bool = True
    control_api_rate_limiter: RequestRateLimiter | None = None
    control_api_replay_protection_enabled: bool = False
    control_api_replay_protector: RequestReplayProtector | None = None

    server_version = "opencane-hw/0.1"

    def do_GET(self) -> None:  # noqa: N802
        if not self._ensure_rate_limited():
            return
        if not self._ensure_authorized():
            return
        parsed = urlparse(self.path)
        parts = [p for p in parsed.path.split("/") if p]
        if parts == ["v1", "runtime", "status"]:
            self._send_json(HTTPStatus.OK, self.runtime.get_runtime_status() if self.runtime else {})
            return
        if parts == ["v1", "runtime", "observability"]:
            self._get_runtime_observability(parsed.query)
            return
        if parts == ["v1", "runtime", "observability", "history"]:
            self._get_runtime_observability_history(parsed.query)
            return
        if len(parts) == 4 and parts[:2] == ["v1", "device"] and parts[3] == "status":
            device_id = parts[2]
            status = self.runtime.get_device_status(device_id) if self.runtime else None
            if status:
                self._send_json(HTTPStatus.OK, {"success": True, "device": status})
            else:
                self._send_json(HTTPStatus.NOT_FOUND, {"success": False, "error": "device not found"})
            return
        if parts == ["v1", "device", "binding"]:
            self._get_device_binding(parsed.query)
            return
        if parts == ["v1", "device", "ops"]:
            self._get_device_operations(parsed.query)
            return
        if parts == ["v1", "lifelog", "timeline"]:
            self._get_lifelog_timeline(parsed.query)
            return
        if parts == ["v1", "lifelog", "thought_trace"]:
            self._get_lifelog_thought_trace(parsed.query)
            return
        if parts == ["v1", "lifelog", "thought_trace", "replay"]:
            self._get_lifelog_thought_trace_replay(parsed.query)
            return
        if parts == ["v1", "lifelog", "telemetry_samples"]:
            self._get_lifelog_telemetry_samples(parsed.query)
            return
        if parts == ["v1", "lifelog", "safety", "stats"]:
            self._get_lifelog_safety_stats(parsed.query)
            return
        if parts == ["v1", "lifelog", "safety"]:
            self._get_lifelog_safety(parsed.query)
            return
        if parts == ["v1", "lifelog", "device_sessions"]:
            self._get_lifelog_device_sessions(parsed.query)
            return
        if parts == ["v1", "digital-task"]:
            self._get_digital_task_list(parsed.query)
            return
        if parts == ["v1", "digital-task", "stats"]:
            self._get_digital_task_stats(parsed.query)
            return
        if len(parts) == 3 and parts[:2] == ["v1", "digital-task"]:
            self._get_digital_task(parts[2])
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"success": False, "error": "unknown endpoint"})

    def do_POST(self) -> None:  # noqa: N802
        if not self._ensure_rate_limited():
            return
        if not self._ensure_authorized():
            return
        if not self._ensure_not_replayed():
            return
        parsed = urlparse(self.path)
        parts = [p for p in parsed.path.split("/") if p]
        payload = self._read_json_body()
        if payload is None:
            return

        if len(parts) == 4 and parts[:2] == ["v1", "device"] and parts[3] == "abort":
            self._post_abort(parts[2], payload)
            return
        if parts == ["v1", "device", "register"]:
            self._post_device_register(payload)
            return
        if parts == ["v1", "device", "bind"]:
            self._post_device_bind(payload)
            return
        if parts == ["v1", "device", "activate"]:
            self._post_device_activate(payload)
            return
        if parts == ["v1", "device", "revoke"]:
            self._post_device_revoke(payload)
            return
        if parts == ["v1", "device", "ops", "dispatch"]:
            self._post_device_operation_dispatch(payload)
            return
        if len(parts) == 5 and parts[:3] == ["v1", "device", "ops"] and parts[4] == "ack":
            self._post_device_operation_ack(parts[3], payload)
            return
        if len(parts) == 4 and parts[:2] == ["v1", "device"] and parts[3] in {"set_config", "tool_call", "ota_plan"}:
            self._post_device_operation_dispatch(
                payload,
                op_type_override=parts[3],
                device_id_override=parts[2],
            )
            return
        if parts == ["v1", "device", "event"]:
            self._post_device_event(payload)
            return
        if parts == ["v1", "vision", "analyze"]:
            self._post_vision(payload)
            return
        if parts == ["v1", "lifelog", "enqueue_image"]:
            self._post_lifelog_enqueue(payload)
            return
        if parts == ["v1", "lifelog", "query"]:
            self._post_lifelog_query(payload)
            return
        if parts == ["v1", "lifelog", "thought_trace"]:
            self._post_lifelog_thought_trace(payload)
            return
        if parts == ["v1", "lifelog", "retention", "cleanup"]:
            self._post_lifelog_retention_cleanup(payload)
            return
        if parts == ["v1", "digital-task", "execute"]:
            self._post_digital_task_execute(payload)
            return
        if len(parts) == 4 and parts[:2] == ["v1", "digital-task"] and parts[3] == "cancel":
            self._post_digital_task_cancel(parts[2], payload)
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"success": False, "error": "unknown endpoint"})

    def log_message(self, fmt: str, *args: Any) -> None:
        logger.debug("control-api " + fmt % args)

    @staticmethod
    def _is_authorized_request(
        headers: Any,
        *,
        enabled: bool,
        token: str,
    ) -> bool:
        if not enabled:
            return True
        expected = (token or "").strip()
        if not expected:
            return False
        raw_auth = str(headers.get("Authorization", "")).strip()
        if raw_auth.lower().startswith("bearer "):
            candidate = raw_auth[7:].strip()
        else:
            candidate = str(headers.get("X-Auth-Token", "")).strip()
        if not candidate:
            return False
        return hmac.compare_digest(candidate, expected)

    def _ensure_authorized(self) -> bool:
        if self._is_authorized_request(
            self.headers,
            enabled=self.auth_enabled,
            token=self.auth_token,
        ):
            return True
        self._send_json(HTTPStatus.UNAUTHORIZED, {"success": False, "error": "unauthorized"})
        return False

    def _request_identity(self) -> str:
        raw_auth = str(self.headers.get("Authorization", "")).strip()
        if raw_auth.lower().startswith("bearer "):
            candidate = raw_auth[7:].strip()
            if candidate:
                fingerprint = hashlib.sha256(candidate.encode("utf-8")).hexdigest()[:16]
                return f"bearer:{fingerprint}"
        x_auth_token = str(self.headers.get("X-Auth-Token", "")).strip()
        if x_auth_token:
            fingerprint = hashlib.sha256(x_auth_token.encode("utf-8")).hexdigest()[:16]
            return f"xauth:{fingerprint}"
        device_id = str(self.headers.get("X-Device-Id", "")).strip()
        if device_id:
            return f"device:{device_id}"
        if self.client_address:
            return f"ip:{self.client_address[0]}"
        return "unknown"

    def _ensure_rate_limited(self) -> bool:
        if not bool(self.control_api_rate_limit_enabled):
            return True
        limiter = self.control_api_rate_limiter
        if limiter is None:
            return True
        allowed = limiter.allow(key=self._request_identity())
        if allowed:
            return True
        self._send_json(
            HTTPStatus.TOO_MANY_REQUESTS,
            {"success": False, "error": "rate_limited"},
        )
        return False

    def _ensure_not_replayed(self) -> bool:
        if not bool(self.control_api_replay_protection_enabled):
            return True
        protector = self.control_api_replay_protector
        if protector is None:
            return True
        nonce = str(self.headers.get("X-Request-Nonce", "")).strip()
        raw_timestamp = self.headers.get("X-Request-Timestamp")
        timestamp_text = str(raw_timestamp or "").strip()
        if not nonce:
            self._send_json(HTTPStatus.BAD_REQUEST, {"success": False, "error": "missing_nonce"})
            return False
        if not timestamp_text:
            self._send_json(HTTPStatus.BAD_REQUEST, {"success": False, "error": "missing_timestamp"})
            return False
        timestamp_ms = parse_timestamp_ms(timestamp_text)
        if timestamp_ms is None:
            self._send_json(HTTPStatus.BAD_REQUEST, {"success": False, "error": "invalid_timestamp"})
            return False
        ok, reason = protector.validate(
            key=self._request_identity(),
            nonce=nonce,
            timestamp_ms=timestamp_ms,
        )
        if ok:
            return True
        code = HTTPStatus.CONFLICT if reason == "replayed_nonce" else HTTPStatus.BAD_REQUEST
        self._send_json(code, {"success": False, "error": reason})
        return False

    def _read_json_body(self) -> dict[str, Any] | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        max_body = max(1024, int(self.max_request_body_bytes))
        if length > max_body:
            self._send_json(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                {
                    "success": False,
                    "error": f"request body too large (max {max_body} bytes)",
                },
            )
            return None
        body = self.rfile.read(length) if length > 0 else b"{}"
        try:
            return json.loads(body.decode("utf-8"))
        except json.JSONDecodeError:
            self._send_json(HTTPStatus.BAD_REQUEST, {"success": False, "error": "invalid json"})
            return None

    @staticmethod
    def _resolve_future_result(
        future: Any,
        *,
        timeout: float,
    ) -> tuple[bool, Any | None, HTTPStatus, str | None]:
        """Resolve a thread-safe asyncio future into (ok, result, http_status, error)."""
        try:
            return True, future.result(timeout=timeout), HTTPStatus.OK, None
        except FutureTimeoutError:
            with contextlib.suppress(Exception):
                future.cancel()
            return False, None, HTTPStatus.GATEWAY_TIMEOUT, "runtime timeout"
        except Exception as e:
            logger.warning(f"control-api future failed: {e}")
            return False, None, HTTPStatus.INTERNAL_SERVER_ERROR, "runtime error"

    def _post_abort(self, device_id: str, payload: dict[str, Any]) -> None:
        if not self.runtime or not self.loop:
            self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"success": False, "error": "runtime unavailable"})
            return
        reason = str(payload.get("reason") or "manual_abort")
        fut = asyncio.run_coroutine_threadsafe(self.runtime.abort(device_id, reason=reason), self.loop)
        ok_wait, result, err_code, err_msg = self._resolve_future_result(fut, timeout=5)
        if not ok_wait:
            self._send_json(err_code, {"success": False, "error": err_msg})
            return
        ok = bool(result)
        code = HTTPStatus.OK if ok else HTTPStatus.NOT_FOUND
        self._send_json(code, {"success": ok, "device_id": device_id})

    def _get_device_binding(self, query: str) -> None:
        if not self.lifelog or not self.loop:
            self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"success": False, "error": "lifelog unavailable"})
            return
        if not hasattr(self.lifelog, "device_binding_query"):
            self._send_json(HTTPStatus.NOT_IMPLEMENTED, {"success": False, "error": "device binding unavailable"})
            return
        params = parse_qs(query or "")
        payload = {
            "device_id": _first_query_value(params, "device_id", "deviceId"),
            "status": _first_query_value(params, "status"),
            "user_id": _first_query_value(params, "user_id", "userId"),
            "mask_sensitive": _first_query_value(params, "mask_sensitive", "maskSensitive"),
            "limit": _first_query_value(params, "limit"),
            "offset": _first_query_value(params, "offset"),
        }
        fut = asyncio.run_coroutine_threadsafe(self.lifelog.device_binding_query(payload), self.loop)
        ok_wait, result, err_code, err_msg = self._resolve_future_result(fut, timeout=10)
        if not ok_wait:
            self._send_json(err_code, {"success": False, "error": err_msg})
            return
        status = HTTPStatus.OK if result.get("success") else HTTPStatus.BAD_REQUEST
        self._send_json(status, result)

    def _post_device_register(self, payload: dict[str, Any]) -> None:
        if not self.lifelog or not self.loop:
            self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"success": False, "error": "lifelog unavailable"})
            return
        if not hasattr(self.lifelog, "device_register"):
            self._send_json(HTTPStatus.NOT_IMPLEMENTED, {"success": False, "error": "device register unavailable"})
            return
        fut = asyncio.run_coroutine_threadsafe(self.lifelog.device_register(payload), self.loop)
        ok_wait, result, err_code, err_msg = self._resolve_future_result(fut, timeout=10)
        if not ok_wait:
            self._send_json(err_code, {"success": False, "error": err_msg})
            return
        status = HTTPStatus.OK if result.get("success") else HTTPStatus.BAD_REQUEST
        self._send_json(status, result)

    def _post_device_bind(self, payload: dict[str, Any]) -> None:
        if not self.lifelog or not self.loop:
            self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"success": False, "error": "lifelog unavailable"})
            return
        if not hasattr(self.lifelog, "device_bind"):
            self._send_json(HTTPStatus.NOT_IMPLEMENTED, {"success": False, "error": "device bind unavailable"})
            return
        fut = asyncio.run_coroutine_threadsafe(self.lifelog.device_bind(payload), self.loop)
        ok_wait, result, err_code, err_msg = self._resolve_future_result(fut, timeout=10)
        if not ok_wait:
            self._send_json(err_code, {"success": False, "error": err_msg})
            return
        status = HTTPStatus.OK if result.get("success") else HTTPStatus.BAD_REQUEST
        self._send_json(status, result)

    def _post_device_activate(self, payload: dict[str, Any]) -> None:
        if not self.lifelog or not self.loop:
            self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"success": False, "error": "lifelog unavailable"})
            return
        if not hasattr(self.lifelog, "device_activate"):
            self._send_json(HTTPStatus.NOT_IMPLEMENTED, {"success": False, "error": "device activate unavailable"})
            return
        fut = asyncio.run_coroutine_threadsafe(self.lifelog.device_activate(payload), self.loop)
        ok_wait, result, err_code, err_msg = self._resolve_future_result(fut, timeout=10)
        if not ok_wait:
            self._send_json(err_code, {"success": False, "error": err_msg})
            return
        status = HTTPStatus.OK if result.get("success") else HTTPStatus.BAD_REQUEST
        self._send_json(status, result)

    def _post_device_revoke(self, payload: dict[str, Any]) -> None:
        if not self.lifelog or not self.loop:
            self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"success": False, "error": "lifelog unavailable"})
            return
        if not hasattr(self.lifelog, "device_revoke"):
            self._send_json(HTTPStatus.NOT_IMPLEMENTED, {"success": False, "error": "device revoke unavailable"})
            return
        fut = asyncio.run_coroutine_threadsafe(self.lifelog.device_revoke(payload), self.loop)
        ok_wait, result, err_code, err_msg = self._resolve_future_result(fut, timeout=10)
        if not ok_wait:
            self._send_json(err_code, {"success": False, "error": err_msg})
            return
        status = HTTPStatus.OK if result.get("success") else HTTPStatus.BAD_REQUEST
        self._send_json(status, result)

    def _get_device_operations(self, query: str) -> None:
        if not self.lifelog or not self.loop:
            self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"success": False, "error": "lifelog unavailable"})
            return
        if not hasattr(self.lifelog, "device_operation_query"):
            self._send_json(HTTPStatus.NOT_IMPLEMENTED, {"success": False, "error": "device ops unavailable"})
            return
        params = parse_qs(query or "")
        payload = {
            "operation_id": _first_query_value(params, "operation_id", "operationId"),
            "device_id": _first_query_value(params, "device_id", "deviceId"),
            "status": _first_query_value(params, "status"),
            "op_type": _first_query_value(params, "op_type", "opType", "operation_type", "operationType", "type"),
            "limit": _first_query_value(params, "limit"),
            "offset": _first_query_value(params, "offset"),
        }
        fut = asyncio.run_coroutine_threadsafe(self.lifelog.device_operation_query(payload), self.loop)
        ok_wait, result, err_code, err_msg = self._resolve_future_result(fut, timeout=10)
        if not ok_wait:
            self._send_json(err_code, {"success": False, "error": err_msg})
            return
        status = HTTPStatus.OK if result.get("success") else HTTPStatus.BAD_REQUEST
        self._send_json(status, result)

    def _post_device_operation_dispatch(
        self,
        payload: dict[str, Any],
        *,
        op_type_override: str | None = None,
        device_id_override: str | None = None,
    ) -> None:
        if not self.runtime or not self.loop:
            self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"success": False, "error": "runtime unavailable"})
            return
        op_type = _normalize_device_op_type(
            op_type_override
            or payload.get("op_type")
            or payload.get("opType")
            or payload.get("operation_type")
            or payload.get("operationType")
            or payload.get("type")
        )
        device_id = str(device_id_override or payload.get("device_id") or payload.get("deviceId") or "").strip()
        session_id = str(payload.get("session_id") or payload.get("sessionId") or "").strip()
        operation_id = str(payload.get("operation_id") or payload.get("operationId") or "").strip()
        trace_id = str(payload.get("trace_id") or payload.get("traceId") or f"device-op:{operation_id}" or "device-op").strip()
        operation_payload = payload.get("payload")
        if not isinstance(operation_payload, dict):
            operation_payload = dict(payload)
            for key in [
                "device_id",
                "deviceId",
                "session_id",
                "sessionId",
                "op_type",
                "opType",
                "operation_type",
                "operationType",
                "type",
                "operation_id",
                "operationId",
                "trace_id",
                "traceId",
            ]:
                operation_payload.pop(key, None)

        record: dict[str, Any] | None = None
        if self.lifelog is not None and hasattr(self.lifelog, "device_operation_enqueue"):
            enqueue_payload = {
                "operation_id": operation_id,
                "device_id": device_id,
                "session_id": session_id,
                "op_type": op_type,
                "payload": operation_payload,
            }
            fut = asyncio.run_coroutine_threadsafe(self.lifelog.device_operation_enqueue(enqueue_payload), self.loop)
            ok_wait, enqueue_result, err_code, err_msg = self._resolve_future_result(fut, timeout=10)
            if not ok_wait:
                self._send_json(err_code, {"success": False, "error": err_msg})
                return
            if not enqueue_result.get("success"):
                self._send_json(HTTPStatus.BAD_REQUEST, enqueue_result)
                return
            record_data = enqueue_result.get("operation")
            record = record_data if isinstance(record_data, dict) else None
            operation_id = str((record or {}).get("operation_id") or operation_id)

        dispatch_body = dict(operation_payload)
        if operation_id and "operation_id" not in dispatch_body:
            dispatch_body["operation_id"] = operation_id
        dispatch_fut = asyncio.run_coroutine_threadsafe(
            self.runtime.dispatch_device_operation(
                device_id=device_id,
                session_id=session_id,
                op_type=op_type,
                payload=dispatch_body,
                trace_id=trace_id or "device-op",
            ),
            self.loop,
        )
        ok_wait, dispatch_result, err_code, err_msg = self._resolve_future_result(dispatch_fut, timeout=10)
        if not ok_wait:
            if self.lifelog is not None and hasattr(self.lifelog, "device_operation_mark") and operation_id:
                mark_fut = asyncio.run_coroutine_threadsafe(
                    self.lifelog.device_operation_mark(
                        {
                            "operation_id": operation_id,
                            "status": "failed",
                            "error": err_msg or "runtime timeout",
                        }
                    ),
                    self.loop,
                )
                with contextlib.suppress(Exception):
                    mark_fut.result(timeout=5)
            self._send_json(err_code, {"success": False, "error": err_msg})
            return
        if not dispatch_result.get("success"):
            if self.lifelog is not None and hasattr(self.lifelog, "device_operation_mark") and operation_id:
                mark_fut = asyncio.run_coroutine_threadsafe(
                    self.lifelog.device_operation_mark(
                        {
                            "operation_id": operation_id,
                            "status": "failed",
                            "error": str(dispatch_result.get("error") or "dispatch_failed"),
                            "result": dispatch_result,
                        }
                    ),
                    self.loop,
                )
                with contextlib.suppress(Exception):
                    mark_fut.result(timeout=5)
            status = _error_to_status(dispatch_result.get("error_code"))
            self._send_json(status, dispatch_result)
            return

        if self.lifelog is not None and hasattr(self.lifelog, "device_operation_mark") and operation_id:
            mark_fut = asyncio.run_coroutine_threadsafe(
                self.lifelog.device_operation_mark(
                    {
                        "operation_id": operation_id,
                        "status": "sent",
                        "result": dispatch_result,
                        "session_id": dispatch_result.get("session_id"),
                    }
                ),
                self.loop,
            )
            with contextlib.suppress(Exception):
                mark_result = mark_fut.result(timeout=5)
                marked = mark_result.get("operation")
                if isinstance(marked, dict):
                    record = marked

        self._send_json(
            HTTPStatus.OK,
            {
                "success": True,
                "operation": record,
                "dispatch": dispatch_result,
            },
        )

    def _post_device_operation_ack(self, operation_id: str, payload: dict[str, Any]) -> None:
        if not self.lifelog or not self.loop:
            self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"success": False, "error": "lifelog unavailable"})
            return
        if not hasattr(self.lifelog, "device_operation_mark"):
            self._send_json(HTTPStatus.NOT_IMPLEMENTED, {"success": False, "error": "device ops unavailable"})
            return
        status = str(payload.get("status") or "acked").strip().lower()
        mark_payload = {
            "operation_id": str(operation_id or ""),
            "status": status,
            "result": payload.get("result") if isinstance(payload.get("result"), dict) else {},
            "error": str(payload.get("error") or ""),
            "acked_at_ms": payload.get("acked_at_ms"),
        }
        fut = asyncio.run_coroutine_threadsafe(self.lifelog.device_operation_mark(mark_payload), self.loop)
        ok_wait, result, err_code, err_msg = self._resolve_future_result(fut, timeout=10)
        if not ok_wait:
            self._send_json(err_code, {"success": False, "error": err_msg})
            return
        status_code = HTTPStatus.OK if result.get("success") else HTTPStatus.BAD_REQUEST
        self._send_json(status_code, result)

    def _get_runtime_observability(self, query: str) -> None:
        if not self.runtime:
            self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"success": False, "error": "runtime unavailable"})
            return
        params = parse_qs(query or "")
        task_failure_rate_max = _to_float_query(
            _first_query_value(params, "task_failure_rate_max", "taskFailureRateMax"),
            0.3,
        )
        safety_downgrade_rate_max = _to_float_query(
            _first_query_value(params, "safety_downgrade_rate_max", "safetyDowngradeRateMax"),
            0.35,
        )
        device_offline_rate_max = _to_float_query(
            _first_query_value(params, "device_offline_rate_max", "deviceOfflineRateMax"),
            0.3,
        )
        ingest_queue_utilization_max = _to_float_query(
            _first_query_value(
                params,
                "ingest_queue_utilization_max",
                "ingestQueueUtilizationMax",
            ),
            0.85,
        )
        min_task_total_for_alert = _to_int_query(
            _first_query_value(
                params,
                "min_task_total_for_alert",
                "minTaskTotalForAlert",
            ),
            10,
        )
        min_safety_applied_for_alert = _to_int_query(
            _first_query_value(
                params,
                "min_safety_applied_for_alert",
                "minSafetyAppliedForAlert",
            ),
            10,
        )
        min_devices_total_for_alert = _to_int_query(
            _first_query_value(
                params,
                "min_devices_total_for_alert",
                "minDevicesTotalForAlert",
            ),
            1,
        )
        ingest_rejected_active_queue_depth_min = _to_int_query(
            _first_query_value(
                params,
                "ingest_rejected_active_queue_depth_min",
                "ingestRejectedActiveQueueDepthMin",
            ),
            1,
        )
        ingest_rejected_active_utilization_min = _to_float_query(
            _first_query_value(
                params,
                "ingest_rejected_active_utilization_min",
                "ingestRejectedActiveUtilizationMin",
            ),
            0.2,
        )
        payload = runtime_observability_payload(
            self.runtime.get_runtime_status(),
            task_failure_rate_max=task_failure_rate_max,
            safety_downgrade_rate_max=safety_downgrade_rate_max,
            device_offline_rate_max=device_offline_rate_max,
            ingest_queue_utilization_max=ingest_queue_utilization_max,
            min_task_total_for_alert=min_task_total_for_alert,
            min_safety_applied_for_alert=min_safety_applied_for_alert,
            min_devices_total_for_alert=min_devices_total_for_alert,
            ingest_rejected_active_queue_depth_min=ingest_rejected_active_queue_depth_min,
            ingest_rejected_active_utilization_min=ingest_rejected_active_utilization_min,
        )
        self._record_observability_sample(payload)
        self._send_json(HTTPStatus.OK, payload)

    def _record_observability_sample(self, payload: dict[str, Any]) -> None:
        metrics = payload.get("metrics")
        metric_map = metrics if isinstance(metrics, dict) else {}
        thresholds = payload.get("thresholds")
        threshold_map = thresholds if isinstance(thresholds, dict) else {}
        sample = {
            "ts": int(_to_int_value(payload.get("ts"), int(time.time() * 1000))),
            "healthy": bool(payload.get("healthy")),
            "metrics": {
                "task_failure_rate": round(_to_float_value(metric_map.get("task_failure_rate"), 0.0), 4),
                "safety_downgrade_rate": round(
                    _to_float_value(metric_map.get("safety_downgrade_rate"), 0.0),
                    4,
                ),
                "device_offline_rate": round(
                    _to_float_value(metric_map.get("device_offline_rate"), 0.0),
                    4,
                ),
                "ingest_queue_utilization": round(
                    _to_float_value(metric_map.get("ingest_queue_utilization"), 0.0),
                    4,
                ),
                "ingest_queue_depth": int(_to_int_value(metric_map.get("ingest_queue_depth"), 0)),
                "ingest_queue_max_size": int(_to_int_value(metric_map.get("ingest_queue_max_size"), 0)),
                "ingest_queue_rejected_total": int(
                    _to_int_value(metric_map.get("ingest_queue_rejected_total"), 0)
                ),
                "ingest_queue_dropped_total": int(
                    _to_int_value(metric_map.get("ingest_queue_dropped_total"), 0)
                ),
                "voice_turn_total": int(_to_int_value(metric_map.get("voice_turn_total"), 0)),
                "voice_turn_failed": int(_to_int_value(metric_map.get("voice_turn_failed"), 0)),
                "voice_turn_failure_rate": round(
                    _to_float_value(metric_map.get("voice_turn_failure_rate"), 0.0),
                    4,
                ),
                "voice_turn_avg_latency_ms": round(
                    _to_float_value(metric_map.get("voice_turn_avg_latency_ms"), 0.0),
                    2,
                ),
                "voice_turn_max_latency_ms": round(
                    _to_float_value(metric_map.get("voice_turn_max_latency_ms"), 0.0),
                    2,
                ),
                "stt_avg_latency_ms": round(_to_float_value(metric_map.get("stt_avg_latency_ms"), 0.0), 2),
                "stt_max_latency_ms": round(_to_float_value(metric_map.get("stt_max_latency_ms"), 0.0), 2),
                "agent_avg_latency_ms": round(
                    _to_float_value(metric_map.get("agent_avg_latency_ms"), 0.0),
                    2,
                ),
                "agent_max_latency_ms": round(
                    _to_float_value(metric_map.get("agent_max_latency_ms"), 0.0),
                    2,
                ),
            },
            "thresholds": {
                "task_failure_rate_max": round(
                    _to_float_value(threshold_map.get("task_failure_rate_max"), 0.0),
                    4,
                ),
                "safety_downgrade_rate_max": round(
                    _to_float_value(threshold_map.get("safety_downgrade_rate_max"), 0.0),
                    4,
                ),
                "device_offline_rate_max": round(
                    _to_float_value(threshold_map.get("device_offline_rate_max"), 0.0),
                    4,
                ),
                "ingest_queue_utilization_max": round(
                    _to_float_value(threshold_map.get("ingest_queue_utilization_max"), 0.0),
                    4,
                ),
            },
        }
        with self.observability_lock:
            self.observability_history.append(sample)
            max_samples = max(100, int(self.observability_max_samples))
            if len(self.observability_history) > max_samples:
                overflow = len(self.observability_history) - max_samples
                if overflow > 0:
                    del self.observability_history[:overflow]
        if self.lifelog is not None and hasattr(self.lifelog, "record_observability_sample"):
            try:
                self.lifelog.record_observability_sample(sample)
            except Exception as e:
                logger.debug(f"observability sample persist failed: {e}")
        if self.observability_store is not None and hasattr(self.observability_store, "add_sample"):
            try:
                self.observability_store.add_sample(sample)
            except Exception as e:
                logger.debug(f"observability sqlite persist failed: {e}")

    def _get_runtime_observability_history(self, query: str) -> None:
        params = parse_qs(query or "")
        window_seconds = max(60, _to_int_value(_first_query_value(params, "window_seconds", "windowSeconds"), 1800))
        bucket_seconds = max(5, _to_int_value(_first_query_value(params, "bucket_seconds", "bucketSeconds"), 60))
        max_points = max(1, _to_int_value(_first_query_value(params, "max_points", "maxPoints"), 240))
        include_raw = _to_bool_query(_first_query_value(params, "include_raw", "includeRaw"), default=False)
        now_ms = int(time.time() * 1000)
        start_ts = int(now_ms - window_seconds * 1000)
        source = "memory"
        samples: list[dict[str, Any]]
        if self.observability_store is not None and hasattr(self.observability_store, "list_samples"):
            try:
                samples = list(
                    self.observability_store.list_samples(
                        start_ts=start_ts,
                        end_ts=now_ms,
                        limit=max(100, max_points * 20),
                        offset=0,
                    )
                )
                source = "sqlite_observability"
            except Exception as e:
                logger.debug(f"observability sqlite load failed, fallback to lifelog/memory: {e}")
                samples = []
        else:
            samples = []
        if not samples and self.lifelog is not None and hasattr(self.lifelog, "list_observability_samples"):
            try:
                samples = list(
                    self.lifelog.list_observability_samples(
                        start_ts=start_ts,
                        end_ts=now_ms,
                        limit=max(100, max_points * 20),
                        offset=0,
                    )
                )
                source = "sqlite"
            except Exception as e:
                logger.debug(f"observability history load failed, fallback to memory: {e}")
        if not samples:
            with self.observability_lock:
                samples = list(self.observability_history)
            source = "memory"
        payload = build_observability_history_payload(
            samples=samples,
            now_ms=now_ms,
            window_seconds=window_seconds,
            bucket_seconds=bucket_seconds,
            max_points=max_points,
            include_raw=include_raw,
        )
        payload["source"] = source
        self._send_json(HTTPStatus.OK, payload)

    def _post_device_event(self, payload: dict[str, Any]) -> None:
        if not self.adapter or not hasattr(self.adapter, "inject_event"):
            self._send_json(HTTPStatus.BAD_REQUEST, {"success": False, "error": "adapter cannot inject events"})
            return
        try:
            env = CanonicalEnvelope.from_dict(payload)
        except ValueError as e:
            self._send_json(HTTPStatus.BAD_REQUEST, {"success": False, "error": str(e)})
            return
        fut = asyncio.run_coroutine_threadsafe(getattr(self.adapter, "inject_event")(env), self.loop)
        ok_wait, _, err_code, err_msg = self._resolve_future_result(fut, timeout=5)
        if not ok_wait:
            self._send_json(err_code, {"success": False, "error": err_msg})
            return
        self._send_json(HTTPStatus.OK, {"success": True, "event": env.to_dict()})

    def _post_vision(self, payload: dict[str, Any]) -> None:
        if not self.vision or not self.loop:
            self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"success": False, "error": "vision unavailable"})
            return
        fut = asyncio.run_coroutine_threadsafe(self.vision.analyze_payload(payload), self.loop)
        ok_wait, result, err_code, err_msg = self._resolve_future_result(fut, timeout=30)
        if not ok_wait:
            self._send_json(err_code, {"success": False, "error": err_msg})
            return
        code = HTTPStatus.OK if result.get("success") else HTTPStatus.BAD_REQUEST
        self._send_json(code, result)

    def _post_lifelog_enqueue(self, payload: dict[str, Any]) -> None:
        if not self.lifelog or not self.loop:
            self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"success": False, "error": "lifelog unavailable"})
            return
        fut = asyncio.run_coroutine_threadsafe(self.lifelog.enqueue_image(payload), self.loop)
        ok_wait, result, err_code, err_msg = self._resolve_future_result(fut, timeout=30)
        if not ok_wait:
            self._send_json(err_code, {"success": False, "error": err_msg})
            return
        status = HTTPStatus.OK if result.get("success") else HTTPStatus.BAD_REQUEST
        self._send_json(status, result)

    def _post_lifelog_query(self, payload: dict[str, Any]) -> None:
        if not self.lifelog or not self.loop:
            self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"success": False, "error": "lifelog unavailable"})
            return
        fut = asyncio.run_coroutine_threadsafe(self.lifelog.query(payload), self.loop)
        ok_wait, result, err_code, err_msg = self._resolve_future_result(fut, timeout=15)
        if not ok_wait:
            self._send_json(err_code, {"success": False, "error": err_msg})
            return
        status = HTTPStatus.OK if result.get("success") else HTTPStatus.BAD_REQUEST
        self._send_json(status, result)

    def _post_lifelog_thought_trace(self, payload: dict[str, Any]) -> None:
        if not self.lifelog or not self.loop:
            self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"success": False, "error": "lifelog unavailable"})
            return
        if not hasattr(self.lifelog, "thought_trace_append"):
            self._send_json(HTTPStatus.NOT_IMPLEMENTED, {"success": False, "error": "thought trace unavailable"})
            return
        fut = asyncio.run_coroutine_threadsafe(self.lifelog.thought_trace_append(payload), self.loop)
        ok_wait, result, err_code, err_msg = self._resolve_future_result(fut, timeout=10)
        if not ok_wait:
            self._send_json(err_code, {"success": False, "error": err_msg})
            return
        status = HTTPStatus.OK if result.get("success") else HTTPStatus.BAD_REQUEST
        self._send_json(status, result)

    def _get_lifelog_timeline(self, query: str) -> None:
        if not self.lifelog or not self.loop:
            self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"success": False, "error": "lifelog unavailable"})
            return
        params = parse_qs(query or "")
        payload = {
            "session_id": _first_query_value(params, "session_id", "sessionId"),
            "start_ts": _first_query_value(params, "start_ts"),
            "end_ts": _first_query_value(params, "end_ts"),
            "event_type": _first_query_value(params, "event_type", "eventType"),
            "risk_level": _first_query_value(params, "risk_level", "riskLevel"),
            "limit": _first_query_value(params, "limit"),
            "offset": _first_query_value(params, "offset"),
        }
        fut = asyncio.run_coroutine_threadsafe(self.lifelog.timeline_query(payload), self.loop)
        ok_wait, result, err_code, err_msg = self._resolve_future_result(fut, timeout=15)
        if not ok_wait:
            self._send_json(err_code, {"success": False, "error": err_msg})
            return
        status = HTTPStatus.OK if result.get("success") else HTTPStatus.BAD_REQUEST
        self._send_json(status, result)

    def _get_lifelog_thought_trace(self, query: str) -> None:
        if not self.lifelog or not self.loop:
            self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"success": False, "error": "lifelog unavailable"})
            return
        if not hasattr(self.lifelog, "thought_trace_query"):
            self._send_json(HTTPStatus.NOT_IMPLEMENTED, {"success": False, "error": "thought trace unavailable"})
            return
        params = parse_qs(query or "")
        payload = {
            "trace_id": _first_query_value(params, "trace_id", "traceId"),
            "session_id": _first_query_value(params, "session_id", "sessionId"),
            "source": _first_query_value(params, "source"),
            "stage": _first_query_value(params, "stage"),
            "start_ts": _first_query_value(params, "start_ts"),
            "end_ts": _first_query_value(params, "end_ts"),
            "order": _first_query_value(params, "order"),
            "limit": _first_query_value(params, "limit"),
            "offset": _first_query_value(params, "offset"),
        }
        fut = asyncio.run_coroutine_threadsafe(self.lifelog.thought_trace_query(payload), self.loop)
        ok_wait, result, err_code, err_msg = self._resolve_future_result(fut, timeout=15)
        if not ok_wait:
            self._send_json(err_code, {"success": False, "error": err_msg})
            return
        status = HTTPStatus.OK if result.get("success") else HTTPStatus.BAD_REQUEST
        self._send_json(status, result)

    def _get_lifelog_thought_trace_replay(self, query: str) -> None:
        if not self.lifelog or not self.loop:
            self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"success": False, "error": "lifelog unavailable"})
            return
        if not hasattr(self.lifelog, "thought_trace_replay"):
            self._send_json(HTTPStatus.NOT_IMPLEMENTED, {"success": False, "error": "thought trace unavailable"})
            return
        params = parse_qs(query or "")
        payload = {
            "trace_id": _first_query_value(params, "trace_id", "traceId"),
            "session_id": _first_query_value(params, "session_id", "sessionId"),
            "source": _first_query_value(params, "source"),
            "stage": _first_query_value(params, "stage"),
            "start_ts": _first_query_value(params, "start_ts"),
            "end_ts": _first_query_value(params, "end_ts"),
            "limit": _first_query_value(params, "limit"),
            "offset": _first_query_value(params, "offset"),
        }
        fut = asyncio.run_coroutine_threadsafe(self.lifelog.thought_trace_replay(payload), self.loop)
        ok_wait, result, err_code, err_msg = self._resolve_future_result(fut, timeout=20)
        if not ok_wait:
            self._send_json(err_code, {"success": False, "error": err_msg})
            return
        status = HTTPStatus.OK if result.get("success") else HTTPStatus.BAD_REQUEST
        self._send_json(status, result)

    def _get_lifelog_telemetry_samples(self, query: str) -> None:
        if not self.lifelog or not self.loop:
            self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"success": False, "error": "lifelog unavailable"})
            return
        if not hasattr(self.lifelog, "telemetry_samples_query"):
            self._send_json(HTTPStatus.NOT_IMPLEMENTED, {"success": False, "error": "telemetry samples unavailable"})
            return
        params = parse_qs(query or "")
        payload = {
            "device_id": _first_query_value(params, "device_id", "deviceId"),
            "session_id": _first_query_value(params, "session_id", "sessionId"),
            "trace_id": _first_query_value(params, "trace_id", "traceId"),
            "start_ts": _first_query_value(params, "start_ts"),
            "end_ts": _first_query_value(params, "end_ts"),
            "limit": _first_query_value(params, "limit"),
            "offset": _first_query_value(params, "offset"),
        }
        fut = asyncio.run_coroutine_threadsafe(self.lifelog.telemetry_samples_query(payload), self.loop)
        ok_wait, result, err_code, err_msg = self._resolve_future_result(fut, timeout=15)
        if not ok_wait:
            self._send_json(err_code, {"success": False, "error": err_msg})
            return
        status = HTTPStatus.OK if result.get("success") else HTTPStatus.BAD_REQUEST
        self._send_json(status, result)

    def _post_lifelog_retention_cleanup(self, payload: dict[str, Any]) -> None:
        if not self.lifelog or not self.loop:
            self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"success": False, "error": "lifelog unavailable"})
            return
        if not hasattr(self.lifelog, "retention_cleanup"):
            self._send_json(HTTPStatus.NOT_IMPLEMENTED, {"success": False, "error": "retention cleanup unavailable"})
            return
        fut = asyncio.run_coroutine_threadsafe(self.lifelog.retention_cleanup(payload), self.loop)
        ok_wait, result, err_code, err_msg = self._resolve_future_result(fut, timeout=20)
        if not ok_wait:
            self._send_json(err_code, {"success": False, "error": err_msg})
            return
        status = HTTPStatus.OK if result.get("success") else HTTPStatus.BAD_REQUEST
        self._send_json(status, result)

    def _get_lifelog_safety(self, query: str) -> None:
        if not self.lifelog or not self.loop:
            self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"success": False, "error": "lifelog unavailable"})
            return
        if not hasattr(self.lifelog, "safety_query"):
            self._send_json(HTTPStatus.NOT_IMPLEMENTED, {"success": False, "error": "safety query unavailable"})
            return
        params = parse_qs(query or "")
        payload = {
            "session_id": _first_query_value(params, "session_id", "sessionId"),
            "trace_id": _first_query_value(params, "trace_id", "traceId"),
            "source": _first_query_value(params, "source"),
            "risk_level": _first_query_value(params, "risk_level", "riskLevel"),
            "downgraded": _first_query_value(params, "downgraded"),
            "start_ts": _first_query_value(params, "start_ts"),
            "end_ts": _first_query_value(params, "end_ts"),
            "limit": _first_query_value(params, "limit"),
            "offset": _first_query_value(params, "offset"),
        }
        fut = asyncio.run_coroutine_threadsafe(self.lifelog.safety_query(payload), self.loop)
        ok_wait, result, err_code, err_msg = self._resolve_future_result(fut, timeout=15)
        if not ok_wait:
            self._send_json(err_code, {"success": False, "error": err_msg})
            return
        status = HTTPStatus.OK if result.get("success") else HTTPStatus.BAD_REQUEST
        self._send_json(status, result)

    def _get_lifelog_safety_stats(self, query: str) -> None:
        if not self.lifelog or not self.loop:
            self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"success": False, "error": "lifelog unavailable"})
            return
        if not hasattr(self.lifelog, "safety_stats"):
            self._send_json(HTTPStatus.NOT_IMPLEMENTED, {"success": False, "error": "safety stats unavailable"})
            return
        params = parse_qs(query or "")
        payload = {
            "session_id": _first_query_value(params, "session_id", "sessionId"),
            "source": _first_query_value(params, "source"),
            "risk_level": _first_query_value(params, "risk_level", "riskLevel"),
            "start_ts": _first_query_value(params, "start_ts"),
            "end_ts": _first_query_value(params, "end_ts"),
        }
        fut = asyncio.run_coroutine_threadsafe(self.lifelog.safety_stats(payload), self.loop)
        ok_wait, result, err_code, err_msg = self._resolve_future_result(fut, timeout=15)
        if not ok_wait:
            self._send_json(err_code, {"success": False, "error": err_msg})
            return
        status = HTTPStatus.OK if result.get("success") else HTTPStatus.BAD_REQUEST
        self._send_json(status, result)

    def _get_lifelog_device_sessions(self, query: str) -> None:
        if not self.lifelog or not self.loop:
            self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"success": False, "error": "lifelog unavailable"})
            return
        if not hasattr(self.lifelog, "device_sessions_query"):
            self._send_json(
                HTTPStatus.NOT_IMPLEMENTED,
                {"success": False, "error": "device session query unavailable"},
            )
            return
        params = parse_qs(query or "")
        payload = {
            "device_id": _first_query_value(params, "device_id", "deviceId"),
            "state": _first_query_value(params, "state"),
            "limit": _first_query_value(params, "limit"),
            "offset": _first_query_value(params, "offset"),
        }
        fut = asyncio.run_coroutine_threadsafe(self.lifelog.device_sessions_query(payload), self.loop)
        ok_wait, result, err_code, err_msg = self._resolve_future_result(fut, timeout=15)
        if not ok_wait:
            self._send_json(err_code, {"success": False, "error": err_msg})
            return
        status = HTTPStatus.OK if result.get("success") else HTTPStatus.BAD_REQUEST
        self._send_json(status, result)

    def _post_digital_task_execute(self, payload: dict[str, Any]) -> None:
        if not self.digital_task or not self.loop:
            self._send_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"success": False, "error": "digital_task unavailable"},
            )
            return
        fut = asyncio.run_coroutine_threadsafe(self.digital_task.execute(payload), self.loop)
        ok_wait, result, err_code, err_msg = self._resolve_future_result(fut, timeout=10)
        if not ok_wait:
            self._send_json(err_code, {"success": False, "error": err_msg})
            return
        status = _error_to_status(result.get("error_code")) if not result.get("success") else HTTPStatus.OK
        self._send_json(status, result)

    def _get_digital_task(self, task_id: str) -> None:
        if not self.digital_task or not self.loop:
            self._send_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"success": False, "error": "digital_task unavailable"},
            )
            return
        fut = asyncio.run_coroutine_threadsafe(self.digital_task.get_task(task_id), self.loop)
        ok_wait, result, err_code, err_msg = self._resolve_future_result(fut, timeout=5)
        if not ok_wait:
            self._send_json(err_code, {"success": False, "error": err_msg})
            return
        status = _error_to_status(result.get("error_code")) if not result.get("success") else HTTPStatus.OK
        self._send_json(status, result)

    def _get_digital_task_list(self, query: str) -> None:
        if not self.digital_task or not self.loop:
            self._send_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"success": False, "error": "digital_task unavailable"},
            )
            return
        params = parse_qs(query or "")
        payload = {
            "session_id": _first_query_value(params, "session_id", "sessionId"),
            "status": _first_query_value(params, "status"),
            "limit": _first_query_value(params, "limit"),
            "offset": _first_query_value(params, "offset"),
        }
        fut = asyncio.run_coroutine_threadsafe(self.digital_task.list_tasks(payload), self.loop)
        ok_wait, result, err_code, err_msg = self._resolve_future_result(fut, timeout=10)
        if not ok_wait:
            self._send_json(err_code, {"success": False, "error": err_msg})
            return
        status = _error_to_status(result.get("error_code")) if not result.get("success") else HTTPStatus.OK
        self._send_json(status, result)

    def _get_digital_task_stats(self, query: str) -> None:
        if not self.digital_task or not self.loop:
            self._send_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"success": False, "error": "digital_task unavailable"},
            )
            return
        params = parse_qs(query or "")
        payload = {
            "session_id": _first_query_value(params, "session_id", "sessionId"),
        }
        fut = asyncio.run_coroutine_threadsafe(self.digital_task.stats(payload), self.loop)
        ok_wait, result, err_code, err_msg = self._resolve_future_result(fut, timeout=5)
        if not ok_wait:
            self._send_json(err_code, {"success": False, "error": err_msg})
            return
        status = _error_to_status(result.get("error_code")) if not result.get("success") else HTTPStatus.OK
        self._send_json(status, result)

    def _post_digital_task_cancel(self, task_id: str, payload: dict[str, Any]) -> None:
        if not self.digital_task or not self.loop:
            self._send_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"success": False, "error": "digital_task unavailable"},
            )
            return
        reason = str(payload.get("reason") or "manual_cancel")
        fut = asyncio.run_coroutine_threadsafe(self.digital_task.cancel(task_id, reason=reason), self.loop)
        ok_wait, result, err_code, err_msg = self._resolve_future_result(fut, timeout=5)
        if not ok_wait:
            self._send_json(err_code, {"success": False, "error": err_msg})
            return
        status = _error_to_status(result.get("error_code")) if not result.get("success") else HTTPStatus.OK
        self._send_json(status, result)

    def _send_json(self, code: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json_response(payload)
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class HardwareControlServer:
    """Threaded HTTP control endpoint for runtime status and debug actions."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        runtime: DeviceRuntimeCore,
        vision: VisionService | None,
        lifelog: LifelogService | None,
        adapter: GatewayAdapter,
        loop: asyncio.AbstractEventLoop,
        digital_task: DigitalTaskService | None = None,
        observability_store: Any | None = None,
        observability_max_samples: int = 4000,
        max_request_body_bytes: int = 12 * 1024 * 1024,
        auth_enabled: bool = False,
        auth_token: str = "",
        control_api_rate_limit_enabled: bool = True,
        control_api_rate_limit_rpm: int = 600,
        control_api_rate_limit_burst: int = 120,
        control_api_replay_protection_enabled: bool = False,
        control_api_replay_window_seconds: int = 300,
    ) -> None:
        self.host = host
        self.port = port
        self.runtime = runtime
        self.vision = vision
        self.lifelog = lifelog
        self.digital_task = digital_task
        self.adapter = adapter
        self.loop = loop
        self.auth_enabled = auth_enabled
        self.auth_token = auth_token
        self.observability_store = observability_store
        self.observability_max_samples = max(100, int(observability_max_samples))
        self.max_request_body_bytes = max(1024, int(max_request_body_bytes))
        self.control_api_rate_limit_enabled = bool(control_api_rate_limit_enabled)
        self.control_api_rate_limit_rpm = max(1, int(control_api_rate_limit_rpm))
        self.control_api_rate_limit_burst = max(0, int(control_api_rate_limit_burst))
        self.control_api_replay_protection_enabled = bool(control_api_replay_protection_enabled)
        self.control_api_replay_window_seconds = max(10, int(control_api_replay_window_seconds))
        self._thread: threading.Thread | None = None
        self._server: ThreadingHTTPServer | None = None

    def start(self) -> None:
        handler_cls = type("BoundControlRequestHandler", (_ControlRequestHandler,), {})
        handler_cls.runtime = self.runtime
        handler_cls.vision = self.vision
        handler_cls.lifelog = self.lifelog
        handler_cls.digital_task = self.digital_task
        handler_cls.adapter = self.adapter
        handler_cls.loop = self.loop
        handler_cls.auth_enabled = self.auth_enabled
        handler_cls.auth_token = self.auth_token
        handler_cls.observability_history = []
        handler_cls.observability_lock = threading.Lock()
        handler_cls.observability_store = self.observability_store
        handler_cls.observability_max_samples = self.observability_max_samples
        handler_cls.max_request_body_bytes = self.max_request_body_bytes
        handler_cls.control_api_rate_limit_enabled = self.control_api_rate_limit_enabled
        handler_cls.control_api_rate_limiter = (
            RequestRateLimiter(
                requests_per_minute=self.control_api_rate_limit_rpm,
                burst=self.control_api_rate_limit_burst,
            )
            if self.control_api_rate_limit_enabled
            else None
        )
        handler_cls.control_api_replay_protection_enabled = self.control_api_replay_protection_enabled
        handler_cls.control_api_replay_protector = (
            RequestReplayProtector(window_seconds=self.control_api_replay_window_seconds)
            if self.control_api_replay_protection_enabled
            else None
        )
        self._server = ThreadingHTTPServer((self.host, self.port), handler_cls)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        logger.info(f"Hardware control API listening on http://{self.host}:{self.port}")

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        self._thread = None


def create_adapter_from_config(config: HardwareConfig) -> GatewayAdapter:
    """Factory helper to build selected hardware adapter."""
    adapter_name = (config.adapter or "websocket").lower()
    if adapter_name == "mock":
        return MockAdapter()
    if adapter_name == "ec600":
        return EC600MQTTAdapter(config.mqtt, packet_magic=config.packet_magic)
    if adapter_name == "generic_mqtt":
        mqtt_config, profile, packet_magic, audio_mode = build_generic_mqtt_runtime(
            config.mqtt,
            profile_name=config.device_profile,
            profile_overrides=config.profile_overrides,
            fallback_packet_magic=config.packet_magic,
        )
        return GenericMQTTAdapter(
            config=mqtt_config,
            profile=profile,
            packet_magic=packet_magic,
            audio_up_mode=audio_mode,
        )
    if adapter_name == "legacy_demo":
        from opencane.hardware.adapter.legacy_websocket_adapter import LegacyWebSocketAdapter
        return LegacyWebSocketAdapter(config={"device_id": "legacy-device-001"})
    return WebSocketAdapter(
        host=config.host,
        port=config.port,
        require_token=config.auth.enabled,
        token=config.auth.token,
        packet_magic=config.packet_magic,
    )


def _error_to_status(error_code: Any) -> HTTPStatus:
    code = str(error_code or "")
    if code == "not_found":
        return HTTPStatus.NOT_FOUND
    if code == "conflict":
        return HTTPStatus.CONFLICT
    return HTTPStatus.BAD_REQUEST


def _normalize_device_op_type(value: Any) -> str:
    text = str(value or "").strip().lower()
    alias = {
        "set_config": "set_config",
        "config": "set_config",
        "tool_call": "tool_call",
        "tool": "tool_call",
        "ota_plan": "ota_plan",
        "ota": "ota_plan",
    }
    return alias.get(text, text)
