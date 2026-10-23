"""Browser client for the authenticated C.O.R.E. portal; no host internals."""
import argparse
import os
from urllib.parse import urlparse
import webbrowser


def validate_url(url):
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Use the full C.O.R.E. portal HTTP(S) URL without embedded credentials.")
    if parsed.scheme == "http" and parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("Remote C.O.R.E. portals require HTTPS.")
    return url


def main(argv=None):
    parser = argparse.ArgumentParser(description="Open your C.O.R.E. profile and RISARMS services.")
    parser.add_argument("--url", default=os.environ.get("CORE_PORTAL_URL", "http://127.0.0.1:8780"))
    args = parser.parse_args(argv)
    try: url = validate_url(args.url)
    except ValueError as exc: parser.error(str(exc))
    print("C.O.R.E. portal:", url)
    if not webbrowser.open(url): print("Open this URL in your browser.")
    return 0


if __name__ == "__main__": raise SystemExit(main())
