extends Control
class_name AegisMainUI

const BRIDGE_URL := "http://127.0.0.1:8765"
const POLL_INTERVAL := 0.35
const MODULE_POLL_INTERVAL := 0.6
const LISTEN_POLL_INTERVAL := 0.35

# State
var current_plugin := "CONVERSATION"
var status := "ready"
var emotion := "NEUTRAL"
var trust_score := 100
var trust_level_str := "MEDIUM"
var active_user := "Guest"
var reply_text := "Welcome to Aegis. How can I assist you today?"
var is_speaking := false
var is_listening := false

# Animation variables
var _time := 0.0
var _poll_timer := 0.0
var _module_poll_timer := 0.0
var _avatar_base_y := 0.0
var _wave_bars_left: Array[Control] = []
var _wave_bars_right: Array[Control] = []

# HTTP requests — separate clients to prevent blocking
var _http: HTTPRequest
var _http_voice: HTTPRequest
var _http_module: HTTPRequest
var _pending_voice_request := ""
var _listen_poll_timer := 0.0

# Live speech typewriter state
var _is_typewriting := false
var _typewriter_text := ""
var _typewriter_index := 0
var _typewriter_timer := 0.0
var _typewriter_pending_send := false

# Nodes
@onready var avatar_rect: TextureRect = $CenterContainer/AvatarAnchor/Avatar
@onready var avatar_anchor: Control = $CenterContainer/AvatarAnchor
@onready var captions_panel: PanelContainer = $CaptionsPanel
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
@onready var module_content: RichTextLabel = $ModuleOverlay/VBox/Scroll/Content
@onready var module_live_indicator: Label = $ModuleOverlay/VBox/Header/LiveIndicator
@onready var status_indicator_top: Label = $TopHeader/SystemStatus
@onready var status_indicator_left: Label = $LeftPanel/BottomStatus/VBox/OnlineLabel
@onready var right_panel: PanelContainer = $RightPanel
@onready var left_panel: Control = $LeftPanel
@onready var top_header: VBoxContainer = $TopHeader

# Live Telemetry Badges
@onready var emotion_badge: Label = $TopHeader/TelemetryBar/EmotionBadge
@onready var trust_badge: Label = $TopHeader/TelemetryBar/TrustBadge
@onready var user_badge: Label = $TopHeader/TelemetryBar/UserBadge

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

	# Voice HTTP client
	_http_voice = HTTPRequest.new()
	add_child(_http_voice)
	_http_voice.request_completed.connect(_on_voice_http_completed)

	# Module inspection HTTP client
	_http_module = HTTPRequest.new()
	add_child(_http_module)
	_http_module.request_completed.connect(_on_module_http_completed)

	_avatar_base_y = avatar_anchor.position.y

	# Connect UI actions
	input_line_edit.text_submitted.connect(_on_input_submitted)
	send_btn.pressed.connect(_on_send_pressed)
	mic_btn.pressed.connect(_on_mic_pressed)
	$ModuleOverlay/VBox/Header/CloseButton.pressed.connect(_close_module_overlay)

	# Connect plugin buttons
	for plugin_name in plugin_buttons:
		var btn: Button = plugin_buttons[plugin_name]
		btn.pressed.connect(func(): _select_plugin(plugin_name))

	# Attach laser beam procedural drawing
	var beam = get_node_or_null("LeftGlowBeam")
	if beam:
		beam.set_script(load("res://scripts/laser_beam.gd"))
		beam.queue_redraw()

	# Connect responsive resize handler
	var vp = get_viewport()
	if vp:
		vp.size_changed.connect(_on_window_resized)
	_apply_responsive_layout(get_viewport_rect().size)

	_select_plugin("CONVERSATION")
	_init_waveform_bars()
	_update_clock()
	_poll_bridge()

func _notification(what: int) -> void:
	if what == NOTIFICATION_RESIZED and is_node_ready():
		_apply_responsive_layout(get_viewport_rect().size)

func _on_window_resized() -> void:
	if is_node_ready():
		_apply_responsive_layout(get_viewport_rect().size)

