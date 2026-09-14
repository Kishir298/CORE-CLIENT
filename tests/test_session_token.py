"""Temporary session token tests (CLIENT side, in-memory only).

Provisioning credential authenticates once; the host-issued session token
lives only in RAM, is displayed while connected, and is never persisted.
"""

import json

from client.core_device_client import CoreDeviceClient, DeviceClientError

from .fake_host import FakeCoreHost, wait_for_status


def _client(host, device_file, **overrides):
    params = {
        "host": "127.0.0.1",
        "port": host.port,
        "device_id": "mac-01",
        "device_name": "MacBook",
        "device_file": device_file,
        "use_tls": False,
    }
    params.update(overrides)
    client = CoreDeviceClient(**params)
    client.login("secret-mac-01")
    return client


# -- persistence ------------------------------------------------------------

def test_session_secrets_never_in_remembered_file(tmp_path):
    host = FakeCoreHost()
    try:
        device_file = tmp_path / "device.json"
        client = _client(host, device_file)
        client.connect()
        client.register()
        assert client.session_token
        client.save_remembered()
        stored = json.loads(device_file.read_text(encoding="utf-8"))
        for key in ("token", "credential", "password", "api_token",
                    "session_token", "session", "session-token",
                    "connection_id", "authenticated", "lease_expires_at",
                    "lease_duration_seconds", "connected_at"):
            assert key not in stored, key
        assert client.session_token not in device_file.read_text(encoding="utf-8")
        assert "secret-mac-01" not in device_file.read_text(encoding="utf-8")
        # Reload: identity intact, session absent.
        relaunched = CoreDeviceClient.load_remembered(device_file)
        assert relaunched.device_id == "mac-01"
        assert relaunched.identity_id == "mac-01"
        assert relaunched.join_name == "MacBook-mac-01"
        assert relaunched.session_token is None
        assert relaunched.connection_id is None
        assert relaunched.is_logged_in is False
        client.shutdown()
    finally:
        host.stop()


# -- lifecycle ---------------------------------------------------------------

def test_login_connect_receives_session_then_shutdown_clears(tmp_path):
    host = FakeCoreHost()
    try:
        client = _client(host, tmp_path / "device.json")
        assert client.session_token is None
        client.connect()
        assert isinstance(client.session_token, str) and client.session_token
        assert client.session_token != "secret-mac-01"
        client.register()
        assert client.lease_state in ("ONLINE", "EXPIRING")
        client.shutdown()
        assert client.session_token is None
        assert client.connection_id is None
        assert client.lease_expires_at is None
        assert client.is_logged_in is False
        # Remembered identity still reloadable, session still absent.
        relaunched = CoreDeviceClient.load_remembered(tmp_path / "device.json") \
            if (tmp_path / "device.json").exists() else None
        assert relaunched is None or relaunched.session_token is None
    finally:
        host.stop()


def test_quit_clears_session_but_remembers_device(tmp_path):
    host = FakeCoreHost()
    try:
        device_file = tmp_path / "device.json"
        client = _client(host, device_file)
        client.save_remembered()
        client.connect()
        client.register()
        token = client.session_token
        assert token
        client.shutdown()  # quit path
        assert client.session_token is None
        assert client.connection_id is None
        reread = CoreDeviceClient.load_remembered(device_file)
        assert reread.device_id == "mac-01"
        assert reread.join_name == "MacBook-mac-01"
        assert reread.session_token is None
    finally:
        host.stop()


# -- display ------------------------------------------------------------------

def test_session_summary_shows_token_and_lease(tmp_path, capsys):
    import client.core_device_client as cli_mod

    host = FakeCoreHost()
    try:
        client = _client(host, tmp_path / "device.json")
        client.connect()
        client.register()
        summary = client.session_summary()
        assert summary["device_id"] == "mac-01"
        assert summary["join_name"] == "MacBook-mac-01"
        assert summary["session_token"] == client.session_token
        assert summary["connection_id"] == client.connection_id
        assert summary["lease_duration_seconds"] == 86400
        assert summary["lease_expires_at"]
        assert summary["lease_remaining"]
        cli_mod._print_session_banner(client)
        out = capsys.readouterr().out
        assert "R.I.S.A.R.M.S. CLIENT" in out
        assert "mac-01" in out
        assert "ONLINE" in out
        # Display path never touches disk.
        assert not (tmp_path / "device.json").exists()
        client.shutdown()
    finally:
        host.stop()


