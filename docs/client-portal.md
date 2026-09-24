# Client Portal (v0.4.0)

> Note: portal doc is at v0.4.0, ahead of package version 0.3.0 in `pyproject.toml` (package bump pending).

Localhost dashboard for every C.O.R.E.-CLIENT device. Works offline:
local sections always render; remote sections report
`C.O.R.E. HOST OFFLINE` instead of fabricating data. No internet
required — everything binds `127.0.0.1`.

## Startup

```bash
python3 -m client --device-file ~/.risarms-device.json \
  --host 192.168.1.67 --port 5000 --ca-file ~/core-client-cert.pem
```

Output includes:

```text
Portal:
http://127.0.0.1:8766
```

Flags: `--no-portal` disables it; `--portal-port` changes the port;
`--location-precision exact|approximate|city|hidden` controls shared
coordinate precision (default `approximate`).

## Sections (8)

Dashboard, Devices, R.E.S.C.S., Services, AI, Network, Session, Settings.

## Dashboard

Device name/ID/identity/join name/platform/type, CPU/RAM/storage facts,
capability class, connection status/ID, session state, lease
duration/expiry/remaining, host address + reachability, last connection.
Provisioning credentials are never displayed; TLS keys never leave the
client; the session token renders only as `ACTIVE` and is never
persisted (not in the remembered file, cookies, storage, logs, or URLs).

## Capability detection (`client/capabilities.py`, `client/models.py`)

Deterministic, dependency-free, gracefully degrading. Classes
HIGH/MEDIUM/LOW/UNKNOWN from RAM + CPU cores against configurable
thresholds. Model profiles live only in `MODEL_PROFILES`
(`qwen3-coder:30b`, `qwen2.5-coder:14b`, `qwen3:14b`): installed size,
minimum RAM, runtime. Ollama is probed at `127.0.0.1:11434` (`/api/tags`).

## AI section

- Local model AVAILABLE → `POST /api/ai/request` runs against local
  Ollama (`/api/chat`, non-streaming) and returns the text.
- Local INSUFFICIENT/UNAVAILABLE → request routes to the host `agent`
  `infer` service via the existing authenticated service path; the
  Windows host places it with `AgentScheduler` and returns placement.
- No arbitrary host commands are reachable: only the `infer` operation
  with `device_id` + `prompt`.

## R.E.S.C.S. section

Files, records, search, recent (RAM-only MRU of 20), uploads/downloads
metadata, storage counts — mapped only onto existing contract
operations (`record_list/search/get`, `file_metadata/download`) through
authenticated `DATA_REQUEST`s. No second database; no credentials in the
browser (the portal backend holds no RESCS keys — the host does).

## Network / Devices

`DEVICE_DISCOVER` results the client is authorized to see, plus host
address classification (`ipaddress`: loopback / private LAN / public —
never implying geography), TCP reachability probe, per-device location
(RAM-only reports) and Haversine distance (both sides need coordinates).

## Location model (`client/geo.py`)

Sources: `device_reported`, `manual`, `geoip`, `unknown`. Precision is
applied before sharing. Unknown stays unknown — coordinates are never
fabricated, never inferred from IPs/IDs/hostnames.

## Session section

Provisioning credential: NOT DISPLAYED. Session: AUTHENTICATED flag,
connection ID, lease countdown, remembered-file key audit (keys only —
proves no secrets are stored). `Reconnect` calls the client's own
reconnect (login session must still exist).

## Offline behavior

Unplug the host: Dashboard/Device/Capabilities/AI-local/Session/Settings
keep working; Devices/Network/RESCS/AI-offload show
`C.O.R.E. HOST OFFLINE`.