# ═══════════════════════════════════════════════════
#  RESPONSIVE UI LAYOUT
# ═══════════════════════════════════════════════════
func _apply_responsive_layout(win_size: Vector2) -> void:
	if not is_inside_tree() or right_panel == null or captions_panel == null or module_overlay == null or top_header == null:
		return

	var w := win_size.x
	var h := win_size.y

	# Responsive RightPanel: auto-hide on smaller screens to prevent collision
	if w < 1120.0:
		right_panel.visible = false
	else:
		right_panel.visible = (current_plugin == "CONVERSATION")

	# Responsive CaptionsPanel: dynamically size between left and right elements
	var left_clearance := 168.0
	var right_clearance := 315.0 if right_panel.visible else 30.0
	var avail_w := w - left_clearance - right_clearance
	var target_w := clampf(avail_w, 360.0, 780.0)

	captions_panel.offset_left = -target_w * 0.5
	captions_panel.offset_right = target_w * 0.5
	captions_panel.offset_top = -175.0
	captions_panel.offset_bottom = -50.0

	# Responsive ModuleOverlay
	var overlay_w := clampf(w * 0.68, 420.0, 740.0)
	var overlay_h := clampf(h * 0.65, 300.0, 520.0)
	module_overlay.offset_left = -overlay_w * 0.5
	module_overlay.offset_right = overlay_w * 0.5
	module_overlay.offset_top = -overlay_h * 0.5
	module_overlay.offset_bottom = overlay_h * 0.5

	# Keep TopHeader nicely framed
	var header_half_w := minf(340.0, w * 0.45)
	top_header.offset_left = -header_half_w
	top_header.offset_right = header_half_w

func _process(delta: float) -> void:
	_time += delta
	_poll_timer += delta

	# State poll
	if _poll_timer >= POLL_INTERVAL and _http.get_http_client_status() == HTTPClient.STATUS_DISCONNECTED:
		_poll_timer = 0.0
		_poll_bridge()

	# While listening, poll /listen_status periodically
	if is_listening:
		_listen_poll_timer += delta
		if _listen_poll_timer >= LISTEN_POLL_INTERVAL and _http_voice.get_http_client_status() == HTTPClient.STATUS_DISCONNECTED and _pending_voice_request == "":
			_listen_poll_timer = 0.0
			_poll_listen_status()

	# While module overlay is open, poll live module data periodically
	if module_overlay.visible and current_plugin != "CONVERSATION":
		_module_poll_timer += delta
		if _module_poll_timer >= MODULE_POLL_INTERVAL and _http_module.get_http_client_status() == HTTPClient.STATUS_DISCONNECTED:
			_module_poll_timer = 0.0
			_poll_module_endpoint(current_plugin)

	# Typewriter effect for speech-to-text dictation
	if _is_typewriting:
		_typewriter_timer += delta
		if _typewriter_timer >= 0.025:
			_typewriter_timer = 0.0
			_typewriter_index += 1
			var typed := _typewriter_text.substr(0, _typewriter_index)
			input_line_edit.text = typed
			input_line_edit.caret_column = typed.length()
			captions_label.text = "🎤 You said: " + typed

			if _typewriter_index >= _typewriter_text.length():
				_is_typewriting = false
				if _typewriter_pending_send:
					_typewriter_pending_send = false
					_finish_speech_respond(_typewriter_text)

	_update_avatar_bobbing()
	_update_waveform_bars(delta)
	_update_clock()

# ═══════════════════════════════════════════════════
#  AVATAR ANIMATION & EMOTION MODULATION
# ═══════════════════════════════════════════════════
func _update_avatar_bobbing() -> void:
	var offset = sin(_time * 1.8) * 4.0
	if status == "speaking":
		offset = sin(_time * 3.5) * 5.0
	avatar_anchor.position.y = _avatar_base_y + offset

