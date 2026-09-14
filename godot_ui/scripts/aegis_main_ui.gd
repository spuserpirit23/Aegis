extends Control
class_name AegisMainUI

const BRIDGE_URL := "http://127.0.0.1:8765"
const POLL_INTERVAL := 0.35

# State
var current_plugin := "CONVERSATION"
var status := "ready"
var emotion := "NEUTRAL"
var trust_score := 100
var reply_text := "Welcome to Aegis. How can I assist you today?"
var is_speaking := false
var is_listening := false

# Animation variables
var _time := 0.0
var _poll_timer := 0.0
var _avatar_base_y := 0.0
var _wave_bars_left: Array[Control] = []
var _wave_bars_right: Array[Control] = []

# HTTP requests — we use two so state polling and voice actions don't block each other
var _http: HTTPRequest
var _http_voice: HTTPRequest
var _pending_request := ""  # tracks what _http_voice is doing: "", "listen_start", "listen_poll", "voice_respond"
var _listen_poll_timer := 0.0
const LISTEN_POLL_INTERVAL := 0.4

# Nodes
@onready var avatar_rect: TextureRect = $CenterContainer/AvatarAnchor/Avatar
@onready var avatar_anchor: Control = $CenterContainer/AvatarAnchor
@onready var captions_label: Label = $CaptionsPanel/VBox/CaptionsText
@onready var input_line_edit: LineEdit = $CaptionsPanel/VBox/InputRow/InputContainer/HBox/LineEdit
@onready var send_btn: Button = $CaptionsPanel/VBox/InputRow/SendButton
@onready var mic_btn: Button = $CaptionsPanel/VBox/InputRow/InputContainer/HBox/MicButton
@onready var status_text_bottom: Label = $AudioVisualizer/StatusText
@onready var wave_left_container: HBoxContainer = $AudioVisualizer/WaveLeft
@onready var wave_right_container: HBoxContainer = $AudioVisualizer/WaveRight
@onready var clock_label: Label = $LeftPanel/BottomStatus/VBox/ClockContainer/ClockLabel
@onready var date_label: Label = $LeftPanel/BottomStatus/VBox/ClockContainer/DateLabel
@onready var module_overlay: PanelContainer = $ModuleOverlay
@onready var module_title: Label = $ModuleOverlay/VBox/Header/Title
@onready var module_content: Label = $ModuleOverlay/VBox/Content
@onready var status_indicator_top: Label = $TopHeader/SystemStatus
@onready var status_indicator_left: Label = $LeftPanel/BottomStatus/VBox/OnlineLabel

# Plugin buttons
@onready var plugin_buttons := {
	"CONVERSATION": $LeftPanel/PluginsList/BtnConversation,
	"MEMORY": $LeftPanel/PluginsList/BtnMemory,
	"TRUST ENGINE": $LeftPanel/PluginsList/BtnTrust,
	"EMOTION ENGINE": $LeftPanel/PluginsList/BtnEmotion,
	"ANALYTICS": $LeftPanel/PluginsList/BtnAnalytics,
	"INTEGRATIONS": $LeftPanel/PluginsList/BtnIntegrations,
	"TOOLS": $LeftPanel/PluginsList/BtnTools,
	"SETTINGS": $LeftPanel/PluginsList/BtnSettings,
}

func _ready() -> void:
	# Main state-polling HTTP client
	_http = HTTPRequest.new()
	add_child(_http)
	_http.request_completed.connect(_on_http_completed)

	# Separate HTTP client for voice commands (listen / voice_respond)
	_http_voice = HTTPRequest.new()
	add_child(_http_voice)
	_http_voice.request_completed.connect(_on_voice_http_completed)

	_avatar_base_y = avatar_anchor.position.y

	# Connect UI actions
	input_line_edit.text_submitted.connect(_on_input_submitted)
	send_btn.pressed.connect(_on_send_pressed)
	mic_btn.pressed.connect(_on_mic_pressed)
	$ModuleOverlay/VBox/Header/CloseButton.pressed.connect(func(): module_overlay.visible = false)

	# Connect plugin buttons
	for plugin_name in plugin_buttons:
		var btn: Button = plugin_buttons[plugin_name]
		btn.pressed.connect(func(): _select_plugin(plugin_name))

	# Attach laser beam procedural drawing
	var beam = get_node_or_null("LeftGlowBeam")
	if beam:
		beam.set_script(load("res://scripts/laser_beam.gd"))
		beam.queue_redraw()

	_select_plugin("CONVERSATION")
	_init_waveform_bars()
	_update_clock()
	_poll_bridge()

