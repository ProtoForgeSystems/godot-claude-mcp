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

WHY THIS ROUTES BY PROJECT PATH
-------------------------------
The addon does not pick a port — it opens a peer on *every* port in
6505-6514 at once and keeps them all (websocket_server.gd's start_server
loops the whole range). So every Godot editor running on the machine
connects to every bridge running on the machine, and a bridge that simply
kept "the current connection" would execute its commands against whichever
editor most recently reconnected. Since the addon force-reconnects after a
30s heartbeat gap, that winner changes over the life of a session.

The failure mode is not a refused connection, it is a *silent wrong-project
write*: a session working in a git worktree issues save_scene and it lands
in the main checkout, or in an entirely different game that happens to have
the addon installed.

The fix needs nothing from the addon, which already reports its own identity
through the stock `get_export_info` command (it returns
ProjectSettings.globalize_path("res://")). This bridge learns which project
it serves from its own working directory, asks each editor that connects
which project *it* has open, and routes only to the one that matches.
Editors belonging to other projects stay connected and idle — dropping them
would just make the addon redial every 3s forever.

When the bridge cannot tell which project it serves (started outside any
Godot project), it cannot discriminate, so it does not try: it skips the
identity probe entirely and behaves exactly as the single-editor bridge
always did.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import os
import sys
from pathlib import Path
from typing import Any

from websockets.asyncio.server import ServerConnection, serve

# The addon's websocket_server.gd documents 6505-6509 as the sub-range for
# stdio MCP servers (6510-6514 is reserved for its separate CLI mode) and
# polls all ten ports looking for any listener. Each concurrent Claude Code
# session spawns its own server process, so without scanning this range,
# every session after the first fails to bind 6505 and crashes outright.
BASE_PORT = 6505
PORT_RANGE_SIZE = 5

#: Escape hatch for a layout the search below cannot work out on its own.
PROJECT_DIR_ENV = "GODOT_MCP_PROJECT_DIR"

#: How long an editor gets to answer the identity probe before it is treated
#: as unidentifiable. Generous: the addon answers on Godot's main thread, so
#: a busy editor (reimporting, building) can take seconds to get to it.
IDENTIFY_TIMEOUT = 20.0

# Godot projects are usually one or two directories below the repo root
# (game/, godot/client/), so a shallow scan finds them without turning into
# a filesystem crawl.
_SCAN_MAX_DEPTH = 3
# Never descend into these. `.worktrees` is the load-bearing one: worktrees
# live *inside* the repo, so without skipping hidden directories a session in
# the main checkout would find every worktree's project.godot too and give up
# as ambiguous.
_SCAN_SKIP_NAMES = frozenset({"addons", "node_modules", "bin", "obj", "target", "build"})


def _is_project_dir(path: Path) -> bool:
    return (path / "project.godot").is_file()


def _scan_down(root: Path) -> list[Path]:
    """Godot project directories at or below `root`, to _SCAN_MAX_DEPTH."""
    found: list[Path] = []
    root_depth = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        if "project.godot" in filenames:
            found.append(current)
            # A Godot project never contains another one; stop descending.
            dirnames[:] = []
            continue
        if len(current.parts) - root_depth >= _SCAN_MAX_DEPTH:
            dirnames[:] = []
            continue
        dirnames[:] = [
            d for d in dirnames if not d.startswith(".") and d not in _SCAN_SKIP_NAMES
        ]
    return found


def resolve_project_dir(start: str | os.PathLike[str] | None = None) -> Path | None:
    """Work out which Godot project this bridge serves, or None if unclear.

    Order: the PROJECT_DIR_ENV override, then the nearest project.godot at or
    above the starting directory, then a shallow scan below it. Returning None
    is a legitimate answer — it means "do not filter", not "error".
    """
    override = os.environ.get(PROJECT_DIR_ENV)
    if override:
        candidate = Path(override).expanduser().resolve()
        if _is_project_dir(candidate):
            return candidate
        print(
            f"[godot-mcp] {PROJECT_DIR_ENV}={override} has no project.godot; ignoring it",
            file=sys.stderr,
        )

    try:
        base = Path(start) if start is not None else Path.cwd()
        base = base.resolve()
    except OSError:
        return None

    for candidate in (base, *base.parents):
        if _is_project_dir(candidate):
            return candidate

    below = _scan_down(base)
    if len(below) == 1:
        return below[0]
    if len(below) > 1:
        print(
            f"[godot-mcp] {len(below)} Godot projects under {base}; "
            "cannot tell which one this session serves, so editor filtering is off. "
            f"Set {PROJECT_DIR_ENV} to enable it.",
            file=sys.stderr,
        )
    return None


def _normalize_reported_path(raw: Any) -> Path | None:
    """Turn the addon's globalize_path("res://") into a comparable Path.

    It arrives with a trailing separator, and either side may sit behind a
    symlink, so both ends go through resolve() before they are compared.
    """
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return Path(raw).resolve()
    except OSError:
        return None


class GodotRpcError(Exception):
    """Raised when Godot's command router returns a JSON-RPC error object."""

    def __init__(self, error: dict[str, Any]) -> None:
        super().__init__(error.get("message", "Godot RPC error"))
        self.code = error.get("code")
        self.data = error.get("data")


