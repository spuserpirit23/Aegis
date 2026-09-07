extends Node
class_name AegisExpressionController

const NEUTRAL := "NEUTRAL"
const HAPPY := "HAPPY"
const SAD := "SAD"
const ANGRY := "ANGRY"
const SURPRISED := "SURPRISED"
const THINKING := "THINKING"
const CONFUSED := "CONFUSED"
const EXPRESSIONS := [NEUTRAL, HAPPY, SAD, ANGRY, SURPRISED, THINKING, CONFUSED]

@export_enum("NEUTRAL", "HAPPY", "SAD", "ANGRY", "SURPRISED", "THINKING", "CONFUSED") var emotion := NEUTRAL
@export_range(0.0, 1.0, 0.01) var intensity := 1.0

@onready var left_eye: Polygon2D = $"../GestureRig/FaceRig/Eyes/LeftEye"
@onready var right_eye: Polygon2D = $"../GestureRig/FaceRig/Eyes/RightEye"
@onready var left_brow: Line2D = $"../GestureRig/FaceRig/Eyebrows/LeftEyebrow"
@onready var right_brow: Line2D = $"../GestureRig/FaceRig/Eyebrows/RightEyebrow"
@onready var mouth_line: Line2D = $"../GestureRig/FaceRig/Mouth/MouthLine"
@onready var emotion_glow: Polygon2D = $"../GestureRig/FaceRig/FacialEffects/EmotionGlow"
@onready var speaking_controller: Node = $"../SpeakingController"

var _ready_to_apply := false
var _left_eye_base_position := Vector2.ZERO
var _right_eye_base_position := Vector2.ZERO
var _left_brow_base_position := Vector2.ZERO
var _right_brow_base_position := Vector2.ZERO
var _mouth_base_position := Vector2.ZERO
var _eye_base_scale := Vector2.ONE


func _ready() -> void:
	_left_eye_base_position = left_eye.position
	_right_eye_base_position = right_eye.position
	_left_brow_base_position = left_brow.position
	_right_brow_base_position = right_brow.position
	_mouth_base_position = mouth_line.position
	_eye_base_scale = left_eye.scale
	_ready_to_apply = true
	_apply_expression()


func set_emotion(new_emotion) -> bool:
	var normalized := String(new_emotion).strip_edges().to_upper()
	if not EXPRESSIONS.has(normalized):
		push_warning("Unknown Aegis expression: %s" % normalized)
		return false

	emotion = normalized
	_apply_expression()
	return true


func set_intensity(value) -> void:
	intensity = clampf(float(value), 0.0, 1.0)
	_apply_expression()


func _apply_expression() -> void:
	if not _ready_to_apply:
		return

	var pose := _pose_for(emotion)
	var t := intensity
	left_eye.position = _left_eye_base_position.lerp(_left_eye_base_position + pose.left_eye_offset, t)
	right_eye.position = _right_eye_base_position.lerp(_right_eye_base_position + pose.right_eye_offset, t)
	left_eye.scale = _eye_base_scale.lerp(pose.eye_scale, t)
	right_eye.scale = _eye_base_scale.lerp(pose.eye_scale, t)
	left_brow.position = _left_brow_base_position.lerp(_left_brow_base_position + pose.left_brow_offset, t)
	right_brow.position = _right_brow_base_position.lerp(_right_brow_base_position + pose.right_brow_offset, t)
	mouth_line.position = _mouth_base_position.lerp(_mouth_base_position + pose.mouth_offset, t)

	left_brow.points = _blend_points(PackedVector2Array([-20, 0, 18, -6]), pose.left_brow_points, t)
	right_brow.points = _blend_points(PackedVector2Array([-18, -6, 20, 0]), pose.right_brow_points, t)
	
	# Update emotional mouth shape - notify speaking controller for blending
	var emotional_mouth_points = _blend_points(PackedVector2Array([-18, 0, 0, 6, 18, 0]), pose.mouth_points, t)
	mouth_line.points = emotional_mouth_points
	if speaking_controller != null and speaking_controller.has_method("set_emotional_mouth_shape"):
		speaking_controller.set_emotional_mouth_shape(emotional_mouth_points)
	
	emotion_glow.color = Color(0.35, 0.78, 1.0, 0.08).lerp(pose.glow_color, t)


