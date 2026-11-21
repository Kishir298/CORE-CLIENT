# RISARMS Portal Linux Server and Desktop Guide

4 November 2026

RISARMS Portal is now a desktop workspace backed by a Linux CORE server. People can register from the app, request access and wait for owner approval. Approved public accounts receive messaging and personal cloud storage. Server administration, coding agents and other privileged services remain owner-only. Browser access is optional.

This is an installable Python application and source release, not a signed Windows EXE, macOS DMG or a completed public deployment. The initial server target is Ubuntu/Debian with systemd. The client targets Windows, macOS and Linux; Linux execution is verified. Keep the previous packages and data backups for rollback.

## What changed

- CORE-HOST starts headlessly and includes a Linux service installer, systemd unit, environment example and HTTPS proxy example. core-host remains a separate installed command.
- CORE-CLIENT now opens the native RISARMS Portal desktop app by default. core-client browser retains the web interface; core-client native retains the TCP/TLS device client.
- Public registration creates a pending account. Owner approval is required before server login. Rejecting or revoking an account invalidates its server sessions.
- Public members see messaging and their own storage. The server enforces these permissions even if someone modifies the app or sends API requests directly.
- Public messaging uses a tool-free Ollama worker. Owner ASIS/ASCS/TIVISS adapters remain separate. Public messages cannot invoke coding, shell tools or arbitrary agent file operations.
- Each account receives separate chat context, history, logs and cloud ownership. Local device profiles also separate their records, files, history and model preferences.
- Offline records and files remain local and pending until explicit Sync. Conflicting record edits are retained locally rather than silently overwriting the server.
- Hardware checks recommend a local model using available memory, container memory limits, free disk and installed Ollama tags. Downloads use Ollama and verify the resulting exact tag.
- Prior audit fixes and the later ASCS/RESCS/cancellation adapter updates are retained. Python 3.12 annotation failures and outdated messaging test fixtures were corrected.

## Components and access

| Component | Purpose | Daily command |
|---|---|---|
| Linux CORE server | Authentication, approvals, service orchestration and RESCS gateway | core-host |
| RISARMS desktop app | Messaging, personal storage and offline work | core-client |
| Optional browser portal | Same server accounts and owner administration | core-client browser |
| Native device client | Existing TCP/TLS device integration | core-client native |

| Account state | Server messaging | Personal cloud storage | Coding and administration |
|---|---|---|---|
| Pending or rejected | No | No | No |
| Approved member | Tool-free messaging | Own records and files | No |
| Owner | Messaging and installed services | Own scoped data | Yes |

A local device profile can work offline before server approval. This does not grant server access. Revoking access blocks future server requests; it cannot erase files that the user already downloaded to their own device.

## Install the Linux server

Use an Ubuntu/Debian server with Python 3.10+ and venv support. The server does not need a graphical desktop. Install dependencies and review the included installer before running it as root.

```sh
sudo apt update
sudo apt install python3 python3-venv
cd /path/to/CORE-HOST
sudo bash deploy/linux/install-server.sh
sudo nano /etc/core/core.env
```

Set CORE_PUBLIC_ORIGIN to your real HTTPS domain, then configure the reverse proxy using deploy/Caddyfile.example. Point DNS at the server and configure HTTPS ingress. The API binds to 127.0.0.1:8780; the reverse proxy is the public entry point. Never expose the old unauthenticated portal, Ollama listener or raw RESCS API publicly.

```sh
sudo systemctl enable --now core
sudo systemctl status core
sudo journalctl -u core -b --no-pager
```

The journal prints a first-owner setup key while no owner exists. Use that key in the desktop app's Connection tab with First-owner setup. Keep journal access limited to server administrators.

The installer creates the core service account, /opt/core/venv and /var/lib/core. It installs the unit but deliberately leaves starting/enabling it to the operator after domain configuration. It does not install Caddy, provision DNS, buy a domain or deploy cloud infrastructure.

The supplied unit uses gateway-only mode. This runs the authenticated API, approvals and service jobs without the old native TCP runtime. If native TCP/TLS device support is needed, configure it explicitly and remove --gateway-only from the unit. Preserve TLS/authentication for non-loopback native listeners.

## Prepare server AI and storage

Ollama supports Linux, including documented x86_64 and ARM64 installation paths. Install and start Ollama using its official Linux instructions. Keep its API on loopback. GPU acceleration depends on supported hardware and drivers; CPU inference is available but may be slow.

Run the checker as the same account that will configure CORE:

```sh
sudo -u core env CORE_WEB_DATA=/var/lib/core/web /opt/core/venv/bin/core-host model-check
sudo -u core env CORE_WEB_DATA=/var/lib/core/web /opt/core/venv/bin/core-host model-download
sudo systemctl restart core
```

model-download chooses the recommended model, downloads it if missing, verifies its installed tag and configures the default ASIS/TIVISS model names. Existing downloaded layers are managed by Ollama. A failed download is an error, not a mock success. Internet is required for the initial download. No model weights were downloaded as part of this build.

