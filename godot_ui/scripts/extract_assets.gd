@tool
extends SceneTree

func _init() -> void:
	var img := Image.new()
	var err := img.load("res://assets/reference.png")
	if err != OK:
		print("Error loading reference: ", err)
		quit(1)
		return

	# Character crop:
	# From reference 1024x682:
	# Center character is at x: 380 to 625, y: 210 to 535
	# Let's crop x: 375 to 630 (width: 255), y: 205 to 535 (height: 330)
	var char_img := img.get_region(Rect2i(375, 205, 256, 330))
	char_img.save_png("res://assets/character_avatar.png")

	# Aegis Logo (clean crop)
	var logo_img := img.get_region(Rect2i(13, 25, 34, 38))
	logo_img.save_png("res://assets/icon_logo.png")

	# Clean left plugin icons:
	var left_icons := {
		"icon_conversation": Rect2i(17, 153, 20, 20),
		"icon_memory": Rect2i(17, 197, 20, 20),
		"icon_trust": Rect2i(17, 240, 20, 20),
		"icon_emotion": Rect2i(17, 283, 20, 20),
		"icon_analytics": Rect2i(17, 330, 20, 20),
		"icon_integrations": Rect2i(17, 372, 20, 20),
		"icon_tools": Rect2i(17, 417, 20, 20),
		"icon_settings": Rect2i(17, 461, 20, 20),
	}
	for name in left_icons:
		var icon: Image = img.get_region(left_icons[name])
		icon.save_png("res://assets/" + name + ".png")

	# Clean right instructions icons:
	var right_icons := {
		"icon_inst_chat": Rect2i(788, 112, 22, 22),
		"icon_inst_plugins": Rect2i(788, 187, 22, 22),
		"icon_inst_shield": Rect2i(788, 277, 22, 22),
		"icon_inst_gear": Rect2i(788, 357, 22, 22),
		"icon_inst_help": Rect2i(788, 432, 22, 22),
		"icon_info": Rect2i(968, 62, 16, 16),
	}
	for name in right_icons:
		var icon: Image = img.get_region(right_icons[name])
		icon.save_png("res://assets/" + name + ".png")

	# Bottom bar icons:
	var mic_img := img.get_region(Rect2i(714, 594, 18, 22))
	mic_img.save_png("res://assets/icon_mic.png")
	var send_img := img.get_region(Rect2i(742, 584, 48, 40))
	send_img.save_png("res://assets/icon_send.png")

	# Clean left glow beam strip (x: 130 to 142)
	var glow_img := img.get_region(Rect2i(131, 0, 16, 682))
	glow_img.save_png("res://assets/left_glow_beam.png")

	print("Refined assets extracted!")
	quit(0)
