import httpx
import pytest
import respx

from godot_mcp.server import _call, GODOT_URL


@respx.mock
def test_call_returns_error_on_connection_refused():
    respx.get(f"{GODOT_URL}/scene/tree").mock(side_effect=httpx.ConnectError("refused"))
    result = _call("GET", "/scene/tree", params={"path": "res://Room4.tscn"})
    assert result["ok"] is False
    assert "not running" in result["error"].lower() or "Cannot connect" in result["error"]


@respx.mock
def test_call_returns_error_on_timeout():
    respx.get(f"{GODOT_URL}/scene/tree").mock(side_effect=httpx.TimeoutException("timeout"))
    result = _call("GET", "/scene/tree", params={"path": "res://Room4.tscn"})
    assert result["ok"] is False
    assert "timed out" in result["error"]


@respx.mock
def test_call_returns_response_on_success():
    respx.get(f"{GODOT_URL}/scene/tree").mock(
        return_value=httpx.Response(200, json={"ok": True, "root": "Room4", "nodes": []})
    )
    result = _call("GET", "/scene/tree", params={"path": "res://Room4.tscn"})
    assert result["ok"] is True
    assert result["root"] == "Room4"


# ── Query tool tests ──────────────────────────────────────────────────────────

from godot_mcp.server import get_scene_tree, list_scenes


@respx.mock
def test_get_scene_tree_success():
    respx.get(f"{GODOT_URL}/scene/tree").mock(return_value=httpx.Response(200, json={
        "ok": True,
        "root": "Room4",
        "nodes": [
            {"path": ".", "type": "Node3D", "x": 0.0, "y": 0.0, "z": 0.0,
             "rot_x": 0.0, "rot_y": 0.0, "rot_z": 0.0,
             "scale_x": 1.0, "scale_y": 1.0, "scale_z": 1.0},
        ],
    }))
    result = get_scene_tree("res://Content/Room4.tscn")
    assert result["ok"] is True
    assert result["root"] == "Room4"
    assert len(result["nodes"]) == 1
    assert result["nodes"][0]["path"] == "."


@respx.mock
def test_get_scene_tree_passes_path_param():
    route = respx.get(f"{GODOT_URL}/scene/tree").mock(
        return_value=httpx.Response(200, json={"ok": True, "root": "R", "nodes": []})
    )
    get_scene_tree("res://Content/Room4.tscn")
    assert route.called
    assert route.calls[0].request.url.params["path"] == "res://Content/Room4.tscn"


@respx.mock
def test_list_scenes_success():
    respx.get(f"{GODOT_URL}/scene/list").mock(return_value=httpx.Response(200, json={
        "ok": True,
        "scenes": [
            "res://Assets/Props/Barrel.tscn",
            "res://Assets/Props/Crate.tscn",
        ],
    }))
    result = list_scenes("res://Assets/Props/")
    assert result["ok"] is True
    assert "res://Assets/Props/Barrel.tscn" in result["scenes"]


@respx.mock
def test_list_scenes_passes_dir_param():
    route = respx.get(f"{GODOT_URL}/scene/list").mock(
        return_value=httpx.Response(200, json={"ok": True, "scenes": []})
    )
    list_scenes("res://Assets/Props/")
    assert route.called
    assert route.calls[0].request.url.params["dir"] == "res://Assets/Props/"


@respx.mock
def test_get_scene_tree_propagates_plugin_error():
    respx.get(f"{GODOT_URL}/scene/tree").mock(return_value=httpx.Response(400, json={
        "ok": False, "error": "scene not found: res://Missing.tscn"
    }))
    result = get_scene_tree("res://Missing.tscn")
    assert result["ok"] is False
    assert "not found" in result["error"]


# ── Mutation tool tests ───────────────────────────────────────────────────────

from godot_mcp.server import place_scene, remove_node, set_node_transform


@respx.mock
def test_place_scene_sends_correct_body():
    route = respx.post(f"{GODOT_URL}/scene/place").mock(
        return_value=httpx.Response(200, json={"ok": True, "node_path": "Layout/Barrel"})
    )
    result = place_scene(
        scene_path="res://Assets/Props/Barrel.tscn",
        x=1.0, y=0.0, z=2.0,
        rot_y=45.0,
        parent_path="Layout",
        name="Barrel",
    )
    assert result["ok"] is True
    assert result["node_path"] == "Layout/Barrel"
    body = route.calls[0].request.read()
    import json
    parsed = json.loads(body)
    assert parsed["scene_path"] == "res://Assets/Props/Barrel.tscn"
    assert parsed["x"] == 1.0
    assert parsed["rot_y"] == 45.0
    assert parsed["parent_path"] == "Layout"
    assert parsed["name"] == "Barrel"


@respx.mock
def test_place_scene_default_rotation_is_zero():
    route = respx.post(f"{GODOT_URL}/scene/place").mock(
        return_value=httpx.Response(200, json={"ok": True, "node_path": "Barrel"})
    )
    place_scene(scene_path="res://Barrel.tscn", x=0.0, y=0.0, z=0.0)
    import json
    body = json.loads(route.calls[0].request.read())
    assert body["rot_x"] == 0.0
    assert body["rot_y"] == 0.0
    assert body["rot_z"] == 0.0


@respx.mock
def test_remove_node_sends_correct_body():
    route = respx.delete(f"{GODOT_URL}/scene/node").mock(
        return_value=httpx.Response(200, json={"ok": True, "removed": "Layout/Barrel"})
    )
    result = remove_node(
        scene_path="res://Content/Room4.tscn",
        node_path="Layout/Barrel",
    )
    assert result["ok"] is True
    import json
    body = json.loads(route.calls[0].request.read())
    assert body["node_path"] == "Layout/Barrel"
    assert body["scene_path"] == "res://Content/Room4.tscn"


@respx.mock
def test_set_node_transform_sends_all_fields():
    route = respx.put(f"{GODOT_URL}/scene/transform").mock(
        return_value=httpx.Response(200, json={"ok": True, "node_path": "Layout/Barrel"})
    )
    result = set_node_transform(
        scene_path="res://Content/Room4.tscn",
        node_path="Layout/Barrel",
        x=3.0, y=0.0, z=4.0,
        rot_x=0.0, rot_y=90.0, rot_z=0.0,
        scale_x=2.0, scale_y=2.0, scale_z=2.0,
    )
    assert result["ok"] is True
    import json
    body = json.loads(route.calls[0].request.read())
    assert body["x"] == 3.0
    assert body["rot_y"] == 90.0
    assert body["scale_x"] == 2.0


@respx.mock
def test_mutation_propagates_plugin_error():
    respx.post(f"{GODOT_URL}/scene/place").mock(return_value=httpx.Response(400, json={
        "ok": False, "error": "could not load scene: res://Missing.tscn"
    }))
    result = place_scene(scene_path="res://Missing.tscn", x=0.0, y=0.0, z=0.0)
    assert result["ok"] is False
    assert "Missing.tscn" in result["error"]