class GodotBridge:
    """Owns the connection to the Godot editor that has *this* project open.

    One process runs per Claude Code session, and Godot polls every port in
    6505-6514 for a listener — so this binds the first free port starting
    at base_port (rather than hard-coding 6505) to let multiple sessions
    run against the same editor concurrently without colliding.

    Every editor on the machine reaches every one of those listeners, so see
    the module docstring for why connections are filtered by project path
    rather than last-one-wins.
    """

    def __init__(
        self,
        base_port: int = BASE_PORT,
        port_range_size: int = PORT_RANGE_SIZE,
        project_dir: str | os.PathLike[str] | None = None,
    ) -> None:
        self._base_port = base_port
        self._port_range_size = port_range_size
        if project_dir is not None:
            self._project_dir: Path | None = Path(project_dir).expanduser().resolve()
        else:
            self._project_dir = resolve_project_dir()
        # connection -> the project path it reported, or None until it answers
        self._peers: dict[ServerConnection, Path | None] = {}
        self._connection: ServerConnection | None = None
        self._connected = asyncio.Event()
        # request id -> (future, the connection it was sent on)
        self._pending: dict[int, tuple[asyncio.Future[dict[str, Any]], ServerConnection]] = {}
        self._next_id = itertools.count(1)

    @property
    def project_dir(self) -> Path | None:
        """The project this bridge serves, or None when it could not tell."""
        return self._project_dir

    async def serve_forever(self) -> None:
        if self._project_dir is not None:
            print(f"[godot-mcp] serving project {self._project_dir}", file=sys.stderr)
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
        self._peers[connection] = None
        probe: asyncio.Task[None] | None = None
        if self._project_dir is None:
            # Nothing to discriminate against — keep the historical behaviour
            # of talking to whoever showed up.
            self._promote(connection)
        else:
            # Must run alongside the read loop below, not before it: the probe's
            # reply arrives on this same socket and only that loop reads it.
            probe = asyncio.create_task(self._identify(connection))
        try:
            async for raw in connection:
                await self._dispatch(raw, connection)
        finally:
            if probe is not None:
                probe.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await probe
            self._peers.pop(connection, None)
            if self._connection is connection:
                self._connection = None
                self._connected.clear()
                self._elect()
            self._fail_pending_for(connection, "Godot editor disconnected")

    async def _identify(self, connection: ServerConnection) -> None:
        """Ask a freshly connected editor which project it has open."""
        try:
            info = await self._call_on(
                connection, "get_export_info", {}, call_timeout=IDENTIFY_TIMEOUT
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - a failed probe must not kill the socket
            print(
                f"[godot-mcp] could not identify a connected editor ({e}); "
                "ignoring it. Is the godot_mcp addon up to date?",
                file=sys.stderr,
            )
            return

        reported = _normalize_reported_path(info.get("project_path"))
        if connection not in self._peers:  # disconnected while we were asking
            return
        self._peers[connection] = reported
        if reported is not None and reported == self._project_dir:
            self._promote(connection)
            print(f"[godot-mcp] editor attached for {reported}", file=sys.stderr)
        else:
            print(
                f"[godot-mcp] ignoring editor open on {reported or 'an unknown project'}; "
                f"this session serves {self._project_dir}",
                file=sys.stderr,
            )

    def _promote(self, connection: ServerConnection) -> None:
        self._connection = connection
        self._connected.set()

    def _elect(self) -> None:
        """Pick a replacement after the active editor drops."""
        for connection, reported in self._peers.items():
            if self._project_dir is None or reported == self._project_dir:
                self._promote(connection)
                return

    async def _dispatch(self, raw: str | bytes, connection: ServerConnection) -> None:
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            return
        if not isinstance(message, dict):
            return

        method = message.get("method")
        if method == "ping":
            # Answer on the socket that pinged. Replying on the *active*
            # connection instead would let an ignored editor time out and
            # redial every 30s, and would reset the wrong editor's timer.
            await self._send_on(connection, {"jsonrpc": "2.0", "method": "pong", "params": {}})
            return
        if method == "pong":
            return

        msg_id = message.get("id")
        entry = self._pending.pop(msg_id, None)
        if entry is None:
            return
        future, _ = entry
        if future.done():
            return
        if "error" in message:
            future.set_exception(GodotRpcError(message["error"]))
        else:
            future.set_result(message.get("result", {}))

    def _fail_pending_for(self, connection: ServerConnection, reason: str) -> None:
        """Fail only the calls that were in flight on the dropped socket."""
        for msg_id, (future, sent_on) in list(self._pending.items()):
            if sent_on is not connection:
                continue
            self._pending.pop(msg_id, None)
            if not future.done():
                future.set_exception(ConnectionError(reason))

    async def _send_on(self, connection: ServerConnection, payload: dict[str, Any]) -> None:
        await connection.send(json.dumps(payload))

    async def _call_on(
        self,
        connection: ServerConnection,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        call_timeout: float = 30.0,
    ) -> dict[str, Any]:
        request_id = next(self._next_id)
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = (future, connection)
        try:
            await self._send_on(
                connection,
                {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}},
            )
            return await asyncio.wait_for(future, timeout=call_timeout)
        finally:
            self._pending.pop(request_id, None)

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
            raise ConnectionError(self._not_connected_message()) from None

        connection = self._connection
        if connection is None:
            raise ConnectionError(self._not_connected_message())
        return await self._call_on(connection, method, params, call_timeout=call_timeout)

    def _not_connected_message(self) -> str:
        base = (
            "Godot editor is not connected — make sure it's running with the "
            "godot_mcp plugin enabled"
        )
        if self._project_dir is None:
            return base
        others = sorted({str(p) for p in self._peers.values() if p is not None})
        detail = f" and open on {self._project_dir}"
        if others:
            detail += f" (connected editors are open on: {', '.join(others)})"
        return base + detail
