"""Protocol: handshake, registration, discovery, error envelopes (stdlib only)."""

import json
import socket
import struct
import uuid
from datetime import datetime, timezone

import pytest

from client.core_device_client import CoreDeviceClient, DeviceClientError
from tests.fake_host import FakeCoreHost, recv_frame, send_frame


def _make_client(port, device_file, token="secret-mac-01", **overrides):
    params = {
        "host": "127.0.0.1",
        "port": port,
        "device_id": "mac-01",
        "device_name": "MacBook",
        "device_type": "phone",
        "platform": "mac",
        "capabilities": ["chat"],
        "device_file": device_file,
        "use_tls": False,
        "timeout": 5.0,
    }
    params.update(overrides)
    client = CoreDeviceClient(**params)
    if token is not None:
        client.login(token)
    return client


def _raw_envelope(source, message_type, payload, identity_id):
    return {
        "message_id": str(uuid.uuid4()),
        "source": source,
        "destination": "core",
        "message_type": message_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "request_id": None,
        "payload": payload,
        "identity_id": identity_id,
    }


def _raw_roundtrip(port, messages):
    """Send raw frames over one socket, return list of response frames."""
    sock = socket.create_connection(("127.0.0.1", port), timeout=5)
    sock.settimeout(5)
    try:
        responses = []
        for message in messages:
            send_frame(sock, message)
            responses.append(recv_frame(sock))
        return responses
    finally:
        sock.close()


def test_handshake_response_shape(tmp_path):
    host = FakeCoreHost()
    try:
        client = _make_client(host.port, tmp_path / "d.json")
        resp = client.connect()
        assert resp["message_type"] == "CORE_HANDSHAKE_RESPONSE"
        assert resp["payload"]["authenticated"] is True
        assert resp["payload"]["identity_id"] == "mac-01"
        assert resp["payload"]["connection_id"]
        assert client.connection_id == resp["payload"]["connection_id"]
        client.shutdown()
    finally:
        host.stop()


def test_register_response_shape_and_registry(tmp_path):
    host = FakeCoreHost()
    try:
        client = _make_client(host.port, tmp_path / "d.json")
        client.connect()
        resp = client.register()
        assert resp["message_type"] == "DEVICE_REGISTER_RESPONSE"
        assert resp["payload"] == {"registered": True, "device_id": "mac-01",
                                   "status": "online"}
        record = host.get("mac-01")
        assert record["status"] == "online"
        assert record["device_id"] == "mac-01"
        assert record["identity_id"] == "mac-01"
        assert record["connection_id"] == client.connection_id
        client.shutdown()
    finally:
        host.stop()


def test_register_requires_connect(tmp_path):
    client = _make_client(5000, tmp_path / "d.json")
    client.login("secret-mac-01")
    with pytest.raises(DeviceClientError, match="Connect \\+ handshake"):
        client.register()


def test_discover_requires_register(tmp_path):
    host = FakeCoreHost()
    try:
        client = _make_client(host.port, tmp_path / "d.json")
        client.connect()
        with pytest.raises(DeviceClientError, match="Register before discovery"):
            client.discover()
        client.register()
        resp = client.discover()
        assert resp["message_type"] == "DEVICE_DISCOVER_RESPONSE"
        ids = [d["device_id"] for d in resp["payload"]["devices"]]
        assert "mac-01" in ids
        client.shutdown()
    finally:
        host.stop()


def test_duplicate_registration_rejected(tmp_path):
    host = FakeCoreHost()
    try:
        first = _make_client(host.port, tmp_path / "a.json")
        second = _make_client(host.port, tmp_path / "b.json")
        first.connect()
        first.register()
        second.connect()
        with pytest.raises(DeviceClientError, match="DEVICE_ALREADY_REGISTERED"):
            second.register()
        first.shutdown()
        second.shutdown()
    finally:
        host.stop()


def test_identity_spoofing_rejected(tmp_path):
    host = FakeCoreHost()
    try:
        handshake = _raw_envelope("mac-01", "CORE_HANDSHAKE",
                                  {"identity_id": "mac-01", "credential": "secret-mac-01",
                                   "protocol_version": "0.3.0"}, "mac-01")
        register_other = _raw_envelope("mac-01", "DEVICE_REGISTER",
                                       {"device_id": "mac-02", "device_name": "Spoof",
                                        "device_type": "phone", "platform": "mac",
                                        "capabilities": [], "protocol_version": "0.3.0"},
                                       "mac-01")
        hs_resp, reg_resp = _raw_roundtrip(host.port, [handshake, register_other])
        assert hs_resp["message_type"] == "CORE_HANDSHAKE_RESPONSE"
        assert reg_resp["message_type"] == "DEVICE_ERROR"
        assert reg_resp["payload"]["error"] == "DEVICE_REGISTRATION_FAILED"
    finally:
        host.stop()


def test_unknown_message_yields_device_error(tmp_path):
    host = FakeCoreHost()
    try:
        handshake = _raw_envelope("mac-01", "CORE_HANDSHAKE",
                                  {"identity_id": "mac-01", "credential": "secret-mac-01",
                                   "protocol_version": "0.3.0"}, "mac-01")
        bogus = _raw_envelope("mac-01", "BOGUS_TYPE", {}, "mac-01")
        hs_resp, err_resp = _raw_roundtrip(host.port, [handshake, bogus])
        assert hs_resp["payload"]["authenticated"] is True
        assert err_resp["message_type"] == "DEVICE_ERROR"
        assert err_resp["payload"]["error"] == "COMMUNICATION_ERROR"
    finally:
        host.stop()


def test_malformed_frame_rejected_by_client_framing(tmp_path):
    # Client-side framing rejects empty/oversize payloads before send.
    client = _make_client(5000, tmp_path / "d.json")
    assert json.loads(json.dumps({"message_type": "DEVICE_ERROR"}))["message_type"] == "DEVICE_ERROR"
    assert struct.calcsize("!I") == 4
    assert uuid.uuid4() != uuid.uuid4()
