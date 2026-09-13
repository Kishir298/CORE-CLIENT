# C.O.R.E.-CLIENT — External-Device Client

**Version:** `0.3.0` (protocol-compatible with C.O.R.E.-HOST v0.3.0)
**Status:** **Implementation Complete · Physical LAN Validation Pending**
**Platform:** macOS / Linux / any Python `>=3.10` device
**R.I.S.A.R.M.S. subsystem:** C.O.R.E. client

C.O.R.E.-CLIENT is the **independent external-device client** for
**C.O.R.E.-HOST**, the Windows-hosted C.O.R.E. server/runtime
(`Kishir298/CORE`, to be renamed `Kishir298/CORE-HOST`).

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

No dependencies beyond Python `>=3.10`:

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
reports `online`. Interactive commands: `discover | reconnect | quit`.
`Ctrl+C` disconnects cleanly.

## Authentication (Option A)

Persistent remembered state (`~/.risarms-device.json`) holds only:

```text
device_id, identity_id, device_name, device_type, platform,
capabilities, protocol_version, host, port
```

It MUST NOT and does NOT contain: `token, credential, password,
api_token, session, connection_id, authenticated`.

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

## Protocol

Framing: 4-byte big-endian length prefix + UTF-8 JSON, 10 MiB max frame.

```text
CORE_HANDSHAKE / CORE_HANDSHAKE_RESPONSE
DEVICE_REGISTER / DEVICE_REGISTER_RESPONSE
DEVICE_DISCOVER / DEVICE_DISCOVER_RESPONSE
DEVICE_INFO / DEVICE_INFO_RESPONSE
DEVICE_ERROR
```

Identity model: `device_id` (stable) + `identity_id` (security, bound to
`device_id`) stay constant; the host issues a fresh `connection_id` per
connection. Duplicate active registration is rejected with
`DEVICE_ALREADY_REGISTERED`. See `docs/architecture.md`.

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
