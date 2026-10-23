import pytest
from client.web import main, validate_url


@pytest.mark.parametrize("url",["https://core.example.com", "http://127.0.0.1:8780"])
def test_valid_portal_url(url):
    assert validate_url(url) == url


@pytest.mark.parametrize("url",["http://192.168.1.2:8780", "javascript:alert(1)", "https://user:secret@core.example.com"])
def test_unsafe_portal_url(url):
    with pytest.raises(ValueError): validate_url(url)


def test_launcher_opens_browser_without_host_code(monkeypatch):
    opened=[]
    monkeypatch.setattr("webbrowser.open",lambda url: opened.append(url) or True)
    assert main(["--url","https://core.example.com"]) == 0
    assert opened == ["https://core.example.com"]
