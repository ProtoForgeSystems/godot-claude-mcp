@tool
class_name GodotMcpHttpServer

var _tcp: TCPServer
var _scene_api


func _init(scene_api) -> void:
	_scene_api = scene_api


func start(port: int) -> void:
	_tcp = TCPServer.new()
	var err := _tcp.listen(port, "127.0.0.1")
	if err != OK:
		push_error("[GodotMCP] Failed to listen on port %d: %s" % [port, error_string(err)])
	else:
		print("[GodotMCP] Listening on localhost:%d" % port)


func stop() -> void:
	if _tcp:
		_tcp.stop()
		print("[GodotMCP] Stopped.")


func poll() -> void:
	if not _tcp or not _tcp.is_connection_available():
		return
	var peer: StreamPeerTCP = _tcp.take_connection()
	_handle(peer)


func _handle(peer: StreamPeerTCP) -> void:
	var raw := ""
	# Wait up to 2s for the full request. A POST body arrives in a later TCP
	# segment than the headers, so we must actually sleep between idle polls
	# rather than spin — otherwise the loop exits before the body lands and
	# JSON parsing yields an empty body. Only bail on a hard-dead socket; a
	# freshly accepted connection reports STATUS_CONNECTING on the first poll.
	var deadline := Time.get_ticks_msec() + 2000
	while Time.get_ticks_msec() < deadline:
		peer.poll()
		var status := peer.get_status()
		var available := peer.get_available_bytes()
		if available > 0:
			raw += peer.get_utf8_string(available)
			if "\r\n\r\n" in raw:
				var header_end := raw.find("\r\n\r\n")
				var body_so_far := raw.substr(header_end + 4)
				var content_length := _parse_content_length(raw.substr(0, header_end))
				if body_so_far.to_utf8_buffer().size() >= content_length:
					break
		elif status == StreamPeerTCP.STATUS_ERROR or status == StreamPeerTCP.STATUS_NONE:
			break
		else:
			OS.delay_msec(2)

	if "\r\n\r\n" not in raw:
		_respond(peer, 400, {"ok": false, "error": "incomplete request"})
		return

	var header_end := raw.find("\r\n\r\n")
	var header_section := raw.substr(0, header_end)
	var body := raw.substr(header_end + 4)

	var first_line := header_section.split("\r\n")[0]
	var tokens := first_line.split(" ")
	if tokens.size() < 2:
		_respond(peer, 400, {"ok": false, "error": "bad request line"})
		return

	var method := tokens[0]
	var full_path := tokens[1]
	var path := full_path.split("?")[0]
	var query_str := full_path.substr(full_path.find("?") + 1) if "?" in full_path else ""

	var params := {}
	if not query_str.is_empty():
		for pair in query_str.split("&"):
			var kv := pair.split("=", true, 1)
			if kv.size() == 2:
				params[kv[0]] = kv[1].uri_decode()

	var body_data := {}
	if not body.strip_edges().is_empty():
		var json := JSON.new()
		if json.parse(body.strip_edges()) == OK:
			body_data = json.get_data()

	# The capture route renders across real frames, so it must be awaited;
	# all other routes resolve synchronously.
	var result: Dictionary
	if method == "POST" and path == "/view/capture":
		result = await _scene_api.capture_view(body_data)
	else:
		result = _dispatch(method, path, params, body_data)
	var status := 200 if result.get("ok", false) else 400
	_respond(peer, status, result)


func _parse_content_length(headers: String) -> int:
	for line in headers.split("\r\n"):
		if line.to_lower().begins_with("content-length:"):
			return int(line.split(":")[1].strip_edges())
	return 0


func _dispatch(method: String, path: String, params: Dictionary, body: Dictionary) -> Dictionary:
	if method == "GET" and path == "/ping":
		return {"ok": true, "status": "running"}
	if method == "GET" and path == "/scene/current":
		return _scene_api.get_current_scene()
	if method == "GET" and path == "/scene/tree":
		return _scene_api.get_scene_tree(params.get("path", ""))
	if method == "GET" and path == "/scene/list":
		return _scene_api.list_scenes(params.get("dir", ""))
	if method == "POST" and path == "/scene/place":
		return _scene_api.place_scene(body)
	if method == "DELETE" and path == "/scene/node":
		return _scene_api.remove_node(body)
	if method == "PUT" and path == "/scene/transform":
		return _scene_api.set_node_transform(body)
	if method == "POST" and path == "/view/capture":
		return _scene_api.capture_view(body)
	return {"ok": false, "error": "unknown route: %s %s" % [method, path]}


func _respond(peer: StreamPeerTCP, status: int, data: Dictionary) -> void:
	var body := JSON.stringify(data)
	var response := "HTTP/1.1 %d OK\r\nContent-Type: application/json\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%s" % [
		status, body.to_utf8_buffer().size(), body
	]
	peer.put_data(response.to_utf8_buffer())
