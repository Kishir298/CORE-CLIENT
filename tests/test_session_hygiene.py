"""Session hygiene regressions: stale state, request_id, relay, file parsing."""

from __future__ import annotations

import json

import pytest

from client.core_device_client import (
    _recv_frame,
    CoreDeviceClient,
    DeviceClientError,
)

from .fake_host import FakeCoreHost


def _make_client(port, device_file, token="secret-mac-01", **kw):
    params = {
        "host": "127.0.0.1",
        "port": port,
        "device_id": "mac-01",
        "device_name": "MacBook",
        "device_file": device_file,
        "use_tls": False,
        "timeout": 5.0,
    }
    params.update(kw)
    client = CoreDeviceClient(**params)
    if token is not None:
        client.login(token)
    return client


def test_failed_connect_clears_stale_session(tmp_path):
    host = FakeCoreHost()
    try:
        client = _make_client(host.port, tmp_path / "d.json")
        client.connect()
        client.register()
        assert client.session_token is not None
        # Poison the credential, then reconnect: the handshake is rejected
        # and no stale token/connection/lease may survive.
        client.login("wrong-token")
        with pytest.raises(DeviceClientError):
            client.connect()
        assert client.session_token is None
        assert client.connection_id is None
        assert client.is_connected is False
        assert client.lease_state == "DISCONNECTED"
        # The (wrong) login credential itself is untouched.
        assert client.is_logged_in is True
        client.shutdown()
    finally:
        host.stop()


def test_logout_closes_socket_and_clears(tmp_path):
    host = FakeCoreHost()
    try:
        client = _make_client(host.port, tmp_path / "d.json")
        client.connect()
        client.register()
        client.logout()
        assert client._sock is None
        assert client.session_token is None
        assert client.is_connected is False
        assert client.is_logged_in is False
        client.shutdown()
    finally:
        host.stop()


def test_close_socket_clears_session_token(tmp_path):
    host = FakeCoreHost()
    try:
        client = _make_client(host.port, tmp_path / "d.json")
        client.connect()
        client.register()
        assert client.session_token is not None
        client.close_socket()
        assert client.session_token is None
        assert client.connection_id is None
        assert client.is_connected is False
        # Credential preserved: reconnect still possible while app is open.
        client.reconnect()
        assert client.is_connected is True
        client.shutdown()
    finally:
        host.stop()


def test_request_id_mismatch_drops_connection(tmp_path, monkeypatch):
    import client.core_device_client as mod

    host = FakeCoreHost()
    try:
        client = _make_client(host.port, tmp_path / "d.json")
        client.connect()
        client.register()
        real_recv = mod._recv_frame

        def _tampered(sock):
            resp = real_recv(sock)
            resp["request_id"] = "not-ours"
            return resp

        monkeypatch.setattr(mod, "_recv_frame", _tampered)
        with pytest.raises(DeviceClientError, match="request_id mismatch"):
            client.device_info("mac-01")
        assert client.is_connected is False
        assert client.session_token is None
        client.shutdown()
    finally:
        host.stop()


def test_send_to_device_fire_and_forget(tmp_path):
    host = FakeCoreHost(peers={"mac-02": "secret-mac-02"})
    sender = _make_client(host.port, tmp_path / "a.json")
    peer = _make_client(
        host.port, tmp_path / "b.json", token="secret-mac-02",
        device_id="mac-02", device_name="MacMini",
    )
    try:
        sender.connect()
        sender.register()
        peer.connect()
        peer.register()
        ack = sender.send_to_device("mac-02", "APP_PING", {"text": "hi"})
        assert ack["dispatched"] is True
        assert ack["destination"] == "mac-02"
        peer._sock.settimeout(5.0)
        forwarded = _recv_frame(peer._sock)
        assert forwarded["payload"]["text"] == "hi"
        assert "_session_token" not in forwarded["payload"]
        # Error path with confirmation still raises.
        with pytest.raises(DeviceClientError, match="DEVICE_NOT_FOUND"):
            sender.send_to_device(
                "ghost-99", "APP_PING", {"text": "hi"}, wait_reply=True
            )
    finally:
        sender.shutdown()
        peer.shutdown()
        host.stop()


def test_load_remembered_rejects_unknown_fields(tmp_path):
    path = tmp_path / "d.json"
    path.write_text(
        json.dumps({"device_id": "mac-01", "evil": 1}), encoding="utf-8"
    )
    with pytest.raises(DeviceClientError, match="unknown fields"):
        CoreDeviceClient.load_remembered(path)


def test_load_remembered_rejects_corrupt_json(tmp_path):
    path = tmp_path / "d.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(DeviceClientError, match="corrupt"):
        CoreDeviceClient.load_remembered(path)


def test_load_remembered_rejects_missing_required_fields(tmp_path):
    path = tmp_path / "d.json"
    path.write_text(json.dumps({"device_id": "mac-01"}), encoding="utf-8")
    with pytest.raises(DeviceClientError, match="required fields"):
        CoreDeviceClient.load_remembered(path)


def test_load_remembered_strips_session_token_hyphen(tmp_path):
    path = tmp_path / "d.json"
    path.write_text(
        json.dumps(
            {
                "device_id": "mac-01",
                "host": "127.0.0.1",
                "port": 5000,
                "session-token": "s3cr3t",
            }
        ),
        encoding="utf-8",
    )
    client = CoreDeviceClient.load_remembered(path)
    assert client.session_token is None
    assert client.device_id == "mac-01"
