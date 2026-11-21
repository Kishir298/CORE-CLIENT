"""TLS: enabled by default, validated by default, never silent downgrade."""

import socket
import ssl

import pytest

from client.core_device_client import CoreDeviceClient, DeviceClientError
from tests.fake_host import FakeCoreHost


def _make_client(device_file, **overrides):
    params = {
        "host": "127.0.0.1",
        "port": 5000,
        "device_id": "mac-01",
        "device_file": device_file,
    }
    params.update(overrides)
    return CoreDeviceClient(**params)


def test_tls_enabled_by_default(tmp_path):
    client = _make_client(tmp_path / "d.json")
    assert client.use_tls is True


def test_context_requires_cert_by_default(tmp_path):
    client = _make_client(tmp_path / "d.json")
    ctx = client._build_tls_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED


def test_insecure_mode_is_explicit_opt_in(tmp_path):
    client = _make_client(tmp_path / "d.json", insecure=True)
    ctx = client._build_tls_context()
    assert ctx.verify_mode == ssl.CERT_NONE


def test_minimum_tls_version_12(tmp_path):
    client = _make_client(tmp_path / "d.json")
    ctx = client._build_tls_context()
    try:
        assert ctx.minimum_version == ssl.TLSVersion.TLSv1_2
    except AttributeError:
        # Fallback path on old interpreters disables 1.0/1.1 explicitly.
        assert ctx.options & getattr(ssl, "OP_NO_TLSv1", 0)
        assert ctx.options & getattr(ssl, "OP_NO_TLSv1_1", 0)


def test_invalid_ca_file_surfaces_error(tmp_path):
    host = FakeCoreHost()
    try:
        client = _make_client(
            tmp_path / "d.json",
            ca_file=tmp_path / "missing-ca.pem",
            timeout=5.0,
        )
        client.login("secret-mac-01")
        client.host, client.port = "127.0.0.1", host.port
        with pytest.raises(Exception):
            client.connect()
        client.shutdown()
    finally:
        host.stop()


def test_weak_tls_version_rejected(tmp_path, monkeypatch):
    class FakeRaw:
        def settimeout(self, timeout):
            pass

        def close(self):
            self.closed = True
        closed = False

    class FakeTLSSock:
        def version(self):
            return "TLSv1"
        def close(self):
            pass

    class FakeCtx:
        def wrap_socket(self, raw, server_hostname=None):
            return FakeTLSSock()

    raw = FakeRaw()
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: raw)
    client = _make_client(tmp_path / "d.json", timeout=5.0)
    client.login("secret-mac-01")
    monkeypatch.setattr(client, "_build_tls_context", lambda: FakeCtx())
    with pytest.raises(DeviceClientError, match="TLS 1.2"):
        client.connect()


def test_no_silent_plaintext_downgrade(tmp_path):
    """A TLS client against a plaintext server must fail, never downgrade."""
    host = FakeCoreHost()
    try:
        client = _make_client(tmp_path / "d.json", insecure=True, timeout=5.0)
        client.login("secret-mac-01")
        client.host, client.port = "127.0.0.1", host.port
        assert client.use_tls is True
        with pytest.raises(Exception):
            client.connect()
        # Socket cleaned up; explicit opt-out still required for plaintext.
        assert client._sock is None
        client.shutdown()
    finally:
        host.stop()
