extends Node
class_name AegisSpeakingController

const MOUTH_STATES := [
	"CLOSED",
	"SLIGHTLY_OPEN",
	"OPEN",
	"WIDE_OPEN"
]

@export var speaking := false
@export var speech_rate := 4.0  # cycles per second

@onready var mouth_line: Line2D = $"../GestureRig/FaceRig/Mouth/MouthLine"

var _ready_to_apply := false
var _mouth_base_points: PackedVector2Array
var _speaking_tween: Tween
var _current_mouth_shape: PackedVector2Array = PackedVector2Array([-18, 0, 0, 6, 18, 0])  # neutral default

# Mouth shape definitions (relative to base position)
# Each state: [left, control, right] points for the mouth curve
static var MOUTH_SHAPES := {
	"CLOSED": PackedVector2Array([-18, 0, 0, 0, 18, 0]),
	"SLIGHTLY_OPEN": PackedVector2Array([-16, 0, 0, 4, 16, 0]),
	"OPEN": PackedVector2Array([-14, 0, 0, 10, 14, 0]),
	"WIDE_OPEN": PackedVector2Array([-12, 0, 0, 16, 12, 0]),
}

func _ready() -> void:
	_mouth_base_points = mouth_line.points.duplicate()
	_ready_to_apply = true

func start_speaking() -> void:
	if speaking:
		return
	speaking = true
	_cycle_mouth()

func stop_speaking() -> void:
	speaking = false
	_stop_cycle()
	_restore_emotional_mouth()

func set_emotional_mouth_shape(points: PackedVector2Array) -> void:
	"""Called by ExpressionController to update the emotional base shape."""
	_current_mouth_shape = points.duplicate()
	if not speaking and _ready_to_apply:
		_apply_mouth_shape(_current_mouth_shape)

func _cycle_mouth() -> void:
	if not speaking or not _ready_to_apply:
		return
	
	_stop_cycle()
	_speaking_tween = create_tween()
	_speaking_tween.set_parallel(false)
	_speaking_tween.set_loops()
	
	# Pick a random sequence of mouth states for natural variation
	var states = MOUTH_STATES.duplicate()
	states.shuffle()
	
	for state in states:
		var target_shape = _blend_with_emotion(MOUTH_SHAPES[state])
		var duration = 1.0 / speech_rate / states.size()
		_speaking_tween.tween_callback(_apply_mouth_shape.bind(target_shape))
		_speaking_tween.tween_interval(duration)
	
	# Loop by restarting
	_speaking_tween.finished.connect(_cycle_mouth.bind())

func _stop_cycle() -> void:
	if _speaking_tween != null and _speaking_tween.is_valid():
		_speaking_tween.kill()
		_speaking_tween = null

func _apply_mouth_shape(shape: PackedVector2Array) -> void:
	if not _ready_to_apply:
		return
	mouth_line.points = shape

func _blend_with_emotion(speaking_shape: PackedVector2Array) -> PackedVector2Array:
	"""Blend speaking shape with current emotional mouth shape."""
	if _current_mouth_shape.size() != speaking_shape.size():
		return speaking_shape
	
	var result = PackedVector2Array()
	for i in _current_mouth_shape.size():
		# Blend: emotional shape provides base, speaking adds vertical opening
		var emotional_y = _current_mouth_shape[i].y
		var speaking_y = speaking_shape[i].y
		# Use speaking vertical opening but keep emotional horizontal shape
		var base_x = _current_mouth_shape[i].x
		result.append(Vector2(base_x, speaking_y))
	return result

func _restore_emotional_mouth() -> void:
	if _ready_to_apply:
		_apply_mouth_shape(_current_mouth_shape)