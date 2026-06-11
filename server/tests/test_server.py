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
