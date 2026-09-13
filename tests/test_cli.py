"""CLI: help, --remember, error paths, full login flow."""

import json

import pytest

from client.core_device_client import build_parser, main
from tests.fake_host import FakeCoreHost


def test_help_exits_zero(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0


def test_parser_defaults():
    args = build_parser().parse_args([])
    assert args.no_tls is False
    assert args.insecure is False
    assert args.device_type == "generic"
    assert args.platform == "mac"


def test_remember_saves_without_secrets(tmp_path):
    device_file = tmp_path / "device.json"
    rc = main([
        "--remember", "--device-file", str(device_file),
        "--device-id", "mac-01", "--device-name", "MacBook",
        "--host", "192.168.1.67", "--port", "5000",
    ])
    assert rc == 0
    stored = json.loads(device_file.read_text(encoding="utf-8"))
    assert stored["host"] == "192.168.1.67"
    assert stored["device_id"] == "mac-01"
    assert "token" not in stored


def test_remember_requires_device_id(tmp_path, capsys):
    rc = main(["--remember", "--device-file", str(tmp_path / "d.json")])
    assert rc == 2
    assert "device-id" in capsys.readouterr().out


def test_missing_remembered_device_errors(tmp_path, capsys):
    rc = main([
        "--device-file", str(tmp_path / "missing.json"),
        "--host", "127.0.0.1", "--port", "5000", "--token", "x",
    ])
    assert rc == 2
    assert "No remembered device" in capsys.readouterr().out


def test_missing_token_errors(tmp_path, capsys, monkeypatch):
    device_file = tmp_path / "device.json"
    assert main(["--remember", "--device-file", str(device_file),
                 "--device-id", "mac-01"]) == 0
    monkeypatch.setattr("getpass.getpass", lambda *a, **k: "")
    rc = main(["--device-file", str(device_file)])
    assert rc == 2
    assert "login required" in capsys.readouterr().out.lower()


def test_full_cli_login_flow(tmp_path, capsys, monkeypatch):
    host = FakeCoreHost()
    try:
        device_file = tmp_path / "device.json"
        assert main(["--remember", "--device-file", str(device_file),
                     "--device-id", "mac-01", "--host", "127.0.0.1",
                     "--port", str(host.port)]) == 0
        monkeypatch.setattr("builtins.input", lambda *a, **k: "quit")
        rc = main(["--device-file", str(device_file),
                   "--host", "127.0.0.1", "--port", str(host.port),
                   "--token", "secret-mac-01", "--no-tls"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "Authenticated" in out
        assert "Registered" in out
        assert "login session cleared" in out
    finally:
        host.stop()


def test_cli_tls_flags_accepted(tmp_path):
    args = build_parser().parse_args(["--ca-file", "runtime/certificates/core.pem",
                                      "--insecure"])
    assert args.ca_file == "runtime/certificates/core.pem"
    assert args.insecure is True
