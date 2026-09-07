import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from brain.planner import Planner
from voice.tts import speak


class AegisBridge:
    def __init__(self, planner: Planner):
        self.planner = planner
        self.lock = threading.Lock()
        self.reply = "Aegis is ready."
        self.status = "ready"

    def state(self) -> dict:
        return {
            "reply": self.reply,
            "status": self.status,
            "emotion": self.planner.last_emotion.upper(),
            "trust": self.planner.last_trust,
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
        if self.path != "/state":
            self._send_json({"error": "not found"}, 404)
            return
        with self.bridge.lock:
            self._send_json(self.bridge.state())

    def do_POST(self) -> None:
        if self.path != "/respond":
            self._send_json({"error": "not found"}, 404)
            return
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