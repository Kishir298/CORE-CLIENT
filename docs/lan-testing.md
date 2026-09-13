# Physical LAN Testing — C.O.R.E.-HOST ↔ C.O.R.E.-CLIENT

## Status

```text
AUTOMATED TESTING:       IMPLEMENTED (client suite 39 passed; host suite 661 passed, 3 skipped)
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
   NEW `connection_id`.
9. Wrong token → rejected, stays `offline`.
10. Client restart → login required again; remembered device survives.

## Checklist (fill in during the physical test)

- [ ] TLS handshake succeeds Mac → Windows
- [ ] `CORE_HANDSHAKE_RESPONSE.authenticated == true`
- [ ] First `DEVICE_REGISTER` returns `status: online`
- [ ] Host registry shows device `online`
- [ ] Device record persists across host restart (restored `offline`)
- [ ] Disconnect marks `offline`, record retained
- [ ] Reconnect → `online`, same IDs, new `connection_id`
- [ ] Wrong credential rejected, stays `offline`
- [ ] Claiming another `device_id` rejected
- [ ] Client restart requires login; device still remembered
- [ ] No private key material ever copied to the Mac
