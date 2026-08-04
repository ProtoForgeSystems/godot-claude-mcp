"""MCP server bridging Claude Code to the Godot MCP Pro editor plugin.

This is a free, self-hosted replacement for the paid Node.js relay that
ships with godot-mcp-pro (https://github.com/youichi-uda/godot-mcp-pro).
The Godot-side plugin (addons/godot_mcp, MIT licensed) is unmodified —
only the transport between Claude Code and the plugin's WebSocket server
is reimplemented here, in Python, against the plugin's own (documented-
by-source, not separately specified) JSON-RPC framing. See transport.py
for the protocol notes.

Tool coverage is intentionally a curated subset of the ~172 commands the
plugin exposes — see README "Follow-up: wrapping the rest" for what's not
yet here and why.
"""

from __future__ import annotations

import asyncio
from typing import Any

from mcp.server.fastmcp import FastMCP

from godot_mcp.transport import GodotBridge, GodotRpcError

mcp = FastMCP("godot-editor")
bridge = GodotBridge()


async def _call(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        return await bridge.call(method, params)
    except GodotRpcError as e:
        return {"ok": False, "error": str(e), "code": e.code, "data": e.data}
    except (ConnectionError, asyncio.TimeoutError) as e:
        return {"ok": False, "error": str(e)}


# ── Scene tools ────────────────────────────────────────────────────────────


@mcp.tool()
async def get_scene_tree(max_depth: int = -1) -> dict:
    """Return the live node tree of whichever scene is currently open in the editor.

    Args:
        max_depth: Limit recursion depth, or -1 for the full tree.
    """
    return await _call("get_scene_tree", {"max_depth": max_depth})


@mcp.tool()
async def get_scene_file_content(path: str) -> dict:
    """Return the raw .tscn text of a scene file (doesn't need to be open).

    Args:
        path: res:// path to the scene file.
    """
    return await _call("get_scene_file_content", {"path": path})


@mcp.tool()
async def open_scene(path: str) -> dict:
    """Open a scene file in the editor.

    Args:
        path: res:// path to the scene file.
    """
    return await _call("open_scene", {"path": path})


@mcp.tool()
async def save_scene(path: str = "") -> dict:
    """Save the currently open scene.

    Args:
        path: Optional res:// path to save as; defaults to the scene's existing path.
    """
    return await _call("save_scene", {"path": path} if path else {})


@mcp.tool()
async def add_scene_instance(scene_path: str, parent_path: str = ".", name: str = "") -> dict:
    """Instantiate a .tscn file as a child node in the currently open scene.

    Args:
        scene_path: res:// path to the scene to instantiate.
        parent_path: Node path of the parent, "." for the scene root.
        name: Override the instance name (default: the scene's root node name).
    """
    return await _call(
        "add_scene_instance", {"scene_path": scene_path, "parent_path": parent_path, "name": name}
    )


@mcp.tool()
async def play_scene(mode: str = "main") -> dict:
    """Run a scene in the editor's game runtime.

    Args:
        mode: "main" (the project's main scene), "current" (whatever's open in
            the editor), or a res:// path to a specific scene.
    """
    return await _call("play_scene", {"mode": mode})


@mcp.tool()
async def stop_scene() -> dict:
    """Stop the currently playing scene, if any."""
    return await _call("stop_scene", {})


# ── Node tools ────────────────────────────────────────────────────────────


@mcp.tool()
async def add_node(
    type: str, parent_path: str = ".", name: str = "", properties: dict[str, Any] | None = None
) -> dict:
    """Add a new node to the currently open scene.

    Args:
        type: Godot class name (e.g. "StaticBody3D") or a script's class_name.
        parent_path: Node path of the parent, "." for the scene root.
        name: Node name (default: the type name).
        properties: Initial property values, e.g. {"collision_layer": 2}.
    """
    return await _call(
        "add_node",
        {"type": type, "parent_path": parent_path, "name": name, "properties": properties or {}},
    )


@mcp.tool()
async def delete_node(node_path: str) -> dict:
    """Delete a node from the currently open scene (undo-able).

    Args:
        node_path: Node path within the scene, e.g. "StaticBody3D".
    """
    return await _call("delete_node", {"node_path": node_path})


@mcp.tool()
async def move_node(node_path: str, new_parent_path: str) -> dict:
    """Reparent a node, preserving its place in the tree via Godot's own reparent
    logic (not manual transform math) (undo-able).

    Args:
        node_path: Node path of the node to move.
        new_parent_path: Node path of the new parent.
    """
    return await _call("move_node", {"node_path": node_path, "new_parent_path": new_parent_path})


@mcp.tool()
async def update_property(node_path: str, property: str, value: Any) -> dict:
    """Set a property on a node, parsed against the property's existing type (undo-able).

    Args:
        node_path: Node path of the target node.
        property: Property name, e.g. "collision_layer".
        value: New value. Strings are parsed against the property's current type.
    """
    return await _call("update_property", {"node_path": node_path, "property": property, "value": value})


@mcp.tool()
async def get_node_properties(node_path: str, category: str = "") -> dict:
    """Return all editor-visible properties of a node.

    Args:
        node_path: Node path of the target node.
        category: Optional property name prefix filter, e.g. "collision_".
    """
    return await _call(
        "get_node_properties", {"node_path": node_path, "category": category} if category else {"node_path": node_path}
    )


@mcp.tool()
async def add_resource(
    node_path: str,
    property: str,
    resource_type: str,
    resource_properties: dict[str, Any] | None = None,
) -> dict:
    """Create a Resource (e.g. a Shape3D) and assign it to a node property (undo-able).

    Args:
        node_path: Node path of the target node.
        property: Property to assign the resource to, e.g. "shape".
        resource_type: Godot Resource class name, e.g. "BoxShape3D".
        resource_properties: Initial property values on the new resource, e.g. {"size": "Vector3(1,1,1)"}.
    """
    return await _call(
        "add_resource",
        {
            "node_path": node_path,
            "property": property,
            "resource_type": resource_type,
            "resource_properties": resource_properties or {},
        },
    )


# ── Editor tools ──────────────────────────────────────────────────────────


@mcp.tool()
async def execute_editor_script(code: str, allow_unsafe_editor_io: bool = False) -> dict:
    """Run arbitrary GDScript inside the Godot editor process and return printed output.

    Use this for batch operations across many files that would otherwise need
    one tool call per file (e.g. EditorInterface.get_resource_filesystem().reimport_files(...)).
    Direct file/resource-save APIs (ResourceSaver.save, FileAccess WRITE, DirAccess
    mutations) are refused unless allow_unsafe_editor_io=true, and only safe when
    no open editor resource could be clobbered.

    Args:
        code: GDScript statements. Call _mcp_print(value) to capture output.
        allow_unsafe_editor_io: Permit direct file/resource writes from the script.
    """
    return await _call(
        "execute_editor_script", {"code": code, "allow_unsafe_editor_io": allow_unsafe_editor_io}
    )


@mcp.tool()
async def get_editor_errors() -> dict:
    """Return recent editor errors and stack traces."""
    return await _call("get_editor_errors", {})


@mcp.tool()
async def get_editor_screenshot(save_path: str = "") -> dict:
    """Capture the editor's main viewport (the 3D/2D scene view, not the running game).

    Args:
        save_path: res:// or user:// path to save the PNG to. If omitted, the
            image is returned as base64 instead — prefer save_path and then
            read the file directly; a base64 PNG round-tripped through MCP
            JSON bloats context for no benefit.
    """
    return await _call("get_editor_screenshot", {"save_path": save_path} if save_path else {})


@mcp.tool()
async def get_game_screenshot(save_path: str = "") -> dict:
    """Capture a frame from the currently playing game. Requires play_scene first
    — fails with "No scene is currently playing" otherwise.

    Args:
        save_path: res:// or user:// path to save the PNG to. If omitted, the
            image is returned as base64 instead — prefer save_path and then
            read the file directly.
    """
    return await _call("get_game_screenshot", {"save_path": save_path} if save_path else {})


# ── Live game tools ───────────────────────────────────────────────────────
#
# Everything below acts on a RUNNING game, not on the editor. All of it requires
# play_scene() first and fails with "No scene is currently playing" otherwise.
#
# HOW THIS ACTUALLY WORKS, because it is not a direct channel and the failure modes
# only make sense once you know: the editor-side plugin and the running game are
# separate processes, so they talk through files under user://. An input call writes
# user://mcp_input_commands; the game's MCPInputService autoload polls that file and
# feeds the events into Godot's input system. Inspection works the same way in
# reverse via MCPGameInspector.
#
# Two consequences worth remembering:
#   1. Those autoloads only exist while the [autoload] block is in project.godot,
#      which the editor plugin injects on open and removes on close. So these tools
#      work for editor-launched runs and are inert in an exported build — which is
#      the intended safety property, not a limitation to work around.
#   2. Input is delivered on the game's next polled frame, not synchronously. A
#      screenshot taken immediately after an input call will usually predate its
#      effect. Use capture_frames, or wait, rather than assuming ordering.


@mcp.tool()
async def simulate_action(action: str, pressed: bool = True, strength: float = 1.0) -> dict:
    """Press or release a named InputMap action in the running game.

    Prefer this over simulate_key: it goes through the same action names the game's
    own code polls, so it keeps working when a keybinding changes.

    Held inputs are NOT auto-released — call again with pressed=False, or use
    simulate_sequence. Movement that never stops is almost always a forgotten release.

    Args:
        action: InputMap action name, e.g. "move_right", "jump".
        pressed: True to press, False to release.
        strength: Analog strength 0..1, for actions read via get_axis/get_action_strength.
    """
    return await _call(
        "simulate_action", {"action": action, "pressed": pressed, "strength": strength}
    )


@mcp.tool()
async def simulate_key(
    keycode: str,
    pressed: bool = True,
    shift: bool = False,
    ctrl: bool = False,
    alt: bool = False,
) -> dict:
    """Press or release a physical key in the running game.

    Args:
        keycode: Godot key name, e.g. "A", "Space", "Escape".
        pressed: True to press, False to release.
        shift: Hold shift alongside.
        ctrl: Hold ctrl alongside.
        alt: Hold alt alongside.
    """
    return await _call(
        "simulate_key",
        {"keycode": keycode, "pressed": pressed, "shift": shift, "ctrl": ctrl, "alt": alt},
    )


@mcp.tool()
async def simulate_sequence(events: list[dict], frame_delay: int = 1) -> dict:
    """Play a timed list of input events, spaced frame_delay frames apart.

    This is the tool for anything with duration — running for half a second, holding
    a jump to full height, a press/release pair. Single calls cannot express timing.

    Each event is a dict with a "type" of "action", "key", "mouse_button" or
    "mouse_motion", plus that type's fields, e.g.
        {"type": "action", "action": "move_right", "pressed": true}

    Args:
        events: Ordered event dicts.
        frame_delay: Frames between consecutive events. 0 sends them all in one frame.
    """
    return await _call("simulate_sequence", {"events": events, "frame_delay": frame_delay})


@mcp.tool()
async def get_game_scene_tree(max_depth: int = -1, type_filter: str = "", named_only: bool = False) -> dict:
    """Return the node tree of the RUNNING game (not the editor's open scene).

    This is how you see nodes that only exist at runtime — anything spawned in code
    rather than authored into the .tscn.

    Args:
        max_depth: Limit recursion depth, or -1 for the full tree.
        type_filter: Only include nodes of this class, e.g. "CharacterBody3D".
        named_only: Skip nodes with engine-generated names.
    """
    params: dict[str, Any] = {"max_depth": max_depth, "named_only": named_only}
    if type_filter:
        params["type_filter"] = type_filter
    return await _call("get_game_scene_tree", params)


@mcp.tool()
async def get_game_node_properties(node_path: str) -> dict:
    """Read a live node's properties from the running game.

    Args:
        node_path: Absolute path in the running tree, e.g. "/root/AppRoot/WorldLayer".
    """
    return await _call("get_game_node_properties", {"node_path": node_path})


@mcp.tool()
async def set_game_node_property(node_path: str, property: str, value: Any) -> dict:
    """Write a property on a live node in the running game.

    Intended for probing — nudging a tuning value and seeing the result without a
    rebuild. It does NOT persist: the change dies with the running process, so fold
    anything worth keeping back into the scene or the code.

    Args:
        node_path: Absolute path in the running tree.
        property: Property name.
        value: New value.
    """
    return await _call(
        "set_game_node_property", {"node_path": node_path, "property": property, "value": value}
    )


@mcp.tool()
async def execute_game_script(code: str) -> dict:
    """Run GDScript inside the running game and return its result.

    The most direct way to assert on runtime state — reach a node, read a private
    value, call a method. Prefer a returned value over a print; printed output goes
    to the game's log, not back through this call.

    Args:
        code: GDScript source. Use `return` to send a value back.
    """
    return await _call("execute_game_script", {"code": code})


@mcp.tool()
async def capture_frames(count: int = 5, frame_interval: int = 10, half_resolution: bool = True) -> dict:
    """Capture several frames from the running game, spaced frame_interval apart.

    Use this rather than repeated get_game_screenshot calls for anything that MOVES —
    a walk cycle, a jump arc, a transition. One frame cannot show whether motion is
    smooth, and round-tripping several single screenshots is far slower.

    Args:
        count: How many frames to capture.
        frame_interval: Frames to wait between captures.
        half_resolution: Halve the resolution. Leave on unless inspecting fine detail.
    """
    return await _call(
        "capture_frames",
        {"count": count, "frame_interval": frame_interval, "half_resolution": half_resolution},
    )


@mcp.tool()
async def wait_for_node(node_path: str, timeout: float = 5.0, poll_frames: int = 5) -> dict:
    """Block until a node exists in the running game, or the timeout elapses.

    The correct way to synchronise with a load. Sleeping a fixed interval instead is
    what makes these sessions flaky on a cold cache.

    Args:
        node_path: Absolute path to wait for.
        timeout: Seconds before giving up.
        poll_frames: Frames between checks.
    """
    return await _call(
        "wait_for_node", {"node_path": node_path, "timeout": timeout, "poll_frames": poll_frames}
    )


@mcp.tool()
async def click_button_by_text(text: str) -> dict:
    """Find a Button in the running game by its visible label and click it.

    Saves resolving a node path for menu navigation, which is most of what clicking
    is needed for.

    Args:
        text: The button's visible text, e.g. "Start Run".
    """
    return await _call("click_button_by_text", {"text": text})


async def _main_async() -> None:
    async with asyncio.TaskGroup() as tg:
        tg.create_task(bridge.serve_forever())
        tg.create_task(mcp.run_stdio_async())


def main() -> None:
    asyncio.run(_main_async())
