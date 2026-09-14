@tool
extends SceneTree

func _init() -> void:
	var img := Image.new()
	img.load("res://assets/character_avatar.png")
	
	print("Scanning eye region (y=30 to 52, x=95 to 160):")
	# Left eye roughly x=100..118, Right eye roughly x=138..156
	var min_dark := 1.0
	var left_eye_pt := Vector2i.ZERO
	var right_eye_pt := Vector2i.ZERO
	
	for y in range(32, 52):
		for x in range(95, 125):
			var c := img.get_pixel(x, y)
			# Purple/dark eye pupil has low brightness or high purple
			if c.r < 0.35 and c.g < 0.25 and c.b > 0.35:
				left_eye_pt = Vector2i(x, y)
		for x in range(132, 160):
			var c := img.get_pixel(x, y)
			if c.r < 0.35 and c.g < 0.25 and c.b > 0.35:
				right_eye_pt = Vector2i(x, y)
				
	print("Sampled eye coordinates: Left ~ ", left_eye_pt, " Right ~ ", right_eye_pt)
	quit(0)