func _update_emotion_aesthetics() -> void:
	# Modulate avatar and badge colors based on live emotion
	var badge_col := Color(0.75, 0.45, 1.0)
	var tint_col := Color(1.0, 1.0, 1.0)

	match emotion:
		"HAPPY":
			badge_col = Color(0.95, 0.82, 0.25)
			tint_col = Color(1.04, 1.02, 0.96)
		"THINKING":
			badge_col = Color(0.25, 0.85, 1.0)
			tint_col = Color(0.94, 0.98, 1.05)
		"CONCERNED":
			badge_col = Color(1.0, 0.45, 0.45)
			tint_col = Color(1.05, 0.94, 0.94)
		"ANGRY":
			badge_col = Color(1.0, 0.3, 0.3)
			tint_col = Color(1.06, 0.9, 0.9)
		"SURPRISED":
			badge_col = Color(1.0, 0.65, 0.2)
			tint_col = Color(1.04, 1.0, 0.94)
		_:
			badge_col = Color(0.75, 0.45, 1.0)
			tint_col = Color(1.0, 1.0, 1.0)

	emotion_badge.text = "EMOTION: " + emotion
	emotion_badge.add_theme_color_override("font_color", badge_col)
	avatar_rect.modulate = tint_col

	trust_badge.text = "TRUST: %s (%d/100)" % [trust_level_str, trust_score]
	if trust_score >= 70:
		trust_badge.add_theme_color_override("font_color", Color(0.3, 0.92, 0.55))
	elif trust_score >= 40:
		trust_badge.add_theme_color_override("font_color", Color(0.35, 0.85, 0.95))
	else:
		trust_badge.add_theme_color_override("font_color", Color(1.0, 0.55, 0.35))

	user_badge.text = "IDENTITY: " + active_user.to_upper()

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
	var speed = 7.0 if status == "speaking" else (5.5 if is_listening else 3.5)
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
#  PLUGIN NAVIGATION & CONNECTED ENDPOINTS
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
		module_title.text = plugin_name
		module_overlay.visible = true
		_poll_module_endpoint(plugin_name)
	else:
		module_overlay.visible = false

	_apply_responsive_layout(get_viewport_rect().size)

func _close_module_overlay() -> void:
	module_overlay.visible = false
	_select_plugin("CONVERSATION")

func _poll_module_endpoint(plugin_name: String) -> void:
	if _http_module.get_http_client_status() != HTTPClient.STATUS_DISCONNECTED:
		return

	var ep := ""
	match plugin_name:
		"MEMORY":
			ep = "/memory"
		"TRUST ENGINE":
			ep = "/trust"
		"EMOTION ENGINE":
			ep = "/emotion"
		"ANALYTICS":
			ep = "/analytics"
		"INTEGRATIONS":
			ep = "/integrations"
		"TOOLS":
			ep = "/tools"
		"SETTINGS":
			ep = "/settings"
		_:
			return

	_http_module.request(BRIDGE_URL + ep, [], HTTPClient.METHOD_GET)

func _on_module_http_completed(_result: int, response_code: int, _headers: PackedStringArray, body: PackedByteArray) -> void:
	if response_code != 200:
		module_content.text = "[color=#f87171]Failed to fetch live telemetry from bridge (%d). Ensure Aegis bridge is running.[/color]" % response_code
		return

	var parsed = JSON.parse_string(body.get_string_from_utf8())
	if typeof(parsed) != TYPE_DICTIONARY:
		return

	match current_plugin:
		"MEMORY":
			_render_memory_view(parsed)
		"TRUST ENGINE":
			_render_trust_view(parsed)
		"EMOTION ENGINE":
			_render_emotion_view(parsed)
		"ANALYTICS":
			_render_analytics_view(parsed)
		"INTEGRATIONS":
			_render_integrations_view(parsed)
		"TOOLS":
			_render_tools_view(parsed)
		"SETTINGS":
			_render_settings_view(parsed)

