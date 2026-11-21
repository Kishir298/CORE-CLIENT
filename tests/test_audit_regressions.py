"""Client regression checks independent of local socket permissions."""
import io
import json
import socket
import struct
from email.message import Message as Headers
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import client.core_device_client as wire
from client.core_device_client import CoreDeviceClient, DeviceClientError
from client.portal.server import INDEX_HTML, MAX_BODY_BYTES, ClientPortal


def client():
    return CoreDeviceClient("127.0.0.1", 5000, "alice", use_tls=False)


def invoke(portal, method, path, body=None, headers=None, raw_body=None):
    cls = portal._make_handler()
    handler = cls.__new__(cls)
    handler.path, handler.command = path, method
    handler.request_version = "HTTP/1.1"
    handler.requestline = method + " " + path + " HTTP/1.1"
    handler.headers = Headers()
    raw = raw_body if raw_body is not None else json.dumps(body if body is not None else {}).encode()
    values = {"Host": "127.0.0.1:8766", "Content-Type": "application/json",
              "X-Core-Request": "true", "Content-Length": str(len(raw))}
    values.update(headers or {})
    for key, value in values.items():
        if value is not None: handler.headers[key] = value
    handler.rfile, handler.wfile = io.BytesIO(raw), io.BytesIO()
    getattr(handler, "do_" + method)()
    return handler.wfile.getvalue()


def status_of(raw):
    return int(raw.split(b"\r\n", 1)[0].split()[1])


def origin_handler(origin, *, host="127.0.0.1:8766", content_type="application/json",
                   csrf="true"):
    headers = {"Host": host, "Content-Type": content_type}
    if origin is not None:
        headers["Origin"] = origin
    if csrf is not None:
        headers["X-Core-Request"] = csrf
    return SimpleNamespace(headers=headers)


def test_client_portal_refuses_remote_bind():
    portal = ClientPortal(lambda: None, host="0.0.0.0")
    with patch("client.portal.server.ThreadingHTTPServer") as server:
        with pytest.raises(RuntimeError): portal.start()
    server.assert_not_called()


def test_get_rejects_rebinding_host():
    portal = ClientPortal(lambda: None)
    assert b"403" in invoke(portal, "GET", "/", headers={"Host": "evil.example:8766"}).split(b"\r\n", 1)[0]


def test_dashboard_helper_sets_csrf_header():
    assert '"X-Core-Request"' in INDEX_HTML


def test_mutation_rejects_different_port():
    portal = ClientPortal(lambda: None)
    handler = SimpleNamespace(headers={"Content-Type": "application/json", "Host": "127.0.0.1:8766",
                                       "Origin": "http://127.0.0.1:9000", "X-Core-Request": "true"})
    with pytest.raises(ValueError): portal._check_portal_origin(handler)


def test_record_get_uses_host_contract():
    c = client()
    c._authenticated, c._registered, c._sock = True, True, object()
    portal = ClientPortal(lambda: c)
    with patch.object(c, "data_request", return_value={"payload": {}}) as call:
        portal.api_rescs(operation="record_get", namespace="notes", key="a")
    assert call.call_args.args == ("record_get", {"namespace": "notes", "key": "a"})


def test_tls_setup_failure_closes_raw_socket():
    c = CoreDeviceClient("127.0.0.1", 5000, "alice")
    c.login("secret")
    raw = SimpleNamespace(settimeout=lambda _: None)
    closed = []
    raw.close = lambda: closed.append(True)
    with patch.object(wire.socket, "create_connection", return_value=raw), patch.object(c, "_build_tls_context", side_effect=ValueError("bad CA")):
        with pytest.raises(DeviceClientError): c.connect()
    assert closed


def test_partial_frame_survives_read_timeout():
    message = {"message_type": "DATA_RESPONSE", "payload": {"ok": True}}
    body = json.dumps(message).encode()
    header = struct.pack("!I", len(body))
    chunks = [header[:2], socket.timeout(), header[2:], body[:3], body[3:]]
    class Sock:
        def gettimeout(self): return 2.0
        def settimeout(self, timeout): pass
        def recv(self, size):
            item = chunks.pop(0)
            if isinstance(item, Exception): raise item
            return item
    assert wire._recv_frame(Sock()) == message


def test_oversized_or_invalid_frame_clears_connection():
    c = client()
    class Sock:
        def gettimeout(self): return 1.0
        def settimeout(self, value): pass
        def sendall(self, value): pass
        def shutdown(self, value): pass
        def close(self): pass
        def recv(self, size): return struct.pack("!I", wire.MAX_FRAME_SIZE + 1)
    c._sock, c._authenticated, c._registered = Sock(), True, True
    with pytest.raises(DeviceClientError): c.request("core", "DEVICE_DISCOVER")
    assert not c.is_connected