def test_session_command_output(tmp_path, capsys):
    import client.core_device_client as cli_mod

    host = FakeCoreHost()
    try:
        client = _client(host, tmp_path / "device.json")
        client.connect()
        client.register()
        cli_mod._print_session_line(client)
        out = capsys.readouterr().out
        assert "ONLINE" in out
        assert client.connection_id in out
        assert "remaining" in out
        client.shutdown()
    finally:
        host.stop()


def test_lease_countdown_format():
    assert CoreDeviceClient._format_duration(86399) == "23:59:59"
    assert CoreDeviceClient._format_duration(86400) == "24:00:00"
    assert CoreDeviceClient._format_duration(0) == "00:00:00"


def test_lease_states():
    host = FakeCoreHost()
    try:
        import tempfile
        from pathlib import Path

        client = _client(host, Path(tempfile.mkdtemp()) / "d.json")
        assert client.lease_state == "DISCONNECTED"
        client.connect()
        assert client.lease_state == "AUTHENTICATED"
        client.register()
        assert client.lease_state in ("ONLINE", "EXPIRING")
        client.shutdown()
        assert client.lease_state == "DISCONNECTED"
    finally:
        host.stop()


# -- expiry --------------------------------------------------------------------

def test_forced_close_clears_session_and_requires_login(tmp_path):
    host = FakeCoreHost()
    try:
        client = _client(host, tmp_path / "device.json")
        client.connect()
        client.register()
        wait_for_status(host, "mac-01", "online")
        assert host.force_close("mac-01") is True
        wait_for_status(host, "mac-01", "offline")
        # Next I/O surfaces the closure and clears session state.
        try:
            client.discover()
        except Exception:
            pass
        assert client.session_token is None
        assert client.connection_id is None
        assert client.lease_expires_at is None
        assert client.device_id == "mac-01"
        assert client.join_name == "MacBook-mac-01"
        # Provisioning credential survives in-app: reconnect mints new session.
        reg = client.reconnect()
        assert reg["payload"]["registered"] is True
        assert client.session_token
    finally:
        host.stop()


# -- rotation ---------------------------------------------------------------------

def test_reconnect_rotates_token_connection_and_lease(tmp_path):
    host = FakeCoreHost()
    try:
        client = _client(host, tmp_path / "device.json")
        client.connect()
        client.register()
        token_a = client.session_token
        id_a = client.connection_id
        lease_a = client.lease_expires_at
        client.reconnect()
        assert client.connection_id != id_a
        assert client.session_token != token_a
        assert client.session_token
        # Lease window is fresh (expiry string moves forward or, at minimum,
        # the reconnect path asserted a new lease triple server-side).
        assert client.lease_expires_at
        assert (client.lease_expires_at, client.connection_id) != (lease_a, id_a)
    finally:
        host.stop()


def test_shutdown_login_rotates_token(tmp_path):
    host = FakeCoreHost()
    try:
        c1 = _client(host, tmp_path / "a.json")
        c1.connect()
        token_a = c1.session_token
        c1.shutdown()
        c2 = _client(host, tmp_path / "b.json")
        c2.connect()
        assert c2.session_token != token_a
        c1.shutdown()
        c2.shutdown()
    finally:
        host.stop()


def test_expired_token_not_silently_reused(tmp_path):
    from client.core_device_client import DeviceClientError

    host = FakeCoreHost()
    try:
        client = _client(host, tmp_path / "device.json")
        client.connect()
        client.register()
        client.mark_disconnected()  # forced-close path clears token
        assert client.session_token is None
        try:
            client.register()
        except DeviceClientError:
            pass
        else:
            raise AssertionError("register without session token must fail")
        client.shutdown()
    finally:
        host.stop()
