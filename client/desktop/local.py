"""Local Ollama and explicit standalone service execution; no CORE imports."""
import codecs
import os
import signal
import subprocess
import threading
from pathlib import Path

from ..inference import ChatStream


class LocalRunner:
    def __init__(self):
        self.cancel = threading.Event()
        self.process = None
        self.response = None
        self.lock = threading.RLock()

    def model(self, model, prompt, emit, history=None):
        if not model.strip(): raise ValueError("Choose an installed local Ollama model.")
        stream = ChatStream(model, (history or []) + [{"role":"user","content":prompt}])
        with self.lock: self.response = stream
        try:
            if self.cancel.is_set(): return "cancelled"
            with stream:
                for chunk in stream:
                    if self.cancel.is_set(): return "cancelled"
                    emit(chunk.get("message", {}).get("content", ""))
            return "cancelled" if self.cancel.is_set() else "completed"
        except Exception:
            if self.cancel.is_set(): return "cancelled"
            raise
        finally:
            with self.lock: self.response = None

    @staticmethod
    def command(service, root, home, prompt, model, mode="plan"):
        if service not in ("asis", "ascs", "tiviss"):
            raise ValueError("Only installed ASIS, ASCS or TIVISS can run locally.")
        root, home = Path(root).expanduser().resolve(), Path(home).resolve()
        marker = "agent" if service == "ascs" else service
        if not (root / marker).is_dir(): raise ValueError("Configure this service's installed source directory in Local setup.")
        python = root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if not python.is_file(): raise ValueError("This service needs its own installed .venv Python environment.")
        workspace = home / "workspace"; workspace.mkdir(parents=True, exist_ok=True)
        env = {k:v for k,v in os.environ.items() if not k.startswith(("CORE_", "RESCS_", "ASIS_", "AGENT_", "TIVISS_"))}
        env.update(PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
        if service == "asis":
            for part in ("DATA", "CONFIG", "CACHE", "LOG", "MEMORY", "RUNTIME"):
                env[f"ASIS_{part}_DIRECTORY"] = str(home / "asis" / part.lower())
            env["ASIS_CORE_ENABLED"] = "false"
            argv = [str(python), "-u", "-m", "asis", "--mode", "general", "--workspace", str(workspace),
                    "--memory-db", str(home / "asis-memory.sqlite3"), "--message=" + prompt]
        elif service == "ascs":
            if mode not in ("plan", "build", "auto"): raise ValueError("Invalid coding mode.")
            env.update(AGENT_PROFILE_DIRECTORY=str(home / "ascs"), AGENT_EXPERIENCE_PATH=str(home / "ascs-experiences.jsonl"),
                       AGENT_TUI_STATE_PATH=str(home / "ascs/tui_state.json"), AGENT_NONINTERACTIVE="1",
                       AGENT_ALLOW_MODEL_FALLBACK="false", AGENT_PROVIDER="ollama", AGENT_STOP_OLLAMA_ON_EXIT="false")
            argv = [str(python), "-u", "-m", "agent.main", "--workspace", str(workspace), "--mode", mode, "--", prompt]
        else:
            if not model: raise ValueError("Select a local Ollama model for TIVISS.")
            env.update(TIVISS_MODEL_PROVIDER="ollama", TIVISS_MODEL_ID=model, TIVISS_MEMORY_BACKEND="sqlite",
                TIVISS_MEMORY_CONVERSATION_DB=str(home / "tiviss-conversations.sqlite3"),
                TIVISS_MEMORY_SEMANTIC_DB=str(home / "tiviss-semantic.sqlite3"), TIVISS_OWNER_ID=home.name,
                TIVISS_CORE_ENABLED="false", TIVISS_RESCS_ENABLED="false")
            argv = [str(python), "-u", "-m", "tiviss", "--message=" + prompt]
        if service in ("asis", "ascs") and model: argv[4:4] = ["--model=" + model]
        return argv, root, env

    def service(self, service, root, home, prompt, model, emit, mode="plan"):
        argv, cwd, env = self.command(service, root, home, prompt, model, mode)
        with self.lock:
            if self.cancel.is_set(): return "cancelled"
            self.process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, start_new_session=os.name != "nt",
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)
        timer = threading.Timer(900, self.stop); timer.daemon = True; timer.start()
        try:
            decoder = codecs.getincrementaldecoder("utf-8")("replace")
            while True:
                chunk = self.process.stdout.read1(512)
                if not chunk: break
                emit(decoder.decode(chunk))
            emit(decoder.decode(b"", final=True))
            code = self.process.wait()
            if self.cancel.is_set(): return "cancelled"
            if code: raise RuntimeError(f"Local {service.upper()} failed with exit {code}.")
            return "completed"
        finally: timer.cancel()

    def stop(self):
        self.cancel.set()
        with self.lock:
            process, response = self.process, self.response
        if response:
            try: response.close()
            except OSError: pass
        if process and process.poll() is None:
            if os.name == "nt":
                try: process.send_signal(signal.CTRL_BREAK_EVENT); process.wait(timeout=3)
                except (OSError, subprocess.TimeoutExpired):
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=10)
            else:
                try: os.killpg(process.pid, signal.SIGTERM); process.wait(timeout=3)
                except subprocess.TimeoutExpired: os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError: pass
