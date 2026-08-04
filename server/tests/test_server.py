from unittest.mock import AsyncMock

import pytest

from godot_mcp import server
from godot_mcp.transport import GodotRpcError


@pytest.fixture(autouse=True)
def fake_bridge(monkeypatch):
    fake = AsyncMock()
    monkeypatch.setattr(server, "bridge", fake)
    return fake


@pytest.mark.asyncio
async def test_call_returns_result_on_success(fake_bridge):
    fake_bridge.call.return_value = {"node_path": "StaticBody3D"}
    result = await server._call("add_node", {"type": "StaticBody3D"})
    assert result == {"node_path": "StaticBody3D"}
    fake_bridge.call.assert_awaited_once_with("add_node", {"type": "StaticBody3D"})


@pytest.mark.asyncio
async def test_call_wraps_godot_rpc_error(fake_bridge):
    fake_bridge.call.side_effect = GodotRpcError({"code": -32601, "message": "Method not found: bogus"})
    result = await server._call("bogus", {})
    assert result["ok"] is False
    assert "Method not found" in result["error"]
    assert result["code"] == -32601


@pytest.mark.asyncio
async def test_call_wraps_connection_error(fake_bridge):
    fake_bridge.call.side_effect = ConnectionError("Godot editor is not connected")
    result = await server._call("get_scene_tree", {})
    assert result["ok"] is False
    assert "not connected" in result["error"]


@pytest.mark.asyncio
async def test_move_node_passes_through_params(fake_bridge):
    fake_bridge.call.return_value = {"node": "CollisionShape3D"}
    result = await server.move_node(node_path="StaticBody/CollisionShape3D", new_parent_path=".")
    assert result == {"node": "CollisionShape3D"}
    fake_bridge.call.assert_awaited_once_with(
        "move_node", {"node_path": "StaticBody/CollisionShape3D", "new_parent_path": "."}
    )


@pytest.mark.asyncio
async def test_execute_editor_script_defaults_unsafe_io_to_false(fake_bridge):
    fake_bridge.call.return_value = {"output": []}
    await server.execute_editor_script(code="print(1)")
    fake_bridge.call.assert_awaited_once_with(
        "execute_editor_script", {"code": "print(1)", "allow_unsafe_editor_io": False}
    )


@pytest.mark.asyncio
async def test_play_scene_defaults_to_main(fake_bridge):
    fake_bridge.call.return_value = {"playing": True, "mode": "main"}
    await server.play_scene()
    fake_bridge.call.assert_awaited_once_with("play_scene", {"mode": "main"})


@pytest.mark.asyncio
async def test_stop_scene_sends_no_params(fake_bridge):
    fake_bridge.call.return_value = {"stopped": True}
    await server.stop_scene()
    fake_bridge.call.assert_awaited_once_with("stop_scene", {})


@pytest.mark.asyncio
async def test_get_editor_screenshot_omits_save_path_when_blank(fake_bridge):
    fake_bridge.call.return_value = {"image_base64": "abc", "width": 100, "height": 100}
    await server.get_editor_screenshot()
    fake_bridge.call.assert_awaited_once_with("get_editor_screenshot", {})


@pytest.mark.asyncio
async def test_get_editor_screenshot_passes_save_path(fake_bridge):
    fake_bridge.call.return_value = {"saved_path": "res://shot.png", "width": 100, "height": 100}
    await server.get_editor_screenshot(save_path="res://shot.png")
    fake_bridge.call.assert_awaited_once_with("get_editor_screenshot", {"save_path": "res://shot.png"})


@pytest.mark.asyncio
async def test_get_game_screenshot_passes_save_path(fake_bridge):
    fake_bridge.call.return_value = {"saved_path": "res://shot.png", "width": 100, "height": 100}
    await server.get_game_screenshot(save_path="res://shot.png")
    fake_bridge.call.assert_awaited_once_with("get_game_screenshot", {"save_path": "res://shot.png"})


# ── Editor camera ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_set_editor_camera_omits_unset_params(fake_bridge):
    fake_bridge.call.return_value = {"fov": 45.0}
    await server.set_editor_camera(look_at=[0, 1, 0])
    fake_bridge.call.assert_awaited_once_with(
        "set_editor_camera", {"look_at": {"x": 0.0, "y": 1.0, "z": 0.0}}
    )


