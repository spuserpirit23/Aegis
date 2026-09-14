extends SceneTree

var _frames := 0

func _init() -> void:
	# Load main scene
	var scene: PackedScene = load("res://scenes/AegisMainUI.tscn")
	var instance: Node = scene.instantiate()
	root.add_child(instance)

func _process(_delta: float) -> bool:
	_frames += 1
	if _frames == 15:
		var img: Image = root.get_viewport().get_texture().get_image()
		var save_path := "res://screenshot.png"
		var err := img.save_png(save_path)
		print("Screenshot saved to ", save_path, " err=", err)
		quit(0)
		return true
	return false
