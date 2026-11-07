import io
import json
import os
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

import pytest

from brain.emotion_config import EmotionConfig
from brain.emotion_debug import Repl, SimulatedClock, cmd_live, main
from brain.emotion_engine import EmotionEngine


class Clock:
    def __init__(self, now=1_000.0):
        self.now = now

    def __call__(self):
        return self.now


def run(argv):
    out = io.StringIO()
    code = main(argv, out=out)
    return code, out.getvalue()


def repl_output(script, start=5_000.0):
    out = io.StringIO()
    Repl(EmotionConfig(), out=out, clock=SimulatedClock(start)).run(script.splitlines())
    return out.getvalue()


@pytest.fixture
def planner():
    from brain.memory import Memory
    from brain.planner import Planner

    with tempfile.NamedTemporaryFile(delete=False) as handle:
        path = handle.name
    with patch("brain.planner.load_env_file", return_value={}):
        os.environ.pop("GEMINI_API_KEY", None)
        yield Planner(Memory(path=path))
    if os.path.exists(path):
        os.remove(path)


# ---- decay threshold (regression: every planner turn logged micro-decay)

def test_planner_turn_does_not_log_microscopic_decay(planner):
    planner.respond("this is broken and nothing works")
    names = [e["event"] for e in planner.emotion_engine.events()]
    assert "emotion.decayed" not in names
    entity = planner.last_observation.entity
    assert len(planner.emotion_engine.history(entity)) == 1


def test_small_decay_is_returned_but_not_recorded():
    clock = Clock()
    engine = EmotionEngine(clock=clock)
    engine.process("I'm frustrated", entity="user")
    start = engine.state("user").emotions["frustrated"]
    clock.now += 30
    decayed = engine.state("user").emotions["frustrated"]
    assert decayed < start                      # caller sees true decayed value
    assert len(engine.history("user")) == 1     # but nothing was recorded
    assert "emotion.decayed" not in [e["event"] for e in engine.events()]


def test_unrecorded_reads_do_not_drift():
    clock_a, clock_b = Clock(), Clock()
    a, b = EmotionEngine(clock=clock_a), EmotionEngine(clock=clock_b)
    for engine in (a, b):
        engine.process("I'm frustrated", entity="user")
    for _ in range(100):  # many tiny reads on a
        clock_a.now += 1
        a.state("user")
    clock_b.now += 100    # one read on b
    assert a.state("user").emotions == pytest.approx(b.state("user").emotions)


def test_large_decay_is_recorded():
    clock = Clock()
    engine = EmotionEngine(clock=clock)
    engine.process("I'm frustrated", entity="user")
    clock.now += 1200
    engine.state("user")
    assert len(engine.history("user")) == 2
    assert "emotion.decayed" in [e["event"] for e in engine.events()]


def test_zero_threshold_records_any_decay():
    clock = Clock()
    engine = EmotionEngine(clock=clock, change_threshold=0)
    engine.process("I'm frustrated", entity="user")
    clock.now += 1
    engine.state("user")
    assert len(engine.history("user")) == 2


# ---- analyze

def test_analyze_text_shows_observation_state_tendency_and_events():
    code, out = run(["analyze", "I'm stuck and nothing works", "--regulate", "--provider", "rule"])
    assert code == 0
    assert "provider: rule" in out
    assert "observed user: frustrated" in out
    assert "aegis tendency: concerned" in out
    assert "neutral -> frustrated (observation" in out
    out.encode("ascii")  # renders on any Windows console


def test_analyze_json_includes_full_event_log():
    code, out = run(["analyze", "I'm worried", "--json", "--provider", "rule"])
    data = json.loads(out)
    assert data["provider"] == "rule"
    assert data["events"] == [e["event"] for e in data["event_log"]]
    assert data["event_log"][0]["observation"]["primary"] == "anxious"


