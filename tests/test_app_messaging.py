"""Application messaging: generic request() over the authenticated session.

Covers device_info / data_request / service_request / send_to_device,
request_id correlation, timeout, session-token gating, host errors,
malformed/oversized payloads and lifecycle shutdown. Stdlib only.
"""

from __future__ import annotations

import socket

import pytest

from client.core_device_client import (
    _new_message,
    _recv_frame,
    _send_frame,
    CoreDeviceClient,
    DeviceClientError,
)

from .fake_host import FakeCoreHost


def _make_client(port, device_file, **kw):
    client = CoreDeviceClient(
        host="127.0.0.1",
        port=port,
        device_id="mac-01",
        device_name="MacBook",
        device_file=device_file,
        use_tls=False,
        **kw,
    )
    client.login("secret-mac-01")
    return client


def _connected(tmp_path, **kw):
    host = FakeCoreHost()
    client = _make_client(host.port, tmp_path / "d.json", **kw)
    client.connect()
    client.register()
    return host, client


def test_device_info_roundtrip(tmp_path):
    host, client = _connected(tmp_path)
    try:
        resp = client.device_info("mac-01")
        assert resp["message_type"] == "DEVICE_INFO_RESPONSE"
        assert resp["payload"]["device"]["device_id"] == "mac-01"
    finally:
        client.shutdown()
        host.stop()


def test_device_info_unknown_device(tmp_path):
    host, client = _connected(tmp_path)
    try:
        with pytest.raises(DeviceClientError, match="DEVICE_NOT_FOUND"):
            client.device_info("ghost-99")
    finally:
        client.shutdown()
        host.stop()


def test_device_info_requires_register(tmp_path):
    host = FakeCoreHost()
    try:
        client = _make_client(host.port, tmp_path / "d.json")
        client.connect()
        with pytest.raises(DeviceClientError, match="Register before"):
            client.device_info("mac-01")
        client.shutdown()
    finally:
        host.stop()


def test_service_request_roundtrip(tmp_path):
    host, client = _connected(tmp_path)
    try:
        resp = client.service_request("health", "status", {"verbose": True})
        assert resp["message_type"] == "SERVICE_RESPONSE"
        assert resp["payload"]["service_id"] == "health"
        assert resp["payload"]["operation"] == "status"
        assert resp["payload"]["success"] is True
    finally:
        client.shutdown()
        host.stop()


def test_service_request_validation(tmp_path):
    host, client = _connected(tmp_path)
    try:
        with pytest.raises(DeviceClientError, match="service_id must not be empty"):
            client.service_request("", "status")
        with pytest.raises(DeviceClientError, match="operation must not be empty"):
            client.service_request("health", "")
    finally:
        client.shutdown()
        host.stop()


def test_data_request_roundtrip(tmp_path):
    host, client = _connected(tmp_path)
    try:
        resp = client.data_request("record_list", {"namespace": "notes", "limit": 5})
        assert resp["message_type"] == "DATA_RESPONSE"
        assert resp["payload"]["request_type"] == "record_list"
    finally:
        client.shutdown()
        host.stop()


def test_data_request_missing_type_rejected(tmp_path):
    host, client = _connected(tmp_path)
    try:
        with pytest.raises(DeviceClientError, match="INVALID_DATA_REQUEST"):
            client.request("core", "DATA_REQUEST", {})
    finally:
        client.shutdown()
        host.stop()


def test_generic_request_ids_and_envelope(tmp_path):
    host, client = _connected(tmp_path)
    try:
        first = client.request("core", "DEVICE_DISCOVER", {})
        second = client.request("core", "DEVICE_DISCOVER", {})
        assert first["message_type"] == "DEVICE_DISCOVER_RESPONSE"
        assert first["request_id"] != second["request_id"]
    finally:
        client.shutdown()
        host.stop()


def test_request_invalid_args_rejected(tmp_path):
    host, client = _connected(tmp_path)
    try:
        with pytest.raises(DeviceClientError, match="destination must not be empty"):
            client.request("", "DEVICE_DISCOVER")
        with pytest.raises(DeviceClientError, match="message_type must not be empty"):
            client.request("core", "")
        with pytest.raises(DeviceClientError, match="timeout must be positive"):
            client.request("core", "DEVICE_DISCOVER", timeout=0)
    finally:
        client.shutdown()
        host.stop()


def test_request_host_error_surfaced(tmp_path):
    host, client = _connected(tmp_path)
    try:
        with pytest.raises(DeviceClientError, match="Host error"):
            client.request("core", "BOGUS_TYPE", {})
    finally:
        client.shutdown()
        host.stop()


def test_request_after_close_marks_disconnected(tmp_path):
    host, client = _connected(tmp_path)
    try:
        host.force_close("mac-01")
        with pytest.raises(DeviceClientError):
            client.request("core", "DEVICE_DISCOVER", {})
        assert client.lease_state == "DISCONNECTED"
    finally:
        client.shutdown()
        host.stop()


def test_request_requires_reconnect_after_shutdown(tmp_path):
    host, client = _connected(tmp_path)
    try:
        client.shutdown()
        with pytest.raises(DeviceClientError, match="Register before"):
            client.request("core", "DEVICE_DISCOVER", {})
    finally:
        host.stop()


def _peer_client(port, device_file, device_id, name, token):
    client = CoreDeviceClient(
        host="127.0.0.1",
        port=port,
        device_id=device_id,
        device_name=name,
        device_file=device_file,
        use_tls=False,
    )
    client.login(token)
    return client


def test_device_to_device_relay_strips_session_token(tmp_path):
    host = FakeCoreHost(peers={"mac-02": "secret-mac-02"})
    sender = _peer_client(host.port, tmp_path / "a.json", "mac-01", "MacBook", "secret-mac-01")
    peer = _peer_client(host.port, tmp_path / "b.json", "mac-02", "MacMini", "secret-mac-02")
    try:
        sender.connect()
        sender.register()
        peer.connect()
        peer.register()
        _send_frame(
            sender._sock,
            _new_message(
                source="mac-01",
                destination="mac-02",
                message_type="APP_PING",
                payload=sender._authed_payload({"text": "hello peer"}),
                identity_id="mac-01",
            ),
        )
        peer._sock.settimeout(5.0)
        forwarded = _recv_frame(peer._sock)
        assert forwarded["source"] == "mac-01"
        assert forwarded["destination"] == "mac-02"
        assert forwarded["identity_id"] == "mac-01"
        assert forwarded["payload"]["text"] == "hello peer"
        assert "_session_token" not in forwarded["payload"]
        # One-way routing: the sender gets no reply envelope.
        sender._sock.settimeout(0.5)
        with pytest.raises(socket.timeout):
            _recv_frame(sender._sock)
    finally:
        sender.shutdown()
        peer.shutdown()
        host.stop()


def test_device_to_device_unknown_device_rejected(tmp_path):
    host, client = _connected(tmp_path)
    try:
        with pytest.raises(DeviceClientError, match="DEVICE_NOT_FOUND"):
            client.send_to_device("ghost-99", "APP_PING", {"text": "hi"})
    finally:
        client.shutdown()
        host.stop()
