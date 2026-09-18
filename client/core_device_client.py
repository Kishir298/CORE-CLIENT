"""Minimal external-device client for C.O.R.E. v0.3.0.

Stdlib only — intentionally does NOT import internal ``core`` modules so the
Mac behaves as a true external device over real LAN TCP + TLS.

Protocol (matches TcpTransport hardened path):
    TCP connect -> TLS handshake -> CORE_HANDSHAKE -> CORE_HANDSHAKE_RESPONSE
    -> DEVICE_REGISTER -> DEVICE_REGISTER_RESPONSE -> online

OPTION A behavior:
    Persistent (remembered device file, no secrets):
        device_id, identity_id, join_name, device_name, device_type,
        platform, capabilities, protocol_version, host, port.
    Ephemeral (memory only, never written to disk):
        login token/credential, active socket, connection_id, auth state,
        lease information (connected_at, lease_expires_at,
        lease_duration_seconds).
    - Startup loads the remembered device but ALWAYS requires login again.
    - Full shutdown destroys the session; next launch requires login.
    - Reconnect while the app is open reuses the in-memory token and the
      remembered device identity (no re-registration from scratch).
    - join_name is generated once at first configuration, remembered, and
      stays stable across reconnects and restarts.
    - The host is authoritative for the 24-hour connection lease; the client
      only tracks lease information for state/UX.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import socket
import ssl
import struct
import uuid
from datetime import datetime, timezone
from pathlib import Path

PROTOCOL_VERSION = "0.3.0"
HANDSHAKE_TYPE = "CORE_HANDSHAKE"
HANDSHAKE_RESPONSE_TYPE = "CORE_HANDSHAKE_RESPONSE"
REGISTER_TYPE = "DEVICE_REGISTER"
REGISTER_RESPONSE_TYPE = "DEVICE_REGISTER_RESPONSE"
DISCOVER_TYPE = "DEVICE_DISCOVER"
DISCOVER_RESPONSE_TYPE = "DEVICE_DISCOVER_RESPONSE"
INFO_TYPE = "DEVICE_INFO"
INFO_RESPONSE_TYPE = "DEVICE_INFO_RESPONSE"
DATA_REQUEST_TYPE = "DATA_REQUEST"
DATA_RESPONSE_TYPE = "DATA_RESPONSE"
DATA_ERROR_TYPE = "DATA_ERROR"
SERVICE_REQUEST_TYPE = "SERVICE_REQUEST"
SERVICE_RESPONSE_TYPE = "SERVICE_RESPONSE"
ERROR_TYPE = "DEVICE_ERROR"
HEADER_SIZE = 4
MAX_FRAME_SIZE = 10 * 1024 * 1024

# Keys that must NEVER be persisted to the remembered-device file.
_EPHEMERAL_KEYS = frozenset(
    {
        "token",
        "credential",
        "password",
        "api_token",
        "session",
        "session_token",
        "connection_id",
        "authenticated",
        "lease_expires_at",
        "lease_duration_seconds",
        "connected_at",
    }
)


class DeviceClientError(Exception):
    """Raised for client-side protocol/transport failures."""


def _sanitize_name_part(value: str) -> str:
    """Keep a join-name part human-readable and token-free."""
    cleaned = "".join(c if (c.isalnum() or c in ("-", "_", ".")) else "-" for c in value.strip())
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-._") or "device"


def generate_join_name(device_name: str, device_id: str) -> str:
    """Derive the stable human-readable join name for a device.

    Format: ``<device-name>-<short-device-id>`` (e.g. ``MacBook-mac-01``).
    Deterministic for the same inputs; contains no secrets.
    """
    name = _sanitize_name_part(device_name or device_id)
    short_id = _sanitize_name_part(device_id)
    join_name = f"{name}-{short_id}"
    return join_name[:64]


def default_device_file() -> Path:
    return Path.home() / ".risarms-device.json"


def _new_message(
    source: str,
    destination: str,
    message_type: str,
    payload: dict,
    identity_id: str | None,
) -> dict:
    return {
        "message_id": str(uuid.uuid4()),
        "source": source,
        "destination": destination,
        "message_type": message_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "request_id": None,
        "payload": payload,
        "identity_id": identity_id,
    }


def _send_frame(sock: socket.socket, message: dict) -> None:
    data = json.dumps(message).encode("utf-8")
    if not data or len(data) > MAX_FRAME_SIZE:
        raise DeviceClientError("Outbound frame size invalid.")
    sock.sendall(struct.pack("!I", len(data)) + data)


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise DeviceClientError("Connection closed by C.O.R.E. host.")
        buf += chunk
    return buf


def _recv_frame(sock: socket.socket) -> dict:
    header = _recv_exact(sock, HEADER_SIZE)
    (length,) = struct.unpack("!I", header)
    if length <= 0 or length > MAX_FRAME_SIZE:
        raise DeviceClientError("Invalid inbound frame size.")
    data = _recv_exact(sock, length)
    try:
        message = json.loads(data.decode("utf-8"))
    except Exception as exc:
        raise DeviceClientError("Invalid inbound message encoding.") from exc
    if not isinstance(message, dict) or not message.get("message_type"):
        raise DeviceClientError("Malformed inbound message.")
    return message


class CoreDeviceClient:
    """External C.O.R.E. device with Option A login semantics."""

    def __init__(
        self,
        host: str,
        port: int,
        device_id: str,
        device_name: str = "",
        device_type: str = "generic",
        platform: str = "mac",
        capabilities: list | None = None,
        protocol_version: str = PROTOCOL_VERSION,
        device_file: Path | str | None = None,
        use_tls: bool = True,
        ca_file: Path | str | None = None,
        insecure: bool = False,
        timeout: float = 10.0,
        join_name: str | None = None,
    ) -> None:
        if not device_id or not device_id.strip():
            raise ValueError("device_id cannot be empty.")
        self.host = host
        self.port = int(port)
        self.device_id = device_id.strip()
        # identity_id is bound to device_id per server contract.
        self.identity_id = self.device_id
        self.device_name = device_name or self.device_id
        # join_name is generated once, remembered, and stable across
        # reconnects and restarts. Never derived from secrets.
        self.join_name = (join_name or "").strip() or generate_join_name(
            self.device_name, self.device_id
        )
        self.device_type = device_type
        self.platform = platform
        self.capabilities = list(capabilities or [])
        self.protocol_version = protocol_version
        self.device_file = Path(device_file) if device_file else default_device_file()
        self.use_tls = bool(use_tls)
        self.ca_file = Path(ca_file) if ca_file else None
        self.insecure = bool(insecure)
        self.timeout = float(timeout)
        # -- ephemeral session state (never persisted) --
        # Provisioning credential: long-term secret, RAM only, used solely
        # to authenticate at CORE_HANDSHAKE. Never confused with the
        # temporary host-issued session token below.
        self._provisioning_credential: str | None = None
        # Temporary host-issued session token: exists only after successful
        # authentication, RAM only, rotated every connection, destroyed on
        # disconnect/expiry/shutdown. Never persisted.
        self._session_token: str | None = None
        self._sock: socket.socket | None = None
        self._connection_id: str | None = None
        self._authenticated = False
        self._registered = False
        # -- lease tracking (host-authoritative, tracked locally only) --
        self._connected_at: str | None = None
        self._lease_expires_at: str | None = None
        self._lease_duration_seconds: int | None = None

    # -- remembered device (persistent, no secrets) --
    def remembered_state(self) -> dict:
        return {
            "device_id": self.device_id,
            "identity_id": self.identity_id,
            "join_name": self.join_name,
            "device_name": self.device_name,
            "device_type": self.device_type,
            "platform": self.platform,
            "capabilities": list(self.capabilities),
            "protocol_version": self.protocol_version,
            "host": self.host,
            "port": self.port,
        }

    def save_remembered(self) -> Path:
        state = self.remembered_state()
        for key in _EPHEMERAL_KEYS:
            state.pop(key, None)
        self.device_file.parent.mkdir(parents=True, exist_ok=True)
        self.device_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
        try:
            os.chmod(self.device_file, 0o600)
        except OSError:
            pass
        return self.device_file

    @classmethod
    def load_remembered(
        cls,
        device_file: Path | str | None = None,
        **overrides,
    ) -> "CoreDeviceClient":
        path = Path(device_file) if device_file else default_device_file()
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise DeviceClientError(f"No remembered device at {path}.") from exc
        if not isinstance(state, dict):
            raise DeviceClientError("Remembered device file is corrupt.")
        for key in _EPHEMERAL_KEYS:
            state.pop(key, None)
        # identity_id is derived from device_id; keep the file as the
        # remembered source but never let it diverge into a second identity.
        remembered_identity = state.pop("identity_id", None)
        state.update({k: v for k, v in overrides.items() if v is not None})
        client = cls(device_file=path, **state)
        if (
            isinstance(remembered_identity, str)
            and remembered_identity
            and remembered_identity != client.identity_id
        ):
            raise DeviceClientError("Remembered device file identity mismatch.")
        return client

    # -- login session (ephemeral) --
    def login(self, token: str) -> None:
        """Begin a login session. Provisioning credential, memory only."""
        if not token or not str(token).strip():
            raise DeviceClientError("Login requires a non-empty token.")
        self._provisioning_credential = str(token).strip()

    @property
    def _token(self) -> str | None:
        """Backward-compatible alias for the provisioning credential."""
        return self._provisioning_credential

    @_token.setter
    def _token(self, value: str | None) -> None:
        self._provisioning_credential = value

    def logout(self) -> None:
        """Destroy the login session without touching the remembered device."""
        self._provisioning_credential = None
        self._session_token = None
        self._authenticated = False
        self._registered = False
        self._connection_id = None
        self._connected_at = None
        self._lease_expires_at = None
        self._lease_duration_seconds = None

    def mark_disconnected(self) -> None:
        """Mark this client disconnected after a (possibly forced) close.

        Clears connection-scoped state (socket, session token,
        connection_id, auth flags, lease tracking) while preserving the
        remembered identity (device_id/identity_id/join_name) and — if the
        application is still running — the in-memory provisioning
        credential, so a fresh authenticated reconnect remains possible
        per Option A semantics. The expired session token is never reused.
        """
        self.close_socket()
        self._session_token = None
        self._connection_id = None
        self._authenticated = False
        self._registered = False
        self._connected_at = None
        self._lease_expires_at = None
        self._lease_duration_seconds = None

    @property
    def is_logged_in(self) -> bool:
        return self._provisioning_credential is not None

    @property
    def session_token(self) -> str | None:
        """Temporary host-issued session token (RAM only, None when offline)."""
        return self._session_token

    @property
    def connection_id(self) -> str | None:
        return self._connection_id

    @property
    def connected_at(self) -> str | None:
        """Host-reported connection start (tracked locally, not authoritative)."""
        return self._connected_at

    @property
    def lease_expires_at(self) -> str | None:
        """Host-reported lease expiry (tracked locally, not authoritative)."""
        return self._lease_expires_at

    @property
    def lease_duration_seconds(self) -> int | None:
        """Host-reported lease duration in seconds, if provided."""
        return self._lease_duration_seconds

    @property
    def is_connected(self) -> bool:
        return self._sock is not None and self._authenticated

    def _track_lease(self, payload: dict) -> None:
        """Record host-provided lease information (local tracking only)."""
        if not isinstance(payload, dict):
            return
        connected_at = payload.get("connected_at")
        lease_expires_at = payload.get("lease_expires_at")
        lease_duration = payload.get("lease_duration_seconds")
        if isinstance(connected_at, str) and connected_at:
            self._connected_at = connected_at
        if isinstance(lease_expires_at, str) and lease_expires_at:
            self._lease_expires_at = lease_expires_at
        if isinstance(lease_duration, int) and lease_duration > 0:
            self._lease_duration_seconds = lease_duration

    def _track_session(self, payload: dict) -> None:
        """Record the host-issued temporary session token (RAM only)."""
        if not isinstance(payload, dict):
            return
        token = payload.get("session_token")
        if isinstance(token, str) and token:
            self._session_token = token

    @staticmethod
    def _format_duration(total_seconds: float) -> str:
        total = max(0, int(total_seconds))
        hours, rem = divmod(total, 3600)
        minutes, seconds = divmod(rem, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    def lease_remaining_seconds(self, now: datetime | None = None) -> float | None:
        """Seconds until the tracked lease expiry (None when unknown)."""
        if not self._lease_expires_at:
            return None
        try:
            expiry = datetime.fromisoformat(self._lease_expires_at)
        except Exception:
            return None
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        current = now or datetime.now(timezone.utc)
        return (expiry - current).total_seconds()

    @property
    def lease_state(self) -> str:
        """Local UX lease state; the host remains authoritative."""
        if self._sock is None or not self._authenticated:
            return "DISCONNECTED"
        if not self._registered:
            return "AUTHENTICATED"
        remaining = self.lease_remaining_seconds()
        if remaining is None:
            return "ONLINE"
        if remaining <= 0:
            return "EXPIRED"
        if remaining <= 5 * 60:
            return "EXPIRING"
        return "ONLINE"

    def session_summary(self) -> dict:
        """In-memory session snapshot for display (never persisted)."""
        remaining = self.lease_remaining_seconds()
        return {
            "device": self.device_name,
            "device_id": self.device_id,
            "join_name": self.join_name,
            "status": self.lease_state,
            "connection_id": self._connection_id,
            "session_token": self._session_token,
            "lease_duration_seconds": self._lease_duration_seconds,
            "connected_at": self._connected_at,
            "lease_expires_at": self._lease_expires_at,
            "lease_remaining": (
                self._format_duration(remaining) if remaining is not None else None
            ),
        }

    @staticmethod
    def _is_closure_error(exc: Exception) -> bool:
        if isinstance(exc, DeviceClientError) and "Connection closed" in str(exc):
            return True
        # A write to a peer-closed socket surfaces as EPIPE/reset rather
        # than a clean EOF; both mean the connection is gone.
        return isinstance(
            exc, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)
        )

    def _connection_lost(self, exc: Exception) -> DeviceClientError:
        """Mark this client disconnected and normalize the error type."""
        self.mark_disconnected()
        if isinstance(exc, DeviceClientError):
            return exc
        return DeviceClientError(f"C.O.R.E. host connection lost: {exc}")

    # -- wire --
    def _build_tls_context(self) -> ssl.SSLContext:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        try:
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        except Exception:
            ctx.options |= getattr(ssl, "OP_NO_TLSv1", 0) | getattr(
                ssl, "OP_NO_TLSv1_1", 0
            )
        if self.insecure:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        else:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_REQUIRED
            if self.ca_file is not None:
                ctx.load_verify_locations(cafile=str(self.ca_file))
            else:
                try:
                    ctx.load_default_certs()
                except Exception:
                    pass
        return ctx

    def connect(self) -> dict:
        """TCP (+TLS) connect and CORE_HANDSHAKE. Requires login() first."""
        if not self.is_logged_in:
            raise DeviceClientError("Login required before connecting.")
        self.close_socket()
        raw = socket.create_connection((self.host, self.port), timeout=self.timeout)
        raw.settimeout(self.timeout)
        sock: socket.socket = raw
        if self.use_tls:
            ctx = self._build_tls_context()
            try:
                sock = ctx.wrap_socket(raw, server_hostname=self.host)
            except Exception:
                raw.close()
                raise
            try:
                version = sock.version()
            except Exception:
                version = ""
            if version in ("TLSv1", "TLSv1.1"):
                sock.close()
                raise DeviceClientError(f"Server negotiated weak {version}; need TLS 1.2+.")
        self._sock = sock
        hello = _new_message(
            source=self.identity_id,
            destination="core",
            message_type=HANDSHAKE_TYPE,
            payload={
                "identity_id": self.identity_id,
                "credential": self._token,
                "protocol_version": self.protocol_version,
                "join_name": self.join_name,
            },
            identity_id=self.identity_id,
        )
        _send_frame(sock, hello)
        try:
            resp = _recv_frame(sock)
        except Exception:
            self.close_socket()
            raise
        if resp.get("message_type") != HANDSHAKE_RESPONSE_TYPE or not (
            isinstance(resp.get("payload"), dict)
            and resp["payload"].get("authenticated") is True
        ):
            self.close_socket()
            raise DeviceClientError("Authentication rejected by C.O.R.E. host.")
        payload = resp["payload"]
        self._connection_id = payload.get("connection_id")
        self._session_token = None
        self._authenticated = True
        self._registered = False
        self._connected_at = None
        self._lease_expires_at = None
        self._lease_duration_seconds = None
        self._track_session(payload)
        if not self._session_token:
            self.close_socket()
            self._authenticated = False
            raise DeviceClientError("Host did not issue a session token.")
        self._track_lease(payload)
        return resp

    def _authed_payload(self, extra: dict | None = None) -> dict:
        """Payload carrier for the temporary session token (never secrets)."""
        payload = dict(extra or {})
        if self._session_token:
            payload["_session_token"] = self._session_token
        return payload

    def register(self) -> dict:
        if self._sock is None or not self._authenticated:
            raise DeviceClientError("Connect + handshake before DEVICE_REGISTER.")
        if not self._session_token:
            raise DeviceClientError("Active session token required.")
        msg = _new_message(
            source=self.identity_id,
            destination="core",
            message_type=REGISTER_TYPE,
            payload=self._authed_payload(
                {
                    "device_id": self.device_id,
                    "join_name": self.join_name,
                    "device_name": self.device_name,
                    "device_type": self.device_type,
                    "platform": self.platform,
                    "capabilities": list(self.capabilities),
                    "protocol_version": self.protocol_version,
                }
            ),
            identity_id=self.identity_id,
        )
        try:
            _send_frame(self._sock, msg)
            resp = _recv_frame(self._sock)
        except Exception as exc:
            if self._is_closure_error(exc):
                raise self._connection_lost(exc) from exc
            raise
        if resp.get("message_type") == ERROR_TYPE:
            payload = resp.get("payload", {}) if isinstance(resp.get("payload"), dict) else {}
            raise DeviceClientError(
                f"Registration rejected: {payload.get('error', 'DEVICE_ERROR')}: "
                f"{payload.get('message', '')}"
            )
        if resp.get("message_type") != REGISTER_RESPONSE_TYPE:
            raise DeviceClientError("Unexpected response to DEVICE_REGISTER.")
        self._registered = True
        if isinstance(resp.get("payload"), dict):
            self._track_session(resp["payload"])
            self._track_lease(resp["payload"])
        return resp

    def discover(self) -> dict:
        """List devices via DEVICE_DISCOVER (convenience over request())."""
        if self._sock is None or not self._registered:
            raise DeviceClientError("Register before discovery.")
        return self.request("core", DISCOVER_TYPE)

    def device_info(self, device_id: str) -> dict:
        """Fetch one device record via DEVICE_INFO."""
        if not isinstance(device_id, str) or not device_id.strip():
            raise DeviceClientError("device_id must not be empty.")
        return self.request(
            "core", INFO_TYPE, {"device_id": device_id.strip()}
        )

    def data_request(
        self,
        request_type: str,
        payload: dict | None = None,
        destination: str = "core",
        timeout: float | None = None,
    ) -> dict:
        """Send a DATA_REQUEST (record_get/list/search, file_metadata/download)."""
        if not isinstance(request_type, str) or not request_type.strip():
            raise DeviceClientError("request_type must not be empty.")
        body = dict(payload or {})
        body["request_type"] = request_type.strip()
        return self.request(destination, DATA_REQUEST_TYPE, body, timeout=timeout)

    def service_request(
        self,
        service_id: str,
        operation: str,
        params: dict | None = None,
        timeout: float | None = None,
    ) -> dict:
        """Invoke a host service via destination service:<id>.

        Payload carries ``operation`` plus params; the host strips the
        session token before dispatch and replies SERVICE_RESPONSE.
        """
        if not isinstance(service_id, str) or not service_id.strip():
            raise DeviceClientError("service_id must not be empty.")
        if not isinstance(operation, str) or not operation.strip():
            raise DeviceClientError("operation must not be empty.")
        if params is not None and not isinstance(params, dict):
            raise DeviceClientError("params must be a dict.")
        body = dict(params or {})
        body["operation"] = operation.strip()
        return self.request(
            f"service:{service_id.strip()}",
            SERVICE_REQUEST_TYPE,
            body,
            timeout=timeout,
        )

    def send_to_device(
        self,
        device_id: str,
        message_type: str,
        payload: dict | None = None,
        timeout: float | None = None,
    ) -> dict:
        """Send a device-to-device application message via the host router."""
        if not isinstance(device_id, str) or not device_id.strip():
            raise DeviceClientError("device_id must not be empty.")
        if not isinstance(message_type, str) or not message_type.strip():
            raise DeviceClientError("message_type must not be empty.")
        return self.request(
            device_id.strip(), message_type.strip(), payload, timeout=timeout
        )

    def request(
        self,
        destination: str,
        message_type: str,
        payload: dict | None = None,
        timeout: float | None = None,
    ) -> dict:
        """Send one authenticated application request, wait for one response.

        Reuses the existing socket/TLS/session/connection/framing. Every
        request carries a unique request_id; timeouts are bounded (default
        self.timeout). DEVICE_ERROR/DATA_ERROR payloads raise
        DeviceClientError with the host's error code preserved.
        """
        if self._sock is None or not self._registered:
            raise DeviceClientError("Register before sending application requests.")
        if not isinstance(destination, str) or not destination.strip():
            raise DeviceClientError("destination must not be empty.")
        if not isinstance(message_type, str) or not message_type.strip():
            raise DeviceClientError("message_type must not be empty.")
        if payload is not None and not isinstance(payload, dict):
            raise DeviceClientError("payload must be a dict.")
        wait = self.timeout if timeout is None else float(timeout)
        if wait <= 0:
            raise DeviceClientError("timeout must be positive.")
        msg = _new_message(
            source=self.identity_id,
            destination=destination.strip(),
            message_type=message_type.strip(),
            payload=self._authed_payload(dict(payload or {})),
            identity_id=self.identity_id,
        )
        msg["request_id"] = str(uuid.uuid4())
        sock = self._sock
        try:
            previous = sock.gettimeout()
        except Exception:
            previous = None
        try:
            try:
                sock.settimeout(wait)
            except Exception:
                pass
            _send_frame(sock, msg)
            resp = _recv_frame(sock)
        except Exception as exc:
            if self._is_closure_error(exc):
                raise self._connection_lost(exc) from exc
            if isinstance(exc, DeviceClientError):
                raise
            raise DeviceClientError(f"Application request failed: {exc}") from exc
        finally:
            try:
                sock.settimeout(previous if previous is not None else self.timeout)
            except Exception:
                pass
        if not isinstance(resp, dict):
            raise DeviceClientError("Malformed application response.")
        rtype = resp.get("message_type")
        if rtype in (ERROR_TYPE, DATA_ERROR_TYPE):
            detail = resp.get("payload", {})
            if not isinstance(detail, dict):
                detail = {}
            raise DeviceClientError(
                f"Host error: {detail.get('error', rtype)}: "
                f"{detail.get('message', '')}"
            )
        return resp

    def reconnect(self) -> dict:
        """Reconnect: same provisioning credential, brand-new session."""
        if not self.is_logged_in:
            raise DeviceClientError("Login required before reconnect.")
        old_id = self._connection_id
        old_token = self._session_token
        old_lease = self._lease_expires_at
        resp = self.connect()
        reg = self.register()
        if old_id is not None and self._connection_id == old_id:
            raise DeviceClientError("Reconnect did not yield a new connection_id.")
        if old_token is not None and self._session_token == old_token:
            raise DeviceClientError("Reconnect did not yield a new session token.")
        if (
            old_lease is not None
            and self._lease_expires_at is not None
            and self._lease_expires_at == old_lease
        ):
            raise DeviceClientError("Reconnect did not yield a new lease.")
        return reg

    def close_socket(self) -> None:
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass
                sock.close()
            except Exception:
                pass

    def shutdown(self) -> None:
        """Full application shutdown: close + destroy ephemeral session."""
        self.close_socket()
        self.logout()

    # -- context manager: session lives only inside the block --
    def __enter__(self) -> "CoreDeviceClient":
        return self

    def __exit__(self, *exc) -> None:
        self.shutdown()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="core-device",
        description="External C.O.R.E. device client (Option A: remembered device, login each launch).",
    )
    parser.add_argument("--host", default=None, help="Windows C.O.R.E. host/IP.")
    parser.add_argument("--port", type=int, default=None, help="C.O.R.E. TCP port (e.g. 5000).")
    parser.add_argument("--device-file", default=str(default_device_file()))
    parser.add_argument("--device-id", default=None)
    parser.add_argument("--device-name", default=None)
    parser.add_argument(
        "--join-name",
        default=None,
        help="Stable human-readable join name (generated once if omitted).",
    )
    parser.add_argument("--device-type", default="generic")
    parser.add_argument("--platform", default="mac")
    parser.add_argument("--capabilities", default="", help="Comma-separated list.")
    parser.add_argument("--token", default=None, help="Login token (else prompted).")
    parser.add_argument("--no-tls", action="store_true", help="Use plaintext (localhost tests only; never for LAN).")
    parser.add_argument("--ca-file", default=None, help="Custom CA/server cert for TLS verify.")
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Skip TLS cert verification (self-signed LAN certs; explicit only).",
    )
    parser.add_argument(
        "--remember",
        action="store_true",
        help="Save remembered device file (no secrets) and exit.",
    )
    return parser


def _print_session_banner(client: "CoreDeviceClient") -> None:
    """Display the active in-memory session (DISPLAYED, never persisted)."""
    summary = client.session_summary()
    bar = "╔" + "═" * 46 + "╗"
    mid = "╠" + "═" * 46 + "╣"
    end = "╚" + "═" * 46 + "╝"

    def row(label: str, value: object) -> str:
        text = f"║ {label:<14} {value}"
        return text[:47] + "║"

    print(bar)
    print("║             R.I.S.A.R.M.S. CLIENT            ║")
    print(mid)
    print(row("Device:", summary["device"]))
    print(row("Device ID:", summary["device_id"]))
    print(row("Join Name:", summary["join_name"]))
    print(row("Status:", summary["status"]))
    print("║                                              ║")
    print(row("Session Token:", summary["session_token"] or "<none>"))
    print("║                                              ║")
    print(row("Connection ID:", summary["connection_id"] or "<none>"))
    duration = summary["lease_duration_seconds"]
    hours = (duration // 3600) if isinstance(duration, int) else 24
    print(row("Lease:", f"{hours:02d}:00:00"))
    print(row("Expires:", summary["lease_expires_at"] or "<unknown>"))
    print(end)


def _print_session_line(client: "CoreDeviceClient") -> None:
    summary = client.session_summary()
    print(f"Status: {summary['status']}")
    print(f"Connection ID: {summary['connection_id']}")
    print(f"Session Token: {summary['session_token']}")
    print(f"Lease: {summary['lease_remaining'] or '?'} remaining")
    print(f"Expires: {summary['lease_expires_at']}")


def main(argv: list | None = None) -> int:
    args = build_parser().parse_args(argv)
    device_file = Path(args.device_file)
    caps = [c.strip() for c in (args.capabilities or "").split(",") if c.strip()]

    if args.remember:
        if not args.device_id:
            print("ERROR: --device-id is required with --remember.")
            return 2
        client = CoreDeviceClient(
            host=args.host or "127.0.0.1",
            port=args.port or 5000,
            device_id=args.device_id,
            device_name=args.device_name or args.device_id,
            join_name=args.join_name,
            device_type=args.device_type,
            platform=args.platform,
            capabilities=caps,
            device_file=device_file,
        )
        path = client.save_remembered()
        print(f"Remembered device saved to {path} (no secrets stored).")
        print(f"Join name: {client.join_name} (stable across reconnects).")
        print("Login is still required on every launch (Option A).")
        return 0

    # Normal launch: load remembered device, then REQUIRE login.
    try:
        client = CoreDeviceClient.load_remembered(
            device_file,
            host=args.host,
            port=args.port,
            device_id=args.device_id,
            device_name=args.device_name,
            join_name=args.join_name,
            capabilities=caps or None,
        )
    except DeviceClientError as exc:
        print(f"ERROR: {exc}")
        print("First run: save the device with --remember, then launch again to log in.")
        return 2

    token = args.token
    if not token:
        try:
            token = getpass.getpass(f"Token for {client.identity_id}: ")
        except Exception:
            token = None
    if not token:
        print("ERROR: login required on every launch (Option A); no token given.")
        return 2
    client.login(token)
    # Wipe the local reference; the live session owns the in-memory copy only.
    token = None

    if client.use_tls and args.no_tls:
        client.use_tls = False
        print("WARNING: plaintext mode (--no-tls); localhost tests only, never LAN.")
    if args.ca_file:
        client.ca_file = Path(args.ca_file)
    if args.insecure:
        client.insecure = True
        print("WARNING: --insecure skips cert verification; use only on trusted LAN.")
        if args.ca_file:
            print("WARNING: --ca-file is ignored with --insecure.")

    try:
        hs = client.connect()
        print("Connecting to C.O.R.E...")
        print("TLS established" if client.use_tls else "PLAINTEXT (no TLS) — localhost only")
        print("Authentication successful")
        print("Session established")
        client.register()
        print("Device registered")
        _print_session_banner(client)
        print("Commands: discover | reconnect | session | quit")
        try:
            while True:
                line = input("core-device> ").strip()
                if line in ("quit", "exit"):
                    break
                if line == "discover":
                    print(client.discover()["payload"])
                elif line == "reconnect":
                    client.reconnect()
                    print(f"Reconnected (connection_id={client.connection_id}).")
                    _print_session_banner(client)
                elif line == "session":
                    _print_session_line(client)
                elif line:
                    print("Commands: discover | reconnect | session | quit")
        except KeyboardInterrupt:
            print()
    except DeviceClientError as exc:
        print(f"ERROR: {exc}")
        return 1
    finally:
        client.shutdown()
        print("Disconnected; session token cleared (device remains remembered).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