def test_legacy_engine_cli_still_works(capsys):
    from brain.emotion_engine import main as engine_main

    assert engine_main(["I am furious", "--entity", "user:demo", "--regulate", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["observation"]["entity"] == "user:demo"
    assert "aegis_state" in data


def test_analyze_uses_config_file(tmp_path):
    path = tmp_path / "emotion.yaml"
    path.write_text("regulation: {concern_base: 0.9, concern_gain: 0.1}\n", encoding="utf-8")
    _, out = run(["analyze", "I'm frustrated", "--regulate", "--json", "--config", str(path)])
    assert json.loads(out)["aegis_state"]["emotions"]["concerned"] > .9


# ---- repl

def test_repl_turn_and_commands():
    out = repl_output("\n".join([
        "I'm so frustrated, nothing works",
        ":history 5",
        ":entity user:sam",
        "thanks, that's awesome",
        ":state",
        ":events 3",
        ":provider",
        ":config",
    ]))
    assert "observed user:debug: frustrated" in out
    assert "speaker is now user:sam" in out
    assert "observed user:sam: happy" in out
    assert "state user:debug:" in out and "state user:sam:" in out and "state aegis:" in out
    assert "provider is rule; available:" in out
    assert "concern_gain: 0.45" in out


def test_repl_wait_shows_decay_and_decay_cause():
    out = repl_output("I'm so frustrated, nothing works\n:wait 3600")
    assert "(simulated clock +3600s, total +3600s)" in out
    assert "concerned -> calm (decay" in out


def test_repl_time_is_frozen_without_wait():
    out = repl_output("I'm so frustrated, nothing works\n:state\n:state\n:state")
    assert "decayed" not in out


def test_repl_is_reproducible():
    script = "I'm frustrated\n:wait 600\nthanks\n:wait 60\n:history 10"
    assert repl_output(script) == repl_output(script)


def test_repl_bad_input_does_not_end_session():
    out = repl_output(":wait abc\n:wait -5\n:bogus\n:provider nope\nI'm worried")
    assert out.count("usage: :wait SECONDS") == 2
    assert "unknown command :bogus" in out
    assert "observed user:debug: anxious" in out


def test_repl_quit_stops_reading():
    out = repl_output("I'm worried\n:quit\nI'm furious")
    assert "anxious" in out and "angry" not in out


def test_repl_reset_starts_fresh():
    out = repl_output("I'm frustrated\n:reset\n:state")
    assert "(fresh engine)" in out
    tail = out.split("(fresh engine)")[1]
    assert "frustrated" not in tail


def test_repl_survives_event_log_rollover():
    out = io.StringIO()
    repl = Repl(EmotionConfig(), out=out, clock=SimulatedClock(0))
    repl.engine.event_limit = 4
    repl.run(["I'm frustrated", "I'm worried", "thanks"])
    assert "observed user:debug: grateful" in out.getvalue()


# ---- config

def test_config_command_reports_source_and_override():
    code, out = run(["config", "--json", "--provider", "auto"])
    data = json.loads(out)
    assert code == 0
    assert data["provider"] == "auto"
    assert data["source"].endswith("emotion.yaml")
    assert data["engine"]["change_threshold"] == .03


# ---- live

# Avoids "I'm ...": brain.memory.extract_name would read the feeling as a name.
def bridge_payloads(planner, text="this is broken and nothing works"):
    from aegis_bridge import AegisBridge

    if text:
        planner.respond(text)
    bridge = AegisBridge(planner)
    # Round-trip through JSON exactly as the HTTP endpoint would.
    return {"/emotion": json.loads(json.dumps(bridge.emotion_state())),
            "/emotion/debug": json.loads(json.dumps(bridge.emotion_debug_state()))}


class LiveArgs:
    url, json, events, history = "http://test", False, 15, 5


def test_live_renders_real_bridge_payloads(planner):
    payloads = bridge_payloads(planner)
    out = io.StringIO()
    assert cmd_live(LiveArgs(), out, fetch=lambda url: payloads[url.removeprefix("http://test")]) == 0
    text = out.getvalue()
    assert "user (blended): frustrated" in text
    assert "user (this turn): frustrated" in text
    assert "aegis: concerned" in text
    assert "trust=low" in text  # guest
    assert "neutral -> frustrated" in text


def test_live_before_any_turn(planner):
    payloads = bridge_payloads(planner, text=None)
    out = io.StringIO()
    assert cmd_live(LiveArgs(), out, fetch=lambda url: payloads[url.removeprefix("http://test")]) == 0
    assert "no turns observed yet" in out.getvalue()


def test_live_over_real_http(planner):
    payloads = bridge_payloads(planner)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps(payloads[self.path]).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        code, out = run(["live", "--url", f"http://127.0.0.1:{server.server_port}", "--json"])
    finally:
        server.shutdown()
    assert code == 0
    assert json.loads(out)["emotion"]["user_emotion"] == "frustrated"


def test_live_unreachable_bridge_exits_1():
    code, out = run(["live", "--url", "http://127.0.0.1:9"])
    assert code == 1
    assert "could not reach the AEGIS bridge" in out
