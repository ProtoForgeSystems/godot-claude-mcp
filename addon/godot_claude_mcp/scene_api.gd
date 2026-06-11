@tool
class_name GodotMcpSceneApi

var _editor: EditorInterface


func _init(editor: EditorInterface) -> void:
	_editor = editor


func get_scene_tree(_scene_path: String) -> Dictionary:
	return {"ok": false, "error": "not yet implemented"}


func list_scenes(_directory: String) -> Dictionary:
	return {"ok": false, "error": "not yet implemented"}


func place_scene(_body: Dictionary) -> Dictionary:
	return {"ok": false, "error": "not yet implemented"}


func remove_node(_body: Dictionary) -> Dictionary:
	return {"ok": false, "error": "not yet implemented"}


func set_node_transform(_body: Dictionary) -> Dictionary:
	return {"ok": false, "error": "not yet implemented"}
