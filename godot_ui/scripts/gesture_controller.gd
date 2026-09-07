extends Node
class_name AegisGestureController

const BLINK := "BLINK"
const NOD := "NOD"
const HEAD_TILT_LEFT := "HEAD_TILT_LEFT"
const HEAD_TILT_RIGHT := "HEAD_TILT_RIGHT"
const LOOK_LEFT := "LOOK_LEFT"
const LOOK_RIGHT := "LOOK_RIGHT"
const SHAKE_HEAD := "SHAKE_HEAD"
const GESTURES := [BLINK, NOD, HEAD_TILT_LEFT, HEAD_TILT_RIGHT, LOOK_LEFT, LOOK_RIGHT, SHAKE_HEAD]

@export_enum("BLINK", "NOD", "HEAD_TILT_LEFT", "HEAD_TILT_RIGHT", "LOOK_LEFT", "LOOK_RIGHT", "SHAKE_HEAD") var gesture := BLINK

@onready var gesture_rig: Node2D = $"../GestureRig"
@onready var eyes: Node2D = $"../GestureRig/FaceRig/Eyes"

var _gesture_rig_base_position := Vector2.ZERO
var _gesture_rig_base_rotation := 0.0
var _eyes_base_position := Vector2.ZERO
var _eyes_base_scale := Vector2.ONE
var _active_tween: Tween
var _ready_to_apply := false


func _ready() -> void:
	_gesture_rig_base_position = gesture_rig.position
	_gesture_rig_base_rotation = gesture_rig.rotation
	_eyes_base_position = eyes.position
	_eyes_base_scale = eyes.scale
	_ready_to_apply = true
	_reset_pose()


func set_gesture(new_gesture) -> bool:
	var normalized := String(new_gesture).strip_edges().to_upper()
	if not GESTURES.has(normalized):
		push_warning("Unknown Aegis gesture: %s" % normalized)
		return false

	gesture = normalized
	_play_gesture(normalized)
	return true


func stop_gesture() -> void:
	_stop_active_tween()
	_reset_pose()


func _play_gesture(next_gesture: String) -> void:
	if not _ready_to_apply:
		return

	_stop_active_tween()
	_reset_pose()
	_active_tween = create_tween()
	_active_tween.set_parallel(false)

	match next_gesture:
		BLINK:
			_active_tween.tween_property(eyes, "scale", Vector2(_eyes_base_scale.x, 0.12), 0.08)
			_active_tween.tween_property(eyes, "scale", _eyes_base_scale, 0.10)
		NOD:
			_active_tween.tween_property(gesture_rig, "position", _gesture_rig_base_position + Vector2(0, 10), 0.12)
			_active_tween.parallel().tween_property(gesture_rig, "rotation", deg_to_rad(3.0), 0.12)
			_active_tween.tween_property(gesture_rig, "position", _gesture_rig_base_position + Vector2(0, -4), 0.12)
			_active_tween.parallel().tween_property(gesture_rig, "rotation", deg_to_rad(-1.0), 0.12)
			_active_tween.tween_property(gesture_rig, "position", _gesture_rig_base_position, 0.14)
			_active_tween.parallel().tween_property(gesture_rig, "rotation", _gesture_rig_base_rotation, 0.14)
		HEAD_TILT_LEFT:
			_active_tween.tween_property(gesture_rig, "rotation", deg_to_rad(-9.0), 0.18)
		HEAD_TILT_RIGHT:
			_active_tween.tween_property(gesture_rig, "rotation", deg_to_rad(9.0), 0.18)
		LOOK_LEFT:
			_active_tween.tween_property(eyes, "position", _eyes_base_position + Vector2(-10, 0), 0.12)
		LOOK_RIGHT:
			_active_tween.tween_property(eyes, "position", _eyes_base_position + Vector2(10, 0), 0.12)
		SHAKE_HEAD:
			_active_tween.tween_property(gesture_rig, "rotation", deg_to_rad(-8.0), 0.09)
			_active_tween.tween_property(gesture_rig, "rotation", deg_to_rad(8.0), 0.12)
			_active_tween.tween_property(gesture_rig, "rotation", deg_to_rad(-5.0), 0.10)
			_active_tween.tween_property(gesture_rig, "rotation", _gesture_rig_base_rotation, 0.12)


func _stop_active_tween() -> void:
	if _active_tween != null and _active_tween.is_valid():
		_active_tween.kill()


func _reset_pose() -> void:
	gesture_rig.position = _gesture_rig_base_position
	gesture_rig.rotation = _gesture_rig_base_rotation
	eyes.position = _eyes_base_position
	eyes.scale = _eyes_base_scale