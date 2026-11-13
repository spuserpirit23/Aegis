"""Emotion engine debugger.

    python -m brain.emotion_debug analyze "I'm stuck and nothing works" --regulate
    python -m brain.emotion_debug repl            # interactive, with a simulated clock
    python -m brain.emotion_debug live            # inspect a running AEGIS bridge
    python -m brain.emotion_debug config          # effective tuning and its source

Engines are built from ``mind/emotion.yaml`` plus environment overrides, the
same way the planner builds them. Output is ASCII-only so it renders in any
Windows console.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Iterable, TextIO

from .emotion_config import EmotionConfig, load_emotion_config
from .emotion_engine import EmotionEngine, EmotionObservation, EmotionState
from .emotion_providers import PROVIDER_CHOICES, build_emotion_engine

DEFAULT_BRIDGE_URL = "http://127.0.0.1:8765"
BAR_WIDTH = 20


# ---------------------------------------------------------------- rendering

def jsonable(value: Any) -> Any:
    """Convert states, observations and nested containers to plain JSON data."""
    if hasattr(value, "to_dict"):
        return jsonable(value.to_dict())
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return value


def bar(score: float, width: int = BAR_WIDTH) -> str:
    filled = round(max(0.0, min(1.0, score)) * width)
    return "#" * filled + "." * (width - filled)


def _num(value: Any, digits: int = 2) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def _emotion_lines(emotions: dict[str, float], indent: str = "    ") -> list[str]:
    if not emotions:
        return [indent + "(no active emotions)"]
    width = max(len(name) for name in emotions)
    ordered = sorted(emotions.items(), key=lambda item: -item[1])
    return [f"{indent}{name:<{width}}  {bar(score)}  {score:.2f}" for name, score in ordered]


def format_state(label: str, state: EmotionState | dict) -> str:
    data = jsonable(state)
    head = (f"{label}: {data['primary']}  intensity={_num(data['intensity'])}  "
            f"valence={_num(data['valence'])}  arousal={_num(data['arousal'])}  "
            f"confidence={_num(data['confidence'])}  source={data['source']}")
    return "\n".join([head, *_emotion_lines(data["emotions"])])


def format_observation(observation: EmotionObservation | dict) -> str:
    data = jsonable(observation)
    head = (f"observed {data['entity']}: {data['primary']}  "
            f"confidence={_num(data['confidence'])}  source={data['source']}")
    return "\n".join([head, *_emotion_lines(data["emotions"])])


def format_event(event: dict) -> str:
    name, entity = event.get("event", "?"), event.get("entity", "?")
    short = name.removeprefix("emotion.")
    if name == "emotion.changed":
        detail = f"{event['previous']} -> {event['current']} ({event['cause']}, intensity {event['intensity']:.2f})"
    elif name == "emotion.provider_fallback":
        detail = f"{event['provider']} failed -> {event['fallback']}: {event['error']}"
    elif name == "emotion.detected":
        observation = jsonable(event["observation"])
        detail = f"{observation['primary']} via {event['source']}"
    elif "state" in event:
        detail = jsonable(event["state"])["primary"]
    else:
        detail = ""
    return f"  {short:<18} {entity:<14} {detail}"


def format_history(states: Iterable[EmotionState | dict], limit: int) -> str:
    rows = [jsonable(s) for s in states][-limit:]
    if not rows:
        return "  (no history)"
    return "\n".join(
        f"  {i:>3}. {row['primary']:<12} intensity={row['intensity']:.2f}  "
        f"valence={_num(row['valence'])}  source={row['source']}"
        for i, row in enumerate(rows, 1))


# ---------------------------------------------------------- engine building

class SimulatedClock:
    """Frozen clock that only ``:wait`` moves, so sessions are reproducible:
    decay happens when you ask for it, not while you are typing."""

    def __init__(self, start: float | None = None) -> None:
        self.start = time.time() if start is None else start
        self.offset = 0.0

    def __call__(self) -> float:
        return self.start + self.offset

    def advance(self, seconds: float) -> None:
        self.offset += seconds


def _gemini_client(config: EmotionConfig):
    if config.provider not in ("cloud", "auto"):
        return None
    try:
        from .planner import load_env_file  # also reads .env, like the app does
        load_env_file()
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            return None
        from google import genai
        return genai.Client(api_key=key)
    except Exception as exc:  # missing SDK etc.: provider selection will warn and use rules
        print(f"note: could not create Gemini client ({exc})", file=sys.stderr)
        return None


def load_config(args) -> EmotionConfig:
    config = load_emotion_config(getattr(args, "config", None))
    if getattr(args, "provider", None):
        config = dataclasses.replace(config, provider=args.provider)
    return config


def make_engine(config: EmotionConfig, **kwargs) -> EmotionEngine:
    return build_emotion_engine(config=config, cloud_client=_gemini_client(config), **kwargs)


# ------------------------------------------------------------------ analyze

def cmd_analyze(args, out: TextIO) -> int:
    engine = make_engine(load_config(args))
    observation = engine.process(args.text, entity=args.entity)
    aegis = engine.regulate(observation) if args.regulate else None
    if args.json:
        payload: dict[str, Any] = {
            "provider": engine.provider_name,
            "observation": jsonable(observation),
            "state": jsonable(engine.state(args.entity)),
            "events": [event["event"] for event in engine.events()],
            "event_log": jsonable(engine.events()),
        }
        if aegis is not None:
            payload["aegis_state"] = jsonable(aegis)
        print(json.dumps(payload, indent=2, sort_keys=True), file=out)
        return 0
    print(f"provider: {engine.provider_name}", file=out)
    print(format_observation(observation), file=out)
    print(format_state(f"state {args.entity}", engine.state(args.entity)), file=out)
    if aegis is not None:
        print(format_state("aegis tendency", aegis), file=out)
    print("events:", file=out)
    for event in engine.events():
        print(format_event(event), file=out)
    return 0


# --------------------------------------------------------------------- repl

REPL_HELP = """\
Type a message to analyze it as the current speaker. Commands:
  :state [entity]        current state (all entities if omitted)
  :history [entity] [n]  last n states (default: current speaker, 10)
  :events [n]            last n events (default 10)
  :wait SECONDS          advance the simulated clock, then show decayed state
                         (time is frozen otherwise, so results are repeatable)
  :entity NAME           change who is speaking (default user:debug)
  :provider NAME         switch provider (rule, local, cloud, auto if available)
  :config                show effective tuning
  :reset                 start over with a fresh engine
  :help                  this text
  :quit                  exit"""


class Repl:
    def __init__(self, config: EmotionConfig, *, entity: str = "user:debug", out: TextIO = sys.stdout,
                 clock: SimulatedClock | None = None) -> None:
        self.config, self.entity, self.out = config, entity, out
        self.clock = clock or SimulatedClock()
        self.engine = make_engine(config, clock=self.clock)
        self._seen_events = 0

    def say(self, text: str = "") -> None:
        print(text, file=self.out)

    def _new_events(self) -> list[dict]:
        events = self.engine.events()
        # The engine log is bounded; if it rolled over, show what is left.
        start = self._seen_events if self._seen_events <= len(events) else 0
        self._seen_events = len(events)
        return events[start:]

    def _print_new_events(self) -> None:
        for event in self._new_events():
            self.say(format_event(event))

    def turn(self, text: str) -> None:
        observation = self.engine.process(text, entity=self.entity)
        aegis = self.engine.regulate(observation)
        self.say(format_observation(observation))
        self.say(format_state(f"state {self.entity}", self.engine.state(self.entity)))
        self.say(format_state("aegis tendency", aegis))
        self._print_new_events()

    def command(self, line: str) -> bool:
        """Run a ':' command. Returns False when the session should end."""
        name, *rest = line[1:].split()
        if name in ("quit", "q", "exit"):
            return False
        if name == "help":
            self.say(REPL_HELP)
        elif name == "state":
            targets = rest or self.engine.entities() or [self.entity]
            for entity in targets:
                self.say(format_state(f"state {entity}", self.engine.state(entity)))
            self._print_new_events()
        elif name == "history":
            entity = rest[0] if rest and not rest[0].isdigit() else self.entity
            numbers = [r for r in rest if r.isdigit()]
            limit = int(numbers[0]) if numbers else 10
            self.say(f"history {entity}:")
            self.say(format_history(self.engine.history(entity), limit))
        elif name == "events":
            limit = int(rest[0]) if rest and rest[0].isdigit() else 10
            events = self.engine.events()[-limit:]
            self.say("\n".join(format_event(e) for e in events) if events else "  (no events)")
        elif name == "wait":
            try:
                seconds = float(rest[0])
                if seconds < 0:
                    raise ValueError
            except (IndexError, ValueError):
                self.say("usage: :wait SECONDS (a non-negative number)")
                return True
            self.clock.advance(seconds)
            self.say(f"(simulated clock +{seconds:g}s, total +{self.clock.offset:g}s)")
            for entity in self.engine.entities():
                self.say(format_state(f"state {entity}", self.engine.state(entity)))
            self._print_new_events()
        elif name == "entity":
            if not rest:
                self.say(f"speaker is {self.entity}")
            else:
                self.entity = rest[0]
                self.say(f"speaker is now {self.entity}")
        elif name == "provider":
            available = sorted(self.engine.providers)
            if not rest or rest[0] not in self.engine.providers:
                self.say(f"provider is {self.engine.provider_name}; available: {', '.join(available)}")
            else:
                self.engine.use_provider(rest[0])
                self.say(f"provider is now {rest[0]}")
        elif name == "config":
            self.say(format_config(self.engine.config or self.config))
        elif name == "reset":
            self.clock = SimulatedClock(self.clock.start)
            self.engine = make_engine(self.config, clock=self.clock)
            self._seen_events = 0
            self.say("(fresh engine)")
        else:
            self.say(f"unknown command :{name} (try :help)")
        return True

    def run(self, lines: Iterable[str], *, prompt: bool = False) -> int:
        self.say(f"emotion debugger - provider {self.engine.provider_name}, speaker {self.entity}. "
                 ":help for commands.")
        for raw in lines:
            line = raw.strip()
            if not line:
                continue
            try:
                if line.startswith(":"):
                    if not self.command(line):
                        break
                else:
                    self.turn(line)
            except Exception as exc:  # keep the session alive on bad input
                self.say(f"error: {type(exc).__name__}: {exc}")
            if prompt:
                self.out.write("> ")
                self.out.flush()
        return 0


def _stdin_lines(prompt: bool) -> Iterable[str]:
    if prompt:
        sys.stdout.write("> ")
        sys.stdout.flush()
    for line in sys.stdin:
        yield line


def cmd_repl(args, out: TextIO) -> int:
    interactive = sys.stdin.isatty() and out is sys.stdout
    return Repl(load_config(args), entity=args.entity, out=out).run(
        _stdin_lines(interactive), prompt=interactive)


# --------------------------------------------------------------------- live

def _bridge_token() -> str | None:
    from .bridge_auth import TOKEN_ENV, read_token_file
    return os.environ.get(TOKEN_ENV, "").strip() or read_token_file()


def fetch_json(url: str, timeout: float = 3.0) -> dict:
    request = urllib.request.Request(url)
    token = _bridge_token()
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def cmd_live(args, out: TextIO, fetch: Callable[[str], dict] = fetch_json) -> int:
    base = args.url.rstrip("/")
    try:
        summary = fetch(base + "/emotion")
        debug = fetch(base + "/emotion/debug")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"could not reach the AEGIS bridge at {base} ({exc}).\n"
              "Start the app (run_ui.bat or python aegis_bridge.py) or pass --url.", file=out)
        return 1
    if args.json:
        print(json.dumps({"emotion": summary, "debug": debug}, indent=2, sort_keys=True), file=out)
        return 0
    entity = debug.get("entity", "?")
    print(f"bridge {base}  speaker {entity}  source={summary.get('source')}", file=out)
    context = summary.get("context") or {}
    user = context.get("user")
    if user:
        print(f"user (blended): {user['emotion']}  intensity={user['intensity']:.2f}  "
              f"valence={_num(user['valence'])}", file=out)
        print(f"user (this turn): {user['turn_emotion']}  confidence={user['confidence']:.2f}  "
              f"source={user['source']}", file=out)
    else:
        print("user: no turns observed yet", file=out)
    print(f"aegis: {summary.get('emotion', '?').lower()}  intensity={summary.get('intensity', 0):.2f}  "
          f"trust={context.get('trust', '?')}  label={context.get('label', '?')}", file=out)
    print(*_emotion_lines(summary.get("secondary") or {}), sep="\n", file=out)
    print(f"history {entity}:", file=out)
    print(format_history(debug.get("user_history", []), args.history), file=out)
    print("history aegis:", file=out)
    print(format_history(debug.get("aegis_history", []), args.history), file=out)
    events = debug.get("events", [])[-args.events:]
    print(f"last {len(events)} events:", file=out)
    for event in events:
        try:
            print(format_event(event), file=out)
        except (KeyError, TypeError):
            print(f"  {event.get('event', '?')}", file=out)
    return 0


# ------------------------------------------------------------------- config

def format_config(config: EmotionConfig) -> str:
    lines = [f"source: {config.source}",
             f"provider: {config.provider}",
             f"local_model: {config.local_model}",
             f"cloud_model: {config.cloud_model}",
             "engine:"]
    lines += [f"  {k}: {v}" for k, v in config.engine_kwargs().items()]
    lines.append("regulation:")
    lines += [f"  {k}: {v}" for k, v in config.regulation_kwargs().items()]
    lines.append("half_lives:" + ("" if config.half_lives else " (built-in)"))
    lines += [f"  {k}: {v:g}" for k, v in sorted(config.half_lives.items())]
    return "\n".join(lines)


def cmd_config(args, out: TextIO) -> int:
    config = load_config(args)
    if args.json:
        payload = {"source": config.source, "provider": config.provider,
                   "local_model": config.local_model, "cloud_model": config.cloud_model,
                   "engine": config.engine_kwargs(), "regulation": config.regulation_kwargs(),
                   "half_lives": dict(config.half_lives)}
        print(json.dumps(payload, indent=2, sort_keys=True), file=out)
    else:
        print(format_config(config), file=out)
    return 0


# --------------------------------------------------------------------- main

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m brain.emotion_debug",
                                     description="Inspect and exercise the AEGIS emotion engine.")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", help="emotion config file (default: mind/emotion.yaml)")
    common.add_argument("--provider", choices=PROVIDER_CHOICES, help="override the configured provider")
    sub = parser.add_subparsers(dest="command", required=True)

    analyze = sub.add_parser("analyze", parents=[common], help="analyze one message")
    analyze.add_argument("text", nargs="?", default="I've been trying this for hours and nothing works!")
    analyze.add_argument("--entity", default="user", help="who produced the text")
    analyze.add_argument("--regulate", action="store_true", help="also show AEGIS's regulated tendency")
    analyze.add_argument("--json", action="store_true", help="machine-readable output")

    repl = sub.add_parser("repl", parents=[common], help="interactive session with a simulated clock")
    repl.add_argument("--entity", default="user:debug", help="initial speaker")

    live = sub.add_parser("live", help="inspect a running AEGIS bridge")
    live.add_argument("--url", default=DEFAULT_BRIDGE_URL)
    live.add_argument("--events", type=int, default=15, help="how many recent events to show")
    live.add_argument("--history", type=int, default=5, help="how many history states to show")
    live.add_argument("--json", action="store_true")

    config = sub.add_parser("config", parents=[common], help="show effective tuning and its source")
    config.add_argument("--json", action="store_true")
    return parser


_COMMANDS = {"analyze": cmd_analyze, "repl": cmd_repl, "live": cmd_live, "config": cmd_config}


def main(argv: list[str] | None = None, out: TextIO | None = None) -> int:
    args = build_parser().parse_args(argv)
    return _COMMANDS[args.command](args, out or sys.stdout)


if __name__ == "__main__":
    raise SystemExit(main())
