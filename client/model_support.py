"""Conservative local-model sizing and explicit Ollama downloads, shared by host/client."""
import ctypes
import json
import os
import platform
import re
import shutil
import subprocess
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener

# These are CORE's conservative planning estimates, not vendor guarantees.
# Memory includes headroom for a bounded context; disk allows staging overhead.
CATALOG = [
    {"name":"qwen3.5:0.8b", "memory_gib":3, "disk_gib":3},
    {"name":"qwen3.5:2b", "memory_gib":5, "disk_gib":6},
    {"name":"qwen3.5:4b", "memory_gib":8, "disk_gib":8},
    {"name":"qwen3.5:9b", "memory_gib":12, "disk_gib":15},
]
GIB = 1024 ** 3


def _command(argv):
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=4, check=True).stdout
    except (OSError, subprocess.SubprocessError): return ""


def memory_bytes():
    system = platform.system()
    total = available = 0
    if system == "Linux":
        fields = {}
        try:
            for line in Path('/proc/meminfo').read_text().splitlines():
                key, value = line.split(':',1); fields[key] = int(value.strip().split()[0]) * 1024
            total, available = fields.get('MemTotal',0), fields.get('MemAvailable', fields.get('MemFree',0))
            # Respect a container's memory limit rather than the physical host RAM.
            for limit_path, used_path in [('/sys/fs/cgroup/memory.max','/sys/fs/cgroup/memory.current'),
                    ('/sys/fs/cgroup/memory/memory.limit_in_bytes','/sys/fs/cgroup/memory/memory.usage_in_bytes')]:
                limit = Path(limit_path)
                if limit.is_file():
                    text = limit.read_text().strip()
                    if text.isdigit() and 0 < int(text) < total:
                        total = int(text)
                        available = min(available, max(0,total-int(Path(used_path).read_text())))
        except (OSError,ValueError,IndexError): pass
    elif system == "Windows":
        class MemoryStatus(ctypes.Structure):
            _fields_ = [('length',ctypes.c_ulong),('load',ctypes.c_ulong)] + [(n,ctypes.c_ulonglong) for n in
                ('total','available','total_page','available_page','total_virtual','available_virtual','extended')]
        status = MemoryStatus(); status.length = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            total, available = status.total, status.available
    elif system == "Darwin":
        value = _command(['sysctl','-n','hw.memsize']).strip()
        total = int(value) if value.isdigit() else 0
        raw = _command(['vm_stat'])
        size = re.search(r'page size of (\d+) bytes',raw)
        pages = re.findall(r'Pages (?:free|inactive|speculative):\s+(\d+)',raw)
        available = sum(map(int,pages))*int(size.group(1)) if size else total//2
    return max(0,total), max(0,available)


def recommend(available_gib, disk_gib):
    # Reserve 20% of currently available memory. A long context can still exceed this estimate.
    candidates = [m for m in CATALOG if m['memory_gib'] <= available_gib*.8 and m['disk_gib'] <= disk_gib]
    return candidates[-1]['name'] if candidates else None


def ollama_json(path, timeout=2):
    request = Request('http://127.0.0.1:11434'+path)
    with build_opener(ProxyHandler({})).open(request,timeout=timeout) as response:
        data = response.read(2*1024*1024+1)
        if len(data)>2*1024*1024: raise ValueError('Ollama metadata exceeds the limit.')
        return json.loads(data)


def system_report(directory=None):
    total, available = memory_bytes()
    disk_path = Path(os.environ.get('OLLAMA_MODELS') or directory or Path.home()).expanduser().resolve()
    while not disk_path.exists() and disk_path != disk_path.parent: disk_path = disk_path.parent
    free = shutil.disk_usage(disk_path).free
    gpus = _command(['nvidia-smi','--query-gpu=name,memory.total,memory.free','--format=csv,noheader,nounits']).strip()
    report = {'os':platform.system(),'architecture':platform.machine(),'cpu_threads':os.cpu_count(),
        'total_ram_gib':round(total/GIB,2),'available_ram_gib':round(available/GIB,2),
        'free_disk_gib':round(free/GIB,2),'disk_probe_path':str(disk_path),'nvidia_probe':gpus or 'not detected',
        'requested_context':'installed model maximum (not covered by small-context memory estimates)', 'generation_tokens':-1, 'idle_keep_alive_seconds':60, 'startup_timeout_seconds':30, 'recommended_model':recommend(available/GIB,free/GIB),'ollama_available':False,'installed_models':[],
        'note':'Model sizing estimates assume a small context, not the requested maximum. Maximum context can require much more RAM; inference can fail even when a model is recommended. GPU detection does not prove driver acceleration. The daemon model filesystem may differ; set OLLAMA_MODELS when probing a custom model disk. CPU-only inference is supported but may be slow.'}
    try:
        report['installed_models']=[m['name'] for m in ollama_json('/api/tags').get('models',[])]
        report['ollama_available']=True
    except (OSError,ValueError,KeyError): pass
    report['recommended_installed'] = report['recommended_model'] in report['installed_models']
    return report


def download_recommended(report, emit=print):
    name = report.get('recommended_model')
    if name not in {m['name'] for m in CATALOG}: raise ValueError('Not enough verified available memory/disk for a recommended local model.')
    if not report.get('ollama_available'): raise ConnectionError('Install/start Ollama first: https://ollama.com/download . No model was downloaded.')
    if name in report.get('installed_models',[]):
        emit(name+' is already installed.'); return name
    request = Request('http://127.0.0.1:11434/api/pull',data=json.dumps({'model':name,'stream':True}).encode(),headers={'Content-Type':'application/json'})
    with build_opener(ProxyHandler({})).open(request,timeout=120) as response:
        while True:
            raw = response.readline(1024*1024+1)
            if not raw: break
            if len(raw)>1024*1024: raise ValueError('Invalid Ollama progress response.')
            event=json.loads(raw)
            if event.get('error'): raise RuntimeError(str(event['error']))
            status=event.get('status','downloading')
            if event.get('total'): status+=' '+str(round(100*event.get('completed',0)/event['total']))+'%'
            emit(status)
            if event.get('status')=='success':
                if name not in [m['name'] for m in ollama_json('/api/tags',5).get('models',[])]:
                    raise RuntimeError('Download finished but the exact model tag was not found.')
                return name
    raise ConnectionError('Model download ended before success. Retry when online; Ollama may resume existing layers.')
