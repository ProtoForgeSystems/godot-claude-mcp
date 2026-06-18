"""WebSocket bridge to the Godot MCP Pro editor plugin.

The Godot-side plugin (addons/godot_mcp) is a WebSocket *client* — it dials
out to ws://127.0.0.1:6505 (and 6506-6514 for additional sessions) and
retries every 3s until something answers. This module is that something:
a WebSocket *server* speaking the same bare JSON-RPC 2.0 framing the plugin
expects, so the existing, unmodified GDScript command surface in
godot-mcp-pro works without the paid Node.js relay.

Protocol, read directly from addons/godot_mcp/websocket_server.gd:
  request:  {"jsonrpc": "2.0", "id": N, "method": "...", "params": {...}}
  result:   {"jsonrpc": "2.0", "id": N, "result": {...}}
  error:    {"jsonrpc": "2.0", "id": N, "error": {"code": ..., "message": ...}}
  heartbeat: {"jsonrpc": "2.0", "method": "ping"/"pong", "params": {}}
            Godot pings every 5s and force-reconnects after 30s of silence,
            so any reply (including a tool response) resets its timer.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import sys
from typing import Any

from websockets.asyncio.server import ServerConnection, serve

# The addon's websocket_server.gd documents 6505-6509 as the sub-range for
# stdio MCP servers (6510-6514 is reserved for its separate CLI mode) and
# polls all ten ports looking for any listener. Each concurrent Claude Code
# session spawns its own server process, so without scanning this range,
# every session after the first fails to bind 6505 and crashes outright.
BASE_PORT = 6505
PORT_RANGE_SIZE = 5


class GodotRpcError(Exception):
    """Raised when Godot's command router returns a JSON-RPC error object."""

    def __init__(self, error: dict[str, Any]) -> None:
        super().__init__(error.get("message", "Godot RPC error"))
        self.code = error.get("code")
        self.data = error.get("data")


class GodotBridge:
    """Owns the single active connection from the Godot editor plugin.

    One process runs per Claude Code session, and Godot polls every port in
    6505-6514 for a listener — so this binds the first free port starting
    at base_port (rather than hard-coding 6505) to let multiple sessions
    run against the same editor concurrently without colliding.
    """

    def __init__(self, base_port: int = BASE_PORT, port_range_size: int = PORT_RANGE_SIZE) -> None:
        self._base_port = base_port
        self._port_range_size = port_range_size
        self._connection: ServerConnection | None = None
        self._connected = asyncio.Event()
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._next_id = itertools.count(1)

    async def serve_forever(self) -> None:
        last_error: OSError | None = None
        for port in range(self._base_port, self._base_port + self._port_range_size):
            try:
                async with serve(self._handle_connection, "127.0.0.1", port) as server:
                    print(f"[godot-mcp] listening on 127.0.0.1:{port}", file=sys.stderr)
                    await server.serve_forever()
                return
            except OSError as e:
                last_error = e
                continue
        # Every port in the range is already claimed by other sessions.
        # Degrade gracefully: the stdio/MCP side still runs, tool calls just
        # report "Godot editor is not connected" instead of the whole
        # process dying — better than taking down a session over a bind
        # collision that resolves itself once another session closes.
        print(
            f"[godot-mcp] no free port in {self._base_port}-"
            f"{self._base_port + self._port_range_size - 1}, "
            f"Godot bridge disabled: {last_error}",
            file=sys.stderr,
        )

    async def _handle_connection(self, connection: ServerConnection) -> None:
        # A second Godot instance connecting just replaces the active one —
        # there's nothing meaningful to do with two editors talking at once.
        self._connection = connection
        self._connected.set()
        try:
            async for raw in connection:
                await self._dispatch(raw)
        finally:
            if self._connection is connection:
                self._connection = None
                self._connected.clear()
            self._fail_all_pending("Godot editor disconnected")

    async def _dispatch(self, raw: str | bytes) -> None:
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            return
        if not isinstance(message, dict):
            return

        method = message.get("method")
        if method == "ping":
            await self._send({"jsonrpc": "2.0", "method": "pong", "params": {}})
            return
        if method == "pong":
            return

        msg_id = message.get("id")
        future = self._pending.pop(msg_id, None)
        if future is None or future.done():
            return
        if "error" in message:
            future.set_exception(GodotRpcError(message["error"]))
        else:
            future.set_result(message.get("result", {}))

    def _fail_all_pending(self, reason: str) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(ConnectionError(reason))
        self._pending.clear()

    async def _send(self, payload: dict[str, Any]) -> None:
        if self._connection is None:
            raise ConnectionError("Godot editor is not connected")
        await self._connection.send(json.dumps(payload))

    async def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        connect_timeout: float = 10.0,
        call_timeout: float = 30.0,
    ) -> dict[str, Any]:
        """Invoke a Godot MCP command and wait for its response.

        Raises GodotRpcError for a JSON-RPC error from Godot, ConnectionError
        if no editor is attached, or asyncio.TimeoutError if it never replies.
        """
        try:
            await asyncio.wait_for(self._connected.wait(), timeout=connect_timeout)
        except asyncio.TimeoutError:
            raise ConnectionError(
                "Godot editor is not connected — make sure it's running with the "
                "godot_mcp plugin enabled"
            ) from None

        request_id = next(self._next_id)
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._send(
                {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
            )
            return await asyncio.wait_for(future, timeout=call_timeout)
        finally:
            self._pending.pop(request_id, None)
