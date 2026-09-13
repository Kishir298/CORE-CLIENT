"""Minimal external-device client for C.O.R.E. v0.3.0.

Stdlib only — intentionally does NOT import internal ``core`` modules so the
Mac behaves as a true external device over real LAN TCP + TLS.

Protocol (matches TcpTransport hardened path):
    TCP connect -> TLS handshake -> CORE_HANDSHAKE -> CORE_HANDSHAKE_RESPONSE
    -> DEVICE_REGISTER -> DEVICE_REGISTER_RESPONSE -> online

OPTION A behavior:
    Persistent (remembered device file, no secrets):
        device_id, identity_id, device_name, device_type, platform,
        capabilities, protocol_version, host, port.
    Ephemeral (memory only, never written to disk):
        login token/credential, active socket, connection_id, auth state.
    - Startup loads the remembered device but ALWAYS requires login again.
    - Full shutdown destroys the session; next launch requires login.
    - Reconnect while the app is open reuses the in-memory token and the
      remembered device identity (no re-registration from scratch).
"""

from __future__ import annotations

import argparse
import getpass
import json
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
    }
)


class DeviceClientError(Exception):
    """Raised for client-side protocol/transport failures."""


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
    ) -> None:
        if not device_id or not device_id.strip():
            raise ValueError("device_id cannot be empty.")
        self.host = host
        self.port = int(port)
        self.device_id = device_id.strip()
        # identity_id is bound to device_id per server contract.
        self.identity_id = self.device_id
        self.device_name = device_name or self.device_id
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
        self._token: str | None = None
        self._sock: socket.socket | None = None
        self._connection_id: str | None = None
        self._authenticated = False
        self._registered = False

    # -- remembered device (persistent, no secrets) --
    def remembered_state(self) -> dict:
        return {
            "device_id": self.device_id,
            "identity_id": self.identity_id,
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
        """Begin a login session. Token is kept in memory only."""
        if not token or not str(token).strip():
            raise DeviceClientError("Login requires a non-empty token.")
        self._token = str(token)

    def logout(self) -> None:
        """Destroy the login session without touching the remembered device."""
        self._token = None
        self._authenticated = False
        self._registered = False
        self._connection_id = None

    @property
    def is_logged_in(self) -> bool:
        return self._token is not None

    @property
    def connection_id(self) -> str | None:
        return self._connection_id

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
        self._authenticated = True
        self._registered = False
        return resp

    def register(self) -> dict:
        if self._sock is None or not self._authenticated:
            raise DeviceClientError("Connect + handshake before DEVICE_REGISTER.")
        msg = _new_message(
            source=self.identity_id,
            destination="core",
            message_type=REGISTER_TYPE,
            payload={
                "device_id": self.device_id,
                "device_name": self.device_name,
                "device_type": self.device_type,
                "platform": self.platform,
                "capabilities": list(self.capabilities),
                "protocol_version": self.protocol_version,
            },
            identity_id=self.identity_id,
        )
        _send_frame(self._sock, msg)
        resp = _recv_frame(self._sock)
        if resp.get("message_type") == ERROR_TYPE:
            payload = resp.get("payload", {}) if isinstance(resp.get("payload"), dict) else {}
            raise DeviceClientError(
                f"Registration rejected: {payload.get('error', 'DEVICE_ERROR')}: "
                f"{payload.get('message', '')}"
            )
        if resp.get("message_type") != REGISTER_RESPONSE_TYPE:
            raise DeviceClientError("Unexpected response to DEVICE_REGISTER.")
        self._registered = True
        return resp

    def discover(self) -> dict:
        if self._sock is None or not self._registered:
            raise DeviceClientError("Register before discovery.")
        _send_frame(
            self._sock,
            _new_message(
                source=self.identity_id,
                destination="core",
                message_type=DISCOVER_TYPE,
                payload={},
                identity_id=self.identity_id,
            ),
        )
        return _recv_frame(self._sock)

    def reconnect(self) -> dict:
        """Reconnect while the app stays open: same token, new connection_id."""
        if not self.is_logged_in:
            raise DeviceClientError("Login required before reconnect.")
        old_id = self._connection_id
        resp = self.connect()
        reg = self.register()
        if old_id is not None and self._connection_id == old_id:
            raise DeviceClientError("Reconnect did not yield a new connection_id.")
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
            device_type=args.device_type,
            platform=args.platform,
            capabilities=caps,
            device_file=device_file,
        )
        path = client.save_remembered()
        print(f"Remembered device saved to {path} (no secrets stored).")
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

    try:
        hs = client.connect()
        print(f"Authenticated (connection_id={hs['payload'].get('connection_id')}).")
        reg = client.register()
        print(f"Registered: {reg['payload']}. Status: online.")
        print("Press Ctrl+C to disconnect (session ends; device stays remembered).")
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
                elif line:
                    print("Commands: discover | reconnect | quit")
        except KeyboardInterrupt:
            print()
    except DeviceClientError as exc:
        print(f"ERROR: {exc}")
        return 1
    finally:
        client.shutdown()
        print("Disconnected; login session cleared (device remains remembered).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