def test_bad_remembered_values_are_client_errors(tmp_path):
    path = tmp_path / "device.json"
    path.write_text(json.dumps({"device_id": "a", "host": "localhost", "port": "bad"}))
    with pytest.raises(DeviceClientError): CoreDeviceClient.load_remembered(path)


def test_untagged_model_does_not_match_arbitrary_version():
    from client.models import find_model
    assert find_model([{"name": "model:old"}], "model") is None
    assert find_model([{"name": "model:latest"}], "model") == {"name": "model:latest"}


def test_ai_offload_includes_device_identity():
    c = client()
    c._authenticated, c._registered, c._sock = True, True, object()
    portal = ClientPortal(lambda: c)
    with patch.object(portal, "_ai_state", return_value={"local": {"state": "UNAVAILABLE"}, "offload": {"available": True}}), patch.object(c, "service_request", return_value={"payload": {"success": True}}) as call:
        portal.ai_request("hello")
    assert call.call_args.args[2] == {"device_id": "alice", "prompt": "hello"}


def test_ai_offload_reports_service_failure():
    c = client()
    c._authenticated, c._registered, c._sock = True, True, object()
    portal = ClientPortal(lambda: c)
    with patch.object(portal, "_ai_state", return_value={"local": {"state": "UNAVAILABLE"}, "offload": {"available": True}}), patch.object(c, "service_request", return_value={"payload": {"success": False, "error": "permission denied"}}):
        with pytest.raises(ConnectionError, match="permission denied"): portal.ai_request("hello")


@pytest.mark.parametrize("authority", [
    "127.0.0.1:8766",
    "localhost:8766",
    "[::1]:8766",
])
def test_portal_accepts_loopback_host_authorities(authority):
    portal = ClientPortal(lambda: None)
    assert status_of(invoke(portal, "GET", "/", headers={"Host": authority})) == 200


@pytest.mark.parametrize("authority", [
    "evil.example:8766",
    "user:pass@127.0.0.1:8766",
    "127.0.0.1:8766/extra",
    "127.0.0.1:8766?query=1",
    "127.0.0.1:8766#fragment",
    "127.0.0.1:9999",
    "0.0.0.0:8766",
])
def test_portal_rejects_foreign_or_malformed_host(authority):
    portal = ClientPortal(lambda: None)
    assert status_of(invoke(portal, "GET", "/", headers={"Host": authority})) == 403


@pytest.mark.parametrize("origin", [
    "https://127.0.0.1:8766",
    "http://evil.example:8766",
    "http://127.0.0.1:9999",
    "http://user:pass@127.0.0.1:8766",
    "http://127.0.0.1:8766/extra",
    "http://127.0.0.1:8766?query=1",
    "http://127.0.0.1:8766#fragment",
])
def test_mutation_rejects_foreign_or_malformed_origin(origin):
    portal = ClientPortal(lambda: None)
    with pytest.raises(ValueError):
        portal._check_portal_origin(origin_handler(origin))


@pytest.mark.parametrize("origin", [None, "http://127.0.0.1:8766"])
def test_mutation_allows_same_origin_or_header_only_requests(origin):
    portal = ClientPortal(lambda: None)
    portal._check_portal_origin(origin_handler(origin))


def test_mutation_requires_csrf_header():
    portal = ClientPortal(lambda: None)
    with pytest.raises(ValueError, match="X-Core-Request"):
        portal._check_portal_origin(origin_handler(None, csrf=None))


@pytest.mark.parametrize("content_type", ["text/plain", "application/xml", ""])
def test_mutation_rejects_non_json_content_type(content_type):
    portal = ClientPortal(lambda: None)
    with pytest.raises(ValueError, match="Content-Type"):
        portal._check_portal_origin(origin_handler(None, content_type=content_type))


@pytest.mark.parametrize("content_type", ["application/json; charset=utf-8",
                                          "Application/JSON"])
def test_mutation_accepts_json_content_type_with_parameters(content_type):
    portal = ClientPortal(lambda: None)
    portal._check_portal_origin(origin_handler(None, content_type=content_type))


@pytest.mark.parametrize("raw_body", [b"[]", b'"text"', b"null", b"{", b""])
def test_mutation_rejects_non_object_or_malformed_json(raw_body):
    portal = ClientPortal(lambda: None)
    result = invoke(portal, "POST", "/api/location", raw_body=raw_body)
    assert status_of(result) == 400


@pytest.mark.parametrize("length", ["0", "-1", "not-a-number",
                                    str(MAX_BODY_BYTES + 1)])
def test_mutation_rejects_invalid_body_length(length):
    portal = ClientPortal(lambda: None)
    result = invoke(portal, "POST", "/api/location", raw_body=b"{}",
                    headers={"Content-Length": length})
    assert status_of(result) == 400


def test_mutation_accepts_valid_json_with_charset_parameter():
    portal = ClientPortal(lambda: None)
    result = invoke(
        portal, "POST", "/api/location",
        body={"location": {"latitude": 25.2, "longitude": 55.3}},
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    assert status_of(result) == 200
