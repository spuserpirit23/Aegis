import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from brain.planner import Planner
from voice.tts import speak
from voice.stt import listen


class AegisBridge:
    def __init__(self, planner: Planner):
        self.planner = planner
        self.lock = threading.Lock()
        self.reply = "Aegis is ready."
        self.status = "ready"

        # Voice listening state
        self._listen_status = "idle"   # idle | listening | done | error
        self._listen_text = ""
        self._listen_error = ""

    def state(self) -> dict:
        return {
            "reply": self.reply,
            "status": self.status,
            "emotion": self.planner.last_emotion.upper(),
            "trust": self.planner.last_trust,
        }

    def listen_state(self) -> dict:
        with self.lock:
            return {
                "listen_status": self._listen_status,
                "text": self._listen_text,
                "error": self._listen_error,
            }

    def respond(self, text: str) -> None:
        with self.lock:
            self.status = "thinking"
        try:
            reply = self.planner.respond(text)
            with self.lock:
                self.reply = reply
                self.status = "speaking"
            speak(reply)
            with self.lock:
                self.status = "ready"
        except Exception as error:
            with self.lock:
                self.reply = f"I hit an error: {error}"
                self.status = "ready"

    def start_listening(self) -> bool:
        """Start voice capture in a background thread. Returns False if
        already listening."""
        with self.lock:
            if self._listen_status == "listening":
                return False
            self._listen_status = "listening"
            self._listen_text = ""
            self._listen_error = ""
            self.status = "listening"

        threading.Thread(target=self._listen_worker, daemon=True).start()
        return True

    def stop_listening(self) -> None:
        """Cancel / reset listening state back to idle."""
        with self.lock:
            self._listen_status = "idle"
            self._listen_text = ""
            self._listen_error = ""
            if self.status == "listening":
                self.status = "ready"

    def _listen_worker(self) -> None:
        """Runs in a background thread — calls voice.stt.listen() which
        blocks while the microphone captures audio."""
        try:
            text = listen()
            with self.lock:
                if self._listen_status != "listening":
                    return  # was cancelled
                if text:
                    self._listen_text = text
                    self._listen_status = "done"
                else:
                    self._listen_error = "No speech detected"
                    self._listen_status = "error"
                self.status = "ready"
        except Exception as exc:
            with self.lock:
                self._listen_error = str(exc)
                self._listen_status = "error"
                self.status = "ready"

    def voice_respond(self, text: str) -> None:
        """Reset listen state then run the normal respond flow."""
        with self.lock:
            self._listen_status = "idle"
            self._listen_text = ""
            self._listen_error = ""
        self.respond(text)


class _Handler(BaseHTTPRequestHandler):
    bridge: AegisBridge

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/state":
            with self.bridge.lock:
                self._send_json(self.bridge.state())
        elif self.path == "/listen_status":
            self._send_json(self.bridge.listen_state())
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        if self.path == "/respond":
            self._handle_respond()
        elif self.path == "/listen":
            self._handle_listen()
        elif self.path == "/listen_cancel":
            self.bridge.stop_listening()
            self._send_json({"cancelled": True})
        elif self.path == "/voice_respond":
            self._handle_voice_respond()
        else:
            self._send_json({"error": "not found"}, 404)

    def _handle_respond(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
            text = str(payload.get("text", "")).strip()
        except (ValueError, json.JSONDecodeError):
            self._send_json({"error": "expected JSON with a text field"}, 400)
            return
        if not text:
            self._send_json({"error": "text cannot be empty"}, 400)
            return
        threading.Thread(target=self.bridge.respond, args=(text,), daemon=True).start()
        self._send_json({"accepted": True})

    def _handle_listen(self) -> None:
        started = self.bridge.start_listening()
        if started:
            self._send_json({"started": True})
        else:
            self._send_json({"started": False, "reason": "already listening"})

    def _handle_voice_respond(self) -> None:
        """Convenience endpoint: takes the captured voice text and
        feeds it directly into the planner (same as /respond but resets
        listen state cleanly)."""
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
            text = str(payload.get("text", "")).strip()
        except (ValueError, json.JSONDecodeError):
            self._send_json({"error": "expected JSON with a text field"}, 400)
            return
        if not text:
            self._send_json({"error": "text cannot be empty"}, 400)
            return
        threading.Thread(
            target=self.bridge.voice_respond, args=(text,), daemon=True
        ).start()
        self._send_json({"accepted": True})

    def log_message(self, format: str, *args) -> None:
        return


def serve(planner: Planner, host: str = "127.0.0.1", port: int = 8765) -> None:
    server = create_server(planner, host, port)
    print(f"Aegis bridge online at http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def create_server(planner: Planner, host: str = "127.0.0.1", port: int = 8765):
    bridge = AegisBridge(planner)
    handler = type("AegisRequestHandler", (_Handler,), {"bridge": bridge})
    return ThreadingHTTPServer((host, port), handler)