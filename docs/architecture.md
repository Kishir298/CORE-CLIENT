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
| `generate_join_name` | Stable `<device-name>-<short-device-id>` join name, secrets-free |
| `remembered_state / save_remembered / load_remembered` | Persistent device file, secrets stripped |
| `login / logout / is_logged_in` | Memory-only provisioning credential |
| `_build_tls_context` | TLS 1.2+, `CERT_REQUIRED` default, `CERT_NONE` only via `--insecure` |
| `connect` | TCP (+TLS) + `CORE_HANDSHAKE`, enforces login-first, rejects weak TLS |
| `register` | `DEVICE_REGISTER` after authentication |
| `discover` | `DEVICE_DISCOVER` after registration |
| `reconnect` | Same provisioning credential, new `connection_id`, new session token, new lease |
| `mark_disconnected` | Forced-close handling: clears session token + connection state, keeps identity + provisioning credential |
| `close_socket / shutdown` | Clean disconnect; `shutdown` also destroys the login session |
| `build_parser / main` | CLI: `--remember`, login, connect loop (`discover/reconnect/quit`) |

## Wire protocol

Framing matches the host `TcpTransport`: 4-byte big-endian length prefix +
UTF-8 JSON envelope:

 ```json
 {"message_id": "…", "source": "mac-01", "destination": "core",
  "message_type": "CORE_HANDSHAKE", "timestamp": "…", "request_id": null,
  "payload": {"identity_id": "mac-01", "credential": "<memory-only token>",
              "protocol_version": "0.3.0", "join_name": "MacBook-mac-01"},
  "identity_id": "mac-01"}
 ```

  Sequence per connection:

  ```text
  TCP connect → TLS handshake → CORE_HANDSHAKE {credential: provisioning} →
  CORE_HANDSHAKE_RESPONSE {authenticated: true, connection_id, session_token,
   connected_at, lease_expires_at, lease_duration_seconds} →
  DEVICE_REGISTER {…, join_name, _session_token} →
  DEVICE_REGISTER_RESPONSE {registered: true, status: "online", session_token} →
  online (application messages carry _session_token)
  ```

  `session_token` is a temporary host-issued credential
  (`secrets.token_urlsafe(32)`), distinct from the provisioning credential
  (used ONLY at handshake, never returned). It lives only in RAM on both
  sides, rotates every connection, and is destroyed on disconnect/expiry.

Errors arrive as `DEVICE_ERROR` envelopes
(`DEVICE_ALREADY_REGISTERED`, `DEVICE_REGISTRATION_FAILED`,
`DEVICE_NOT_REGISTERED`, `COMMUNICATION_ERROR`, …) and raise
`DeviceClientError` with the server code preserved in the message.

 ## Identity model

  ```text
  device_id      stable device identity (remembered, e.g. "mac-01")
  identity_id    security identity, always bound to device_id
  join_name      stable human-readable label, e.g. "MacBook-mac-01"
                 (remembered; generated once as <device-name>-<short-device-id>)
  credential     long-term provisioning secret (RAM only, handshake only)
  session_token  temporary host-issued secret (RAM only, per connection)
  connection_id  one live socket session, host-issued, never persisted
  ```

  Persistent device identity (`device_id`, `identity_id`, `join_name`,
  endpoint, non-secret metadata) lives in the remembered device file.
  Ephemeral session state (provisioning credential, session token, socket,
  `connection_id`, auth flags, lease tracking) lives in memory only and is
  destroyed on shutdown. The session token is displayed while connected
  but never persisted: DISPLAYED ≠ PERSISTED.

  Reconnect keeps `device_id`/`identity_id`/`join_name`, yields a new
  `connection_id`, a new session token, and a new 24-hour lease. Only one active connection per
 device; a duplicate active `DEVICE_REGISTER` is rejected so a stale
 session can never hijack a live one.

 ## Connection lease (host-authoritative)

 Every successful connection carries a 24-hour lease
 (`connected_at`, `lease_expires_at`, `lease_duration_seconds = 86400`).
 The client tracks these values locally for state/UX only — the host
 forcibly closes expired connections. On a forced close the client marks
 itself disconnected (socket, `connection_id`, auth flags, and lease
 tracking cleared; identity and in-memory token preserved) and must
 re-authenticate before sending application messages. Lease timing starts
 at successful authentication, never at provisioning or host start.

## Shared-contract policy

Protocol constants (message names, framing limits, version `0.3.0`) are
intentionally duplicated in miniature inside the client rather than shared
via a third package: the host (`core/communication/protocol.py`) is the
contract authority; the client mirrors only the external-device subset it
needs. No third repository.
