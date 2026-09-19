"""Client portal: localhost dashboard over stdlib HTTP.

:class:`ClientPortal` serves a JSON API plus a single-page dashboard for
one :class:`CoreDeviceClient`. It is a presentation layer only: all host
data flows through the client's public, authenticated methods. Offline
operation is first-class — local sections always render; remote sections
report ``C.O.R.E. HOST OFFLINE`` instead of fabricating data.
"""

from __future__ import annotations

import ipaddress
import json
import socket
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from .. import capabilities as capability_probe
from .. import geo
from .. import models as model_policy
from .models import HOST_OFFLINE, envelope, redact, session_view

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8766
MAX_BODY_BYTES = 64 * 1024
OLLAMA_TIMEOUT_S = 10.0
LOCAL_CHAT_TIMEOUT_S = 180.0

KNOWN_SERVICES = (
    ("resources", "Resource Manager: register/discover/list resources"),
    ("health", "Health Monitor: component checks"),
    ("agent", "Agent Scheduler: placement + AI offload (infer)"),
    ("rescs", "R.E.S.C.S. Adapter: records/files through C.O.R.E."),
    ("routing", "Data Router: message routing"),
    ("events", "Event System: publish/subscribe"),
    ("communication", "Communication: device messaging"),
    ("runtime", "Runtime History: lifecycle records"),
)


def classify_host_address(host: str) -> dict:
    """Classify a host address without implying geography."""
    try:
        parsed = ipaddress.ip_address(host.strip())
    except ValueError:
        return {"address": host, "kind": "hostname", "note": "not a literal IP"}
    if parsed.is_loopback:
        kind = "loopback"
    elif parsed.is_private:
        kind = "private LAN IP (no geographic meaning)"
    elif parsed.is_global:
        kind = "public IP"
    elif parsed.is_link_local:
        kind = "link-local"
    else:
        kind = "special-purpose"
    return {"address": host, "kind": kind}


