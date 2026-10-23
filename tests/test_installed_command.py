import pytest
from client.entrypoint import main


def test_saved_url_and_open_are_independent_of_cwd(tmp_path, monkeypatch):
    monkeypatch.setattr('client.entrypoint.settings_path',lambda:tmp_path/'settings/url.txt')
    monkeypatch.delenv('CORE_PORTAL_URL',raising=False)
    assert main(['setup','--url','https://core.example.com']) == 0
    monkeypatch.chdir(tmp_path)
    calls=[]
    monkeypatch.setattr('client.entrypoint.browser_main',lambda args:calls.append(args) or 0)
    assert main([]) == 0
    assert calls == [['--url','https://core.example.com']]


def test_native_arguments_forwarded(monkeypatch):
    calls=[]
    monkeypatch.setattr('client.core_device_client.main',lambda args:calls.append(args) or 7)
    assert main(['native','--host','127.0.0.1']) == 7
    assert calls == [['--host','127.0.0.1']]


def test_doctor_does_not_open_browser(tmp_path, monkeypatch):
    monkeypatch.setattr('client.entrypoint.settings_path',lambda:tmp_path/'url.txt')
    monkeypatch.setattr('client.entrypoint.browser_main',lambda _:pytest.fail('browser opened'))
    assert main(['doctor']) == 0


@pytest.mark.parametrize('url',['http://remote.example.com','https://user:pass@core.example.com'])
def test_setup_rejects_unsafe_url(url,tmp_path,monkeypatch):
    monkeypatch.setattr('client.entrypoint.settings_path',lambda:tmp_path/'url.txt')
    with pytest.raises(SystemExit):main(['setup','--url',url])
    assert not (tmp_path/'url.txt').exists()


@pytest.mark.parametrize('system,expected',[('Windows','windows'),('Darwin','mac'),('Linux','linux')])
def test_native_platform_detection(system,expected,monkeypatch):
    from client.core_device_client import build_parser
    monkeypatch.setattr('platform.system',lambda:system)
    assert build_parser().parse_args([]).platform == expected
