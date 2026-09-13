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
from datetime import datetime, timezone

HEADER = 4
MAX_FRAME = 10 * 1024 * 1024


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

    def __init__(self, token: str = "secret-mac-01", device_id: str = "mac-01") -> None:
        self.token = token
        self.device_id = device_id
        self.devices: dict = {}
        self._lock = threading.Lock()
        self._conns: set = set()
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
        identity = None
        connection_id = None
        device = None
        try:
            while not self._stop.is_set():
                try:
                    msg = recv_frame(conn)
                except (ConnectionError, OSError, ValueError):
                    break
                if not isinstance(msg, dict) or not msg.get("message_type"):
                    self._error(conn, "COMMUNICATION_ERROR", "Malformed message.", msg)
                    continue
                mtype = msg["message_type"]
                payload = msg.get("payload") if isinstance(msg.get("payload"), dict) else {}

                if mtype == "CORE_HANDSHAKE":
                    if (
                        payload.get("identity_id") == self.device_id
                        and payload.get("credential") == self.token
                    ):
                        identity = self.device_id
                        connection_id = str(uuid.uuid4())
                        try:
                            send_frame(
                                conn,
                                _envelope(
                                    identity,
                                    "CORE_HANDSHAKE_RESPONSE",
                                    {"authenticated": True, "identity_id": identity,
                                     "connection_id": connection_id, "protocol_version": "0.3.0"},
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
                            self.devices[did] = {
                                "device_id": did,
                                "identity_id": identity,
                                "device_name": payload.get("device_name", did),
                                "device_type": payload.get("device_type", "generic"),
                                "platform": payload.get("platform", "mac"),
                                "capabilities": list(payload.get("capabilities", [])),
                                "protocol_version": payload.get("protocol_version", "0.3.0"),
                                "status": "online",
                                "connection_id": connection_id,
                                "last_seen": _now(),
                            }
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
                                    {"registered": True, "device_id": did, "status": "online"},
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
                    with self._lock:
                        devices = [
                            {"device_id": r["device_id"], "device_name": r["device_name"],
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
                self._conns.discard(conn)
            try:
                conn.close()
            except OSError:
                pass

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
