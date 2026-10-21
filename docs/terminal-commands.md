# CORE terminal commands and portal setup

This handoff documents the new installed `core-host` and `core-client` commands, the authenticated portal and the earlier audit fixes included in this release. Copy this Markdown document into risarms-docs. The host is intended for Windows; the client has portable entry points for Windows, macOS and Linux.

## Changes made

- Added Python console-script entry points so the installed commands run from any terminal directory.
- `core-host` starts the native CORE runtime and authenticated portal, opening the browser unless `--no-browser` is supplied. It has a read-only `doctor` command.
- Installed host launch uses a stable account/config directory and a sibling runtime working directory. It does not load an unrelated core.yaml from the terminal's current folder. Use `--config` explicitly for native configuration.
- `core-client` opens the browser portal. `setup --url` remembers its address; `doctor` displays configuration without connecting. `native` forwards arguments to the existing independent TCP/TLS client.
- Native device platform detection now reports Windows, Mac or Linux rather than always reporting Mac.
- Added regression tests for installed launch behavior, profile paths, saved URLs, native argument forwarding and platform detection.
- Preserved the previous portal features: owner/member accounts, profile-scoped RESCS records, on-demand ASIS/ASCS/TIVISS tasks, live output, Stop and owner settings. ROVERT currently uses a configurable telemetry link.

## One time installation

Install Python 3.10 or later. Install each source ZIP separately. The host needs PyYAML; the basic client has no third-party runtime dependencies. Package installation requires a build backend such as setuptools. ASCS needs its own Python 3.12+ environment.

Windows PowerShell, inside the extracted CORE-HOST directory:

```powershell
py -m pip install --user .
$userScripts = py -c "import sysconfig; print(sysconfig.get_path('scripts', 'nt_user'))"
$env:Path = "$userScripts;$env:Path"
core-host doctor
core-host
```

The PATH change above applies to the current terminal. For future terminals, add the printed `$userScripts` directory to your user PATH in Windows Environment Variables. Do the same one-time installation inside CORE-CLIENT on a Windows client, then use `core-client` instead. These source installs do not require the original directory after installation; editable installs do.

For macOS/Linux clients, pipx provides an isolated installation and persistent launcher PATH setup:

```sh
pipx install /absolute/path/to/CORE-CLIENT
pipx ensurepath
```

Install pipx through your OS package manager first if unavailable. Reopen the terminal after `ensurepath`, then run `core-client doctor`. A Python virtual environment is also supported: install the package with `python -m pip install /path/to/CORE-CLIENT` and add that environment's Scripts/bin directory to PATH. Do not use sudo pip or bypass an OS externally managed Python restriction.

## Daily commands

```text
core-host
core-host --no-browser
core-host --data C:\RISARMS\COREData\web
core-host --config C:\RISARMS\core.yaml
core-host doctor
core-client setup --url https://core.example.com
core-client
core-client doctor
core-client native --help
core-client native --host HOST_IP --ca-file PATH_TO_CA
```

Native mode uses its existing provisioning/login requirements. It connects to the native TCP/TLS listener configured on the host; port 8780 is the browser gateway, not the native device endpoint. Continue using `python -m core` for the existing host administration CLI. `--help` lists each installed command's options. `core-client --url URL` is a temporary override. CORE_PORTAL_URL overrides a saved client URL.

If a command is not found, verify installation with `py -m pip show core` / `py -m pip show core-client` on Windows, check which Python received the installation and add that interpreter's script directory to PATH. For pipx use `pipx list` and `pipx ensurepath`.

## First owner and services

1. Start `core-host` and visit http://127.0.0.1:8780. Use the setup key printed in the host terminal to create the first owner profile.
2. In Settings, configure the actual ASIS, ASCS, RESCS, TIVISS and ROVERT paths. Installed packages cannot reliably infer where those repositories are stored. Each service needs its own installed dependencies and local models.
3. Start managed RESCS or configure a real RESCS endpoint and API key. Use member profiles for separate records. Browser-supplied ownership is not trusted.
4. Run ASIS, ASCS or TIVISS from the task workspace. ASCS and TIVISS are owner-only; member ASIS uses general mode. Save completed task results explicitly to RESCS.
5. Keep the host terminal open. Ctrl+C stops processes launched by this CORE instance. Jobs have bounded output and a 15-minute limit.

