# C.O.R.E.-CLIENT Setup

## 1. Requirements

* Python `>=3.10`, no third-party packages.
* Network reachability to the Windows C.O.R.E.-HOST (`ping` + TCP port).
* A provisioned device identity (see §2) and the host's **public**
  certificate for TLS verification (see §3).

## 2. Host-side provisioning (Windows, one-time per device)

On C.O.R.E.-HOST with its LAN config:

```powershell
py -m core --config config\core.lan.yaml provision-device `
  --device-id mac-01 --device-name "MacBook" --platform mac `
  --device-type phone --capabilities chat
```

Expected: `Provisioned device: mac-01` (token stored server-side, never
displayed). Hand the token to the device user out-of-band. Re-running
rotates the token.

Then start the host:

```powershell
py -m core --config config\core.lan.yaml start
```

## 3. TLS trust setup (device)

TLS stays enabled. For the default self-signed host certificate, copy ONLY
the public certificate to the device, e.g. `~/core-client-cert.pem`, and
pass it at runtime:

```bash
python3 -m client --ca-file ~/core-client-cert.pem ...
```

Alternatively place it at `runtime/certificates/` inside this project
(git-ignored). NEVER copy `core.key` or any `.pfx` — the private key stays
on the Windows host. `--insecure` skips verification and is only for a
trusted LAN; `--no-tls` is localhost-testing only.

## 4. Remember the device (one-time)

```bash
python3 -m client --remember \
  --device-file ~/.risarms-device.json \
  --device-id mac-01 \
  --device-name "MacBook" \
  --host 192.168.1.67 --port 5000
```

 Verifies: file contains identity/endpoint fields only — no `token`,
 `connection_id`, or session values. The client generates the stable
 `join_name` (`MacBook-mac-01`) automatically; pass `--join-name` to set
 it explicitly.

 ## 5. Log in and connect (every launch)

```bash
python3 -m client \
  --device-file ~/.risarms-device.json \
  --host 192.168.1.67 --port 5000 \
  --ca-file ~/core-client-cert.pem
```

Enter the token at the prompt (or `--token` for scripting). Expected:

```text
Authenticated (connection_id=<uuid>).
Registered: {'registered': True, 'device_id': 'mac-01', 'status': 'online'}. Status: online.
```

 Commands at `core-device>`: `discover`, `reconnect`, `quit`. Quitting (or
 `Ctrl+C`) prints `Disconnected; login session cleared (device remains
 remembered).` — the next launch requires login again. Each connection
 carries a host-authoritative 24-hour lease; if the host closes an expired
 connection, the client marks itself disconnected (identity and remembered
 `join_name` preserved) and must log in and reconnect for a fresh lease.

 ## 6. CLI reference

 ```text
 --host --port --device-file --device-id --device-name --join-name
 --device-type --platform --capabilities --token --remember
 --ca-file --insecure --no-tls
 ```

## 7. Running tests

```bash
python3 -m pytest -q
```

The suite is stdlib-only and uses an in-process fake host
(`tests/fake_host.py`); no C.O.R.E.-HOST checkout required.
