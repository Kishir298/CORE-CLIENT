"""Stdlib-only fake C.O.R.E. host for CORE-CLIENT tests.

Speaks the same framing (4-byte big-endian length + UTF-8 JSON) and the
same external-device messages as C.O.R.E.-HOST, without importing any
host code. Plaintext TCP only; TLS behavior is asserted at the
configuration level in test_tls.py.
"""

from __future__ import annotations

import json
import socket
import struct
import threading
import uuid
from datetime import datetime, timedelta, timezone

HEADER = 4
MAX_FRAME = 10 * 1024 * 1024
DEFAULT_LEASE_SECONDS = 24 * 60 * 60


def send_frame(sock: socket.socket, message: dict) -> None:
    data = json.dumps(message).encode("utf-8")
    sock.sendall(struct.pack("!I", len(data)) + data)


def recv_frame(sock: socket.socket) -> dict:
    header = b""
    while len(header) < HEADER:
        chunk = sock.recv(HEADER - len(header))
        if not chunk:
            raise ConnectionError("closed")
        header += chunk
    (length,) = struct.unpack("!I", header)
    if length <= 0 or length > MAX_FRAME:
        raise ConnectionError("bad frame size")
    buf = b""
    while len(buf) < length:
        chunk = sock.recv(length - len(buf))
        if not chunk:
            raise ConnectionError("closed")
        buf += chunk
    return json.loads(buf.decode("utf-8"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _lease_window(lease_seconds: int) -> tuple[str, str]:
    """Return (connected_at, lease_expires_at) ISO timestamps for a new lease."""
    start = datetime.now(timezone.utc)
    return start.isoformat(), (start + timedelta(seconds=lease_seconds)).isoformat()


def _envelope(destination: str, message_type: str, payload: dict, request_id=None) -> dict:
    return {
        "message_id": str(uuid.uuid4()),
        "source": "core",
        "destination": destination,
        "message_type": message_type,
        "timestamp": _now(),
        "request_id": request_id,
        "payload": payload,
        "identity_id": "core",
    }


class FakeCoreHost:
    """Minimal in-memory host: handshake auth, register, discover, presence."""

    def __init__(
        self,
        token: str = "secret-mac-01",
        device_id: str = "mac-01",
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        peers: dict | None = None,
    ) -> None:
        self.token = token
        self.device_id = device_id
        self.lease_seconds = lease_seconds
        # Extra provisioned devices {device_id: token} for relay tests.
        self.peers: dict = dict(peers or {})
        self.devices: dict = {}
        self._lock = threading.Lock()
        self._conns: set = set()
        self._device_conns: dict = {}
        self._stop = threading.Event()
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(64)
        listener.settimeout(0.2)
        self.port = listener.getsockname()[1]
        self._listener = listener
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    # -- introspection (mirrors host registry reads) --
    def get(self, device_id: str):
        with self._lock:
            record = self.devices.get(device_id)
            return dict(record) if record else None

    def has(self, device_id: str) -> bool:
        with self._lock:
            return device_id in self.devices

    # -- server --
    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            conn.settimeout(10.0)
            with self._lock:
                self._conns.add(conn)
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _error(self, conn, code: str, message: str, req) -> None:
        try:
            send_frame(
                conn,
                _envelope(
                    req.get("source", "unknown") if isinstance(req, dict) else "unknown",
                    "DEVICE_ERROR",
                    {"error": code, "message": message,
                     "request_id": req.get("request_id") if isinstance(req, dict) else None},
                    req.get("request_id") if isinstance(req, dict) else None,
                ),
            )
        except OSError:
            pass

    def _handle(self, conn: socket.socket) -> None:
        import hmac
        import secrets

        identity = None
        connection_id = None
        session_token = None
        device = None
        # Short read timeout so a server-side forced close (lease expiry)
        # is noticed promptly even when the peer is idle: BSD/macOS does
        # not reliably wake a thread blocked in recv() on close() alone.
        # stop()/force_close() may close the fd first — exit quietly then.
        try:
            conn.settimeout(0.5)
        except OSError:
            return
        try:
            while not self._stop.is_set():
                try:
                    msg = recv_frame(conn)
                except socket.timeout:
                    continue
                except (ConnectionError, OSError, ValueError):
                    break
                if not isinstance(msg, dict) or not msg.get("message_type"):
                    self._error(conn, "COMMUNICATION_ERROR", "Malformed message.", msg)
                    continue
                mtype = msg["message_type"]
                payload = msg.get("payload") if isinstance(msg.get("payload"), dict) else {}

                if mtype == "CORE_HANDSHAKE":
                    ident = payload.get("identity_id")
                    cred = payload.get("credential")
                    expected = self.token if ident == self.device_id else self.peers.get(ident)
                    if (
                        isinstance(ident, str)
                        and expected is not None
                        and cred == expected
                    ):
                        identity = ident
                        connection_id = str(uuid.uuid4())
                        session_token = secrets.token_urlsafe(32)
                        connected_at, lease_expires_at = _lease_window(self.lease_seconds)
                        try:
                            send_frame(
                                conn,
                                _envelope(
                                    identity,
                                    "CORE_HANDSHAKE_RESPONSE",
                                    {"authenticated": True, "identity_id": identity,
                                     "connection_id": connection_id,
                                     "session_token": session_token,
                                     "protocol_version": "0.3.0",
                                     "connected_at": connected_at,
                                     "lease_expires_at": lease_expires_at,
                                     "lease_duration_seconds": self.lease_seconds},
                                    msg.get("request_id"),
                                ),
                            )
                        except OSError:
                            break
                    else:
                        try:
                            send_frame(
                                conn,
                                _envelope(
                                    str(payload.get("identity_id") or "unknown"),
                                    "CORE_HANDSHAKE_RESPONSE",
                                    {"authenticated": False},
                                    msg.get("request_id"),
                                ),
                            )
                        except OSError:
                            break

                elif mtype == "DEVICE_REGISTER":
                    if identity is None:
                        self._error(conn, "DEVICE_NOT_REGISTERED", "Authenticate first.", msg)
                        continue
                    presented = payload.get("_session_token")
                    if (
                        not isinstance(presented, str)
                        or not presented
                        or not hmac.compare_digest(presented, session_token or "")
                    ):
                        self._error(conn, "DEVICE_REGISTRATION_FAILED",
                                    "Invalid session token.", msg)
                        continue
                    did = payload.get("device_id")
                    if did != identity:
                        self._error(conn, "DEVICE_REGISTRATION_FAILED",
                                    "identity/device mismatch.", msg)
                        continue
                    with self._lock:
                        rec = self.devices.get(did)
                        dup = (
                            rec is not None
                            and rec["status"] == "online"
                            and rec["connection_id"] != connection_id
                        )
                        if not dup:
                            device_name = payload.get("device_name", did)
                            join_name = payload.get("join_name") or f"{device_name}-{did}"
                            connected_at, lease_expires_at = _lease_window(self.lease_seconds)
                            self.devices[did] = {
                                "device_id": did,
                                "identity_id": identity,
                                "join_name": join_name,
                                "device_name": device_name,
                                "device_type": payload.get("device_type", "generic"),
                                "platform": payload.get("platform", "mac"),
                                "capabilities": list(payload.get("capabilities", [])),
                                "protocol_version": payload.get("protocol_version", "0.3.0"),
                                "status": "online",
                                "connection_id": connection_id,
                                "connected_at": connected_at,
                                "lease_expires_at": lease_expires_at,
                                "lease_duration_seconds": self.lease_seconds,
                                "last_seen": _now(),
                            }
                            self._device_conns[did] = conn
                            device = did
                    if dup:
                        self._error(conn, "DEVICE_ALREADY_REGISTERED",
                                    f"{did} already online.", msg)
                    else:
                        try:
                            send_frame(
                                conn,
                                _envelope(
                                    identity,
                                    "DEVICE_REGISTER_RESPONSE",
                                    {"registered": True, "device_id": did, "status": "online",
                                     "join_name": self.devices[did]["join_name"],
                                     "session_token": session_token,
                                     "connected_at": self.devices[did]["connected_at"],
                                     "lease_expires_at": self.devices[did]["lease_expires_at"],
                                     "lease_duration_seconds": self.lease_seconds},
                                    msg.get("request_id"),
                                ),
                            )
                        except OSError:
                            break

                elif mtype == "DEVICE_DISCOVER":
                    if identity is None or device is None:
                        self._error(conn, "DEVICE_NOT_REGISTERED",
                                    "Register before discovery.", msg)
                        continue
                    presented = payload.get("_session_token")
                    if (
                        not isinstance(presented, str)
                        or not presented
                        or not hmac.compare_digest(presented, session_token or "")
                    ):
                        self._error(conn, "DEVICE_NOT_REGISTERED",
                                    "Invalid session token.", msg)
                        continue
                    with self._lock:
                        devices = [
                            {"device_id": r["device_id"], "join_name": r.get("join_name"),
                             "device_name": r["device_name"],
                             "device_type": r["device_type"], "platform": r["platform"],
                             "capabilities": list(r["capabilities"]), "status": r["status"],
                             "last_seen": r["last_seen"]}
                            for r in self.devices.values()
                        ]
                    try:
                        send_frame(
                            conn,
                            _envelope(identity, "DEVICE_DISCOVER_RESPONSE",
                                      {"devices": devices}, msg.get("request_id")),
                        )
                    except OSError:
                        break
                elif mtype == "DEVICE_INFO":
                    if identity is None or device is None:
                        self._error(conn, "DEVICE_NOT_REGISTERED",
                                    "Register before device info.", msg)
                        continue
                    presented = payload.get("_session_token")
                    if (
                        not isinstance(presented, str)
                        or not presented
                        or not hmac.compare_digest(presented, session_token or "")
                    ):
                        self._error(conn, "DEVICE_NOT_REGISTERED",
                                    "Invalid session token.", msg)
                        continue
                    target = payload.get("device_id")
                    if not isinstance(target, str) or not target.strip():
                        self._error(conn, "INVALID_DESTINATION",
                                    "device_id must not be empty.", msg)
                        continue
                    with self._lock:
                        rec = self.devices.get(target)
                    if rec is None:
                        self._error(conn, "DEVICE_NOT_FOUND",
                                    "Destination device was not found.", msg)
                        continue
                    try:
                        send_frame(
                            conn,
                            _envelope(identity, "DEVICE_INFO_RESPONSE",
                                      {"device": dict(rec)}, msg.get("request_id")),
                        )
                    except OSError:
                        break
                elif mtype == "DATA_REQUEST":
                    if identity is None or device is None:
                        self._error(conn, "DEVICE_NOT_REGISTERED",
                                    "Register before data request.", msg)
                        continue
                    presented = payload.get("_session_token")
                    if (
                        not isinstance(presented, str)
                        or not presented
                        or not hmac.compare_digest(presented, session_token or "")
                    ):
                        self._error(conn, "DEVICE_NOT_REGISTERED",
                                    "Invalid session token.", msg)
                        continue
                    rtype = payload.get("request_type")
                    if not isinstance(rtype, str) or not rtype.strip():
                        try:
                            send_frame(
                                conn,
                                _envelope(identity, "DATA_ERROR",
                                          {"error": "INVALID_DATA_REQUEST",
                                           "message": "request_type is required.",
                                           "request_id": msg.get("request_id")},
                                          msg.get("request_id")),
                            )
                        except OSError:
                            break
                        continue
                    try:
                        send_frame(
                            conn,
                            _envelope(identity, "DATA_RESPONSE",
                                      {"request_type": rtype, "echo": {
                                          k: v for k, v in payload.items()
                                          if k not in ("_session_token", "request_type")}},
                                      msg.get("request_id")),
                        )
                    except OSError:
                        break
                elif mtype == "SERVICE_REQUEST":
                    if identity is None or device is None:
                        self._error(conn, "DEVICE_NOT_REGISTERED",
                                    "Register before service request.", msg)
                        continue
                    presented = payload.get("_session_token")
                    if (
                        not isinstance(presented, str)
                        or not presented
                        or not hmac.compare_digest(presented, session_token or "")
                    ):
                        self._error(conn, "DEVICE_NOT_REGISTERED",
                                    "Invalid session token.", msg)
                        continue
                    dest = msg.get("destination", "")
                    service_id = dest.split("service:", 1)[1] if "service:" in str(dest) else ""
                    operation = payload.get("operation")
                    if not service_id or not isinstance(operation, str) or not operation.strip():
                        self._error(conn, "COMMUNICATION_ERROR",
                                    "service_id and operation are required.", msg)
                        continue
                    try:
                        send_frame(
                            conn,
                            _envelope(identity, "SERVICE_RESPONSE",
                                      {"service_id": service_id,
                                       "operation": operation,
                                       "result": {k: v for k, v in payload.items()
                                                  if k not in ("_session_token", "operation")},
                                       "success": True, "error": None},
                                      msg.get("request_id")),
                        )
                    except OSError:
                        break
                elif isinstance(msg.get("destination"), str) and msg["destination"] not in ("", "core") and not msg["destination"].startswith("service:"):
                    # Device-to-device relay (mirrors host _handle_device_routed):
                    # registered sender + valid session only; the session
                    # token is stripped before forwarding; the sender gets
                    # NO reply (one-way routing).
                    target = msg["destination"]
                    if identity is None or device is None:
                        self._error(conn, "DEVICE_NOT_REGISTERED",
                                    "Register before sending.", msg)
                        continue
                    presented = payload.get("_session_token")
                    if (
                        not isinstance(presented, str)
                        or not presented
                        or not hmac.compare_digest(presented, session_token or "")
                    ):
                        self._error(conn, "DEVICE_NOT_REGISTERED",
                                    "Invalid session token.", msg)
                        continue
                    with self._lock:
                        rec = self.devices.get(target)
                        peer = self._device_conns.get(target)
                        live = (
                            peer is not None
                            and rec is not None
                            and rec.get("status") == "online"
                        )
                    if rec is None:
                        self._error(conn, "DEVICE_NOT_FOUND",
                                    "Destination device was not found.", msg)
                        continue
                    if not live:
                        self._error(conn, "DEVICE_UNAVAILABLE",
                                    "Destination device is offline.", msg)
                        continue
                    forwarded = dict(msg)
                    fwd_payload = dict(payload)
                    fwd_payload.pop("_session_token", None)
                    forwarded["payload"] = fwd_payload
                    try:
                        send_frame(peer, forwarded)
                    except OSError:
                        self._error(conn, "DEVICE_UNAVAILABLE",
                                    "Destination device is offline.", msg)
                    continue
                else:
                    self._error(conn, "COMMUNICATION_ERROR",
                                f"Unknown message type: {mtype}.", msg)
        finally:
            with self._lock:
                if (
                    device
                    and self.devices.get(device, {}).get("connection_id") == connection_id
                ):
                    self.devices[device]["status"] = "offline"
                    self.devices[device]["connection_id"] = None
                    self.devices[device]["last_seen"] = _now()
                if device and self._device_conns.get(device) is conn:
                    del self._device_conns[device]
                self._conns.discard(conn)
            try:
                conn.close()
            except OSError:
                pass

    def force_close(self, device_id: str) -> bool:
        """Simulate a host-forced disconnect (e.g. lease expiry).

        Closes the server side of the device's active connection; the
        handler's cleanup marks the device offline, mirroring host
        lease-expiry semantics. Returns True if a live connection existed.
        """
        with self._lock:
            conn = self._device_conns.get(device_id)
        if conn is None:
            return False
        try:
            conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            conn.close()
        except OSError:
            pass
        return True

    def stop(self) -> None:
        self._stop.set()
        try:
            self._listener.close()
        except OSError:
            pass
        with self._lock:
            conns = list(self._conns)
        for conn in conns:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                conn.close()
            except OSError:
                pass
        self._thread.join(timeout=5)


def wait_for_status(host: FakeCoreHost, device_id: str, status: str, timeout: float = 5.0) -> dict:
    """Poll the fake registry until a device reaches a status (or assert)."""
    import time

    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = host.get(device_id)
        if last is not None and last["status"] == status:
            return last
        time.sleep(0.05)
    raise AssertionError(f"device {device_id} never became {status!r}; last={last!r}")
