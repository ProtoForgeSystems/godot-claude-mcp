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


async def _main_async() -> None:
    async with asyncio.TaskGroup() as tg:
        tg.create_task(bridge.serve_forever())
        tg.create_task(mcp.run_stdio_async())


def main() -> None:
    asyncio.run(_main_async())