The checker offers qwen3.5:0.8b, 2b, 4b or 9b. Selection uses conservative CORE estimates for a small context. The new inference policy requests each model’s native maximum context, which is not covered by those estimates and can require much more RAM. See AI-Idle-and-Tokens.md. These estimates are not manufacturer requirements or guaranteed performance. If memory/disk cannot be established or is insufficient, no model is recommended. GPU probing is informational; detected GPU hardware does not establish that acceleration works.

Set OLLAMA_MODELS when checking a custom model filesystem. Otherwise the disk probe uses the supplied data/home filesystem, which may differ from the Ollama daemon's actual model directory. Confirm space on the real model disk before large downloads.

Install RESCS separately and configure its endpoint/API key in owner settings. The managed local option expects an installed RESCS repository and environment. ASIS, ASCS and TIVISS full-agent repositories are also installed separately for owner use; approved public messaging does not require executing those privileged agent CLIs.

Public users share read-only base model weights. They do not share conversation IDs, context files, workspaces or logs. Copying the same multi-GB model for every account is unnecessary. Different per-account model choices can be set by the owner using profile_models in settings, keyed by the account UUID and service name. This release does not train private fine-tuned weights for each account.

## Install and open the desktop app

Install Python 3.10+ with Tk. On Ubuntu/Debian desktop clients, install python3-tk. Windows/macOS users should use a Python distribution with Tcl/Tk. The server itself does not need Tk.

Windows, from the extracted CORE-CLIENT directory:

```powershell
py -m pip install --user .
$userScripts = py -c "import sysconfig; print(sysconfig.get_path('scripts', 'nt_user'))"
$env:Path = "$userScripts;$env:Path"
core-client setup --url https://your-domain.example
core-client
```

Add the printed scripts directory to the persistent user PATH for future terminals. Alternatively install with pipx, which provides isolated package environments and launcher management.

macOS/Linux, with pipx installed through the OS package manager:

```sh
pipx install /absolute/path/to/CORE-CLIENT
pipx ensurepath
# Reopen the terminal after ensurepath.
core-client setup --url https://your-domain.example
core-client
```

An existing virtual environment also works: install with python -m pip install /path/to/CORE-CLIENT and ensure that environment's Scripts/bin directory is on PATH. The desktop directory contains launchers and a Linux desktop-entry example. These are shortcuts to the installed command, not bundled standalone executable installers.

Useful commands:

```text
core-host --gateway-only --no-browser
core-host doctor
core-host model-check
core-host model-download
core-client
core-client app
core-client doctor
core-client model-check
core-client model-download
core-client browser
core-client native --help
```

core-host is headless by default; --open explicitly opens a browser on a graphical host. --no-browser remains a compatibility flag. Commands work from any directory after installation and PATH setup.

## Register approve and sign in

1. Open RISARMS Portal and create a local device profile with a separate local passphrase. This unlocks account-specific offline files and history.
2. In Connection, enter the server origin, desired server username/password and display name. Choose Register and request portal access. Do not send passwords to the owner by email or chat.
3. The owner signs in, opens Server admin, chooses Refresh accounts, selects the pending account and presses Approve or Reject.
4. After approval, the user signs in from Connection. Approved members receive messaging and personal record/file storage. Owner controls are hidden; API permissions independently enforce the same boundary.
5. For the very first owner, use First-owner setup with the key printed by the server. Public registration remains unavailable until the owner finishes setup.

Server sessions and credentials stay in memory on the client. Each local profile binds to one server origin and remote account UUID. Use another local profile for another account or server. Lock local profile before switching people.

## Messages cloud files and offline work

Tasks has an Auto mode: connected users run on the server; disconnected users use an installed local Ollama model. Auto does not retry a failed remote action locally after it may already have started. If the connection drops, the app reports that condition and keeps local data available. Server tasks can continue after the app closes; use Stop explicitly.

Approved members have ASIS messaging only. Owner accounts may select installed ASIS, ASCS and TIVISS adapters; local coding tasks require confirmation. Standalone service use requires its own installed .venv and dependencies. ROVERT remains an owner-configured telemetry link without automatic hardware commands.

Use Local setup to check the device, choose/download its recommended offline model and save preferences. Model preferences are stored separately for each local profile. The normal app startup never silently downloads multi-GB weights. Local models and service environments must be provisioned before losing internet.

Chat storage can be both, local or server. Public server messaging honors local-only by not saving server conversation context/history. Offline work is always stored locally first; server/both choices queue the output as a personal record for explicit sync when connected. Full owner agent runtimes can maintain their own per-profile memory independently of CORE's chat-history switch.

My storage keeps JSON records. Save locally queues an edit. Refresh server cache never overwrites a pending local edit. Sync sends it only to the bound account and server. If the server changed since the cached revision, the app keeps the local draft and reports a conflict. Compare values, then save a new key or resolve deliberately through the portal. No server deletion is silently mirrored into the local cache.

