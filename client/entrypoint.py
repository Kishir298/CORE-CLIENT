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


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "native":
        from .core_device_client import main as native_main
        return native_main(args[1:])
    parser = argparse.ArgumentParser(prog="core-client", description="Open C.O.R.E.; use 'native --help' for the TCP/TLS client.")
    parser.add_argument("command", nargs="?", choices=("open", "setup", "doctor"), default="open")
    parser.add_argument("--url", help="Portal URL; setup saves it for future launches.")
    options = parser.parse_args(args)
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
        print("Native client: core-client native --help")
        return 0
    return browser_main(["--url", url])
