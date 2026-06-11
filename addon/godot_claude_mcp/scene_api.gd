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


# ── Mutations ────────────────────────────────────────────────────────────────

func place_scene(body: Dictionary) -> Dictionary:
	var scene_path: String = body.get("scene_path", "")
	if scene_path.is_empty():
		return {"ok": false, "error": "scene_path is required"}

	var packed := load(scene_path) as PackedScene
	if not packed:
		return {"ok": false, "error": "could not load scene: " + scene_path}

	var root := _editor.get_edited_scene_root()
	if not root:
		return {"ok": false, "error": "no scene is currently open in the editor"}

	var parent_path: String = body.get("parent_path", ".")
	var parent: Node = root if parent_path == "." else root.get_node_or_null(parent_path)
	if not parent:
		return {"ok": false, "error": "parent node not found: " + parent_path}

	var instance := packed.instantiate()
	var node_name: String = body.get("name", "")
	if not node_name.is_empty():
		instance.name = node_name

	var undo := _editor.get_undo_redo()
	undo.create_action("Place Scene: " + scene_path.get_file())
	undo.add_do_method(parent, "add_child", instance)
	undo.add_do_method(instance, "set_owner", root)
	if instance is Node3D:
		var euler := Vector3(
			deg_to_rad(float(body.get("rot_x", 0.0))),
			deg_to_rad(float(body.get("rot_y", 0.0))),
			deg_to_rad(float(body.get("rot_z", 0.0)))
		)
		var t := Transform3D(Basis.from_euler(euler), Vector3(
			float(body.get("x", 0.0)),
			float(body.get("y", 0.0)),
			float(body.get("z", 0.0))
		))
		undo.add_do_method(instance, "set_transform", t)
		undo.add_undo_method(instance, "set_transform", Transform3D.IDENTITY)
	undo.add_undo_method(parent, "remove_child", instance)
	undo.commit_action()

	return {"ok": true, "node_path": str(root.get_path_to(instance))}


func remove_node(body: Dictionary) -> Dictionary:
	var node_path: String = body.get("node_path", "")
	if node_path.is_empty():
		return {"ok": false, "error": "node_path is required"}

	var root := _editor.get_edited_scene_root()
	if not root:
		return {"ok": false, "error": "no scene is currently open in the editor"}

	var node := root.get_node_or_null(node_path)
	if not node:
		return {"ok": false, "error": "node not found: " + node_path}

	var parent := node.get_parent()
	var idx := node.get_index()

	var undo := _editor.get_undo_redo()
	undo.create_action("Remove Node: " + node.name)
	undo.add_do_method(parent, "remove_child", node)
	undo.add_undo_method(parent, "add_child", node)
	undo.add_undo_method(parent, "move_child", node, idx)
	undo.add_undo_method(node, "set_owner", root)
	undo.commit_action()

	return {"ok": true, "removed": node_path}


func set_node_transform(body: Dictionary) -> Dictionary:
	var node_path: String = body.get("node_path", "")
	if node_path.is_empty():
		return {"ok": false, "error": "node_path is required"}

	var root := _editor.get_edited_scene_root()
	if not root:
		return {"ok": false, "error": "no scene is currently open in the editor"}

	var node := root.get_node_or_null(node_path)
	if not node:
		return {"ok": false, "error": "node not found: " + node_path}

	if not node is Node3D:
		return {"ok": false, "error": "node is not a Node3D: " + node_path}

	var old_transform := (node as Node3D).transform
	var euler := Vector3(
		deg_to_rad(float(body.get("rot_x", 0.0))),
		deg_to_rad(float(body.get("rot_y", 0.0))),
		deg_to_rad(float(body.get("rot_z", 0.0)))
	)
	var new_scale := Vector3(
		float(body.get("scale_x", 1.0)),
		float(body.get("scale_y", 1.0)),
		float(body.get("scale_z", 1.0))
	)
	var new_transform := Transform3D(
		Basis.from_euler(euler).scaled(new_scale),
		Vector3(float(body.get("x", 0.0)), float(body.get("y", 0.0)), float(body.get("z", 0.0)))
	)

	var undo := _editor.get_undo_redo()
	undo.create_action("Set Transform: " + node.name)
	undo.add_do_property(node, "transform", new_transform)
	undo.add_undo_property(node, "transform", old_transform)
	undo.commit_action()

	return {"ok": true, "node_path": node_path}


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