Cloud files supports local file caching, explicit upload sync, downloading into the local cache, exporting cached files and deleting server files. Upload retries use an account-scoped upload ID to avoid duplicate files. Transfers verify SHA-256 and server-assigned ownership. Limits are 8 MiB per file and 250 MiB of server file storage per account; the local file cache has its own 250 MiB limit. Approved member record scopes workspace/asis are limited to 1000 records each. These are file/record limits, not an overall operating-system disk quota.

| Feature | No internet and no server | Connected to server |
|---|---|---|
| Local profile unlock and history | Available | Available |
| Local records and cached files | Available | Available |
| Local AI messaging | Needs installed Ollama/model | Available independently |
| Registration and approval | Unavailable | Available |
| Server models and cloud sync | Queued/local copy only | Available after approval |
| Internet search and downloads | Unavailable | Requires internet |
| Owner local agents | Needs installed dependencies | Independent of server connection |

Network-dependent functions do not pretend they completed offline. No user can access another account by supplying a different owner or namespace. A local app lock is not disk encryption: use OS account permissions and full-disk encryption to protect cached plaintext files from someone with filesystem access.

## Storage locations and backups

The Linux service defaults to /var/lib/core/web. Under profiles/REMOTE_UUID it stores that account's chat-context.json, chats/*.json, logs and service workspaces. Server chat files retain the latest 200 jobs per account; task logs rotate at approximately 1 MiB. Administrative audit events omit prompt bodies and credentials. RESCS records use owner core.profile.REMOTE_UUID and namespace personal.profile.REMOTE_UUID.SERVICE. File ownership uses the same account owner.

On Windows, installed host data defaults to LOCALAPPDATA/RISARMS/CORE/web. On Linux manual launches use XDG_DATA_HOME or ~/.local/share/RISARMS/CORE/web. CORE_WEB_DATA or --data overrides these defaults.

Client settings are under APPDATA/RISARMS/CORE-CLIENT on Windows, ~/Library/Application Support/RISARMS/CORE-CLIENT on macOS, and XDG_CONFIG_HOME/RISARMS/CORE-CLIENT or ~/.config/... on Linux. The offline subdirectory contains desktop.sqlite3 and profiles/LOCAL_UUID preferences, files and standalone service workspaces. Local UUIDs and server UUIDs are intentionally different and mapped by the authenticated binding.

Before upgrading, stop CORE and back up its complete web directory, native runtime state and RESCS database/files together. Back up client offline data separately. Older CORE-HOST/var/web data can be retained by specifying its absolute path with --data or copied into the new location while stopped. Existing owner/member accounts remain valid; new public registrations use pending/rejected roles. The local database adds a remote_role column automatically for existing device profiles.

## Validation and deployment boundaries

Final automated suites: CORE-HOST 808 passed; CORE-CLIENT 179 passed. The focused public-registration, file-ownership and desktop/server contract checks passed again after the final edits (14 tests).

Validation evidence is included under validation in each ZIP. API tests use real loopback HTTP, authentication and CSRF, plus a controlled RESCS fixture for contract checks. GUI tests open the native window on a Linux virtual display and exercise registration, approval, member restrictions, offline records and hardware checks. Model-selection and download-protocol tests use controlled responses; no real model inference or model download was performed in this build.

Linux source execution is tested. Windows/macOS paths and process branches have static/unit coverage but were not executed on those OSs. The systemd unit and installer are supplied for operator deployment; this environment is not a running systemd server, and root installation/public TLS/DNS were not exercised. No public server URL or signed app-store installer has been created. GitHub main has not been updated by this delivery.

Record conflict checks serialize writes through this CORE gateway. An administrator writing directly to RESCS outside CORE is outside that gateway lock; this is not a universal distributed transaction or a bidirectional filesystem sync engine. Keep RESCS private behind CORE and use its own backup/governance controls.

## RISARMS documentation handoff

Add this guide to risarms-docs, with the API/role matrix and storage paths retained. The new endpoints include POST /api/register, owner GET/POST /api/access, authenticated GET /api/chats, GET/POST /api/files and per-file download/delete routes. /api/health advertises conditional_records_v1; the desktop refuses unsafe record sync to an older server that lacks that capability. Deploy this host release before enabling desktop sync.

For rollback, stop the service, reinstall the previous packages and restore matching backups if schemas/data were changed. Keep the new local device database backup: older app versions do not know its offline features. Do not remove data as part of package uninstall.

## References

- Ollama Linux installation and CPU/GPU setup: https://docs.ollama.com/linux
- Ollama chat API: https://docs.ollama.com/api/chat
- Ollama model pull API: https://docs.ollama.com/api/pull
- Qwen model tags: https://ollama.com/library/qwen3.5
- systemd execution and service directories: https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html
- Python Tk interface: https://docs.python.org/3/library/tkinter.html
