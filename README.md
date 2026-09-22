# C.O.R.E.-CLIENT — External-Device Client

**Version:** `0.3.0` (protocol-compatible with C.O.R.E.-HOST v0.3.0)
**Status:** **Implementation Complete · Physical LAN Validation Pending**
**Platform:** macOS / Linux / any Python `>=3.10` device
**R.I.S.A.R.M.S. subsystem:** C.O.R.E. client

C.O.R.E.-CLIENT is the **independent external-device client** for
**C.O.R.E.-HOST**, the Windows-hosted C.O.R.E. server/runtime
(`Kishir298/CORE`, to be renamed `Kishir298/CORE-HOST`).
Naming: `CORE-CLIENT` is the directory, `client` is the Python import package (`python3 -m client`), and `core-device` is the CLI program name.

Relationship:

```text
CORE-HOST (Windows, 192.168.1.67:5000)
    │ TCP + TLS
    ▼
CORE-CLIENT (Mac / external R.I.S.A.R.M.S. device)
```

It connects to C.O.R.E.-HOST over TCP+TLS, authenticates with an explicit
login token, registers as a device, and supports discovery, reconnection,
and clean shutdown. **Standard library only — no third-party packages, no
host imports.**

> Physical Windows ↔ Mac LAN validation has NOT yet been performed.
> See `docs/lan-testing.md`. Do not claim otherwise.

---

## Installation

Runtime stdlib-only (Python `>=3.10`; tests require `pytest>=8.0`):

```bash
cd /path/to/CORE-CLIENT
python3 -m client --help
```

Run the automated tests:

```bash
python3 -m pytest -q
```

## Quick start

One-time: save the remembered device (stores **no secrets**):

```bash
python3 -m client --remember \
  --device-file ~/.risarms-device.json \
  --device-id mac-01 \
  --device-name "MacBook" \
  --host 192.168.1.67 --port 5000
```

Every launch: log in (Option A — token required each time) and connect:

```bash
python3 -m client \
  --device-file ~/.risarms-device.json \
  --host 192.168.1.67 --port 5000 \
  --ca-file ~/core-client-cert.pem
```

The client prompts `Token for mac-01:` (or pass `--token` for scripting),
then performs `CORE_HANDSHAKE` → authentication → `DEVICE_REGISTER` and
reports `online`. Interactive commands: `discover | reconnect | session | quit` (`exit` aliases `quit`).
`Ctrl+C` disconnects cleanly.

On every launch the client also starts its localhost portal and prints:

```text
Portal:
http://127.0.0.1:8766
```

See `docs/client-portal.md` (dashboard, capabilities, AI local/offload,
R.E.S.C.S., network, session). Flags: `--no-portal`, `--portal-port`,
`--location-precision exact|approximate|city|hidden`.

## Authentication (Option A)

 Persistent remembered state (`~/.risarms-device.json`) holds only:

 ```text
 device_id, identity_id, join_name, device_name, device_type, platform,
 capabilities, protocol_version, host, port
 ```

 The `join_name` (e.g. `MacBook-mac-01`) is generated once at first
 configuration, stays stable across reconnects and restarts, and contains
 no secrets.

It MUST NOT and does NOT contain: `token, credential, password,
api_token, session, session_token, connection_id, authenticated`.

Lifecycle:

```text
start → load remembered device → REQUIRE login → token in memory only
→ TLS → CORE_HANDSHAKE → auth → DEVICE_REGISTER → online
→ shutdown: socket closed, token/session/connection cleared
→ device stays remembered → next launch requires login again
```

The host must provision the device first (`provision-device` on
C.O.R.E.-HOST); the token is handed to the device user out-of-band.

## TLS

* Enabled **by default**. No silent plaintext downgrade.
* TLS 1.2 minimum; certificates validated by default (`CERT_REQUIRED`).
* `--ca-file <path>` supplies the host's public certificate for
  self-signed/private-CA setups. Copy ONLY the public `core.crt`/`core.pem`
  to the device — the server private key (`core.key`, `.pfx`) NEVER belongs
  on the client and is never needed.
