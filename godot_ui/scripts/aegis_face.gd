extends Node2D
class_name AegisFace

const BRIDGE_URL := "http://127.0.0.1:8765"
const POLL_SECONDS := 0.35

@export_enum("NEUTRAL", "HAPPY", "SAD", "ANGRY", "SURPRISED", "THINKING", "CONFUSED") var emotion := "NEUTRAL"
@export_range(0.0, 1.0, 0.01) var emotion_intensity := 0.0
@export var gesture := "idle"
@export var speaking := false
@export_multiline var remark := ""

@onready var animation_player: AnimationPlayer = $AnimationPlayer
@onready var expression_controller: Node = $ExpressionController
@onready var gesture_controller: Node = $GestureController
@onready var speaking_controller: Node = $SpeakingController
var _http := HTTPRequest.new()
var _poll_elapsed := 0.0
var _input: LineEdit
var _reply_label: Label
var _status_label: Label


func _ready() -> void:
	add_child(_http)
	_http.request_completed.connect(_on_http_completed)
	_build_interface()
	_poll_state()
	if animation_player.has_animation("idle"):
		animation_player.play("idle")
	_center_in_viewport()
	get_viewport().size_changed.connect(_center_in_viewport)

func _process(delta: float) -> void:
	_poll_elapsed += delta
	if _poll_elapsed >= POLL_SECONDS and _http.get_http_client_status() == HTTPClient.STATUS_DISCONNECTED:
		_poll_elapsed = 0.0
		_poll_state()

func _poll_state() -> void:
	_http.request(BRIDGE_URL + "/state", [], HTTPClient.METHOD_GET)

func _send_message() -> void:
	var text := _input.text.strip_edges()
	if text.is_empty() or _http.get_http_client_status() != HTTPClient.STATUS_DISCONNECTED:
		return
	_input.clear()
	_status_label.text = "Aegis is thinking..."
	var body := JSON.stringify({"text": text})
	_http.request(BRIDGE_URL + "/respond", ["Content-Type: application/json"], HTTPClient.METHOD_POST, body)

func _on_input_submitted(_text: String) -> void:
	_send_message()

func _on_http_completed(_result: int, response_code: int, _headers: PackedStringArray, body: PackedByteArray) -> void:
	if response_code != 200:
		_status_label.text = "Start Aegis with run_ui.bat"
		return
	var parsed = JSON.parse_string(body.get_string_from_utf8())
	if typeof(parsed) != TYPE_DICTIONARY or not parsed.has("emotion"):
		return
	set_emotion(parsed.emotion)
	set_intensity(1.0 if parsed.status != "ready" else 0.65)
	if parsed.status == "speaking":
		start_speaking()
	else:
		stop_speaking()
	_reply_label.text = parsed.reply
	_status_label.text = "Trust: %s   |   %s" % [parsed.trust, parsed.status.capitalize()]

func _build_interface() -> void:
	var layer := CanvasLayer.new()
	add_child(layer)
	var panel := PanelContainer.new()
	panel.position = Vector2(24, 24)
	panel.size = Vector2(360, 150)
	layer.add_child(panel)
	var box := VBoxContainer.new()
	box.add_theme_constant_override("separation", 8)
	panel.add_child(box)
	_reply_label = Label.new()
	_reply_label.text = "Connecting to Aegis..."
	_reply_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_reply_label.custom_minimum_size = Vector2(330, 76)
	box.add_child(_reply_label)
	_status_label = Label.new()
	_status_label.text = "Starting..."
	box.add_child(_status_label)
	var row := HBoxContainer.new()
	box.add_child(row)
	_input = LineEdit.new()
	_input.placeholder_text = "Talk to Aegis"
	_input.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_input.text_submitted.connect(_on_input_submitted)
	row.add_child(_input)
	var button := Button.new()
	button.text = "Send"
	button.pressed.connect(_send_message)
	row.add_child(button)

func _center_in_viewport() -> void:
	var viewport_size = get_viewport().get_visible_rect().size
	position = viewport_size / 2


func set_emotion(new_emotion) -> bool:
	var changed: bool = expression_controller.set_emotion(new_emotion)
	if changed:
		emotion = expression_controller.emotion
	return changed


func set_intensity(value) -> void:
	expression_controller.set_intensity(value)
	emotion_intensity = expression_controller.intensity


func set_gesture(new_gesture) -> bool:
	var changed: bool = gesture_controller.set_gesture(new_gesture)
	if changed:
		gesture = gesture_controller.gesture
	return changed


func start_speaking() -> void:
	speaking_controller.start_speaking()
	speaking = true


func stop_speaking() -> void:
	speaking_controller.stop_speaking()
	speaking = false


func apply_face_state(
	new_emotion: String,
	new_intensity: float,
	new_gesture: String,
	is_speaking: bool,
	new_remark: String
) -> void:
	set_emotion(new_emotion)
	set_intensity(new_intensity)
	set_gesture(new_gesture)
	if is_speaking:
		start_speaking()
	else:
		stop_speaking()
	remark = new_remark