func _process(delta: float) -> void:
	_time += delta
	_poll_timer += delta

	if _poll_timer >= POLL_INTERVAL and _http.get_http_client_status() == HTTPClient.STATUS_DISCONNECTED:
		_poll_timer = 0.0
		_poll_bridge()

	# While listening, poll /listen_status periodically
	if is_listening:
		_listen_poll_timer += delta
		if _listen_poll_timer >= LISTEN_POLL_INTERVAL and _http_voice.get_http_client_status() == HTTPClient.STATUS_DISCONNECTED and _pending_request == "":
			_listen_poll_timer = 0.0
			_poll_listen_status()

	_update_avatar_bobbing()
	_update_waveform_bars(delta)
	_update_clock()

# ═══════════════════════════════════════════════════
#  AVATAR ANIMATION
# ═══════════════════════════════════════════════════
func _update_avatar_bobbing() -> void:
	var offset = sin(_time * 1.8) * 4.0
	if status == "speaking":
		offset = sin(_time * 3.5) * 5.0
	avatar_anchor.position.y = _avatar_base_y + offset

# ═══════════════════════════════════════════════════
#  WAVEFORM EQUALIZER
# ═══════════════════════════════════════════════════
func _init_waveform_bars() -> void:
	for i in range(16):
		var bar_l := ColorRect.new()
		bar_l.custom_minimum_size = Vector2(2, 10)
		bar_l.color = Color(0.65, 0.35, 0.95, 0.75)
		wave_left_container.add_child(bar_l)
		_wave_bars_left.append(bar_l)

		var bar_r := ColorRect.new()
		bar_r.custom_minimum_size = Vector2(2, 10)
		bar_r.color = Color(0.65, 0.35, 0.95, 0.75)
		wave_right_container.add_child(bar_r)
		_wave_bars_right.append(bar_r)

func _update_waveform_bars(_delta: float) -> void:
	var active = (status == "speaking" or status == "thinking" or is_listening)
	var speed = 7.0 if status == "speaking" else 3.5
	var count = _wave_bars_left.size()

	for i in range(count):
		var curve_factor: float = sin(float(i + 1) / float(count + 1) * PI)
		var base_h: float = 3.0 + curve_factor * 10.0
		var dynamic_h: float
		if active:
			var wave_val = (sin(_time * speed + i * 0.8) * 0.5 + 0.5)
			dynamic_h = base_h + wave_val * 12.0 * curve_factor
		else:
			var gentle_pulse = (sin(_time * 1.5 + i * 0.3) * 0.5 + 0.5)
			dynamic_h = base_h + gentle_pulse * 3.0 * curve_factor

		_wave_bars_left[i].custom_minimum_size.y = dynamic_h
		_wave_bars_right[i].custom_minimum_size.y = dynamic_h

# ═══════════════════════════════════════════════════
#  CLOCK
# ═══════════════════════════════════════════════════
func _update_clock() -> void:
	var dt = Time.get_datetime_dict_from_system()
	var hour: int = dt.hour
	var ampm := "AM"
	if hour >= 12:
		ampm = "PM"
	var display_hour := hour % 12
	if display_hour == 0:
		display_hour = 12
	clock_label.text = "%02d:%02d %s" % [display_hour, dt.minute, ampm]

	var months = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
	var month_str = months[dt.month - 1]
	date_label.text = "%d %s %d" % [dt.day, month_str, dt.year]

# ═══════════════════════════════════════════════════
#  PLUGIN NAVIGATION
# ═══════════════════════════════════════════════════
func _select_plugin(plugin_name: String) -> void:
	current_plugin = plugin_name

	for name in plugin_buttons:
		var btn: Button = plugin_buttons[name]
		var is_active = (name == plugin_name)
		if is_active:
			btn.add_theme_color_override("font_color", Color(1.0, 1.0, 1.0, 1.0))
			var active_sb := StyleBoxFlat.new()
			active_sb.bg_color = Color(0.18, 0.09, 0.28, 0.85)
			active_sb.border_color = Color(0.65, 0.35, 0.95, 0.9)
			active_sb.set_border_width_all(1)
			active_sb.set_corner_radius_all(12)
			active_sb.content_margin_left = 10
			active_sb.content_margin_right = 10
			active_sb.content_margin_top = 6
			active_sb.content_margin_bottom = 6
			btn.add_theme_stylebox_override("normal", active_sb)
			btn.add_theme_stylebox_override("hover", active_sb)
		else:
			btn.add_theme_color_override("font_color", Color(0.72, 0.70, 0.80, 0.85))
			var empty_sb := StyleBoxEmpty.new()
			empty_sb.content_margin_left = 10
			empty_sb.content_margin_right = 10
			empty_sb.content_margin_top = 6
			empty_sb.content_margin_bottom = 6
			btn.add_theme_stylebox_override("normal", empty_sb)

			var hover_sb := StyleBoxFlat.new()
			hover_sb.bg_color = Color(0.12, 0.08, 0.20, 0.5)
			hover_sb.set_corner_radius_all(10)
			hover_sb.content_margin_left = 10
			hover_sb.content_margin_right = 10
			hover_sb.content_margin_top = 6
			hover_sb.content_margin_bottom = 6
			btn.add_theme_stylebox_override("hover", hover_sb)

	if plugin_name != "CONVERSATION":
		_show_module_info(plugin_name)
	else:
		module_overlay.visible = false

