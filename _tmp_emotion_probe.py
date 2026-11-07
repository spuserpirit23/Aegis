import time
from brain.emotion_engine import EmotionEngine

t = time.time()
import brain.emotion_engine as e
print("import", round(time.time() - t, 3))

engine = EmotionEngine()
o = engine.process("I've been trying this for hours and nothing works!")
print("obs", o.primary, o.emotions, o.valence, o.arousal, o.confidence)
print("user state", engine.state("user").to_dict())
print("aegis baseline", engine.state("aegis").to_dict())
print("events", [x["event"] for x in engine.events()])
