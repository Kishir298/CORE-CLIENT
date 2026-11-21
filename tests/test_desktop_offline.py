import os
import socket
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from client.desktop.local import LocalRunner
from client.desktop.remote import Remote, RemoteError, sync_records
from client.desktop.store import LocalStore, revision


@pytest.fixture
def store(tmp_path):
    result = LocalStore(tmp_path / 'data')
    yield result
    result.close()


def test_offline_profile_records_history_survive_restart_without_network(tmp_path, monkeypatch):
    monkeypatch.setattr(socket, 'create_connection', lambda *a, **k:pytest.fail('offline operation attempted network'))
    store = LocalStore(tmp_path)
    identity = store.create('alice', 'local-passphrase-one')
    store.save(identity, 'workspace', 'notes', {'text':'offline'})
    store.log(identity, 'local', 'hello', 'saved')
    store.close()
    store = LocalStore(tmp_path)
    assert store.unlock('alice', 'local-passphrase-one') == identity
    assert store.records(identity)[0]['pending'] == 1
    assert store.history(identity)[0]['output'] == 'saved'
    store.close()


def test_local_password_hash_and_profile_separation(store):
    alice = store.create('alice', 'local-passphrase-one')
    bob = store.create('bobby', 'local-passphrase-two')
    with pytest.raises(PermissionError):store.unlock('alice', 'incorrect')
    store.save(alice, 'asis', 'private', {'text':'alice-only'})
    assert store.records(bob) == []
    assert b'local-passphrase-one' not in (store.directory/'desktop.sqlite3').read_bytes()
    if os.name == 'posix':assert (store.directory/'desktop.sqlite3').stat().st_mode & 0o077 == 0


def test_pending_edit_is_not_overwritten_by_refresh(store):
    user = store.create('alice', 'local-passphrase-one')
    store.cache(user, 'workspace', [{'key':'k','value':{'text':'base'}}])
    store.save(user, 'workspace', 'k', {'text':'draft'})
    store.cache(user, 'workspace', [{'key':'k','value':{'text':'server changed'}}])
    row = store.records(user)[0]
    assert row['value'] == {'text':'draft'}
    assert row['base'] == revision({'text':'base'})


def test_edit_during_sync_is_kept_pending(store):
    user = store.create('alice', 'local-passphrase-one')
    store.save(user, 'workspace', 'k', {'text':'first'})
    item = store.records(user)[0]
    store.save(user, 'workspace', 'k', {'text':'second'})
    store.acknowledged(user, item, {'value':{'text':'first'}})
    assert store.records(user)[0]['pending'] == 1
    assert store.records(user)[0]['value'] == {'text':'second'}


def test_binding_cannot_cross_server_or_remote_profile(store):
    user = store.create('alice', 'local-passphrase-one')
    store.bind(user, 'https://core.example', 'a'*32)
    with pytest.raises(PermissionError):store.bind(user, 'https://core.example', 'b'*32)
    with pytest.raises(PermissionError):store.bind(user, 'https://other.example', 'a'*32)


def test_sync_is_capability_gated_and_keeps_conflicts(store):
    user = store.create('alice', 'local-passphrase-one')
    store.bind(user, 'https://core.example', 'a'*32)
    store.save(user, 'workspace', 'k', {'text':'draft'})
    remote = Remote('https://core.example'); remote.profile = {'id':'a'*32}
    with patch.object(remote, 'request', return_value={'capabilities':[]}) as request:
        with pytest.raises(RemoteError, match='safe offline sync'):sync_records(store, user, remote)
        assert request.call_count == 1
    with patch.object(remote, 'request', side_effect=[{'capabilities':['conditional_records_v1']}, RemoteError('conflict',409)]):
        assert sync_records(store, user, remote) == {'saved':0,'conflicts':['workspace/k']}
    assert store.records(user)[0]['pending'] == 1


@pytest.mark.parametrize('url',['http://remote.example','https://user:pass@example.com','https://example.com/path','https://example.com/?key=secret'])
def test_remote_rejects_unsafe_endpoint(url):
    with pytest.raises(ValueError):Remote(url)


def test_local_model_streams_through_demand_policy():
    output = []
    runner = LocalRunner()
    class Stream:
        def __enter__(self):return self
        def __iter__(self):return iter([{'message':{'content':'hello '}},{'message':{'content':'world'},'done':True}])
        def __exit__(self,*args):pass
    with patch('client.desktop.local.ChatStream',return_value=Stream()) as stream:
        assert runner.model('installed:model', 'hello', output.append) == 'completed'
        assert stream.call_args.args==('installed:model',[{'role':'user','content':'hello'}])
    assert ''.join(output) == 'hello world'


def test_local_service_real_process_and_cancellation(tmp_path):
    import sys
    root = tmp_path/'ASIS'; (root/'asis').mkdir(parents=True)
    python = root/'.venv'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    python.parent.mkdir(parents=True)
    if os.name == 'nt':pytest.skip('POSIX symlink fixture; Windows adapter covered by command tests')
    import shutil
    shutil.copy2(sys.executable,python)
    (root/".venv/pyvenv.cfg").write_text("home = "+str(Path(sys.base_prefix)/"bin")+"\ninclude-system-site-packages = false\n")
    (root/'asis/__main__.py').write_text('print("actual child output", flush=True)')
    runner, output = LocalRunner(), []
    assert runner.service('asis', root, tmp_path/'profile', 'hello', '', output.append) == 'completed'
    assert 'actual child output' in ''.join(output)
    (root/'asis/__main__.py').write_text('import time\nprint("started",flush=True)\ntime.sleep(60)')
    runner, output, status = LocalRunner(), [], []
    started = threading.Event()
    def emit(text):output.append(text); started.set()
    thread = threading.Thread(target=lambda:status.append(runner.service('asis',root,tmp_path/'profile','hello','',emit)))
    thread.start(); assert started.wait(5)
    runner.stop(); thread.join(5)
    assert not thread.is_alive()
    assert status == ['cancelled']


def test_local_service_profile_and_noninteractive_contract(tmp_path):
    root = tmp_path/'ASCS'; (root/'agent').mkdir(parents=True)
    python = root/'.venv'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    python.parent.mkdir(parents=True); python.touch()
    argv, cwd, env = LocalRunner.command('ascs', root, tmp_path/'profile-a', '--looks-like-option', 'model', 'plan')
    assert argv[-2:] == ['--','--looks-like-option']
    assert env['AGENT_NONINTERACTIVE'] == '1'
    assert 'profile-a' in env['AGENT_PROFILE_DIRECTORY']
    assert argv[3] == 'agent.main'
    assert Path(cwd) == root


def test_explicit_browser_mode_still_available(monkeypatch):
    from client.entrypoint import main
    calls=[]
    monkeypatch.setattr('client.entrypoint.browser_main', lambda args:calls.append(args) or 0)
    assert main(['browser','--url','https://core.example']) == 0
    assert calls == [['--url','https://core.example']]
