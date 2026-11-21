import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from client import inference


@pytest.fixture
def backend(monkeypatch):
    requests=[]
    state={'context':32768,'delay':0,'heartbeat':False,'thinking':False,'error':False,'missing':False}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append((self.path,data))
            self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers()
            try:
                if self.path=='/api/show':
                    metadata={} if state['missing'] else {'general.architecture':'test','test.context_length':state['context']}
                    self.wfile.write(json.dumps({'model_info':metadata}).encode());return
                if state['heartbeat']:
                    for _ in range(20):
                        self.wfile.write(b'{}\n');self.wfile.flush();time.sleep(.01)
                if state['thinking']:
                    self.wfile.write(b'{"message":{"thinking":"reasoning"}}\n');self.wfile.flush()
                time.sleep(state['delay'])
                events=([{'error':'model requires more memory'}] if state['error'] else
                        [{'message':{'content':'actual output'}},{'done':True}])
                for event in events:
                    self.wfile.write(json.dumps(event).encode()+b'\n');self.wfile.flush()
            except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    original=inference.http.client.HTTPConnection
    def connection(host,port,**kwargs):
        assert host=='127.0.0.1' and port==11434
        return original(host,server.server_port,**kwargs)
    monkeypatch.setattr(inference.http.client,'HTTPConnection',connection)
    try:yield requests,state
    finally:server.shutdown();server.server_close();thread.join(2)


def test_demand_only_requests_set_idle_and_model_maximum(backend):
    requests,state=backend
    stream=inference.ChatStream('installed:model',[{'role':'user','content':'real prompt'}])
    assert requests==[]
    with stream:events=list(stream)
    assert events[-1]['done']
    assert [path for path,_ in requests]==['/api/show','/api/chat']
    payload=requests[-1][1]
    assert payload['keep_alive']=='60s'
    assert payload['options']=={'num_ctx':32768,'num_predict':-1}
    assert payload['messages']==[{'role':'user','content':'real prompt'}]
    assert inference.STARTUP_SECONDS==30


@pytest.mark.parametrize('heartbeat',[False,True])
def test_startup_deadline_is_not_reset_by_empty_progress(backend,monkeypatch,heartbeat):
    requests,state=backend
    state.update(delay=.15,heartbeat=heartbeat)
    monkeypatch.setattr(inference,'STARTUP_SECONDS',.05)
    with pytest.raises(TimeoutError,match='startup exceeded'):
        with inference.ChatStream('model',[{'role':'user','content':'hi'}]) as stream:list(stream)
    assert len(requests)==2  # No retry or warm-up generation.


def test_thinking_token_starts_response_without_truncating_generation(backend,monkeypatch):
    requests,state=backend
    state.update(delay=.12,thinking=True)
    monkeypatch.setattr(inference,'STARTUP_SECONDS',.07)
    with inference.ChatStream('model',[{'role':'user','content':'hi'}]) as stream:
        events=list(stream)
    assert events[0]['message']['thinking']=='reasoning'
    assert events[-1]['done']


def test_missing_context_fails_without_loading_model(backend):
    requests,state=backend;state['missing']=True
    with pytest.raises(ValueError,match='maximum context'):
        with inference.ChatStream('model',[]):pass
    assert [p for p,_ in requests]==['/api/show']


def test_backend_memory_error_is_reported_without_fallback(backend):
    requests,state=backend;state['error']=True
    with pytest.raises(RuntimeError,match='requires more memory'):
        with inference.ChatStream('model',[{'role':'user','content':'hi'}]) as stream:list(stream)
    assert len(requests)==2


def test_cancel_interrupts_blocked_stream(backend):
    requests,state=backend;state['delay']=2
    stream=inference.ChatStream('model',[{'role':'user','content':'hi'}]);stream.__enter__()
    finished=threading.Event()
    def read():
        try:list(stream)
        except (OSError,ValueError):pass
        finally:stream.__exit__();finished.set()
    thread=threading.Thread(target=read);thread.start();time.sleep(.02)
    stream.close();assert finished.wait(1);thread.join(1)


def test_repeated_cancel_interrupts_every_stream(backend):
    requests,state=backend;state['delay']=2
    for attempt in range(10):
        stream=inference.ChatStream('model',[{'role':'user','content':'hi'}])
        stream.__enter__()
        finished=threading.Event();errors=[]
        def read(active=stream,done=finished):
            try:list(active)
            except (OSError,ValueError):pass
            except Exception as exc:errors.append(exc)
            finally:
                active.__exit__();done.set()
        thread=threading.Thread(target=read);thread.start();time.sleep(.01)
        for _ in range(5):stream.close()
        assert finished.wait(1),f"attempt {attempt} did not stop"
        thread.join(1)
        assert not thread.is_alive(),f"attempt {attempt} leaked a reader thread"
        assert not errors,f"attempt {attempt} raised {errors!r}"
    assert len(requests)==20


@pytest.mark.parametrize('info',[{}, {'x.context_length':True},{'x.context_length':-1}])
def test_invalid_context_is_not_guessed(info):
    with pytest.raises(ValueError):inference.maximum_context({'model_info':info})
