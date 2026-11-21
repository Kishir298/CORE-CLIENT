"""Independent cross-platform terminal launcher for browser and native clients."""
import argparse
import os
import sys
from pathlib import Path

from .web import main as browser_main


def settings_path():
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", str(Path.home())))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    return base / "RISARMS" / "CORE-CLIENT" / "portal-url.txt"


def desktop_main(url):
    """Prefer the canonical RISARMS-UI app when it is installed.

    CORE-CLIENT keeps its legacy desktop app as a fallback so existing
    installations keep working; new installations should use RISARMS-UI.
    """
    try:
        from risarms_ui.entrypoint import main as unified_main
    except ImportError:
        from .desktop.app import launch
        return launch(settings_path().parent / "offline", url)
    return unified_main(["--url", url])


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "native":
        from .core_device_client import main as native_main
        return native_main(args[1:])
    parser = argparse.ArgumentParser(prog="core-client", description="Open the C.O.R.E. desktop app; use 'native --help' for the TCP/TLS client.")
    parser.add_argument("command", nargs="?", choices=("app", "open", "browser", "setup", "doctor", "model-check", "model-download"), default="app")
    parser.add_argument("--url", help="Portal URL; setup saves it for future launches.")
    options = parser.parse_args(args)
    if options.command in ("model-check", "model-download"):
        import json

        from .model_support import download_recommended, system_report
        report = system_report(settings_path().parent)
        print(json.dumps(report, indent=2))
        if options.command == "model-download":
            try: download_recommended(report)
            except (OSError, ValueError, RuntimeError) as exc: parser.error(str(exc))
        return 0
    path = settings_path()
    saved = path.read_text(encoding="utf-8").strip() if path.is_file() else None
    url = options.url or os.environ.get("CORE_PORTAL_URL") or saved or "http://127.0.0.1:8780"
    from .web import validate_url
    try:
        validate_url(url)
    except ValueError as exc:
        parser.error(str(exc))
    if options.command == "setup":
        if not options.url:
            parser.error("Use core-client setup --url YOUR_PORTAL_URL")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(url + "\n", encoding="utf-8")
        print("Saved portal URL:", url)
        return 0
    if options.command == "doctor":
        print("C.O.R.E. client command is installed.")
        print("Portal URL:", url)
        print("Settings:", path)
        print("Desktop app: core-client app; browser: core-client browser")
        print("Native client: core-client native --help")
        return 0
    if options.command == "browser": return browser_main(["--url", url])
    return desktop_main(url)
