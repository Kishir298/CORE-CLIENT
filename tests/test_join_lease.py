"""Join name + 24h lease: generation, persistence, tracking, expiry, reconnect."""

import json

import pytest

from client.core_device_client import (
    CoreDeviceClient,
    DeviceClientError,
    generate_join_name,
)
from tests.fake_host import FakeCoreHost, wait_for_status

LEASE_SECONDS = 24 * 60 * 60


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


# -- join name generation --


def test_join_name_generated_default_format(tmp_path):
    client = _make_client(5000, tmp_path / "d.json", token=None)
    assert client.join_name == "MacBook-mac-01"


def test_join_name_explicit_override_kept(tmp_path):
    client = _make_client(5000, tmp_path / "d.json", token=None,
                          join_name="Field-Mac-7")
    assert client.join_name == "Field-Mac-7"


def test_join_name_sanitized_and_bounded(tmp_path):
    name = generate_join_name("Mac Book! Pro@", "mac-01")
    assert name == "Mac-Book-Pro-mac-01"
    assert len(generate_join_name("x" * 200, "y" * 200)) <= 64


def test_join_name_never_contains_token(tmp_path):
    client = _make_client(5000, tmp_path / "d.json", token="secret-mac-01")
    assert "secret-mac-01" not in client.join_name


# -- join name remembered (Option A: identity persists, token does not) --


def test_join_name_remembered_without_secrets(tmp_path):
    device_file = tmp_path / "device.json"
    client = _make_client(5000, device_file, token="secret-mac-01")
    client.save_remembered()
    stored = json.loads(device_file.read_text(encoding="utf-8"))
    assert stored["join_name"] == "MacBook-mac-01"
    assert "token" not in stored
    assert "credential" not in stored


def test_join_name_stable_across_client_restart(tmp_path):
    device_file = tmp_path / "device.json"
    client = _make_client(5000, device_file, token="secret-mac-01")
    client.save_remembered()
    client.shutdown()
    relaunched = CoreDeviceClient.load_remembered(device_file)
    assert relaunched.join_name == "MacBook-mac-01"
    assert relaunched.device_id == "mac-01"
    assert relaunched.identity_id == "mac-01"
    assert relaunched.is_logged_in is False


def test_join_name_sent_at_handshake_and_register(tmp_path):
    host = FakeCoreHost()
    try:
        client = _make_client(host.port, tmp_path / "d.json")
        client.connect()
        client.register()
        record = host.get("mac-01")
        assert record["join_name"] == "MacBook-mac-01"
        client.shutdown()
    finally:
        host.stop()


# -- lease awareness --


def test_successful_connection_receives_lease(tmp_path):
    host = FakeCoreHost()
    try:
        client = _make_client(host.port, tmp_path / "d.json")
        handshake = client.connect()
        assert handshake["payload"]["lease_duration_seconds"] == LEASE_SECONDS
        assert handshake["payload"]["connected_at"]
        assert handshake["payload"]["lease_expires_at"]
        assert client.connected_at == handshake["payload"]["connected_at"]
        assert client.lease_expires_at == handshake["payload"]["lease_expires_at"]
        assert client.lease_duration_seconds == LEASE_SECONDS
        client.register()
        assert client.lease_expires_at is not None
        client.shutdown()
    finally:
        host.stop()


def test_lease_cleared_on_logout_but_join_remains(tmp_path):
    host = FakeCoreHost()
    try:
        client = _make_client(host.port, tmp_path / "d.json")
        client.connect()
        client.register()
        assert client.lease_expires_at is not None
        client.logout()
        assert client.lease_expires_at is None
        assert client.connected_at is None
        assert client.lease_duration_seconds is None
        assert client.join_name == "MacBook-mac-01"
        assert client.device_id == "mac-01"
    finally:
        host.stop()


# -- host-forced disconnect (lease expiry) --


def test_forced_close_marks_client_disconnected(tmp_path):
    host = FakeCoreHost()
    try:
        client = _make_client(host.port, tmp_path / "d.json")
        client.connect()
        client.register()
        assert client.is_connected is True
        assert host.force_close("mac-01") is True
        wait_for_status(host, "mac-01", "offline")
        # Client detects the closure on next use and marks itself disconnected.
        with pytest.raises(DeviceClientError):
            client.discover()
        assert client.is_connected is False
        assert client.connection_id is None
        assert client.lease_expires_at is None
        # Remembered identity survives; app still running so token stays.
        assert client.device_id == "mac-01"
        assert client.identity_id == "mac-01"
        assert client.join_name == "MacBook-mac-01"
        assert client.is_logged_in is True
        client.shutdown()
    finally:
        host.stop()


def test_expired_connection_cannot_send_messages(tmp_path):
    host = FakeCoreHost()
    try:
        client = _make_client(host.port, tmp_path / "d.json")
        client.connect()
        client.register()
        assert host.force_close("mac-01") is True
        wait_for_status(host, "mac-01", "offline")
        with pytest.raises(DeviceClientError):
            client.discover()
        # Guarded state: must re-authenticate before app messages.
        assert client.is_connected is False
        with pytest.raises(DeviceClientError):
            client.discover()
        client.shutdown()
    finally:
        host.stop()


def test_reconnect_after_expiry_new_lease_same_identity(tmp_path):
    host = FakeCoreHost()
    try:
        client = _make_client(host.port, tmp_path / "d.json")
        client.connect()
        client.register()
        first_id = client.connection_id
        first_lease = client.lease_expires_at
        assert first_id and first_lease
        assert host.force_close("mac-01") is True
        wait_for_status(host, "mac-01", "offline")
        client.reconnect()
        assert host.get("mac-01")["status"] == "online"
        assert client.device_id == "mac-01"
        assert client.identity_id == "mac-01"
        assert client.join_name == "MacBook-mac-01"
        assert host.get("mac-01")["join_name"] == "MacBook-mac-01"
        assert client.connection_id is not None
        assert client.connection_id != first_id
        assert client.lease_expires_at is not None
        client.shutdown()
    finally:
        host.stop()


# -- CLI --


def test_cli_remember_saves_join_name(tmp_path):
    from client.core_device_client import main

    device_file = tmp_path / "device.json"
    assert main(["--remember", "--device-file", str(device_file),
                 "--device-id", "mac-01", "--device-name", "MacBook",
                 "--host", "127.0.0.1", "--port", "5000"]) == 0
    stored = json.loads(device_file.read_text(encoding="utf-8"))
    assert stored["join_name"] == "MacBook-mac-01"
    assert "token" not in stored


def test_cli_remember_explicit_join_name(tmp_path):
    from client.core_device_client import main

    device_file = tmp_path / "device.json"
    assert main(["--remember", "--device-file", str(device_file),
                 "--device-id", "mac-01", "--join-name", "Ops-Mac",
                 "--host", "127.0.0.1", "--port", "5000"]) == 0
    stored = json.loads(device_file.read_text(encoding="utf-8"))
    assert stored["join_name"] == "Ops-Mac"
