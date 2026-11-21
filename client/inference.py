"""On-demand loopback inference: no warmups, 60s residency, 30s first-token budget."""
import http.client
import json
import os
import socket
import time

IDLE_KEEP_ALIVE = "60s"
STARTUP_SECONDS = 30
STREAM_IDLE_SECONDS = 60
MAX_CHUNK_BYTES = 1024 * 1024


def maximum_context(metadata):
    info = metadata.get("model_info", {})
    if not isinstance(info, dict):
        raise ValueError("Ollama did not report model context metadata.")
    key = str(info.get("general.architecture", "")) + ".context_length"
    value = info.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        values = [v for k, v in info.items() if k.endswith(".context_length")
                  and isinstance(v, int) and not isinstance(v, bool) and v > 0]
        if not values:
            raise ValueError("Cannot determine this model's maximum context; update Ollama/model metadata.")
        value = min(values)
    return value


class ChatStream:
    """The metadata lookup and first content/thinking token share one startup deadline."""
    def __init__(self, model, messages):
        self.model, self.messages = model, messages
        self.connection = self.response = self.socket = None
        self.cancelled = False
        self.started = self.complete = False
        self.deadline = None

    def remaining(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("AI startup exceeded 30 seconds. No automatic warm-up or retry was sent.")
        return remaining

    def request(self, path, payload):
        self.connection = http.client.HTTPConnection("127.0.0.1", 11434, timeout=self.remaining())
        self.connection.connect()
        self.socket = self.connection.sock
        if self.cancelled:
            self.close(); raise ConnectionAbortedError("AI request cancelled.")
        self.connection.request("POST", path, json.dumps(payload).encode(), {"Content-Type": "application/json"})
        self.socket.settimeout(self.remaining())
        self.response = self.connection.getresponse()
        if self.response.status != 200:
            raise RuntimeError(f"Ollama returned HTTP {self.response.status}: " + self.response.read(4096).decode(errors="replace"))
        return self.response

    def __enter__(self):
        self.deadline = time.monotonic() + STARTUP_SECONDS
        try:
            response = self.request("/api/show", {"model": self.model})
            data = response.read(2 * MAX_CHUNK_BYTES + 1)
            if len(data) > 2 * MAX_CHUNK_BYTES:
                raise ValueError("Ollama model metadata exceeded the limit.")
            context = maximum_context(json.loads(data))
            self._close_connection()
            self.request("/api/chat", {"model": self.model, "messages": self.messages,
                "stream": True, "keep_alive": IDLE_KEEP_ALIVE,
                "options": {"num_ctx": context, "num_predict": -1}})
            return self
        except (TimeoutError, socket.timeout) as exc:
            self._close_connection()
            raise TimeoutError("AI startup exceeded 30 seconds. Try again explicitly or use a smaller model/context.") from exc
        except BaseException:
            self._close_connection(); raise

    def __iter__(self):
        return self

    def __next__(self):
        if self.complete or self.cancelled:
            raise StopIteration
        try:
            while True:
                sock, response = self.socket, self.response
                if self.cancelled: raise StopIteration
                if sock is None or response is None: raise ConnectionError("AI connection closed.")
                sock.settimeout(STREAM_IDLE_SECONDS if self.started else self.remaining())
                raw = response.readline(MAX_CHUNK_BYTES + 1)
                if not raw:
                    raise ConnectionError("Model stream ended without completion.")
                if len(raw) > MAX_CHUNK_BYTES:
                    raise ValueError("Model chunk exceeded the limit.")
                event = json.loads(raw)
                if event.get("error"):
                    raise RuntimeError(str(event["error"]))
                if not self.started:
                    message = event.get("message", {})
                    if message.get("content") or message.get("thinking") or event.get("done"):
                        self.started = True
                self.complete = bool(event.get("done"))
                return event
        except (TimeoutError, socket.timeout) as exc:
            if self.cancelled: raise StopIteration from None
            reason = "AI response went idle for 60 seconds." if self.started else "AI startup exceeded 30 seconds."
            raise TimeoutError(reason + " No automatic warm-up or retry was sent.") from exc
        except Exception:
            if self.cancelled: raise StopIteration from None
            raise

    def _close_connection(self):
        # shutdown interrupts a pending read when Stop is pressed from the UI thread.
        sock, response, connection = self.socket, self.response, self.connection
        self.connection = self.response = self.socket = None
        if sock:
            try: sock.shutdown(socket.SHUT_RDWR)
            except OSError: pass
        if response: response.close()
        if connection: connection.close()

    def close(self):
        self.cancelled = True
        # Only interrupt the socket here. The reading thread owns HTTPResponse
        # cleanup in __exit__; closing its file concurrently races readline().
        sock = self.socket
        if sock:
            try: sock.shutdown(socket.SHUT_RDWR)
            except OSError: pass
            # Windows does not wake a recv blocked in another thread on
            # shutdown(); detach the handle and close it through Winsock.
            if os.name == "nt" and sock.fileno() != -1:
                try:
                    import ctypes
                    handle = sock.detach()
                    ctypes.windll.ws2_32.closesocket(ctypes.c_size_t(handle))
                except (OSError, AttributeError):
                    pass

    def __exit__(self, *args):
        self._close_connection()
