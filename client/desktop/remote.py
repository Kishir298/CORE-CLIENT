"""Authenticated native API client; cookies and credentials stay in memory."""
import json
import platform
import socket
import threading
from http.cookiejar import CookieJar
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import (
    HTTPCookieProcessor,
    HTTPRedirectHandler,
    ProxyHandler,
    Request,
    build_opener,
)

from ..web import validate_url


class RemoteError(ConnectionError):
    def __init__(self, message, status=0):
        super().__init__(message)
        self.status = status


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise RemoteError("Server redirects are refused. Configure its final HTTPS origin.")


class Remote:
    def __init__(self, url, timeout=8):
        validate_url(url)
        parsed = urlparse(url)
        if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise ValueError("Use the server origin without a path, query or fragment.")
        _ = parsed.port
        self.url, self.timeout = url.rstrip("/"), timeout
        self.cookies, self.csrf, self.profile = CookieJar(), "", None
        self.device_credential = ""
        self.lock = threading.RLock()
        self.opener = build_opener(ProxyHandler({}), NoRedirect(), HTTPCookieProcessor(self.cookies))

    def request(self, method, path, body=None):
        if not path.startswith("/api/") or path.startswith("//"):
            raise ValueError("Only CORE API paths are allowed.")
        headers = {"Accept": "application/json", "Content-Type": "application/json", "Origin": self.url, "X-Core-CSRF": self.csrf}
        data = json.dumps(body or {}).encode() if method != "GET" else None
        try:
            with self.lock, self.opener.open(Request(self.url + path, data=data, method=method, headers=headers), timeout=self.timeout) as response:
                limit = 12*1024*1024 if path.startswith("/api/files/") else 8*1024*1024
                raw = response.read(limit + 1)
                if len(raw) > limit:
                    raise RemoteError("Server response exceeded the app limit.")
                return json.loads(raw)
        except HTTPError as exc:
            try: message = json.loads(exc.read(65536)).get("error", "Request rejected")
            except (ValueError, AttributeError): message = "Request rejected"
            raise RemoteError(f"{message} (HTTP {exc.code})", exc.code) from exc
        except (URLError, OSError, ValueError) as exc:
            raise RemoteError("CORE server is unavailable; local data remains available.") from exc

    def login(self, username, password):
        result = self.request("POST", "/api/login", {
            "username": username,
            "password": password,
            "remember_device": True,
            "device_name": socket.gethostname()[:80] or "Desktop",
            "device_type": "desktop",
            "platform": f"{platform.system()} {platform.release()} ({platform.machine()})"[:120],
        })
        self.csrf, self.profile = result["csrf"], result["profile"]
        self.device_credential = result.get("device_credential", "")
        return self.profile

    def close(self):
        try:
            if self.csrf: self.request("POST", "/api/logout")
        except RemoteError: pass
        self.cookies.clear(); self.csrf, self.profile = "", None


def sync_records(store, profile, remote):
    bound = store.binding(profile)
    if not bound or bound["server"] != remote.url or bound["remote_id"] != (remote.profile or {}).get("id"):
        raise PermissionError("Local profile and server account do not match.")
    health = remote.request("GET", "/api/health")
    if "conditional_records_v1" not in health.get("capabilities", []):
        raise RemoteError("This server does not support safe offline sync. Upgrade CORE-HOST before syncing.")
    saved, conflicts = 0, []
    for item in store.records(profile, pending=True):
        try:
            result = remote.request("POST", "/api/records", {"service": item["service"], "key": item["key"],
                "value": item["value"], "expected_revision": item["base"]})
            store.acknowledged(profile, item, result); saved += 1
        except RemoteError as exc:
            if exc.status != 409: raise
            conflicts.append(item["service"] + "/" + item["key"])
    return {"saved": saved, "conflicts": conflicts}