@pytest.mark.asyncio
async def test_set_editor_camera_shapes_all_vectors(fake_bridge):
    fake_bridge.call.return_value = {}
    await server.set_editor_camera(position=[1, 2, 3], rotation_degrees=[0, 90, 0], fov=30.0)
    fake_bridge.call.assert_awaited_once_with(
        "set_editor_camera",
        {
            "position": {"x": 1.0, "y": 2.0, "z": 3.0},
            "rotation_degrees": {"x": 0.0, "y": 90.0, "z": 0.0},
            "fov": 30.0,
        },
    )


@pytest.mark.asyncio
async def test_set_editor_camera_rejects_wrong_length_vector(fake_bridge):
    with pytest.raises(ValueError, match="Expected 3 components"):
        await server.set_editor_camera(position=[1, 2])
    fake_bridge.call.assert_not_awaited()


# ── GDScript-backed tools ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_script_json_parses_last_output_line(fake_bridge):
    fake_bridge.call.return_value = {"output": ["noise", '{"ok": true, "bone_count": 65}']}
    result = await server.get_skeleton_bones()
    assert result == {"ok": True, "bone_count": 65}


@pytest.mark.asyncio
async def test_script_json_reports_non_json_output(fake_bridge):
    fake_bridge.call.return_value = {"output": ["Parse Error: something"]}
    result = await server.get_skeleton_bones()
    assert result["ok"] is False
    assert "not JSON" in result["error"]
    assert result["raw"] == ["Parse Error: something"]


@pytest.mark.asyncio
async def test_script_json_reports_empty_output(fake_bridge):
    fake_bridge.call.return_value = {"output": []}
    result = await server.get_skeleton_bones()
    assert result["ok"] is False
    assert "no output" in result["error"]


@pytest.mark.asyncio
async def test_script_json_propagates_bridge_error(fake_bridge):
    fake_bridge.call.side_effect = ConnectionError("Godot editor is not connected")
    result = await server.get_skeleton_bones()
    assert result["ok"] is False
    assert "not connected" in result["error"]


@pytest.mark.asyncio
async def test_get_skeleton_bones_embeds_args_and_stays_safe(fake_bridge):
    fake_bridge.call.return_value = {"output": ['{"ok": true}']}
    await server.get_skeleton_bones(node_path="Rig/Skeleton3D", filter="leg", include_pose=True)
    method, params = fake_bridge.call.await_args.args
    assert method == "execute_editor_script"
    assert params["allow_unsafe_editor_io"] is False
    assert '"Rig/Skeleton3D"' in params["code"]
    assert '"leg"' in params["code"]
    assert "var want_pose: bool = true" in params["code"]


@pytest.mark.asyncio
async def test_set_import_settings_allows_unsafe_io(fake_bridge):
    fake_bridge.call.return_value = {"output": ['{"ok": true}']}
    await server.set_import_settings(
        path="res://Content/Rig.fbx", options={"nodes/root_type": "StaticBody3D"}
    )
    _, params = fake_bridge.call.await_args.args
    # ConfigFile.save is on the plugin's refusal list; the wrapper must opt in.
    assert params["allow_unsafe_editor_io"] is True
    assert "nodes/root_type" in params["code"]
    assert "var do_reimport: bool = true" in params["code"]


@pytest.mark.asyncio
async def test_create_resource_allows_unsafe_io(fake_bridge):
    fake_bridge.call.return_value = {"output": ['{"ok": true}']}
    await server.create_resource(
        path="res://Data/Claw.tres", type="MutationDefinition", properties={"Damage": 3}
    )
    _, params = fake_bridge.call.await_args.args
    assert params["allow_unsafe_editor_io"] is True
    assert '"MutationDefinition"' in params["code"]
    assert "var overwrite: bool = false" in params["code"]


@pytest.mark.asyncio
async def test_generated_gdscript_escapes_embedded_quotes(fake_bridge):
    """A path containing a quote must not break out of its GDScript literal."""
    fake_bridge.call.return_value = {"output": ['{"ok": true}']}
    await server.get_import_settings(path='res://od"d.fbx')
    _, params = fake_bridge.call.await_args.args
    assert r'"res://od\"d.fbx"' in params["code"]
