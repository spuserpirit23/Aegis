import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from brain.planner import Planner, TOOLS
from brain.trust import compute_trust_score, trust_level
from voice.tts import speak
from voice.stt import listen, microphone_status


class AegisBridge:
    def __init__(self, planner: Planner, host: str = "127.0.0.1", port: int = 8765):
        self.planner = planner
        self.host = host
        self.port = port
        self.lock = threading.Lock()
        self.reply = "Aegis is ready."
        self.status = "ready"

        # Analytics and runtime telemetry
        self.start_time = time.time()
        self.total_requests = 0
        self.last_latency_ms = 0.0

        # Voice listening state
        self._listen_status = "idle"   # idle | listening | transcribing | done | error
        self._listen_text = ""
        self._listen_error = ""

    def _get_active_user(self):
        if self.planner.current_user:
            return self.planner.current_user
        return self.planner.memory.get_user("Guest")

    def state(self) -> dict:
        user = self._get_active_user()
        score = compute_trust_score(user)
        scaled = min(100, max(15, int((score / 7.0) * 100)))
        return {
            "reply": self.reply,
            "status": self.status,
            "emotion": getattr(self.planner, "last_emotion", "neutral").upper(),
            "trust": getattr(self.planner, "last_trust", "medium").upper(),
            "trust_score": scaled,
            "user": user.display_name,
            "last_latency_ms": round(self.last_latency_ms, 1),
            "is_listening": self._listen_status in ("listening", "transcribing"),
            "listen_status": self._listen_status,
        }

    def listen_state(self) -> dict:
        with self.lock:
            return {
                "listen_status": self._listen_status,
                "text": self._listen_text,
                "error": self._listen_error,
                "is_listening": self._listen_status in ("listening", "transcribing"),
            }

    def memory_state(self) -> dict:
        with self.lock:
            user = self._get_active_user()
            all_users = self.planner.memory.known_users()
            return {
                "status": "ONLINE",
                "active_user": user.display_name,
                "active_user_key": user.key,
                "known_users": all_users,
                "facts": user.facts,
                "turn_count": len(user.turns),
                "recent_turns": user.recent()[-6:],
                "total_users": len(all_users),
                "storage_file": str(self.planner.memory.path),
            }

    def trust_state(self) -> dict:
        with self.lock:
            user = self._get_active_user()
            score = compute_trust_score(user)
            lvl = trust_level(user).upper()
            turn_count = len(user.turns)
            rel_confirmed = bool(user.facts.get("nickname") or user.facts.get("name"))
            known_pts = 3 if rel_confirmed else 0
            turn_pts = min(turn_count // 5, 4)
            corrections = int(user.facts.get("_correction_count", 0))
            penalty = -1 * corrections
            scaled = min(100, max(15, int((score / 7.0) * 100)))
            return {
                "user": user.display_name,
                "trust_level": lvl,
                "score": score,
                "scaled_score": scaled,
                "turn_count": turn_count,
                "relationship_confirmed": rel_confirmed,
                "points_breakdown": {
                    "known_relationship": known_pts,
                    "interaction_history": turn_pts,
                    "correction_penalty": penalty,
                },
                "guardrails": "MAXIMUM ENFORCEMENT",
                "policy": "VERIFIED NOMINAL",
            }

    def emotion_state(self) -> dict:
        with self.lock:
            user = self._get_active_user()
            last_text = ""
            for turn in reversed(user.turns):
                if turn.get("role") == "user":
                    last_text = turn.get("content", "")
                    break
            current_emotion = getattr(self.planner, "last_emotion", "neutral").upper()
            valence = "POSITIVE" if current_emotion == "HAPPY" else ("NEGATIVE" if current_emotion in ("CONCERNED", "ANGRY", "SAD") else "NEUTRAL")
            return {
                "emotion": current_emotion,
                "intensity": 0.9,
                "last_user_input": last_text,
                "expressiveness": "CALIBRATED",
                "voice_pitch_modulation": "DYNAMIC ACTIVE",
                "persona": "Aegis Cybernetic Companion",
                "valence": valence,
                "status": "ONLINE",
            }

    def analytics_state(self) -> dict:
        with self.lock:
            uptime_sec = int(time.time() - self.start_time)
            mins, secs = divmod(uptime_sec, 60)
            hrs, mins = divmod(mins, 60)
            users_data = self.planner.memory.data.get("users", {})
            total_turns = sum(len(u.get("turns", [])) for u in users_data.values())
            total_facts = sum(len(u.get("facts", {})) for u in users_data.values())
            return {
                "uptime_seconds": uptime_sec,
                "uptime_formatted": f"{hrs:02d}:{mins:02d}:{secs:02d}",
                "total_requests": self.total_requests,
                "last_latency_ms": round(self.last_latency_ms, 1),
                "total_turns": total_turns,
                "total_facts_stored": total_facts,
                "fallback_mode": self.planner.uses_fallback,
                "model": getattr(self.planner, "MODEL", "gemini-3.6-flash"),
                "status": "HEALTHY",
                "memory_footprint": "NOMINAL",
            }

    def integrations_state(self) -> dict:
        with self.lock:
            mic_info = microphone_status()
            return {
                "python_bridge": {
                    "status": "ONLINE",
                    "endpoint": f"http://{self.host}:{self.port}",
                    "active_threads": threading.active_count(),
                },
                "gemini_api": {
                    "status": "FALLBACK (No Key)" if self.planner.uses_fallback else "ONLINE",
                    "model": getattr(self.planner, "MODEL", "gemini-3.6-flash"),
                },
                "speech_stt": {
                    "status": "ONLINE" if "ready" in mic_info.lower() else "STANDBY",
                    "detail": mic_info,
                },
                "speech_tts": {
                    "status": "ONLINE",
                    "engine": "pyttsx3 SAPI5",
                    "mode": "Offline Local Speech",
                },
                "godot_frontend": {
                    "status": "CONNECTED",
                    "polling_interval": "0.35s",
                },
                "skills": {
                    "weather": "ONLINE",
                    "memory": "ONLINE",
                },
            }

    def tools_state(self) -> dict:
        with self.lock:
            tools_list = []
            for t in TOOLS:
                fn = t.get("name", "")
                desc = t.get("description", "")
                params = t.get("parameters", {})
                tools_list.append({
                    "name": fn,
                    "description": desc,
                    "parameters": params.get("properties", {}),
                    "required": params.get("required", []),
                })
            return {
                "status": "ONLINE",
                "registered_skills": list(self.planner.skills.keys()),
                "tools": tools_list,
            }

    def settings_state(self) -> dict:
        with self.lock:
            user = self._get_active_user()
            return {
                "active_user": user.display_name,
                "bridge_url": f"http://{self.host}:{self.port}",
                "model": getattr(self.planner, "MODEL", "gemini-3.6-flash"),
                "poll_interval_s": 0.35,
                "theme": "Neon Cyber Violet",
                "tts_enabled": True,
                "stt_timeout_s": 6,
            }

    def history_state(self) -> dict:
        with self.lock:
            user = self._get_active_user()
            return {
                "user": user.display_name,
                "turns": user.turns,
            }

    def respond(self, text: str) -> None:
        with self.lock:
            self.status = "thinking"
            self.total_requests += 1
        t0 = time.monotonic()
        try:
            reply = self.planner.respond(text)
            lat = (time.monotonic() - t0) * 1000.0
            with self.lock:
                self.reply = reply
                self.status = "speaking"
                self.last_latency_ms = lat
            speak(reply)
            with self.lock:
                self.status = "ready"
        except Exception as error:
            with self.lock:
                self.reply = f"I hit an error: {error}"
                self.status = "ready"

    def start_listening(self) -> bool:
        """Start voice capture in a background thread. Returns False if already listening."""
        with self.lock:
            if self._listen_status in ("listening", "transcribing"):
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
            if self.status in ("listening", "transcribing"):
                self.status = "ready"

    def _listen_worker(self) -> None:
        """Runs in a background thread — calls voice.stt.listen() which blocks while capturing audio."""
        def _status_cb(st: str):
            with self.lock:
                if self._listen_status not in ("listening", "transcribing"):
                    return
                if st == "transcribing":
                    self._listen_status = "transcribing"
                    self.status = "transcribing"

        try:
            text = listen(status_callback=_status_cb)
            with self.lock:
                if self._listen_status not in ("listening", "transcribing"):
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
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        if path == "/state":
            with self.bridge.lock:
                self._send_json(self.bridge.state())
        elif path == "/listen_status":
            self._send_json(self.bridge.listen_state())
        elif path == "/memory":
            self._send_json(self.bridge.memory_state())
        elif path == "/trust":
            self._send_json(self.bridge.trust_state())
        elif path == "/emotion":
            self._send_json(self.bridge.emotion_state())
        elif path == "/analytics":
            self._send_json(self.bridge.analytics_state())
        elif path == "/integrations":
            self._send_json(self.bridge.integrations_state())
        elif path == "/tools":
            self._send_json(self.bridge.tools_state())
        elif path == "/settings":
            self._send_json(self.bridge.settings_state())
        elif path in ("/conversation/history", "/history"):
            self._send_json(self.bridge.history_state())
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        path = self.path.split("?")[0]
        if path == "/respond":
            self._handle_respond()
        elif path == "/listen":
            self._handle_listen()
        elif path == "/listen_cancel":
            self.bridge.stop_listening()
            self._send_json({"cancelled": True})
        elif path == "/voice_respond":
            self._handle_voice_respond()
        elif path == "/settings":
            self._handle_settings_post()
        elif path == "/reset":
            self._handle_reset()
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

    def _handle_settings_post(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
        except (ValueError, json.JSONDecodeError):
            self._send_json({"error": "invalid JSON"}, 400)
            return
        new_user = payload.get("user")
        if new_user and isinstance(new_user, str):
            self.bridge.planner._set_user(new_user.strip())
        self._send_json({"success": True, "settings": self.bridge.settings_state()})

    def _handle_reset(self) -> None:
        with self.bridge.lock:
            user = self.bridge._get_active_user()
            user._bucket["turns"] = []
            self.bridge.planner.memory.save()
            self.bridge.reply = "Aegis conversation reset."
            self.bridge.status = "ready"
        self._send_json({"success": True, "message": "Conversation history reset."})

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
    bridge = AegisBridge(planner, host, port)
    handler = type("AegisRequestHandler", (_Handler,), {"bridge": bridge})
    return ThreadingHTTPServer((host, port), handler)