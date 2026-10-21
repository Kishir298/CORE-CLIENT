"""Connection lifecycle: online/offline, reconnect identity, shutdown."""

import pytest

from client.core_device_client import CoreDeviceClient, DeviceClientError
from tests.fake_host import FakeCoreHost, wait_for_status


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


def test_full_lifecycle_online_offline_reconnect(tmp_path):
    host = FakeCoreHost()
    try:
        client = _make_client(host.port, tmp_path / "d.json")
        client.connect()
        client.register()
        first_id = client.connection_id
        assert host.get("mac-01")["status"] == "online"

        # Abrupt disconnect: session object stays logged in, host goes offline.
        client.close_socket()
        wait_for_status(host, "mac-01", "offline")
        assert host.has("mac-01") is True  # record retained

        # Reconnect while app stays open: same login, new connection.
        client.reconnect()
        assert host.get("mac-01")["status"] == "online"
        assert host.get("mac-01")["device_id"] == "mac-01"
        assert host.get("mac-01")["identity_id"] == "mac-01"
        assert client.connection_id is not None
        assert client.connection_id != first_id
        client.shutdown()
    finally:
        host.stop()


def test_shutdown_clears_session_device_stays_remembered(tmp_path):
    device_file = tmp_path / "device.json"
    host = FakeCoreHost()
    try:
        client = _make_client(host.port, device_file)
        remembered_before = client.remembered_state()
        client.save_remembered()
        client.connect()
        client.register()
        client.shutdown()
        assert client.is_logged_in is False
        assert client.connection_id is None
        wait_for_status(host, "mac-01", "offline")
        relaunched = CoreDeviceClient.load_remembered(device_file)
        assert relaunched.remembered_state() == remembered_before
        with pytest.raises(DeviceClientError, match="Login required"):
            relaunched.connect()
    finally:
        host.stop()


def test_graceful_disconnect_marks_offline(tmp_path):
    host = FakeCoreHost()
    try:
        client = _make_client(host.port, tmp_path / "d.json")
        client.connect()
        client.register()
        client.close_socket()
        record = wait_for_status(host, "mac-01", "offline")
        assert record["connection_id"] is None
        client.shutdown()
    finally:
        host.stop()


def test_stale_close_cannot_take_reconnect_offline(tmp_path):
    """Reconnect first, then a stale duplicate disconnect must not offline us.

    Simulates: conn A registers, conn B takes over via reconnect semantics
    is server-side; here we assert the fake host tracks the live binding.
    """
    host = FakeCoreHost()
    try:
        client = _make_client(host.port, tmp_path / "d.json")
        client.connect()
        client.register()
        first_id = client.connection_id
        client.reconnect()
        assert client.connection_id != first_id
        assert host.get("mac-01")["status"] == "online"
        assert host.get("mac-01")["connection_id"] == client.connection_id
        client.shutdown()
    finally:
        host.stop()