CORE is not an OS sandbox. Coding tasks run with the host account's privileges. Browser file uploads and interactive agent approval dialogs are not implemented. Streaming here means task output, not remote desktop streaming.

## Data and migration

The installed Windows host defaults to `%LOCALAPPDATA%\RISARMS\CORE\web`; `CORE_WEB_DATA` or `--data` overrides it. Native runtime relative state is under the sibling `runtime` directory. CORE host configuration supplied by `--config` is resolved before entering that directory; relative paths inside that YAML use the runtime working directory, so prefer absolute paths.

Earlier module/script launches used `CORE-HOST/var/web`. To preserve those accounts and settings, stop the old instance and use `core-host --data C:\full\path\CORE-HOST\var\web`, or copy the complete old web directory to the new default before first launch. Also migrate any native runtime state separately. Never run two instances against the same SQLite data.

Client saved portal URL locations: Windows `%APPDATA%\RISARMS\CORE-CLIENT\portal-url.txt`; macOS `~/Library/Application Support/RISARMS/CORE-CLIENT/portal-url.txt`; Linux `$XDG_CONFIG_HOME/RISARMS/CORE-CLIENT/portal-url.txt` or `~/.config/...`. Native device identity remains `~/.risarms-device.json`.

Back up the host web directory, runtime state, external-service state and RESCS database/files together while stopped. These include credentials and must remain private. To roll back, stop the host, reinstall the previous release and restore its matching backup. Do not delete profile data to reinstall.

## Offline behavior

After installation and local model provisioning, the gateway, account authentication and local records/backend operations do not inherently require internet. Local ASIS/ASCS/TIVISS behavior depends on their actual implementations and installed tools; use the accompanying service prompts to implement and verify full standalone offline support. CORE does not magically make a remote model or storage backend local.

Loopback hosting works without Wi-Fi. Devices on the same local network can communicate without an internet connection, provided networking and certificates are correctly configured. Public remote access needs connectivity. Internet search, package/model downloads, external APIs and remote databases inherently need their networks. Offline operation never silently substitutes a mock or different backend.

For offline installation, while online run `py -m pip wheel --wheel-dir wheelhouse .` in each project, then transfer its wheelhouse. On the offline machine use `py -m pip install --user --no-index --find-links wheelhouse core==0.4.0` or `core-client==0.4.0`. This command-release ZIP also includes the project's own wheel under `install/`; host installation still needs a compatible PyYAML wheel or an existing installation. Python itself must already be available. pipx installation also needs its dependencies prepared if performed offline.

## Public hosting

Public deployment has not been performed. Configure an HTTPS public origin in Settings, DNS and a same-machine reverse proxy using deploy/Caddyfile.example. Proxy only the authenticated gateway at loopback port 8780. Do not expose the old 8765 portal or raw agent/model/storage listeners. Public hosting still needs the actual domain and destination server. Public certificate issuance/renewal can require internet.

## Validation and remaining work

The previous full portal run collected 934 tests: 755 passed and 179 encountered environment socket-bind PermissionError. The final command release collected 946 tests: 767 passed and 179 were blocked by the same socket permission restriction, with no other failures. Test evidence and installed-command smoke checks are in validation/ in the new ZIPs. Blocked tests are not passing tests. Actual wheel installation and command execution from outside the source folders were checked on macOS. Windows/Linux platform branches were unit-tested, not executed on those OSs. Live network, Windows startup, public TLS and end-to-end external model integration remain destination-host checks.

The DayQuest architecture document informed the visible authoritative server and encrypted access. Our Library informed first-run setup, account roles and operational logging. Their server ZIP code was not inspected successfully. No GitHub writes or public deployment are implied by this delivery.
