# Use C.O.R.E. in a browser

The browser client uses the authenticated portal hosted by CORE-HOST. It does
not run RESCS, host orchestration, or server credentials on the client device.

- Windows: `open-core.cmd`, then enter the host's HTTPS URL.
- macOS: `CORE_PORTAL_URL=https://your-domain.example sh open-core.command`.
- Any browser: open the host's HTTPS URL directly; no client Python required.
- Python: `python -m client.web --url https://your-domain.example`.

Sign in with a profile created by the host owner. Workspace records are scoped
to your profile on RESCS. The owner controls which services are installed.
ASIS runs on demand; coding/host administration are owner-only. Live output
and Stop controls are in the portal. Existing TCP/TLS `python -m client`
workflows remain available separately.

Remote plain HTTP is rejected by the browser launcher. The host must first
configure its HTTPS public origin and reverse proxy. This source archive does
not itself provide a public hosting account or domain.
