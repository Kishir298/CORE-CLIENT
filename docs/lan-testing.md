# Physical LAN Testing — C.O.R.E.-HOST ↔ C.O.R.E.-CLIENT

## Status

```text
AUTOMATED TESTING:       IMPLEMENTED (client suite 123 passed, 13 files; host suite see CORE-HOST README)
PHYSICAL LAN VALIDATION: NOT YET PERFORMED — do not claim otherwise
```

## Topology

```text
Windows / C.O.R.E.-HOST / 192.168.1.67:5000
        │ Wi-Fi / LAN, TCP + TLS
        ▼
Mac / C.O.R.E.-CLIENT (this project)
```

The host certificate in use has `CN=localhost`, SAN `localhost, 192.168.1.67`.
The Mac receives ONLY the public certificate material (`--ca-file`); the
server private key is never copied.

## Procedure

1. Mac can ping Windows (`ping 192.168.1.67`).
2. TCP 5000 reachable (connection succeeds; failures here are firewall —
   see host `docs/windows-firewall.md`).
3. Provision the device on the host (`provision-device --device-id mac-01 …`)
   and start it (`--config config\core.lan.yaml start`).
4. On the Mac: `--remember` once, then launch with `--ca-file` and log in.
5. Expect `Authenticated (connection_id=…)` then `Registered … online`.
6. On the host: device shows `online`; record persists in `var/rescs.json`
   (identity fields + token; no `connection_id`, no live status).
7. Quit the client → host marks `offline`, record retained.
  8. Launch again (login again) → `online`, same `device_id`/`identity_id`,
     same `join_name`, NEW `connection_id`, NEW 24-hour lease.
  9. Wrong token → rejected, stays `offline`.
  10. Client restart → login required again; remembered device (including
      `join_name`) survives.
  11. Lease expiry is proven by the automated fake-clock tests, not by
      waiting 24 hours physically.
  12. App traffic while online: `discover` lists devices; `DEVICE_INFO`,
      `DATA_REQUEST`, and `service:<id>` round-trips succeed with
      correlated `request_id`s; device-to-device send is fire-and-forget
      (no reply envelope on success — use `wait_reply=True` only when the
      peer is known to reply).
  13. Wi-Fi drop mid-session → host marks `offline`, record retained;
      client `reconnect` (same open app, no re-login) restores `online`
      with a new `connection_id` + fresh lease.
  14. Host restart before client reconnect → device restored `offline`,
      then client reconnect brings it back `online` with the same IDs.

  ## Mac preflight (before the live window)

  - `ping 192.168.1.67` answers (same LAN).
  - `~/core-client-cert.pem` (public cert copied from the host) exists;
    never copy `core.key`/`.pfx` to the Mac.
  - `python3 -m client --help` renders; `--remember` file holds no secrets
    (verify: no `token`/`session` keys in `~/.risarms-device.json`).
  - 2026-09-22 preflight (Mac-only): ping 192.168.1.67 FAIL off-LAN / ~/core-client-cert.pem MISSING / python3 -m client --help OK / ~/.risarms-device.json clean no token-session (live window BLOCKED, needs same-LAN + cert + host operator)

  ## Checklist (fill in during the physical test)

 - [ ] TLS handshake succeeds Mac → Windows
 - [ ] `CORE_HANDSHAKE_RESPONSE.authenticated == true`
 - [ ] Host log shows login attempt with `device_id` + `join_name`
     (token visible only with `log_external_device_tokens: true`)
 - [ ] First `DEVICE_REGISTER` returns `status: online`
 - [ ] Host registry shows device `online` with `join_name` + lease fields
 - [ ] Device record persists across host restart (restored `offline`)
 - [ ] Disconnect marks `offline`, record retained
 - [ ] Reconnect → `online`, same IDs, same `join_name`, new `connection_id`
 - [ ] Wrong credential rejected, stays `offline`
 - [ ] Claiming another `device_id` rejected
 - [ ] Client restart requires login; device still remembered
 - [ ] No private key material ever copied to the Mac

> TLS note: client validates cert chain (CERT_REQUIRED, TLS 1.2+) but leaves hostname unchecked (check_hostname=False) to support CN=localhost / SAN 192.168.1.67 self-signed setups. Copy only the public cert; never core.key/.pfx.
