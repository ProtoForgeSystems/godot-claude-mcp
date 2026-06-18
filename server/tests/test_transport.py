import asyncio
import json

import pytest
from websockets.asyncio.client import connect

from godot_mcp.transport import GodotBridge, GodotRpcError

PORT = 6599  # arbitrary, outside the plugin's 6505-6514 range to avoid collisions


@pytest.fixture
async def bridge():
    b = GodotBridge(base_port=PORT, port_range_size=1)
    server_task = asyncio.create_task(b.serve_forever())
    await _wait_for_port(PORT)
    try:
        yield b
    finally:
        server_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await server_task


async def _wait_for_port(port: int, timeout: float = 2.0) -> None:
    """Poll until the bridge's listener is actually bound before tests connect."""
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        try:
            probe = await connect(f"ws://127.0.0.1:{port}")
            await probe.close()
            return
        except (ConnectionRefusedError, OSError):
            if asyncio.get_running_loop().time() > deadline:
                raise
            await asyncio.sleep(0.02)


async def _fake_godot():
    """Connects like the Godot plugin would and returns the live connection."""
    return await connect(f"ws://127.0.0.1:{PORT}")


@pytest.mark.asyncio
async def test_call_returns_result_on_success(bridge):
    godot = await _fake_godot()
    try:

        async def respond():
            raw = await godot.recv()
            request = json.loads(raw)
            await godot.send(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": {"ok": True}}))

        responder = asyncio.create_task(respond())
        result = await bridge.call("get_scene_tree", {"max_depth": -1})
        await responder
        assert result == {"ok": True}
    finally:
        await godot.close()


@pytest.mark.asyncio
async def test_call_raises_godot_rpc_error(bridge):
    godot = await _fake_godot()
    try:

        async def respond():
            raw = await godot.recv()
            request = json.loads(raw)
            await godot.send(json.dumps({
                "jsonrpc": "2.0",
                "id": request["id"],
                "error": {"code": -32601, "message": "Method not found: bogus"},
            }))

        responder = asyncio.create_task(respond())
        with pytest.raises(GodotRpcError, match="Method not found"):
            await bridge.call("bogus", {})
        await responder
    finally:
        await godot.close()


@pytest.mark.asyncio
async def test_call_raises_connection_error_when_no_godot_attached(bridge):
    with pytest.raises(ConnectionError):
        await bridge.call("get_scene_tree", {}, connect_timeout=0.2)


@pytest.mark.asyncio
async def test_responds_to_godot_ping_with_pong(bridge):
    godot = await _fake_godot()
    try:
        await godot.send(json.dumps({"jsonrpc": "2.0", "method": "ping", "params": {}}))
        raw = await asyncio.wait_for(godot.recv(), timeout=2.0)
        assert json.loads(raw) == {"jsonrpc": "2.0", "method": "pong", "params": {}}
    finally:
        await godot.close()


@pytest.mark.asyncio
async def test_disconnect_fails_pending_calls(bridge):
    godot = await _fake_godot()
    await asyncio.sleep(0.1)  # let the server register the connection

    call_task = asyncio.create_task(bridge.call("get_scene_tree", {}))
    await asyncio.sleep(0.1)  # let the request go out before we yank the connection
    await godot.close()

    with pytest.raises(ConnectionError):
        await call_task


@pytest.mark.asyncio
async def test_falls_through_to_next_port_when_first_is_taken():
    """Regression test: with multiple Claude Code sessions sharing the global
    'godot' MCP server config, the first session's process binds the base
    port and every other session's process must not crash — it should bind
    the next port in range instead, matching what Godot's client already
    polls for."""
    occupied = GodotBridge(base_port=PORT, port_range_size=1)
    first_task = asyncio.create_task(occupied.serve_forever())
    await _wait_for_port(PORT)
    try:
        contender = GodotBridge(base_port=PORT, port_range_size=2)
        second_task = asyncio.create_task(contender.serve_forever())
        try:
            await _wait_for_port(PORT + 1)  # must not raise / hang — proves it didn't crash
        finally:
            second_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await second_task
    finally:
        first_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first_task


@pytest.mark.asyncio
async def test_concurrent_calls_correlate_independently(bridge):
    godot = await _fake_godot()
    try:

        async def respond_in_reverse_order():
            first = json.loads(await godot.recv())
            second = json.loads(await godot.recv())
            # Reply to the second request first to prove correlation isn't order-dependent.
            await godot.send(json.dumps({"jsonrpc": "2.0", "id": second["id"], "result": {"which": "second"}}))
            await godot.send(json.dumps({"jsonrpc": "2.0", "id": first["id"], "result": {"which": "first"}}))

        responder = asyncio.create_task(respond_in_reverse_order())
        first_call = asyncio.create_task(bridge.call("a", {}))
        second_call = asyncio.create_task(bridge.call("b", {}))
        first_result, second_result = await asyncio.gather(first_call, second_call)
        await responder
        assert first_result == {"which": "first"}
        assert second_result == {"which": "second"}
    finally:
        await godot.close()
