"""Authentication: explicit login, memory-only token, rejection paths."""

import pytest

from client.core_device_client import CoreDeviceClient, DeviceClientError
from tests.fake_host import FakeCoreHost


def _make_client(host, port, device_file, token=None, **overrides):
    params = {
        "host": host,
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


def test_login_requires_nonempty_token(tmp_path):
    client = _make_client("127.0.0.1", 5000, tmp_path / "d.json")
    with pytest.raises(DeviceClientError, match="non-empty token"):
        client.login("")
    with pytest.raises(DeviceClientError, match="non-empty token"):
        client.login("   ")
    assert client.is_logged_in is False


def test_connect_requires_login(tmp_path):
    client = _make_client("127.0.0.1", 5000, tmp_path / "d.json")
    with pytest.raises(DeviceClientError, match="Login required"):
        client.connect()
    with pytest.raises(DeviceClientError, match="Login required"):
        client.reconnect()


def test_login_session_cleared_on_shutdown(tmp_path):
    device_file = tmp_path / "device.json"
    client = _make_client("127.0.0.1", 5000, device_file, token="secret-mac-01")
    assert client.is_logged_in is True
    client.save_remembered()
    client.shutdown()
    assert client.is_logged_in is False
    assert client.connection_id is None
    relaunched = CoreDeviceClient.load_remembered(device_file)
    assert relaunched.device_id == "mac-01"
    assert relaunched.is_logged_in is False
    with pytest.raises(DeviceClientError, match="Login required"):
        relaunched.connect()


def test_logout_preserves_remembered_device(tmp_path):
    device_file = tmp_path / "device.json"
    client = _make_client("127.0.0.1", 5000, device_file, token="secret-mac-01")
    before = client.remembered_state()
    client.logout()
    assert client.is_logged_in is False
    assert client.remembered_state() == before


def test_wrong_token_rejected_stays_unregistered(tmp_path):
    host = FakeCoreHost()
    try:
        client = _make_client("127.0.0.1", host.port, tmp_path / "d.json", token="wrong-token")
        with pytest.raises(DeviceClientError, match="Authentication rejected"):
            client.connect()
        assert host.has("mac-01") is False
        client.shutdown()
    finally:
        host.stop()


def test_correct_token_authenticates(tmp_path):
    host = FakeCoreHost()
    try:
        client = _make_client("127.0.0.1", host.port, tmp_path / "d.json",
                              token="secret-mac-01")
        resp = client.connect()
        assert resp["payload"]["authenticated"] is True
        assert client.connection_id
        client.shutdown()
    finally:
        host.stop()