func _render_memory_view(data: Dictionary) -> void:
	var active_u: String = data.get("active_user", "Unknown")
	var known_users: Array = data.get("known_users", [])
	var facts: Dictionary = data.get("facts", {})
	var turns: Array = data.get("recent_turns", [])
	var storage_file: String = data.get("storage_file", "memory.json")

	var bb := "[b][color=#c084fc]MEMORY SUBSYSTEM (MULTI-USER PERSISTENCE)[/color][/b]\n"
	bb += "• [color=#38bdf8]Active User:[/color] [b]%s[/b]  |  [color=#38bdf8]Storage Path:[/color] %s\n" % [active_u, storage_file.get_file()]
	bb += "• [color=#38bdf8]Known Users (%d):[/color] %s\n\n" % [known_users.size(), ", ".join(known_users)]

	bb += "[b][color=#c084fc]DURABLE FACTS ABOUT %s:[/color][/b]\n" % active_u.to_upper()
	if facts.is_empty():
		bb += "  [color=#94a3b8](No durable facts remembered yet)[/color]\n"
	else:
		for k in facts:
			if not str(k).begins_with("_"):
				bb += "  • [color=#e2e8f0]%s:[/color] [color=#38bdf8]%s[/color]\n" % [k, str(facts[k])]

	bb += "\n[b][color=#c084fc]RECENT CONVERSATION EPISODES:[/color][/b]\n"
	if turns.is_empty():
		bb += "  [color=#94a3b8](No turns in active context)[/color]\n"
	else:
		for t in turns:
			var role: String = t.get("role", "")
			var content: String = t.get("content", "")
			if role == "user":
				bb += "[color=#4ade80]You:[/color] %s\n" % content
			else:
				bb += "[color=#c084fc]Aegis:[/color] %s\n" % content
	module_content.text = bb

func _render_trust_view(data: Dictionary) -> void:
	var u: String = data.get("user", "Unknown")
	var lvl: String = data.get("trust_level", "MEDIUM")
	var score: int = int(data.get("score", 0))
	var scaled: int = int(data.get("scaled_score", 50))
	var rel_confirmed: bool = data.get("relationship_confirmed", false)
	var pts: Dictionary = data.get("points_breakdown", {})
	var guardrails: String = data.get("guardrails", "MAXIMUM")

	var lvl_color := "#4ade80" if lvl == "HIGH" else ("#38bdf8" if lvl == "MEDIUM" else "#fb923c")

	var bb := "[b][color=#38bdf8]AEGIS TRUST ENGINE TELEMETRY[/color][/b]\n"
	bb += "• [color=#94a3b8]Subject Identity:[/color] [b]%s[/b]\n" % u
	bb += "• [color=#94a3b8]Current Trust Level:[/color] [b][color=%s]%s[/color][/b]\n" % [lvl_color, lvl]
	bb += "• [color=#94a3b8]Trust Rating:[/color] [b][color=%s]%d / 100[/color][/b] (Raw score: %d pts)\n\n" % [lvl_color, scaled, score]

	bb += "[b][color=#38bdf8]AUDITABLE POINT SIGNALS:[/color][/b]\n"
	bb += "  • Known Relationship Confirmed: [color=#4ade80]+%d pts[/color] (%s)\n" % [pts.get("known_relationship", 0), ("Identity Verified" if rel_confirmed else "Unverified Guest")]
	bb += "  • Interaction History Turns: [color=#4ade80]+%d pts[/color] (%d turns logged)\n" % [pts.get("interaction_history", 0), data.get("turn_count", 0)]
	bb += "  • User Correction Penalty: [color=#f87171]%d pts[/color]\n\n" % [pts.get("correction_penalty", 0)]

	bb += "[b][color=#38bdf8]SECURITY DIRECTIVE & GUARDRAILS:[/color][/b]\n"
	bb += "  • Enforcement Status: [color=#4ade80]%s[/color]\n" % guardrails
	bb += "  • System Policy: Trust derives purely from memory state in ~1ms."
	module_content.text = bb

func _render_emotion_view(data: Dictionary) -> void:
	var emot: String = data.get("emotion", "NEUTRAL")
	var valence: String = data.get("valence", "NEUTRAL")
	var last_input: String = data.get("last_user_input", "")
	var expressiveness: String = data.get("expressiveness", "CALIBRATED")
	var pitch: String = data.get("voice_pitch_modulation", "ACTIVE")

	var bb := "[b][color=#e879f9]COMPUTATIONAL EMOTION SYNTHESIS[/color][/b]\n"
	bb += "• [color=#94a3b8]Current Emotional State:[/color] [b][color=#c084fc]%s[/color][/b]\n" % emot
	bb += "• [color=#94a3b8]Emotional Valence:[/color] [b]%s[/b]\n" % valence
	bb += "• [color=#94a3b8]Pitch & Tone Modulation:[/color] %s\n" % pitch
	bb += "• [color=#94a3b8]Expressiveness Profile:[/color] %s\n\n" % expressiveness

	bb += "[b][color=#e879f9]LAST APPRAISED INPUT:[/color][/b]\n"
	if last_input.is_empty():
		bb += "  [color=#94a3b8](No previous utterance)[/color]\n\n"
	else:
		bb += "  \"%s\"\n\n" % last_input

	bb += "[b][color=#e879f9]AFFECT ARCHITECTURE:[/color][/b]\n"
	bb += "  Aegis maintains consistent internal affective states. Emotion modulates\n"
	bb += "  conversational tone, avatar visual accents, and cognitive priorities."
	module_content.text = bb

