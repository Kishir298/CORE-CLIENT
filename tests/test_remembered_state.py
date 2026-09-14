"""Remembered-device state: persists without secrets (Option A)."""

import json

import pytest

from client.core_device_client import CoreDeviceClient, DeviceClientError

EPHEMERAL_KEYS = (
    "token",
    "credential",
    "password",
    "api_token",
    "session",
    "session_token",
    "connection_id",
    "authenticated",
)


def _make_client(device_file, **overrides):
    params = {
        "host": "127.0.0.1",
        "port": 5000,
        "device_id": "mac-01",
        "device_name": "MacBook",
        "device_type": "phone",
        "platform": "mac",
        "capabilities": ["chat"],
        "device_file": device_file,
    }
    params.update(overrides)
    return CoreDeviceClient(**params)


def test_remembered_device_persists_without_secrets(tmp_path):
    device_file = tmp_path / "device.json"
    client = _make_client(device_file)
    client.login("secret-mac-01")
    client.save_remembered()
    stored = json.loads(device_file.read_text(encoding="utf-8"))
    assert stored["device_id"] == "mac-01"
    assert stored["identity_id"] == "mac-01"
    assert stored["host"] == "127.0.0.1"
    assert stored["port"] == 5000
    for key in EPHEMERAL_KEYS:
        assert key not in stored


def test_remembered_state_shape(tmp_path):
    client = _make_client(tmp_path / "device.json")
    state = client.remembered_state()
    assert set(state) == {
        "device_id", "identity_id", "join_name", "device_name", "device_type",
        "platform", "capabilities", "protocol_version", "host", "port",
    }
    assert state["identity_id"] == state["device_id"]
    assert state["join_name"] == "MacBook-mac-01"


def test_load_roundtrip_preserves_device(tmp_path):
    device_file = tmp_path / "device.json"
    client = _make_client(device_file)
    client.save_remembered()
    relaunched = CoreDeviceClient.load_remembered(device_file)
    assert relaunched.remembered_state() == client.remembered_state()
    assert relaunched.is_logged_in is False


def test_load_missing_file_raises(tmp_path):
    with pytest.raises(DeviceClientError, match="No remembered device"):
        CoreDeviceClient.load_remembered(tmp_path / "missing.json")


def test_load_strips_injected_secrets(tmp_path):
    device_file = tmp_path / "device.json"
    _make_client(device_file).save_remembered()
    stored = json.loads(device_file.read_text(encoding="utf-8"))
    stored["token"] = "injected"
    stored["connection_id"] = "injected-conn"
    stored["authenticated"] = True
    device_file.write_text(json.dumps(stored), encoding="utf-8")
    relaunched = CoreDeviceClient.load_remembered(device_file)
    assert relaunched.is_logged_in is False
    assert relaunched.connection_id is None
    with pytest.raises(DeviceClientError, match="Login required"):
        relaunched.connect()


def test_identity_mismatch_raises(tmp_path):
    device_file = tmp_path / "device.json"
    _make_client(device_file).save_remembered()
    stored = json.loads(device_file.read_text(encoding="utf-8"))
    stored["identity_id"] = "someone-else"
    device_file.write_text(json.dumps(stored), encoding="utf-8")
    with pytest.raises(DeviceClientError, match="identity mismatch"):
        CoreDeviceClient.load_remembered(device_file)
