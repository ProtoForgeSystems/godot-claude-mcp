@tool
class_name GodotMcpSceneApi

var _editor: EditorInterface


func _init(editor: EditorInterface) -> void:
	_editor = editor


# ── Query ────────────────────────────────────────────────────────────────────

func get_scene_tree(scene_path: String) -> Dictionary:
	if scene_path.is_empty():
		return {"ok": false, "error": "scene_path is required"}
	var root := _get_scene_root(scene_path)
	if not root:
		return {"ok": false, "error": "scene not found or could not be opened: " + scene_path}
	var nodes: Array = []
	_collect_nodes(root, root, nodes)
	return {"ok": true, "root": root.name, "nodes": nodes}


func list_scenes(directory: String) -> Dictionary:
	if directory.is_empty():
		return {"ok": false, "error": "dir is required"}
	if not DirAccess.dir_exists_absolute(directory):
		return {"ok": false, "error": "directory not found: " + directory}
	var scenes: Array = []
	_collect_scenes(directory, scenes)
	return {"ok": true, "scenes": scenes}


# ── Mutations (stubs — replaced in Task 4) ───────────────────────────────────

func place_scene(_body: Dictionary) -> Dictionary:
	return {"ok": false, "error": "not yet implemented"}


func remove_node(_body: Dictionary) -> Dictionary:
	return {"ok": false, "error": "not yet implemented"}


func set_node_transform(_body: Dictionary) -> Dictionary:
	return {"ok": false, "error": "not yet implemented"}


# ── Helpers ───────────────────────────────────────────────────────────────────

func _get_scene_root(scene_path: String) -> Node:
	var root := _editor.get_edited_scene_root()
	if root and root.scene_file_path == scene_path:
		return root
	_editor.open_scene_from_path(scene_path)
	return _editor.get_edited_scene_root()


func _collect_nodes(root: Node, node: Node, result: Array) -> void:
	var entry := {
		"path": str(root.get_path_to(node)),
		"type": node.get_class(),
	}
	if node is Node3D:
		var t := node.transform
		var euler := t.basis.get_euler()
		var scale := t.basis.get_scale()
		entry["x"] = t.origin.x
		entry["y"] = t.origin.y
		entry["z"] = t.origin.z
		entry["rot_x"] = rad_to_deg(euler.x)
		entry["rot_y"] = rad_to_deg(euler.y)
		entry["rot_z"] = rad_to_deg(euler.z)
		entry["scale_x"] = scale.x
		entry["scale_y"] = scale.y
		entry["scale_z"] = scale.z
	result.append(entry)
	for child in node.get_children():
		_collect_nodes(root, child, result)


func _collect_scenes(path: String, result: Array) -> void:
	var dir := DirAccess.open(path)
	if not dir:
		return
	dir.list_dir_begin()
	var entry := dir.get_next()
	while not entry.is_empty():
		if dir.current_is_dir() and not entry.begins_with("."):
			_collect_scenes(path.path_join(entry), result)
		elif entry.ends_with(".tscn"):
			result.append(path.path_join(entry))
		entry = dir.get_next()
	dir.list_dir_end()
