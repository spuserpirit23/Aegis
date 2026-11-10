import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from brain.emotion_context import ResponseEmotionContext
from brain.permission import allowed_skills
from brain.planner import Planner, TOOLS
from brain.trust import trust_breakdown
from voice.tts import speak, tts_status
from voice.stt import listen, microphone_status


class AegisBridge:
    def __init__(self, planner: Planner, host: str = "127.0.0.1", port: int = 8765):
        self.planner = planner
        self.host = host
        self.port = port
        self.lock = threading.Lock()
        # Serializes whole turns (planner call + speech) so overlapping
        # requests queue instead of interleaving state or talking over
        # each other. The planner also holds its own lock per turn.
        self._turn_lock = threading.Lock()
        self._inflight = 0
        self.reply = "Aegis is ready."
        self.status = "ready"

        # Analytics and runtime telemetry (all measured, nothing asserted)
        self.start_time = time.time()
        self.total_requests = 0
        self.failed_requests = 0
        self.last_error = ""
        self.last_latency_ms = 0.0
        self.last_ui_poll = None  # time.time() of the last GET /state

        # Voice listening state
        self._listen_status = "idle"   # idle | listening | transcribing | done | error
        self._listen_text = ""
        self._listen_error = ""

    def _get_active_user(self):
        # Read-only: never creates or selects a bucket as a side effect.
        return self.planner.current_user

    def state(self) -> dict:
        user = self._get_active_user()
        trust = trust_breakdown(user)
        return {
            "reply": self.reply,
            "status": self.status,
            "emotion": getattr(self.planner, "last_emotion", "neutral").upper(),
            "trust": trust.level.upper(),
            "trust_score": trust.percent,
            "user": user.display_name,
            "pending_name": self.planner.pending_name,
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
                "active_user": user.display_name,
                "active_user_key": user.key,
                "is_guest": user.is_guest,
                "persistent": not user.is_guest,
                "pending_name": self.planner.pending_name,
                "known_users": all_users,
                "facts": {k: v for k, v in user.facts.items() if not k.startswith("_")},
                "turn_count": len(user.turns),
                "recent_turns": user.recent()[-6:],
                "total_users": len(all_users),
                "storage_file": str(self.planner.memory.path),
                "storage_exists": self.planner.memory.path.exists(),
            }

    def trust_state(self) -> dict:
        with self.lock:
            user = self._get_active_user()
            t = trust_breakdown(user)
            return {
                "user": user.display_name,
                "trust_level": t.level.upper(),
                "score": t.score,
                "scaled_score": t.percent,
                "turn_count": t.turn_count,
                "is_guest": t.is_guest,
                # Self-declared and confirmed in conversation; not verified.
                "relationship_confirmed": t.identity_confirmed,
                "identity_basis": ("guest (unidentified)" if t.is_guest
                                   else "self-declared, confirmed in conversation"),
                "points_breakdown": {
                    "known_relationship": t.known_relationship,
                    "interaction_history": t.interaction_history,
                    "correction_penalty": t.correction_penalty,
                },
                "reason": t.reason,
                "allowed_skills": allowed_skills(),
            }

    def emotion_state(self) -> dict:
        with self.lock:
            user = self._get_active_user()
            last_text = ""
            for turn in reversed(user.turns):
                if turn.get("role") == "user":
                    last_text = turn.get("content", "")
                    break
            ctx = getattr(self.planner, "emotion_context", None) or ResponseEmotionContext.baseline(
                getattr(self.planner, "last_emotion", "neutral"))
            aegis, observed = ctx.aegis, ctx.user
            valence_score = round(aegis.valence, 3)
            valence = "POSITIVE" if valence_score > 0.15 else ("NEGATIVE" if valence_score < -0.15 else "NEUTRAL")
            return {
                "emotion": aegis.tendency.upper(),
                "intensity": round(aegis.intensity, 3),
                "last_user_input": last_text,
                "valence": valence,
                "valence_score": valence_score,
                "arousal": round(aegis.arousal, 3),
                "dominance": round(aegis.dominance, 3),
                "confidence": round(aegis.confidence, 3),
                "secondary": {k: round(v, 3) for k, v in aegis.secondary.items()},
                "source": aegis.source,
                # This turn's evidence (unchanged meaning from before the typed context).
                "user_emotion": observed.turn_emotion if observed else None,
                "user_valence": (round(observed.turn_valence, 3)
                                 if observed and observed.turn_valence is not None else None),
                "context": ctx.to_dict(),
                "provider": getattr(self.planner.emotion_engine, "provider_name", None),
            }

    def emotion_debug_state(self) -> dict:
        """Return bounded state history and event names for UI/debug tooling."""
        with self.lock:
            user = self._get_active_user()
            entity = f"user:{user.key}"
            engine = self.planner.emotion_engine

            def serialise(value):
                if hasattr(value, "to_dict"):
                    return value.to_dict()
                if isinstance(value, dict):
                    return {key: serialise(item) for key, item in value.items()}
                if isinstance(value, list):
                    return [serialise(item) for item in value]
                return value

            return {
                "entity": entity,
                "user_history": [state.to_dict() for state in engine.history(entity)],
                "aegis_history": [state.to_dict() for state in engine.history("aegis")],
                "events": [serialise(event) for event in engine.events()],
                "bus_events": [serialise(event) for event in self.planner.event_bus.events()],
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
                "failed_requests": self.failed_requests,
                "last_error": self.last_error,
                "in_flight": self._inflight,
                "last_latency_ms": round(self.last_latency_ms, 1),
                "total_turns": total_turns,
                "total_facts_stored": total_facts,
                "fallback_mode": self.planner.uses_fallback,
                "model": self.planner.model,
            }

    def integrations_state(self) -> dict:
        with self.lock:
            mic_info = microphone_status()
            ui_age = None if self.last_ui_poll is None else round(time.time() - self.last_ui_poll, 1)
            if self.planner.uses_fallback:
                gemini_status = "NOT CONFIGURED"
            elif self.last_error and self.failed_requests:
                gemini_status = "ERROR"
            elif self.total_requests:
                gemini_status = "OK"
            else:
                gemini_status = "CONFIGURED (untested)"
            tts = tts_status()
            return {
                "python_bridge": {
                    "status": "ONLINE",  # true by construction: it answered
                    "endpoint": f"http://{self.host}:{self.port}",
                    "active_threads": threading.active_count(),
                },
                "gemini_api": {
                    "status": gemini_status,
                    "model": self.planner.model,
                    "last_error": self.last_error,
                },
                "speech_stt": {
                    "status": "READY" if mic_info.startswith("microphone ready") else "UNAVAILABLE",
                    "detail": mic_info,
                },
                "speech_tts": {
                    "status": tts["status"],
                    "engine": "pyttsx3 SAPI5",
                    "detail": tts["detail"],
                },
                "godot_frontend": {
                    "status": ("NOT SEEN" if ui_age is None
                               else "CONNECTED" if ui_age < 5 else "STALE"),
                    "last_poll_seconds_ago": ui_age,
                },
                "skills": {
                    name: ("ENABLED" if name in allowed_skills() else "BLOCKED BY PERMISSION")
                    for name in self.planner.skills
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
                "registered_skills": list(self.planner.skills.keys()),
                "allowed_skills": allowed_skills(),
                "tools": tools_list,
            }

    def settings_state(self) -> dict:
        with self.lock:
            user = self._get_active_user()
            return {
                "active_user": user.display_name,
                "bridge_url": f"http://{self.host}:{self.port}",
                "model": self.planner.model,
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
            self._inflight += 1
            self.status = "thinking"
            self.total_requests += 1
        try:
            with self._turn_lock:
                with self.lock:
                    self.status = "thinking"
                t0 = time.monotonic()
                try:
                    reply = self.planner.respond(text)
                except Exception as error:
                    with self.lock:
                        self.failed_requests += 1
                        self.last_error = f"{type(error).__name__}: {error}"
                        self.reply = f"I hit an error: {error}"
                    return
                lat = (time.monotonic() - t0) * 1000.0
                with self.lock:
                    self.reply = reply
                    self.status = "speaking"
                    self.last_latency_ms = lat
                    self.last_error = ""
                speak(reply)
        finally:
            with self.lock:
                self._inflight -= 1
                self.status = "thinking" if self._inflight else "ready"

    def reset(self) -> None:
        """Clear all conversation state: planner turns/interaction chain/
        pending identity/emotion, plus the bridge's reply and voice state."""
        with self._turn_lock:
            self.planner.reset_conversation()
            with self.lock:
                self.reply = "Aegis conversation reset."
                self.status = "ready"
                self.last_error = ""
                self._listen_status = "idle"
                self._listen_text = ""
                self._listen_error = ""

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
                self.bridge.last_ui_poll = time.time()
                payload = self.bridge.state()
            self._send_json(payload)
        elif path == "/listen_status":
            self._send_json(self.bridge.listen_state())
        elif path == "/memory":
            self._send_json(self.bridge.memory_state())
        elif path == "/trust":
            self._send_json(self.bridge.trust_state())
        elif path == "/emotion":
            self._send_json(self.bridge.emotion_state())
        elif path in ("/emotion/debug", "/emotion/history"):
            self._send_json(self.bridge.emotion_debug_state())
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
        if new_user and isinstance(new_user, str) and new_user.strip():
            # Explicitly choosing an identity in settings counts as confirmation.
            self.bridge.planner.set_user(new_user.strip())
        self._send_json({"success": True, "settings": self.bridge.settings_state()})

    def _handle_reset(self) -> None:
        self.bridge.reset()
        self._send_json({"success": True, "message": "Conversation state reset."})

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
