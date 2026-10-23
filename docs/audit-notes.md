# Audit operating notes — 20 October 2026

This snapshot is based on commit c81341e83d12e8ec09e636600df2e1550f817e91.

- The portal remains loopback-only. Mutation clients must send Content-Type: application/json and X-Core-Request; browser origins must match the portal authority exactly.
- Host record_get calls require namespace and key. Host inference requests include this device ID, and a host service failure is surfaced as an error.
- Host offload requires the device execute permission. The host infer endpoint supplies placement metadata; it does not itself generate model output.
- TLS hostname verification remains disabled under the repository's existing documented LAN trust model; certificate chain verification is retained. This is a remaining trust limitation, especially if using a broadly trusted CA. --insecure explicitly disables certificate verification and should not be used for normal deployment.
- Monitoring dependencies are optional: install the monitoring extra if importing client.monitoring. Exporter declarations alone do not imply end-to-end instrumentation.

Run python -m pytest -q and python -m ruff check --select F client tests on the intended client. Live TLS, reconnect under real network loss, and portal browser interactions still need a permitted network environment. Preserve your existing remembered-device file and certificate/configuration paths when applying these source files.
