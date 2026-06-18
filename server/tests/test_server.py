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