func _blend_points(from_points: PackedVector2Array, to_points: PackedVector2Array, weight: float) -> PackedVector2Array:
	var result := PackedVector2Array()
	for index in from_points.size():
		result.append(from_points[index].lerp(to_points[index], weight))
	return result


func _pose_for(expression: String) -> Dictionary:
	match expression:
		HAPPY:
			return _pose(Vector2.ONE, Vector2.ZERO, Vector2.ZERO, Vector2(0, -4), Vector2(0, -4), Vector2(0, 0), PackedVector2Array([-20, -4, 18, -8]), PackedVector2Array([-18, -8, 20, -4]), PackedVector2Array([-22, 0, 0, 14, 22, 0]), Color(1.0, 0.82, 0.24, 0.18))
		SAD:
			return _pose(Vector2(1.0, 0.82), Vector2(0, 4), Vector2(0, 4), Vector2(0, 6), Vector2(0, 6), Vector2(0, 4), PackedVector2Array([-20, -6, 18, 2]), PackedVector2Array([-18, 2, 20, -6]), PackedVector2Array([-20, 8, 0, 0, 20, 8]), Color(0.27, 0.45, 1.0, 0.16))
		ANGRY:
			return _pose(Vector2(1.0, 0.72), Vector2.ZERO, Vector2.ZERO, Vector2(0, 3), Vector2(0, 3), Vector2(0, 1), PackedVector2Array([-20, -8, 18, 2]), PackedVector2Array([-18, 2, 20, -8]), PackedVector2Array([-18, 4, 0, 0, 18, 4]), Color(1.0, 0.19, 0.12, 0.18))
		SURPRISED:
			return _pose(Vector2(1.12, 1.32), Vector2(0, -2), Vector2(0, -2), Vector2(0, -10), Vector2(0, -10), Vector2(0, 2), PackedVector2Array([-20, -6, 18, -6]), PackedVector2Array([-18, -6, 20, -6]), PackedVector2Array([-10, 0, 0, 18, 10, 0]), Color(0.55, 0.9, 1.0, 0.2))
		THINKING:
			return _pose(Vector2(0.92, 0.88), Vector2(-2, 1), Vector2(-2, 1), Vector2(-2, -2), Vector2(-2, 5), Vector2(3, 1), PackedVector2Array([-20, -2, 18, -9]), PackedVector2Array([-18, 0, 20, 2]), PackedVector2Array([-16, 2, 0, 5, 16, 1]), Color(0.65, 0.42, 1.0, 0.17))
		CONFUSED:
			return _pose(Vector2(0.95, 0.9), Vector2(-1, 2), Vector2(1, -1), Vector2(0, -8), Vector2(0, 5), Vector2(0, 2), PackedVector2Array([-20, -8, 18, -2]), PackedVector2Array([-18, 4, 20, -6]), PackedVector2Array([-18, 1, 0, 7, 18, 2]), Color(0.42, 1.0, 0.78, 0.15))
		_:
			return _pose(Vector2.ONE, Vector2.ZERO, Vector2.ZERO, Vector2.ZERO, Vector2.ZERO, Vector2.ZERO, PackedVector2Array([-20, 0, 18, -6]), PackedVector2Array([-18, -6, 20, 0]), PackedVector2Array([-18, 0, 0, 6, 18, 0]), Color(0.35, 0.78, 1.0, 0.08))


func _pose(
	eye_scale: Vector2,
	left_eye_offset: Vector2,
	right_eye_offset: Vector2,
	left_brow_offset: Vector2,
	right_brow_offset: Vector2,
	mouth_offset: Vector2,
	left_brow_points: PackedVector2Array,
	right_brow_points: PackedVector2Array,
	mouth_points: PackedVector2Array,
	glow_color: Color
) -> Dictionary:
	return {
		"eye_scale": eye_scale,
		"left_eye_offset": left_eye_offset,
		"right_eye_offset": right_eye_offset,
		"left_brow_offset": left_brow_offset,
		"right_brow_offset": right_brow_offset,
		"mouth_offset": mouth_offset,
		"left_brow_points": left_brow_points,
		"right_brow_points": right_brow_points,
		"mouth_points": mouth_points,
		"glow_color": glow_color,
	}