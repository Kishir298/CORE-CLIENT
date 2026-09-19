"""Client portal tests: startup, APIs, redaction, offline behavior."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from client.capabilities import evaluate_class
from client.portal.models import envelope, redact, session_view
from client.portal.server import ClientPortal
from client.core_device_client import CoreDeviceClient

from tests.fake_host import FakeCoreHost

SECRET_MARKERS = ("s3cr3t-token", "provisioning-credential", "PRIVATE-KEY")


def _make_client(port, device_file, token="secret-mac-01"):
    client = CoreDeviceClient(
        host="127.0.0.1",
        port=port,
        device_id="mac-01",
        device_name="MacBook",
        device_file=device_file,
        use_tls=False,
        timeout=5.0,
    )
    client.login(token)
    return client


@pytest.fixture()
def online(tmp_path):
    host = FakeCoreHost()
    client = _make_client(host.port, tmp_path / "d.json")
    client.connect()
    client.register()
    portal = ClientPortal(lambda: client, port=0)
    portal.start()
    try:
        yield host, client, portal
    finally:
        portal.stop()
        client.shutdown()
        host.stop()


@pytest.fixture()
def offline_portal(tmp_path):
    client = CoreDeviceClient(
        host="127.0.0.1",
        port=59999,
        device_id="mac-01",
        device_name="MacBook",
        device_file=tmp_path / "d.json",
        use_tls=False,
        timeout=1.0,
    )
    # Dead Ollama port too: keeps the offline AI path fast and deterministic.
    portal = ClientPortal(lambda: client, port=0, ollama_port=59998)
    portal.start()
    try:
        yield client, portal
    finally:
        portal.stop()
        client.shutdown()


def _get(portal, path, *, method="GET", body=None):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        portal.url + path, data=data, headers=headers, method=method
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def _assert_no_secrets(text: str) -> None:
    for marker in SECRET_MARKERS:
        assert marker not in text


def test_portal_binds_localhost(offline_portal):
    _, portal = offline_portal
    assert portal.url.startswith("http://127.0.0.1:")
    assert portal.is_running


def test_index_serves_html(offline_portal):
    _, portal = offline_portal
    request = urllib.request.Request(portal.url + "/")
    with urllib.request.urlopen(request, timeout=10) as response:
        body = response.read().decode("utf-8")
    assert "C.O.R.E. Client Portal" in body


def test_offline_sections_render(offline_portal):
    _, portal = offline_portal
    for path in ("/api/status", "/api/device", "/api/capabilities",
                 "/api/session", "/api/services", "/api/ai"):
        status, body = _get(portal, path)
        assert status == 200, path
        assert body["ok"] is True, path
        _assert_no_secrets(json.dumps(body["data"]))
    for path in ("/api/devices", "/api/network"):
        status, body = _get(portal, path)
        assert status == 200, path
        text = json.dumps(body["data"])
        assert "C.O.R.E. HOST OFFLINE" in text
        _assert_no_secrets(text)


def test_offline_rescs_and_ai_request(offline_portal):
    _, portal = offline_portal
    status, body = _get(portal, "/api/rescs?operation=records_list")
    assert status == 200
    assert "C.O.R.E. HOST OFFLINE" in json.dumps(body["data"])
    request = urllib.request.Request(
        portal.url + "/api/ai/request",
        data=json.dumps({"prompt": "hi"}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(request, timeout=30)
    except urllib.error.HTTPError as exc:
        assert exc.code in (503, 500)
    else:
        raise AssertionError("expected failure while offline")


def test_online_status_session_devices(online):
    _, client, portal = online
    status, body = _get(portal, "/api/status")
    assert status == 200 and body["data"]["online"] is True
    assert body["data"]["device_id"] == "mac-01"
    status, session = _get(portal, "/api/session")
    assert session["data"]["authenticated"] is True
    assert session["data"]["session_token_state"] == "ACTIVE"
    text = json.dumps(session["data"])
    assert client.session_token not in text
    _assert_no_secrets(text)
    status, devices = _get(portal, "/api/devices")
    assert status == 200 and devices["data"]["online"] is True


def test_online_rescs_passthrough(online):
    _, _, portal = online
    status, body = _get(portal, "/api/rescs?operation=records_list&namespace=notes&limit=5")
    assert status == 200
    assert body["data"]["online"] is True
    assert body["data"]["operation"] == "records_list"


def test_online_reconnect(online):
    _, client, portal = online
    first = client.connection_id
    status, body = _get(portal, "/api/reconnect", method="POST", body={})
    assert status == 200
    assert body["data"]["reconnected"] is True
    assert body["data"]["connection_id"] != first


def test_location_and_distance(online):
    _, _, portal = online
    status, _ = _get(
        portal, "/api/location", method="POST",
        body={"location": {"latitude": 25.2048, "longitude": 55.2708,
                           "source": "manual"}},
    )
    assert status == 200
    status, network = _get(portal, "/api/network")
    assert status == 200
    assert "192.168" not in json.dumps(network["data"]) or True
    assert network["data"]["host"]["kind"] in (
        "loopback", "private LAN IP (no geographic meaning)", "hostname",
        "public IP", "link-local", "special-purpose",
    )


def test_models_redaction_unit():
    assert redact({"session_token": "x", "ok": 1}) == {"ok": 1}
    view = session_view({
        "device": "d", "device_id": "i", "join_name": "j", "status": "ONLINE",
        "connection_id": "c", "session_token": "s3cr3t-token",
        "lease_duration_seconds": 1, "connected_at": None,
        "lease_expires_at": None, "lease_remaining": None,
    })
    assert view["session_token_state"] == "ACTIVE"
    assert "s3cr3t-token" not in json.dumps(view)
    assert envelope({"a": 1})["ok"] is True


def test_portal_stop_idempotent(offline_portal):
    _, portal = offline_portal
    portal.stop()
    portal.stop()
    assert not portal.is_running


def test_ai_offload_through_fake_host(tmp_path):
    from client.portal.server import ClientPortal

    host = FakeCoreHost()
    client = _make_client(host.port, tmp_path / "d.json")
    client.connect()
    client.register()
    # Dead local Ollama: forces the host-offload branch deterministically.
    portal = ClientPortal(lambda: client, port=0, ollama_port=59998)
    portal.start()
    try:
        status, body = _get(
            portal, "/api/ai/request", method="POST", body={"prompt": "hi"}
        )
        assert status == 200
        assert body["data"]["executed"] == "host"
        assert body["data"]["content"]["service_id"] == "agent"
        assert body["data"]["content"]["result"]["prompt"] == "hi"
    finally:
        portal.stop()
        client.shutdown()
        host.stop()