func _render_analytics_view(data: Dictionary) -> void:
	var uptime: String = data.get("uptime_formatted", "00:00:00")
	var reqs: int = int(data.get("total_requests", 0))
	var lat: float = float(data.get("last_latency_ms", 0.0))
	var turns: int = int(data.get("total_turns", 0))
	var facts: int = int(data.get("total_facts_stored", 0))
	var model: String = data.get("model", "gemini-3.6-flash")
	var fallback: bool = data.get("fallback_mode", false)

	var bb := "[b][color=#4ade80]LIVE SYSTEM ANALYTICS[/color][/b]\n"
	bb += "• [color=#94a3b8]Uptime:[/color] [b]%s[/b]  |  [color=#94a3b8]System Health:[/color] [color=#4ade80]NOMINAL[/color]\n" % uptime
	bb += "• [color=#94a3b8]Active LLM Engine:[/color] %s %s\n\n" % [model, ("[color=#fbbf24](Fallback)[/color]" if fallback else "[color=#4ade80](Online)[/color]")]

	bb += "[b][color=#4ade80]RUNTIME PERFORMANCE:[/color][/b]\n"
	bb += "  • Total Requests Processed: [b]%d[/b]\n" % reqs
	bb += "  • Last Request Latency: [b][color=#38bdf8]%.1f ms[/color][/b]\n" % lat
	bb += "  • Total Dialogue Turns: [b]%d[/b]\n" % turns
	bb += "  • Durable Facts in Memory: [b]%d[/b]\n" % facts
	bb += "  • Memory Footprint: [color=#4ade80]OPTIMAL (< 120MB)[/color]"
	module_content.text = bb

func _render_integrations_view(data: Dictionary) -> void:
	var bridge: Dictionary = data.get("python_bridge", {})
	var gemini: Dictionary = data.get("gemini_api", {})
	var stt: Dictionary = data.get("speech_stt", {})
	var tts: Dictionary = data.get("speech_tts", {})
	var godot: Dictionary = data.get("godot_frontend", {})

	var bb := "[b][color=#f472b6]INTEGRATED SUBSYSTEM STATUS[/color][/b]\n\n"
	bb += "• [color=#38bdf8]Python HTTP Bridge:[/color] [color=#4ade80]%s[/color] (%s)\n" % [bridge.get("status", "ONLINE"), bridge.get("endpoint", "8765")]
	bb += "• [color=#38bdf8]Gemini LLM Core:[/color] [color=#4ade80]%s[/color] (%s)\n" % [gemini.get("status", "ONLINE"), gemini.get("model", "gemini-3.6-flash")]
	bb += "• [color=#38bdf8]Voice STT Engine:[/color] [color=#4ade80]%s[/color]\n  [color=#94a3b8]Detail: %s[/color]\n" % [stt.get("status", "ONLINE"), stt.get("detail", "Ready")]
	bb += "• [color=#38bdf8]Voice TTS Engine:[/color] [color=#4ade80]%s[/color] (%s)\n" % [tts.get("status", "ONLINE"), tts.get("engine", "pyttsx3")]
	bb += "• [color=#38bdf8]Godot Frontend:[/color] [color=#4ade80]%s[/color] (%s)\n" % [godot.get("status", "CONNECTED"), godot.get("polling_interval", "0.35s")]
	bb += "• [color=#38bdf8]Cognitive Skills:[/color] [color=#4ade80]Weather Skill, Memory Skill Active[/color]"
	module_content.text = bb

