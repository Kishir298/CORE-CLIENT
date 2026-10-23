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
from client.portal.server import ClientPortal, INDEX_HTML


def client():
    return CoreDeviceClient("127.0.0.1", 5000, "alice", use_tls=False)


def invoke(portal, method, path, body=None, headers=None):
    cls = portal._make_handler()
    handler = cls.__new__(cls)
    handler.path, handler.command = path, method
    handler.request_version = "HTTP/1.1"
    handler.requestline = method + " " + path + " HTTP/1.1"
    handler.headers = Headers()
    raw = json.dumps(body or {}).encode()
    values = {"Host": "127.0.0.1:8766", "Content-Type": "application/json",
              "X-Core-Request": "true", "Content-Length": str(len(raw))}
    values.update(headers or {})
    for key, value in values.items():
        if value is not None: handler.headers[key] = value
    handler.rfile, handler.wfile = io.BytesIO(raw), io.BytesIO()
    getattr(handler, "do_" + method)()
    return handler.wfile.getvalue()


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
    chunks = [struct.pack("!I", len(body))[:2], socket.timeout(), struct.pack("!I", len(body))[2:], body]
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