func _show_module_info(module_name: String) -> void:
	module_title.text = module_name
	module_overlay.visible = true
	match module_name:
		"MEMORY":
			module_content.text = "Memory Subsystem Status: ONLINE\n\n• Long-Term Memory: Active (memory.json)\n• Semantic Associator: Ready\n• Short-term Context: Engaged\n\nRecent context logs and episodic records are being maintained."
		"TRUST ENGINE":
			module_content.text = "Trust Engine Status: ONLINE\n\n• Current Trust Score: %d / 100\n• Security Policy: Verified\n• Action Guardrails: Maximum Safety\n• Integrity Checks: All Systems Nominal" % trust_score
		"EMOTION ENGINE":
			module_content.text = "Emotion Synthesis Engine: ONLINE\n\n• Current Emotion: %s\n• Expressiveness: Calibrated\n• Voice Pitch & Tone: Dynamic Modulation Active\n• Persona: Aegis Cybernetic Companion" % emotion
		"ANALYTICS":
			module_content.text = "System Analytics:\n\n• Latency: < 40ms\n• Uptime: 99.98%%\n• Query Execution Rate: Optimal\n• Memory Footprint: Nominal"
		"INTEGRATIONS":
			module_content.text = "Connected Integrations:\n\n• Python Bridge (HTTP :8765): Connected\n• Voice TTS & STT Engine: Ready\n• Vision / Godot Frontend: Active"
		"TOOLS":
			module_content.text = "Tool Registry:\n\n• Code Synthesizer\n• Context Indexer\n• Audio Synthesis & Playback\n• Autonomous Planner"
		"SETTINGS":
			module_content.text = "System Settings:\n\n• UI Theme: Neon Cyber Violet\n• Bridge Endpoint: %s\n• Polling Rate: %.2fs\n• Sound Effects: Enabled" % [BRIDGE_URL, POLL_INTERVAL]

# ═══════════════════════════════════════════════════
#  BRIDGE POLLING (state)
# ═══════════════════════════════════════════════════
func _poll_bridge() -> void:
	_http.request(BRIDGE_URL + "/state", [], HTTPClient.METHOD_GET)

func _on_http_completed(_result: int, response_code: int, _headers: PackedStringArray, body: PackedByteArray) -> void:
	if response_code != 200:
		status_text_bottom.text = "AEGIS IS OFFLINE (START BRIDGE)"
		status_indicator_top.text = "● SYSTEM OFFLINE ●"
		status_indicator_top.modulate = Color(0.8, 0.2, 0.2)
		status_indicator_left.text = "● SYSTEM OFFLINE"
		status_indicator_left.modulate = Color(0.8, 0.2, 0.2)
		return

	status_indicator_top.text = "● SYSTEM ONLINE ●"
	status_indicator_top.modulate = Color(0.2, 0.9, 0.4)
	status_indicator_left.text = "● SYSTEM ONLINE"
	status_indicator_left.modulate = Color(0.2, 0.9, 0.4)

	var parsed = JSON.parse_string(body.get_string_from_utf8())
	if typeof(parsed) != TYPE_DICTIONARY:
		return

	status = parsed.get("status", "ready")
	emotion = parsed.get("emotion", "NEUTRAL")
	trust_score = int(parsed.get("trust", 100))
	var reply = parsed.get("reply", "")

	if reply != "":
		reply_text = reply
		captions_label.text = reply_text

	# Don't override status text if we're in an active listening session
	if is_listening:
		return

	match status:
		"speaking":
			status_text_bottom.text = "AEGIS IS SPEAKING"
		"thinking":
			status_text_bottom.text = "AEGIS IS THINKING..."
		"listening":
			status_text_bottom.text = "LISTENING... SPEAK NOW"
		"ready":
			status_text_bottom.text = "AEGIS IS LISTENING"
		_:
			status_text_bottom.text = "AEGIS " + status.to_upper()

# ═══════════════════════════════════════════════════
#  TEXT INPUT
# ═══════════════════════════════════════════════════
func _on_input_submitted(_text: String) -> void:
	_send_user_message()

func _on_send_pressed() -> void:
	_send_user_message()

