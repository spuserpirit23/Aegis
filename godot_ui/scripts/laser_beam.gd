extends Control
class_name LaserBeam

func _draw() -> void:
	var h: float = size.y
	# Draw outer purple haze
	for w in range(16, 0, -2):
		var alpha: float = 0.03 * (1.0 - float(w) / 16.0)
		draw_line(Vector2(w, 0), Vector2(w, h), Color(0.68, 0.25, 0.98, alpha), float(w * 2))
	# Draw core beam
	draw_line(Vector2(0, 0), Vector2(0, h), Color(0.55, 0.2, 0.9, 0.45), 4.0)
	draw_line(Vector2(0, 0), Vector2(0, h), Color(0.85, 0.5, 1.0, 0.8), 2.0)
	draw_line(Vector2(0, 0), Vector2(0, h), Color(1.0, 0.9, 1.0, 0.95), 1.0)