* `--insecure` skips verification (trusted LAN only, explicit opt-in).
* `--no-tls` plaintext exists for localhost testing only — never for LAN.
* Note: hostname check is disabled for `CN=localhost/SAN 192.168.1.67` self-signed setups; certificate chain is still required (see `docs/lan-testing.md`).

## Protocol

Framing: 4-byte big-endian length prefix + UTF-8 JSON, 10 MiB max frame.

```text
CORE_HANDSHAKE / CORE_HANDSHAKE_RESPONSE
DEVICE_REGISTER / DEVICE_REGISTER_RESPONSE
DEVICE_DISCOVER / DEVICE_DISCOVER_RESPONSE
DEVICE_INFO / DEVICE_INFO_RESPONSE
DATA_REQUEST / DATA_RESPONSE / DATA_ERROR
SERVICE_REQUEST / SERVICE_RESPONSE (destination service:<id>)
DEVICE_ERROR
```

 Identity model: `device_id` (stable) + `identity_id` (security, bound to
 `device_id`) + `join_name` (stable human-readable label) stay constant;
 the host issues a fresh `connection_id` per connection. Duplicate active
 registration is rejected with `DEVICE_ALREADY_REGISTERED`.
 See `docs/architecture.md`.

 ## Connection lease

 Every connection carries a host-authoritative 24-hour lease
 (`connected_at`, `lease_expires_at`, `lease_duration_seconds`). The
  client tracks it locally; the host forcibly closes expired connections.
  After a forced close the client marks itself disconnected (identity and
  `join_name` preserved) and must log in + reconnect for a fresh lease.

## Application messaging

After `DEVICE_REGISTER`, one authenticated connection carries all
application traffic (no second socket per request). Every call attaches
the ephemeral session token automatically and waits for one bounded
response (`timeout`, default 10 s):

```python
from client.core_device_client import CoreDeviceClient

client = CoreDeviceClient(host="192.168.1.67", port=5000,
                          device_id="mac-01", device_name="MacBook")
client.login(token)          # RAM only, never persisted
client.connect()
client.register()

client.discover()                                   # DEVICE_DISCOVER
client.device_info("mac-01")                        # DEVICE_INFO
client.data_request("record_list", {"namespace": "notes", "limit": 5})
client.service_request("health", "status")          # -> service:health
client.service_request("agent", "status", {"device_id": "mac-01"})
client.send_to_device("other-01", "APP_MESSAGE", {"text": "hi"})
client.request("core", "DEVICE_DISCOVER", {})       # generic escape hatch
```

Rules: register before any application call; each request carries a
unique `request_id`; `DEVICE_ERROR`/`DATA_ERROR` responses raise
`DeviceClientError` with the host code preserved (`DEVICE_NOT_FOUND`,
`INVALID_DATA_REQUEST`, ...); connection loss marks the client
`DISCONNECTED` (session destroyed, identity kept). No protocol change
was needed on the host — these reuse the existing `DEVICE_DISCOVER` /
`DEVICE_INFO` / `DATA_REQUEST` / `service:*` handlers.

## Project structure

```text
CORE-CLIENT/
├── client/                  # stdlib-only device client (Option A login)
├── tests/                   # stdlib-only suite (fake in-process host)
├── docs/
│   ├── architecture.md
│   ├── setup.md
│   └── lan-testing.md
├── runtime/certificates/    # runtime trust input (public certs only, git-ignored)
├── pyproject.toml
└── .gitignore
```

Tracked placeholder `runtime/certificates/.gitkeep` is committed; `.git/`, `.pytest_cache/` and other caches are git-ignored.

## Security rules

1. Never commit credentials, tokens, or private TLS keys.
2. Never copy `core.key` / `.pfx` to a device.
3. Never disable TLS to make LAN testing easier.
4. Never store the login token on disk.
5. Never claim physical LAN validation without performing it.

## Docs

* `docs/architecture.md` — client design, protocol, identity model
* `docs/setup.md` — installation, host provisioning, TLS setup
* `docs/lan-testing.md` — physical LAN validation procedure (NOT YET PERFORMED)