func _render_tools_view(data: Dictionary) -> void:
	var skills: Array = data.get("registered_skills", [])
	var tools_list: Array = data.get("tools", [])

	var bb := "[b][color=#fb923c]AUTONOMOUS TOOL REGISTRY[/color][/b]\n"
	bb += "Active function-calling schemas bound to Gemini Planner:\n\n"

	for t in tools_list:
		var fn_name: String = t.get("name", "")
		var desc: String = t.get("description", "")
		var params: Dictionary = t.get("parameters", {})
		bb += "• [b][color=#fbbf24]%s[/color][/b]\n  %s\n" % [fn_name, desc]
		if not params.is_empty():
			var p_keys := []
			for pk in params:
				p_keys.append(str(pk))
			bb += "  [color=#94a3b8]Parameters: %s[/color]\n\n" % ", ".join(p_keys)
		else:
			bb += "\n"

	bb += "[color=#38bdf8]Permission Model:[/color] Strict runtime gating via brain/permission.py."
	module_content.text = bb

func _render_settings_view(data: Dictionary) -> void:
	var u: String = data.get("active_user", "Guest")
	var b_url: String = data.get("bridge_url", BRIDGE_URL)
	var model: String = data.get("model", "gemini-3.6-flash")
	var poll_s: float = float(data.get("poll_interval_s", 0.35))
	var theme_name: String = data.get("theme", "Neon Cyber Violet")
	var tts_en: bool = data.get("tts_enabled", true)

	var bb := "[b][color=#a78bfa]SYSTEM CONFIGURATION[/color][/b]\n\n"
	bb += "• [color=#38bdf8]Active Identity Session:[/color] [b]%s[/b]\n" % u
	bb += "• [color=#38bdf8]Bridge Endpoint:[/color] %s\n" % b_url
	bb += "• [color=#38bdf8]Language Model:[/color] %s\n" % model
	bb += "• [color=#38bdf8]Telemetry Polling Interval:[/color] %.2fs\n" % poll_s
	bb += "• [color=#38bdf8]Interface Theme:[/color] %s\n" % theme_name
	bb += "• [color=#38bdf8]Audio Speech (TTS):[/color] %s\n" % ("Enabled" if tts_en else "Disabled")
	bb += "• [color=#38bdf8]Visual Mode:[/color] Responsive High-DPI Cybernetic Canvas"
	module_content.text = bb

# ═══════════════════════════════════════════════════
#  BRIDGE POLLING (state)
# ═══════════════════════════════════════════════════
func _poll_bridge() -> void:
	_http.request(BRIDGE_URL + "/state", [], HTTPClient.METHOD_GET)

func _on_http_completed(_result: int, response_code: int, _headers: PackedStringArray, body: PackedByteArray) -> void:
	if response_code != 200:
		status_text_bottom.text = "AEGIS IS OFFLINE (START BRIDGE)"
		status_indicator_top.text = "● SYSTEM OFFLINE ●"
		status_indicator_top.modulate = Color(0.85, 0.2, 0.2)
		status_indicator_left.text = "● SYSTEM OFFLINE"
		status_indicator_left.modulate = Color(0.85, 0.2, 0.2)
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
	trust_level_str = parsed.get("trust", "MEDIUM")
	trust_score = int(parsed.get("trust_score", 100))
	active_user = parsed.get("user", "Guest")

	var reply = parsed.get("reply", "")
	if reply != "" and not _is_typewriting:
		reply_text = reply
		captions_label.text = reply_text

	_update_emotion_aesthetics()

	# Status text bottom
	if is_listening or _is_typewriting:
		return

	match status:
		"speaking":
			status_text_bottom.text = "AEGIS IS SPEAKING"
		"thinking":
			status_text_bottom.text = "AEGIS IS THINKING..."
		"transcribing":
			status_text_bottom.text = "DECODING SPEECH..."
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
		_pending_voice_request = "text_respond"
		_http_voice.request(BRIDGE_URL + "/respond", ["Content-Type: application/json"], HTTPClient.METHOD_POST, payload)
	else:
		_http.request(BRIDGE_URL + "/respond", ["Content-Type: application/json"], HTTPClient.METHOD_POST, payload)

