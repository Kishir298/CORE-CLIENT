# C.O.R.E.-CLIENT Architecture

## Position in R.I.S.A.R.M.S.

```text
Windows 11 host                    LAN / Wi-Fi                  External device
┌──────────────────┐                                          ┌──────────────────┐
│   C.O.R.E.-HOST  │ ─────── TCP + TLS (port 5000) ──────────▶ │  C.O.R.E.-CLIENT  │
│  (server/runtime │                                           │  (this project)  │
│   device registry│                                           │  stdlib only     │
│   R.E.S.C.S. I/O │                                           │  Option A login  │
└──────────────────┘                                          └──────────────────┘
```

The host is authoritative for device registration, connection state,
persistence (via R.E.S.C.S.), routing, and services. The client holds no
server state and invents none.

## Client components (`client/core_device_client.py`)

| Piece | Role |
| --- | --- |
| `CoreDeviceClient` | Device identity + remembered state + ephemeral login session + wire |
| `remembered_state / save_remembered / load_remembered` | Persistent device file, secrets stripped |
| `login / logout / is_logged_in` | Memory-only token session |
| `_build_tls_context` | TLS 1.2+, `CERT_REQUIRED` default, `CERT_NONE` only via `--insecure` |
| `connect` | TCP (+TLS) + `CORE_HANDSHAKE`, enforces login-first, rejects weak TLS |
| `register` | `DEVICE_REGISTER` after authentication |
| `discover` | `DEVICE_DISCOVER` after registration |
| `reconnect` | Same in-memory token, new `connection_id` |
| `close_socket / shutdown` | Clean disconnect; `shutdown` also destroys the login session |
| `build_parser / main` | CLI: `--remember`, login, connect loop (`discover/reconnect/quit`) |

## Wire protocol

Framing matches the host `TcpTransport`: 4-byte big-endian length prefix +
UTF-8 JSON envelope:

```json
{"message_id": "…", "source": "mac-01", "destination": "core",
 "message_type": "CORE_HANDSHAKE", "timestamp": "…", "request_id": null,
 "payload": {"identity_id": "mac-01", "credential": "<memory-only token>",
             "protocol_version": "0.3.0"},
 "identity_id": "mac-01"}
```

Sequence per connection:

```text
TCP connect → TLS handshake → CORE_HANDSHAKE → CORE_HANDSHAKE_RESPONSE
{authenticated: true, connection_id} → DEVICE_REGISTER →
DEVICE_REGISTER_RESPONSE {registered: true, status: "online"} → online
```

Errors arrive as `DEVICE_ERROR` envelopes
(`DEVICE_ALREADY_REGISTERED`, `DEVICE_REGISTRATION_FAILED`,
`DEVICE_NOT_REGISTERED`, `COMMUNICATION_ERROR`, …) and raise
`DeviceClientError` with the server code preserved in the message.

## Identity model

```text
device_id      stable device identity (remembered, e.g. "mac-01")
identity_id    security identity, always bound to device_id
connection_id  one live socket session, host-issued, never persisted
```

Reconnect keeps `device_id`/`identity_id`, yields a new `connection_id`.
Only one active connection per device; a duplicate active `DEVICE_REGISTER`
is rejected so a stale session can never hijack a live one.

## Shared-contract policy

Protocol constants (message names, framing limits, version `0.3.0`) are
intentionally duplicated in miniature inside the client rather than shared
via a third package: the host (`core/communication/protocol.py`) is the
contract authority; the client mirrors only the external-device subset it
needs. No third repository.
