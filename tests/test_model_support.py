import io
from pathlib import Path
from unittest.mock import patch

import pytest

from client.desktop.store import LocalStore
from client.model_support import download_recommended, recommend, system_report


@pytest.mark.parametrize('ram,disk,expected',[(2,100,None),(4,10,'qwen3.5:0.8b'),(8,10,'qwen3.5:2b'),(16,20,'qwen3.5:9b'),(32,6,'qwen3.5:2b'),(32,1,None)])
def test_conservative_model_choice_and_disk_limit(ram,disk,expected):
    assert recommend(ram,disk)==expected


def test_hardware_report_works_when_ollama_is_offline(tmp_path):
    with patch('client.model_support.memory_bytes',return_value=(16*1024**3,12*1024**3)), patch('client.model_support._command',return_value=''), patch('client.model_support.ollama_json',side_effect=OSError('offline')):
        report=system_report(tmp_path)
    assert report['ollama_available'] is False
    assert report['recommended_model'] in ('qwen3.5:0.8b','qwen3.5:2b','qwen3.5:4b','qwen3.5:9b',None)
    assert report['available_ram_gib']==12


def test_download_uses_selected_real_model_and_checks_installed_tag():
    report={'recommended_model':'qwen3.5:0.8b','ollama_available':True,'installed_models':[]}
    with patch('client.model_support.build_opener') as build, patch('client.model_support.ollama_json',return_value={'models':[{'name':'qwen3.5:0.8b'}]}):
        build.return_value.open.return_value=io.BytesIO(b'{"status":"pulling manifest"}\n{"status":"success"}\n')
        assert download_recommended(report,lambda text:None)=='qwen3.5:0.8b'
        request=build.return_value.open.call_args.args[0]
        assert request.full_url=='http://127.0.0.1:11434/api/pull'
        assert b'qwen3.5:0.8b' in request.data


def test_download_does_not_run_when_insufficient_resources():
    with patch('client.model_support.build_opener') as build:
        with pytest.raises(ValueError):download_recommended({'recommended_model':None})
        build.assert_not_called()


def test_account_local_files_are_separate_and_pending(tmp_path):
    store=LocalStore(tmp_path)
    a=store.create('alice','local-passphrase-one');b=store.create('bobby','local-passphrase-two')
    identity=store.add_file(a,'notes.txt',b'private bytes')
    assert store.files(b)==[]
    row=store.files(a)[0];assert Path(row['path']).read_bytes()==b'private bytes' and row['pending']==1
    store.file_ack(a,identity,'remote-file');assert store.files(a)[0]['pending']==0
    with pytest.raises(ValueError):store.add_file(a,'large',b'x'*(8*1024*1024+1))
    store.close()


def test_host_and_client_model_policy_are_identical():
    root=Path(__file__).resolve().parents[2]
    if not (root/'CORE-HOST/core/model_support.py').is_file():pytest.skip('Cross-repo policy check requires sibling CORE-HOST')
    assert (root/'CORE-HOST/core/model_support.py').read_bytes()==(root/'CORE-CLIENT/client/model_support.py').read_bytes()