func _send_user_message() -> void:
	var text := input_line_edit.text.strip_edges()
	if text.is_empty():
		return
	input_line_edit.clear()

	captions_label.text = "You: " + text
	status = "thinking"
	status_text_bottom.text = "AEGIS IS THINKING..."

	var payload := JSON.stringify({"text": text})
	if _http_voice.get_http_client_status() == HTTPClient.STATUS_DISCONNECTED:
		_pending_request = "text_respond"
		_http_voice.request(BRIDGE_URL + "/respond", ["Content-Type: application/json"], HTTPClient.METHOD_POST, payload)
	else:
		# Fallback: use main http if voice channel is busy
		_http.request(BRIDGE_URL + "/respond", ["Content-Type: application/json"], HTTPClient.METHOD_POST, payload)

# ═══════════════════════════════════════════════════
#  VOICE INPUT (MIC BUTTON)
# ═══════════════════════════════════════════════════
func _on_mic_pressed() -> void:
	if is_listening:
		# Cancel listening
		_cancel_listening()
	else:
		# Start listening
		_start_listening()

func _start_listening() -> void:
	if _http_voice.get_http_client_status() != HTTPClient.STATUS_DISCONNECTED:
		return  # voice channel busy

	is_listening = true
	_listen_poll_timer = 0.0

	# Update UI immediately
	captions_label.text = "🎤  Listening... speak now!"
	status_text_bottom.text = "LISTENING... SPEAK NOW"

	# Style mic button to show active state
	mic_btn.modulate = Color(0.85, 0.35, 1.0, 1.0)

	# Tell bridge to start microphone capture
	_pending_request = "listen_start"
	_http_voice.request(BRIDGE_URL + "/listen", [], HTTPClient.METHOD_POST, "")

func _cancel_listening() -> void:
	is_listening = false
	mic_btn.modulate = Color(1.0, 1.0, 1.0, 1.0)
	status_text_bottom.text = "AEGIS IS LISTENING"
	captions_label.text = reply_text

	# Tell bridge to cancel
	if _http_voice.get_http_client_status() == HTTPClient.STATUS_DISCONNECTED:
		_pending_request = "listen_cancel"
		_http_voice.request(BRIDGE_URL + "/listen_cancel", [], HTTPClient.METHOD_POST, "")

func _poll_listen_status() -> void:
	_pending_request = "listen_poll"
	_http_voice.request(BRIDGE_URL + "/listen_status", [], HTTPClient.METHOD_GET)

func _on_voice_http_completed(_result: int, response_code: int, _headers: PackedStringArray, body: PackedByteArray) -> void:
	var req_type := _pending_request
	_pending_request = ""

	if response_code != 200:
		if req_type == "listen_start" or req_type == "listen_poll":
			# Bridge offline or error — cancel listening gracefully
			is_listening = false
			mic_btn.modulate = Color(1.0, 1.0, 1.0, 1.0)
			captions_label.text = "Voice service unavailable. Use text input."
			status_text_bottom.text = "AEGIS IS LISTENING"
		return

	var parsed = JSON.parse_string(body.get_string_from_utf8())
	if typeof(parsed) != TYPE_DICTIONARY:
		return

	match req_type:
		"listen_start":
			# Bridge accepted — keep polling via _process
			if not parsed.get("started", false):
				captions_label.text = "Microphone busy. Try again."
				is_listening = false
				mic_btn.modulate = Color(1.0, 1.0, 1.0, 1.0)

		"listen_poll":
			var listen_status: String = parsed.get("listen_status", "idle")
			match listen_status:
				"listening":
					pass  # still capturing — continue polling
				"done":
					var voice_text: String = parsed.get("text", "")
					if voice_text.is_empty():
						_finish_listening_error("No speech detected.")
					else:
						_finish_listening_success(voice_text)
				"error":
					var err_msg: String = parsed.get("error", "Speech not recognized")
					_finish_listening_error(err_msg)
				"idle":
					# Shouldn't happen, but handle gracefully
					is_listening = false
					mic_btn.modulate = Color(1.0, 1.0, 1.0, 1.0)

		"listen_cancel":
			pass  # nothing to do

		"voice_respond":
			pass  # response will come via /state polling

		"text_respond":
			pass  # response will come via /state polling

func _finish_listening_success(voice_text: String) -> void:
	is_listening = false
	mic_btn.modulate = Color(1.0, 1.0, 1.0, 1.0)

	# Show what was heard
	captions_label.text = "🎤 You said: " + voice_text
	status = "thinking"
	status_text_bottom.text = "AEGIS IS THINKING..."

	# Send the transcribed text to Aegis for a response
	var payload := JSON.stringify({"text": voice_text})
	_pending_request = "voice_respond"
	_http_voice.request(BRIDGE_URL + "/voice_respond", ["Content-Type: application/json"], HTTPClient.METHOD_POST, payload)

func _finish_listening_error(error_msg: String) -> void:
	is_listening = false
	mic_btn.modulate = Color(1.0, 1.0, 1.0, 1.0)
	captions_label.text = "🎤 " + error_msg + " Try again or type your message."
	status_text_bottom.text = "AEGIS IS LISTENING"