# ═══════════════════════════════════════════════════
#  VOICE INPUT (MIC BUTTON & LIVE SPEECH TYPING)
# ═══════════════════════════════════════════════════
func _on_mic_pressed() -> void:
	if is_listening:
		_cancel_listening()
	else:
		_start_listening()

func _start_listening() -> void:
	if _http_voice.get_http_client_status() != HTTPClient.STATUS_DISCONNECTED:
		return

	is_listening = true
	_listen_poll_timer = 0.0

	captions_label.text = "🎤  Listening... speak now!"
	status_text_bottom.text = "LISTENING... SPEAK NOW"
	mic_btn.modulate = Color(0.85, 0.35, 1.0, 1.0)

	_pending_voice_request = "listen_start"
	_http_voice.request(BRIDGE_URL + "/listen", [], HTTPClient.METHOD_POST, "")

func _cancel_listening() -> void:
	is_listening = false
	mic_btn.modulate = Color(1.0, 1.0, 1.0, 1.0)
	status_text_bottom.text = "AEGIS IS LISTENING"
	captions_label.text = reply_text

	if _http_voice.get_http_client_status() == HTTPClient.STATUS_DISCONNECTED:
		_pending_voice_request = "listen_cancel"
		_http_voice.request(BRIDGE_URL + "/listen_cancel", [], HTTPClient.METHOD_POST, "")

func _poll_listen_status() -> void:
	_pending_voice_request = "listen_poll"
	_http_voice.request(BRIDGE_URL + "/listen_status", [], HTTPClient.METHOD_GET)

func _on_voice_http_completed(_result: int, response_code: int, _headers: PackedStringArray, body: PackedByteArray) -> void:
	var req_type := _pending_voice_request
	_pending_voice_request = ""

	if response_code != 200:
		if req_type in ["listen_start", "listen_poll"]:
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
			if not parsed.get("started", false):
				captions_label.text = "Microphone busy. Try again."
				is_listening = false
				mic_btn.modulate = Color(1.0, 1.0, 1.0, 1.0)

		"listen_poll":
			var listen_st: String = parsed.get("listen_status", "idle")
			match listen_st:
				"listening":
					captions_label.text = "🎤  Listening... speak now!"
					status_text_bottom.text = "LISTENING... SPEAK NOW"
				"transcribing":
					captions_label.text = "🎤  Converting speech to text..."
					status_text_bottom.text = "DECODING SPEECH..."
				"done":
					var voice_text: String = parsed.get("text", "")
					if voice_text.is_empty():
						_finish_listening_error("No speech detected.")
					else:
						_start_speech_typewriter(voice_text)
				"error":
					var err_msg: String = parsed.get("error", "Speech not recognized")
					_finish_listening_error(err_msg)
				"idle":
					is_listening = false
					mic_btn.modulate = Color(1.0, 1.0, 1.0, 1.0)

		"voice_respond", "text_respond", "listen_cancel":
			pass

# ═══════════════════════════════════════════════════
#  LIVE SPEECH TYPING INTO INTERFACE
# ═══════════════════════════════════════════════════
func _start_speech_typewriter(voice_text: String) -> void:
	is_listening = false
	mic_btn.modulate = Color(1.0, 1.0, 1.0, 1.0)

	_typewriter_text = voice_text
	_typewriter_index = 0
	_typewriter_timer = 0.0
	_typewriter_pending_send = true
	_is_typewriting = true

	input_line_edit.clear()
	captions_label.text = "🎤 Transcribing: "
	status_text_bottom.text = "TRANSCRIBED SPEECH"

func _finish_speech_respond(voice_text: String) -> void:
	captions_label.text = "🎤 You said: " + voice_text
	status = "thinking"
	status_text_bottom.text = "AEGIS IS THINKING..."

	var payload := JSON.stringify({"text": voice_text})
	_pending_voice_request = "voice_respond"
	_http_voice.request(BRIDGE_URL + "/voice_respond", ["Content-Type: application/json"], HTTPClient.METHOD_POST, payload)

func _finish_listening_error(error_msg: String) -> void:
	is_listening = false
	mic_btn.modulate = Color(1.0, 1.0, 1.0, 1.0)
	captions_label.text = "🎤 " + error_msg + " Try again or type your message."
	status_text_bottom.text = "AEGIS IS LISTENING"
