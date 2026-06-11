@tool
extends EditorPlugin

const HttpServer = preload("res://addons/godot_claude_mcp/http_server.gd")
const SceneApi = preload("res://addons/godot_claude_mcp/scene_api.gd")

const PORT := 6400

var _http_server: GodotMcpHttpServer
var _scene_api: GodotMcpSceneApi


func _enter_tree() -> void:
	_scene_api = SceneApi.new(get_editor_interface())
	_http_server = HttpServer.new(_scene_api)
	_http_server.start(PORT)
	add_tool_menu_item("Godot MCP: Status", _show_status)


func _exit_tree() -> void:
	remove_tool_menu_item("Godot MCP: Status")
	if _http_server:
		_http_server.stop()


func _process(_delta: float) -> void:
	if _http_server:
		_http_server.poll()


func _show_status() -> void:
	print("[GodotMCP] Running on localhost:%d" % PORT)