class ClientPortal:
    """Localhost web portal bound to one client instance factory."""

    def __init__(
        self,
        client_factory,
        *,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        ollama_host: str = DEFAULT_HOST,
        ollama_port: int = 11434,
        model_id: str = "qwen3-coder:30b",
        location_precision: str = geo.PRECISION_APPROXIMATE,
    ) -> None:
        self._factory = client_factory
        self.host = host or DEFAULT_HOST
        # NOTE: port 0 means "ephemeral" — do NOT use `or` here, it would
        # remap 0 to the default and collide with other portal instances.
        self.port = DEFAULT_PORT if port is None else int(port)
        self.ollama_host = ollama_host
        self.ollama_port = int(ollama_port)
        self.model_id = model_id
        self.location_precision = location_precision
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._own_location: dict = geo.unknown_location()
        self._peer_locations: dict[str, dict] = {}
        self._recent: list[str] = []

    @property
    def url(self) -> str:
        """Public portal URL."""
        return f"http://{self.host}:{self.port}"

    @property
    def is_running(self) -> bool:
        """Whether the HTTP server thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    def _client(self):
        try:
            client = self._factory()
        except Exception:
            return None
        return client

    # -- lifecycle ------------------------------------------------------

    def start(self) -> str:
        """Start the portal (idempotent); return the URL."""
        if self.is_running:
            return self.url
        handler = self._make_handler()
        self._server = ThreadingHTTPServer((self.host, self.port), handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": 0.2},
            daemon=True,
            name="core-client-portal",
        )
        self._thread.start()
        return self.url

    def stop(self) -> None:
        """Stop the portal (idempotent)."""
        server, self._server = self._server, None
        if server is not None:
            try:
                server.shutdown()
            except Exception:
                pass
            try:
                server.server_close()
            except Exception:
                pass
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5.0)

    # -- local probes -----------------------------------------------------

    def _capability_profile(self) -> dict:
        try:
            return capability_probe.detect_capabilities()
        except Exception:
            return {}

    def _capability_class(self, profile: dict) -> str:
        try:
            return capability_probe.evaluate_class(profile)
        except Exception:
            return capability_probe.CAPABILITY_UNKNOWN

    def _ollama_models(self) -> list | None:
        try:
            return model_policy.ollama_models(
                self.ollama_host, self.ollama_port, timeout=OLLAMA_TIMEOUT_S
            )
        except Exception:
            return None

    def _ai_state(self) -> dict:
        profile = self._capability_profile()
        installed = self._ollama_models()
        try:
            evaluation = model_policy.evaluate_model(self.model_id, profile, installed)
        except Exception:
            evaluation = {"state": model_policy.LOCAL_AI_UNKNOWN,
                          "model_id": self.model_id, "detail": {}}
        online = self._host_reachable()
        return {
            "capability_class": self._capability_class(profile),
            "local": evaluation,
            "ollama": {
                "reachable": installed is not None,
                "models": [
                    {"name": entry.get("name"), "size": entry.get("size")}
                    for entry in (installed or [])
                    if isinstance(entry, dict)
                ],
            },
            "offload": {
                "available": online,
                "detail": "host AI via agent service"
                if online
                else HOST_OFFLINE["detail"],
            },
        }

    def _host_reachable(self) -> bool:
        client = self._client()
        if client is None:
            return False
        try:
            probe = socket.create_connection((client.host, client.port), timeout=2.0)
            probe.close()
            return True
        except OSError:
            return False

    # -- API builders -------------------------------------------------------

    def api_status(self) -> dict:
        client = self._client()
        if client is None:
            return {"online": False, "detail": "client not initialized"}
        try:
            summary = client.session_summary()
        except Exception:
            return {"online": False, "detail": "session unreadable"}
        return {
            "service": "core-client-portal",
            "online": summary.get("status") not in (None, "DISCONNECTED"),
            "lease_state": summary.get("status"),
            "device_id": client.device_id,
            "join_name": client.join_name,
            "host_reachable": self._host_reachable(),
            "portal": {"url": self.url},
        }

    def api_device(self) -> dict:
        client = self._client()
        profile = self._capability_profile()
        base = {
            "device_name": None, "device_id": None, "identity_id": None,
            "join_name": None, "platform": None, "device_type": None,
            "capabilities": [], "host": None, "port": None,
        }
        if client is not None:
            try:
                remembered = client.remembered_state()
            except Exception:
                remembered = {}
            base.update(
                {
                    "device_name": client.device_name,
                    "device_id": client.device_id,
                    "identity_id": client.identity_id,
                    "join_name": client.join_name,
                    "platform": client.platform,
                    "device_type": client.device_type,
                    "capabilities": list(client.capabilities),
                    "host": client.host,
                    "port": client.port,
                    "remembered_keys": sorted(remembered.keys()),
                }
            )
        base["system"] = {
            "os": profile.get("os"),
            "architecture": profile.get("architecture"),
            "cpu_cores": profile.get("cpu_cores"),
            "python_version": profile.get("python_version"),
            "ram_total_bytes": profile.get("ram_total_bytes"),
            "ram_available_bytes": profile.get("ram_available_bytes"),
            "disk_total_bytes": profile.get("disk_total_bytes"),
            "disk_free_bytes": profile.get("disk_free_bytes"),
            "gpu_present": bool((profile.get("gpu") or {}).get("present")),
        }
        base["capability_class"] = self._capability_class(profile)
        return base

    def api_capabilities(self) -> dict:
        profile = self._capability_profile()
        return {
            "profile": profile,
            "class": self._capability_class(profile),
            "policy": dict(capability_probe.DEFAULT_POLICY),
        }

    def api_session(self) -> dict:
        client = self._client()
        if client is None:
            return {"online": False, "detail": "client not initialized"}
        try:
            view = session_view(client.session_summary())
        except Exception as exc:
            return {"online": False, "detail": f"session unreadable: {exc}"}
        try:
            path = client.device_file
            keys = sorted(json.loads(path.read_text(encoding="utf-8")).keys())
        except Exception:
            keys = []
        view["remembered_file_keys"] = keys
        return view

    def _require_online(self, client):
        if client is None:
            raise ConnectionError(HOST_OFFLINE["detail"])
        try:
            summary = client.session_summary()
        except Exception as exc:
            raise ConnectionError(f"session unreadable: {exc}") from exc
        if summary.get("status") in (None, "DISCONNECTED"):
            raise ConnectionError("device is offline; reconnect first")
        return client

    def api_devices(self) -> dict:
        client = self._client()
        try:
            self._require_online(client)
            assert client is not None
            response = client.discover()
        except Exception as exc:
            return {**HOST_OFFLINE, "error": str(exc)}
        payload = response.get("payload", {}) if isinstance(response, dict) else {}
        devices = payload.get("devices", []) if isinstance(payload, dict) else []
        return {"online": True, "devices": devices if isinstance(devices, list) else []}

    def api_network(self) -> dict:
        client = self._client()
        host_info: dict[str, Any] = {"address": None, "kind": "unknown",
                                     "reachable": False}
        if client is not None:
            host_info = classify_host_address(client.host)
            host_info["port"] = client.port
            host_info["reachable"] = self._host_reachable()
        devices = self.api_devices().get("devices", [])
        for entry in devices:
            if not isinstance(entry, dict):
                continue
            peer = self._peer_locations.get(entry.get("device_id") or "")
            entry["location"] = geo.apply_precision(
                peer or geo.unknown_location(), self.location_precision
            )
            entry["distance"] = geo.format_distance(
                geo.distance_between(self._own_location, peer or {})
            )
        reachable = self._host_reachable()
        return {
            "topology": "Logical C.O.R.E. connectivity (not physical topology).",
            "online": reachable,
            "detail": None if reachable else HOST_OFFLINE["detail"],
            "host": host_info,
            "own_location": geo.apply_precision(
                self._own_location, self.location_precision
            ),
            "devices": devices,
        }

    def api_rescs(self, *, operation: str, **params: Any) -> dict:
        client = self._client()
        try:
            self._require_online(client)
            assert client is not None
            if operation == "records_list":
                response = client.data_request(
                    "record_list",
                    {"namespace": params.get("namespace", "default"),
                     "limit": int(params.get("limit", 20))},
                )
            elif operation == "records_search":
                response = client.data_request(
                    "record_search",
                    {"namespace": params.get("namespace", "default"),
                     "query": params.get("query", ""),
                     "limit": int(params.get("limit", 20))},
                )
            elif operation == "record_get":
                response = client.data_request(
                    "record_get", {"record_id": params.get("record_id")}
                )
            elif operation == "file_metadata":
                response = client.data_request(
                    "file_metadata", {"file_id": params.get("file_id")}
                )
            elif operation == "file_download":
                response = client.data_request(
                    "file_download", {"file_id": params.get("file_id")}
                )
            else:
                raise ValueError(f"unknown RESCS operation: {operation}")
        except Exception as exc:
            return {**HOST_OFFLINE, "error": str(exc)}
        payload = response.get("payload", {}) if isinstance(response, dict) else {}
        if operation in ("record_get", "file_metadata") and isinstance(payload, dict):
            record_id = payload.get("id") or params.get("record_id") or params.get("file_id")
            if record_id and record_id not in self._recent:
                self._recent.insert(0, record_id)
                self._recent = self._recent[:20]
        return {"online": True, "operation": operation, "result": payload,
                "recent": list(self._recent)}

    def api_services(self) -> dict:
        return {
            "online": self._host_reachable(),
            "services": [
                {"service_id": service_id, "description": description}
                for service_id, description in KNOWN_SERVICES
            ],
            "note": "Invocation goes through the authenticated client; "
            "only the agent service is invokable from the AI section.",
        }

    def api_ai(self) -> dict:
        return self._ai_state()

    def ai_request(self, prompt: str) -> dict:
        """Fulfill one AI request: local model first, host offload second."""
        if not (isinstance(prompt, str) and prompt.strip()):
            raise ValueError("prompt must be a non-empty string")
        state = self._ai_state()
        local_state = state["local"]["state"]
        if local_state == model_policy.LOCAL_AI_AVAILABLE:
            return {
                "executed": "local",
                "model_id": self.model_id,
                "content": self._local_chat(prompt.strip()),
            }
        if state["offload"]["available"]:
            client = self._client()
            try:
                self._require_online(client)
                assert client is not None
                response = client.service_request(
                    "agent", "infer", {"prompt": prompt.strip()}
                )
            except Exception as exc:
                raise ConnectionError(f"host offload failed: {exc}") from exc
            payload = response.get("payload", {}) if isinstance(response, dict) else {}
            return {
                "executed": "host",
                "model_id": None,
                "content": payload,
            }
        raise ConnectionError(
            "local model insufficient and C.O.R.E. host unreachable"
        )

    def _local_chat(self, prompt: str) -> str:
        """Run one non-streaming chat against the local Ollama server."""
        body = json.dumps(
            {"model": self.model_id, "messages": [{"role": "user", "content": prompt}],
             "stream": False}
        ).encode("utf-8")
        url = f"http://{self.ollama_host}:{self.ollama_port}/api/chat"
        request = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=LOCAL_CHAT_TIMEOUT_S) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise ConnectionError(f"local model call failed: {exc}") from exc
        message = payload.get("message", {}) if isinstance(payload, dict) else {}
        content = message.get("content", "") if isinstance(message, dict) else ""
        if not (isinstance(content, str) and content.strip()):
            raise ConnectionError("local model returned no content")
        return content

    def reconnect_client(self) -> dict:
        """Reconnect the bound client (login session must still exist)."""
        client = self._client()
        if client is None:
            raise ConnectionError("client not initialized")
        if not client.is_logged_in:
            raise ConnectionError("login required; restart the client CLI to log in")
        response = client.reconnect()
        payload = response.get("payload", {}) if isinstance(response, dict) else {}
        return {"reconnected": True, "connection_id": client.connection_id,
                "lease": {key: payload.get(key) for key in
                          ("connected_at", "lease_expires_at", "lease_duration_seconds")}}

    def set_own_location(self, location: dict) -> dict:
        """Record this device's location (RAM-only, manual source default)."""
        if not isinstance(location, dict):
            raise ValueError("location must be an object")
        self._own_location = geo.make_location(
            latitude=location.get("latitude"),
            longitude=location.get("longitude"),
            source=location.get("source", geo.SOURCE_MANUAL),
            accuracy_meters=location.get("accuracy_meters"),
            timestamp=location.get("timestamp"),
        )
        return geo.apply_precision(self._own_location, self.location_precision)

    def set_peer_location(self, device_id: str, location: dict) -> dict:
        """Record a peer's reported location (RAM-only)."""
        if not (isinstance(device_id, str) and device_id.strip()):
            raise ValueError("device_id must be a non-empty string")
        if not isinstance(location, dict):
            raise ValueError("location must be an object")
        stored = geo.make_location(
            latitude=location.get("latitude"),
            longitude=location.get("longitude"),
            source=location.get("source", geo.SOURCE_MANUAL),
            accuracy_meters=location.get("accuracy_meters"),
            timestamp=location.get("timestamp"),
        )
        self._peer_locations[device_id.strip()] = stored
        return geo.apply_precision(stored, self.location_precision)

    # -- HTTP plumbing ------------------------------------------------------

    def _make_handler(self):
        portal = self

        class _Handler(BaseHTTPRequestHandler):
            server_version = "CoreClientPortal/0.4.0"

            def log_message(self, *args):  # quiet by default
                pass

            def _send_json(self, payload: Any, *, status: int = 200) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def _send_text(self, text: str, content_type: str) -> None:
                body = text.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def _read_json(self) -> dict:
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except (TypeError, ValueError):
                    length = 0
                if length <= 0 or length > MAX_BODY_BYTES:
                    return {}
                try:
                    raw = self.rfile.read(length)
                    parsed = json.loads(raw.decode("utf-8"))
                    return parsed if isinstance(parsed, dict) else {}
                except Exception:
                    return {}

            def _query(self) -> dict:
                from urllib.parse import parse_qsl, urlparse as _urlparse

                return dict(parse_qsl(_urlparse(self.path).query or ""))

            def do_GET(self) -> None:
                path = urlparse(self.path).path.rstrip("/") or "/"
                try:
                    if path == "/":
                        self._send_text(INDEX_HTML, "text/html; charset=utf-8")
                    elif path == "/api/status":
                        self._send_json(envelope(portal.api_status()))
                    elif path == "/api/device":
                        self._send_json(envelope(portal.api_device()))
                    elif path == "/api/capabilities":
                        self._send_json(envelope(portal.api_capabilities()))
                    elif path == "/api/session":
                        self._send_json(envelope(portal.api_session()))
                    elif path == "/api/devices":
                        self._send_json(envelope(portal.api_devices()))
                    elif path == "/api/network":
                        self._send_json(envelope(portal.api_network()))
                    elif path == "/api/rescs":
                        query = self._read_json() or self._query()
                        self._send_json(envelope(portal.api_rescs(
                            operation=query.get("operation", "records_list"),
                            **{k: v for k, v in query.items() if k != "operation"},
                        )))
                    elif path == "/api/services":
                        self._send_json(envelope(portal.api_services()))
                    elif path == "/api/ai":
                        self._send_json(envelope(portal.api_ai()))
                    else:
                        self._send_json(
                            envelope(None, ok=False, error="unknown endpoint"),
                            status=404,
                        )
                except Exception as exc:
                    self._send_json(
                        envelope(None, ok=False, error=f"portal error: {exc}"),
                        status=500,
                    )

            def do_POST(self) -> None:
                path = urlparse(self.path).path.rstrip("/") or "/"
                try:
                    if path == "/api/ai/request":
                        body = self._read_json()
                        prompt = body.get("prompt", "")
                        if not (isinstance(prompt, str) and prompt.strip()):
                            raise ValueError("prompt must be a non-empty string")
                        self._send_json(envelope(portal.ai_request(prompt)))
                    elif path == "/api/reconnect":
                        self._send_json(envelope(portal.reconnect_client()))
                    elif path == "/api/location":
                        body = self._read_json()
                        if not isinstance(body.get("location"), dict):
                            raise ValueError("location object required")
                        self._send_json(envelope(portal.set_own_location(body["location"])))
                    elif path == "/api/devices/location":
                        body = self._read_json()
                        device_id = body.get("device_id")
                        if not device_id or not isinstance(body.get("location"), dict):
                            raise ValueError("device_id + location object required")
                        self._send_json(envelope(
                            portal.set_peer_location(str(device_id), body["location"])))
                    else:
                        self._send_json(
                            envelope(None, ok=False, error="unknown endpoint"),
                            status=404,
                        )
                except ValueError as exc:
                    self._send_json(
                        envelope(None, ok=False, error=str(exc)), status=400
                    )
                except ConnectionError as exc:
                    self._send_json(
                        envelope(None, ok=False, error=str(exc)), status=503
                    )
                except Exception as exc:
                    self._send_json(
                        envelope(None, ok=False, error=f"portal error: {exc}"),
                        status=500,
                    )

        return _Handler


INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>C.O.R.E. Client Portal</title>
<style>
:root{color-scheme:dark light}
body{font-family:system-ui,sans-serif;margin:0;padding:0 12px 40px;max-width:1000px}
nav{display:flex;flex-wrap:wrap;gap:6px;margin:12px 0}
nav button{padding:6px 10px;cursor:pointer}
table{border-collapse:collapse;width:100%;margin:8px 0}
th,td{border:1px solid #8883;padding:4px 8px;text-align:left;font-size:14px;overflow-wrap:anywhere}
.badge{padding:2px 8px;border-radius:8px;font-size:12px}
.ok{background:#1a7f37;color:#fff}.warn{background:#9a6700;color:#fff}
.err{background:#b42318;color:#fff}.mut{background:#555;color:#fff}
pre{background:#0002;padding:8px;overflow:auto;font-size:12px}
textarea{width:100%;min-height:70px}
@media (max-width:640px){table,thead,tbody,tr,th,td{display:block}th{display:none}td{border:none;border-bottom:1px solid #8883}}
</style>
</head>
<body>
<header><h1>C.O.R.E. Client Portal</h1><span id="overall" class="badge mut">…</span></header>
<p id="statusline">connecting…</p>
<nav id="tabs"></nav>
<main id="content"></main>
<script>
const SECTIONS=[["dashboard","Dashboard"],["devices","Devices"],["rescs","R.E.S.C.S."],
 ["services","Services"],["ai","AI"],["network","Network"],["session","Session"],["settings","Settings"]];
let active="dashboard";
async function api(path,opts){const r=await fetch(path,opts);return r.json();}
function esc(v){return String(v??"").replace(/&/g,"&amp;").replace(/</g,"&lt;");}
function badge(t,c){return `<span class="badge ${c}">${esc(t)}</span>`;}
function table(rows){if(!rows||!rows.length)return "<p><i>none</i></p>";
 const keys=[...new Set(rows.flatMap(r=>Object.keys(r)))];
 return "<table><thead><tr>"+keys.map(k=>`<th>${esc(k)}</th>`).join("")+"</tr></thead><tbody>"+
 rows.map(r=>"<tr>"+keys.map(k=>`<td>${esc(typeof r[k]==="object"?JSON.stringify(r[k]):r[k])}</td>`).join("")+"</tr>").join("")+"</tbody></table>";}
async function refreshStatus(){
 try{const j=await api("/api/status");const d=j.data;
  document.getElementById("statusline").textContent=
   d.online?`ONLINE · ${d.device_id} · ${d.join_name} · host reachable: ${d.host_reachable}`:`OFFLINE · ${d.detail||""}`;
  document.getElementById("overall").outerHTML=badge(d.online?"ONLINE":"OFFLINE",d.online?"ok":"err").replace('class="badge','id="overall" class="badge');
 }catch(e){document.getElementById("statusline").textContent="portal unreachable";}}
async function render(){
 const c=document.getElementById("content");
 if(active==="dashboard"){const s=(await api("/api/status")).data;const d=(await api("/api/device")).data;
  c.innerHTML=`<pre>${esc(JSON.stringify({status:s,device:{device_name:d.device_name,device_id:d.device_id,join_name:d.join_name,capability_class:d.capability_class}},null,1))}</pre>`;return;}
 if(active==="devices"){const j=await api("/api/devices");
  c.innerHTML=j.data.online?table(j.data.devices):`<p>${esc(j.data.detail||"offline")}</p>`;return;}
 if(active==="rescs"){c.innerHTML=`<p>Records via C.O.R.E. (namespace default):</p>
  <button id="rl">List records</button> <input id="rq" placeholder="search query"> <button id="rs">Search</button><div id="ro"></div>`;
  document.getElementById("rl").onclick=async()=>{const j=await api("/api/rescs?operation=records_list&limit=20");document.getElementById("ro").innerHTML=`<pre>${esc(JSON.stringify(j.data,null,1))}</pre>`;};
  document.getElementById("rs").onclick=async()=>{const q=document.getElementById("rq").value;const j=await api("/api/rescs?operation=records_search&query="+encodeURIComponent(q));document.getElementById("ro").innerHTML=`<pre>${esc(JSON.stringify(j.data,null,1))}</pre>`;};return;}
 if(active==="services"){const j=await api("/api/services");c.innerHTML=`<p>${esc(j.data.note||"")}</p>`+table(j.data.services);return;}
 if(active==="ai"){const j=await api("/api/ai");const d=j.data;
  c.innerHTML=`<p>Capability: ${esc(d.capability_class)} · Local: ${esc(d.local.state)} (${esc(d.local.model_id)}) · Offload: ${d.offload.available?"available":"unavailable"}</p>
  <textarea id="ap" placeholder="Ask AI…"></textarea><br><button id="ago">Send</button><div id="ao"></div>`;
  document.getElementById("ago").onclick=async()=>{const p=document.getElementById("ap").value;
   document.getElementById("ao").innerHTML="<p><i>working…</i></p>";
   const r=await api("/api/ai/request",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({prompt:p})});
   document.getElementById("ao").innerHTML=`<pre>${esc(JSON.stringify(r.data||r,null,1))}</pre>`;};return;}
 if(active==="network"){const j=await api("/api/network");const d=j.data;
  c.innerHTML=`<p>${esc(d.topology||"")}</p><p>Host: ${esc(d.host.address)} (${esc(d.host.kind)}) reachable: ${d.host.reachable}</p>`+table(d.devices);return;}
 if(active==="session"){const j=await api("/api/session");
  c.innerHTML=`<pre>${esc(JSON.stringify(j.data,null,1))}</pre><button id="rc">Reconnect</button><div id="rco"></div>`;
  document.getElementById("rc").onclick=async()=>{const r=await api("/api/reconnect",{method:"POST"});document.getElementById("rco").innerHTML=`<pre>${esc(JSON.stringify(r.data||r,null,1))}</pre>`;};return;}
 if(active==="settings"){const d=(await api("/api/device")).data;
  c.innerHTML=`<pre>${esc(JSON.stringify({host:d.host,port:d.port,remembered_keys:d.remembered_keys,system:d.system},null,1))}</pre>`;return;}
}
function tabs(){const n=document.getElementById("tabs");
 n.innerHTML=SECTIONS.map(([id,label])=>`<button data-s="${id}">${label}</button>`).join("");
 n.querySelectorAll("button").forEach(b=>b.onclick=()=>{active=b.dataset.s;
  document.getElementById("content").innerHTML="<p>loading…</p>";render();});}
tabs();refreshStatus();render();setInterval(async()=>{await refreshStatus();if(active==="dashboard")await render();},4000);
</script>
</body>
</html>
"""
