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
import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from godot_mcp.runslot import GameProcess, GameRunSlot, SlotHolder
from godot_mcp.transport import GodotBridge, GodotRpcError

mcp = FastMCP("godot-editor")
bridge = GodotBridge()

# Only meaningful once we know which project we serve; without that we cannot
# tell which other checkouts share this game's user:// directory.
run_slot: GameRunSlot | None = (
    GameRunSlot(bridge.project_dir) if bridge.project_dir is not None else None
)


#: Addon commands that act on a RUNNING game through the shared user:// files,
#: and are therefore unsafe while another checkout of this game is also running.
#: Gated centrally rather than at each call site so that adding a live-game tool
#: cannot silently skip the check. play_scene is absent on purpose — it claims
#: the slot itself, atomically, instead of merely testing it.
_LIVE_GAME_METHODS = frozenset(
    {
        "get_game_screenshot",
        "capture_frames",
        "simulate_action",
        "simulate_key",
        "simulate_sequence",
        "click_button_by_text",
        "get_game_scene_tree",
        "get_game_node_properties",
        "set_game_node_property",
        "execute_game_script",
        "wait_for_node",
    }
)


async def _call(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    if method in _LIVE_GAME_METHODS:
        refusal = await _blocked_by_another_run()
        if refusal is not None:
            return refusal
    try:
        return await bridge.call(method, params)
    except GodotRpcError as e:
        return {"ok": False, "error": str(e), "code": e.code, "data": e.data}
    except (ConnectionError, asyncio.TimeoutError) as e:
        return {"ok": False, "error": str(e)}


def _gd(value: Any) -> str:
    """Render a Python value as a GDScript literal.

    JSON's string/number/bool syntax is a subset of GDScript's, so json.dumps
    produces a valid literal for everything passed through here (strings,
    bools, and JSON blobs that the script re-parses with JSON.parse_string).
    """
    return json.dumps(value)


async def _script_json(code: str, *, allow_unsafe_editor_io: bool = False) -> dict:
    """Run a GDScript snippet whose last statement prints a JSON payload.

    execute_editor_script stringifies a script's return value with str(), which
    turns a Dictionary into GDScript's own repr rather than JSON. Printing
    JSON.stringify(...) and parsing the last output line here is the only
    lossless way to get structured data back out of the editor.
    """
    result = await _call(
        "execute_editor_script",
        {"code": code, "allow_unsafe_editor_io": allow_unsafe_editor_io},
    )
    if result.get("ok") is False:
        return result
    lines = result.get("output") or []
    if not lines:
        return {"ok": False, "error": "Editor script produced no output.", "raw": result}
    try:
        return json.loads(lines[-1])
    except (ValueError, TypeError):
        return {"ok": False, "error": "Editor script output was not JSON.", "raw": lines}


def _describe_blocker(blocker: SlotHolder | GameProcess) -> str:
    if isinstance(blocker, GameProcess):
        return f"the checkout at {blocker.project_dir} (game pid {blocker.pid})"
    return blocker.describe()


async def _blocked_by_another_run() -> dict | None:
    """The refusal to return, or None if this session may touch the game.

    Godot keys user:// on the project NAME, so every worktree of this game shares
    one directory — and the addon's screenshot/input/inspector channels are files
    in it. Two games running at once answer each other's requests. See
    runslot.py for why this cannot be fixed by isolating user://.
    """
    if run_slot is None:
        return None
    blocker = await asyncio.to_thread(run_slot.blocker)
    if blocker is None:
        return None
    return {
        "ok": False,
        "error": (
            "Another checkout of this game is already running it: "
            f"{_describe_blocker(blocker)}. Only one may run at a time — they share "
            "one user:// directory, and the MCP screenshot, input and inspector "
            "channels are files in it, so two games answer each other's requests. "
            "Nothing has been started or changed."
        ),
        "code": "game_run_slot_busy",
        "blocked_by": _describe_blocker(blocker),
        "retry": (
            "Poll get_game_run_status until available is true, then try again. "
            "Do not work around this by driving the other checkout's game."
        ),
    }


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

    Only one checkout of a given game may run it at a time — see
    get_game_run_status. This refuses rather than cross-talking with another
    worktree's game.

    Args:
        mode: "main" (the project's main scene), "current" (whatever's open in
            the editor), or a res:// path to a specific scene.
    """
    if run_slot is not None:
        # Claim the slot BEFORE launching: two sessions calling this in the same
        # instant would otherwise both scan, both see nothing, and both launch.
        holder = await asyncio.to_thread(run_slot.acquire)
        if holder is not None:
            return {
                "ok": False,
                "error": (
                    "Another checkout of this game holds the run slot: "
                    f"{holder.describe()}. Only one may run at a time — they share one "
                    "user:// directory, so two games answer each other's MCP requests. "
                    "Nothing has been started."
                ),
                "code": "game_run_slot_busy",
                "blocked_by": holder.describe(),
                "retry": "Poll get_game_run_status until available is true, then try again.",
            }

    result = await _call("play_scene", {"mode": mode})

    if run_slot is not None:
        if result.get("ok") is False:
            # The launch never happened; do not leave the slot claimed.
            await asyncio.to_thread(run_slot.release)
        else:
            # Record the pid so a session that dies leaves reclaimable debris
            # rather than a lock nobody can attribute. It is fine for this to
            # find nothing yet — the grace period in runslot covers the gap.
            pid = await asyncio.to_thread(run_slot.confirm_started)
            if pid is not None:
                result = {**result, "game_pid": pid}
    return result


@mcp.tool()
async def stop_scene() -> dict:
    """Stop the currently playing scene, if any, and free the game-run slot."""
    result = await _call("stop_scene", {})
    if run_slot is not None:
        await asyncio.to_thread(run_slot.release)
    return result


@mcp.tool()
async def get_game_run_status() -> dict:
    """Who, if anyone, is currently running this game — across every checkout.

    Only one checkout of a game may run it at a time, because Godot derives
    user:// from the project name rather than its path, so every worktree shares
    one directory and the MCP game channels are files in it.

    Poll this after a play_scene refusal: when "available" is true the slot is
    free. It reports the process table as well as the lock, so a game started
    from the editor's own UI is visible too.
    """
    if run_slot is None:
        return {
            "available": True,
            "note": (
                "This server could not determine which Godot project it serves, so it "
                "does not arbitrate game runs. Set GODOT_MCP_PROJECT_DIR to enable it."
            ),
        }
    return await asyncio.to_thread(run_slot.status)


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


# ── Editor viewport camera ────────────────────────────────────────────────
#
# Thin passthroughs — the plugin already implements both. They matter because
# get_editor_screenshot captures whatever the viewport happens to be showing;
# without setting the camera first, a screenshot is not a repeatable check.


def _vec3(values: list[float] | None) -> dict[str, float] | None:
    if values is None:
        return None
    if len(values) != 3:
        raise ValueError(f"Expected 3 components (x, y, z), got {len(values)}")
    return {"x": float(values[0]), "y": float(values[1]), "z": float(values[2])}


@mcp.tool()
async def get_editor_camera() -> dict:
    """Return the 3D editor viewport camera's position, rotation, fov and clip planes.

    Requires a 3D scene open in the editor; fails with "No 3D editor camera found"
    if the active tab is 2D or a script.
    """
    return await _call("get_editor_camera", {})


@mcp.tool()
async def set_editor_camera(
    position: list[float] | None = None,
    rotation_degrees: list[float] | None = None,
    look_at: list[float] | None = None,
    fov: float | None = None,
) -> dict:
    """Move the 3D editor viewport camera, so a following get_editor_screenshot is
    framed deterministically rather than wherever the viewport was left.

    Only the arguments you pass are changed; the rest keep their current values.

    Args:
        position: [x, y, z] world position.
        rotation_degrees: [x, y, z] Euler rotation. IGNORED if look_at is also
            given — the plugin applies look_at last and it overwrites rotation.
        look_at: [x, y, z] world point to aim at. Prefer this over rotation for
            framing a subject.
        fov: Vertical field of view in degrees.
    """
    params: dict[str, Any] = {}
    for key, value in (
        ("position", _vec3(position)),
        ("rotation_degrees", _vec3(rotation_degrees)),
        ("look_at", _vec3(look_at)),
    ):
        if value is not None:
            params[key] = value
    if fov is not None:
        params["fov"] = fov
    return await _call("set_editor_camera", params)


# ── Skeleton inspection ───────────────────────────────────────────────────
#
# The plugin has NO skeleton or bone commands at all — animation_commands.gd
# covers AnimationPlayer tracks and animation_tree_commands.gd covers state
# machines, and neither touches Skeleton3D. This is implemented directly
# against the editor's scene tree instead.

_SKELETON_BONES_GD = """
var root := EditorInterface.get_edited_scene_root()
var out := {}
# Typed Skeleton3D, not Node: GDScript 4 does not narrow a type from an `is`
# check, so a Node-typed variable rejects every Skeleton3D method at parse time.
var skel: Skeleton3D = null
if root == null:
	out = {"ok": false, "error": "No scene is open in the editor."}
else:
	var np: String = __NODE_PATH__
	if np.is_empty():
		# owned=false matters: an imported .glb/.fbx wrapper scene keeps its
		# Skeleton3D owned by the instance, not by the edited root, so an
		# owned-only search finds nothing on exactly the rigs we care about.
		var found := root.find_children("*", "Skeleton3D", true, false)
		if found.is_empty():
			out = {"ok": false, "error": "No Skeleton3D found in the open scene."}
		else:
			skel = found[0] as Skeleton3D
	else:
		var node := root.get_node_or_null(NodePath(np))
		if node == null:
			out = {"ok": false, "error": "Node not found: " + np}
		elif not (node is Skeleton3D):
			out = {"ok": false, "error": "Node " + np + " is a " + node.get_class() + ", not a Skeleton3D."}
		else:
			skel = node as Skeleton3D

if skel != null:
	var filt: String = __FILTER__
	var want_rest: bool = __INCLUDE_REST__
	var want_pose: bool = __INCLUDE_POSE__
	var bones := []
	for i in range(skel.get_bone_count()):
		var bname := skel.get_bone_name(i)
		if not filt.is_empty() and not bname.to_lower().contains(filt.to_lower()):
			continue
		var entry := {
			"index": i,
			"name": bname,
			"parent": skel.get_bone_parent(i),
		}
		if want_rest:
			var rest: Transform3D = skel.get_bone_rest(i)
			var rest_euler := rest.basis.get_euler()
			entry["rest_position"] = [snappedf(rest.origin.x, 0.0001), snappedf(rest.origin.y, 0.0001), snappedf(rest.origin.z, 0.0001)]
			entry["rest_rotation_degrees"] = [snappedf(rad_to_deg(rest_euler.x), 0.01), snappedf(rad_to_deg(rest_euler.y), 0.01), snappedf(rad_to_deg(rest_euler.z), 0.01)]
		if want_pose:
			var gp: Transform3D = skel.get_bone_global_pose(i)
			entry["global_pose_position"] = [snappedf(gp.origin.x, 0.0001), snappedf(gp.origin.y, 0.0001), snappedf(gp.origin.z, 0.0001)]
			var gp_euler := gp.basis.get_euler()
			entry["global_pose_rotation_degrees"] = [snappedf(rad_to_deg(gp_euler.x), 0.01), snappedf(rad_to_deg(gp_euler.y), 0.01), snappedf(rad_to_deg(gp_euler.z), 0.01)]
		bones.append(entry)

	# SkeletonModifier3D children are reported because "the modifier silently
	# isn't running" is the usual cause of an IK rig that looks correctly wired
	# and does nothing. active/influence are the two fields that decide it.
	var modifiers := []
	for child in skel.get_children():
		var mod := child as SkeletonModifier3D
		if mod != null:
			modifiers.append({
				"name": str(mod.name),
				"type": mod.get_class(),
				"active": mod.active,
				"influence": snappedf(mod.influence, 0.001),
			})

	var xf: Transform3D = skel.global_transform
	out = {
		"ok": true,
		"skeleton_path": str(root.get_path_to(skel)),
		"bone_count": skel.get_bone_count(),
		"bones_returned": bones.size(),
		"motion_scale": snappedf(skel.motion_scale, 0.0001),
		"skeleton_global_position": [snappedf(xf.origin.x, 0.0001), snappedf(xf.origin.y, 0.0001), snappedf(xf.origin.z, 0.0001)],
		"modifiers": modifiers,
		"bones": bones,
	}
_mcp_print(JSON.stringify(out))
"""


@mcp.tool()
async def get_skeleton_bones(
    node_path: str = "",
    filter: str = "",
    include_rest: bool = False,
    include_pose: bool = False,
) -> dict:
    """List a Skeleton3D's bones, plus any SkeletonModifier3D children and their state.

    By default returns index/name/parent only — a humanoid rig is ~65 bones, and
    transforms roughly quadruple the payload. Turn them on per question, and
    filter when you can.

    CAVEAT on include_pose, and it is the whole reason bone dumps mislead: the
    pose reported here is the PRE-modifier pose. SkeletonModifier3D output
    (TwoBoneIK3D, LookAt, physical bones) is applied downstream of what
    get_bone_global_pose returns, so a correctly working IK solver shows up here
    as if it did nothing. Verify IK from a rendered frame — get_editor_screenshot
    or capture_frames — never from these numbers.

    LIMITATION on "modifiers": this reads the EDITOR's open scene, so it only
    sees modifiers authored into the .tscn. A rig that builds its TwoBoneIK3D in
    code at runtime reports an empty list here and is not broken. Use
    execute_game_script against a running game to inspect those.

    Args:
        node_path: Path to the Skeleton3D. Leave empty to use the first one found
            in the open scene (searches into instanced scenes, which is where an
            imported .glb/.fbx rig lives).
        filter: Case-insensitive substring match on bone name, e.g. "leg".
        include_rest: Also report each bone's rest transform.
        include_pose: Also report each bone's current global pose. See the caveat.
    """
    code = (
        _SKELETON_BONES_GD.replace("__NODE_PATH__", _gd(node_path))
        .replace("__FILTER__", _gd(filter))
        .replace("__INCLUDE_REST__", "true" if include_rest else "false")
        .replace("__INCLUDE_POSE__", "true" if include_pose else "false")
    )
    return await _script_json(code)


# ── Asset import settings ─────────────────────────────────────────────────
#
# Also absent from the plugin: nothing in commands/ reads or writes .import
# files or calls reimport_files(). Retyping a .glb's imported root, changing
# LOD/skin options, or re-running an import otherwise means hand-editing text
# on disk and hand-writing the reimport script every time.

_GET_IMPORT_GD = """
var p: String = __PATH__
var imp := p + ".import"
var out := {}
if not FileAccess.file_exists(imp):
	out = {"ok": false, "error": "No .import file for " + p, "hint": "Either it is not an imported asset (.tscn/.gd/.cs have none), or the editor has not scanned it yet."}
else:
	var cf := ConfigFile.new()
	var err := cf.load(imp)
	if err != OK:
		out = {"ok": false, "error": "Failed to read " + imp + ": " + error_string(err)}
	else:
		var sections := {}
		for s in cf.get_sections():
			var kv := {}
			for k in cf.get_section_keys(s):
				var v: Variant = cf.get_value(s, k)
				var t := typeof(v)
				if t == TYPE_BOOL or t == TYPE_INT or t == TYPE_FLOAT or t == TYPE_STRING or t == TYPE_DICTIONARY or t == TYPE_ARRAY:
					kv[k] = v
				elif t == TYPE_PACKED_STRING_ARRAY:
					kv[k] = Array(v)
				else:
					# Anything else (Transform3D, Color, ...) is stringified rather
					# than dropped, so it is at least visible in the output.
					kv[k] = str(v)
			sections[s] = kv
		out = {"ok": true, "path": p, "import_file": imp, "importer": cf.get_value("remap", "importer", ""), "sections": sections}
_mcp_print(JSON.stringify(out))
"""

_SET_IMPORT_GD = """
var p: String = __PATH__
var section: String = __SECTION__
var opts: Dictionary = JSON.parse_string(__OPTIONS__)
var do_reimport: bool = __REIMPORT__
var imp := p + ".import"
var out := {}
if not FileAccess.file_exists(imp):
	out = {"ok": false, "error": "No .import file for " + p}
else:
	var cf := ConfigFile.new()
	var err := cf.load(imp)
	if err != OK:
		out = {"ok": false, "error": "Failed to read " + imp + ": " + error_string(err)}
	else:
		var changed := {}
		for k in opts:
			var v: Variant = opts[k]
			# JSON has one number type; the importer does not. Coerce against the
			# existing value's type so an int option does not get rewritten as
			# "1.0" and quietly break the import.
			if cf.has_section_key(section, k):
				var cur: Variant = cf.get_value(section, k)
				var ct := typeof(cur)
				if ct == TYPE_INT and typeof(v) == TYPE_FLOAT:
					v = int(v)
				elif ct == TYPE_BOOL:
					v = bool(v)
				elif ct == TYPE_STRING and typeof(v) != TYPE_STRING:
					v = str(v)
			changed[k] = {"from": str(cf.get_value(section, k, null)), "to": str(v)}
			cf.set_value(section, k, v)
		var serr := cf.save(imp)
		if serr != OK:
			out = {"ok": false, "error": "Failed to write " + imp + ": " + error_string(serr)}
		else:
			var reimported := false
			if do_reimport:
				var fs := EditorInterface.get_resource_filesystem()
				fs.update_file(p)
				fs.reimport_files(PackedStringArray([p]))
				reimported = true
			out = {"ok": true, "path": p, "section": section, "changed": changed, "reimported": reimported}
_mcp_print(JSON.stringify(out))
"""


@mcp.tool()
async def get_import_settings(path: str) -> dict:
    """Read an imported asset's .import file — importer name and every option section.

    Use this before set_import_settings to learn the exact option keys, which are
    importer-specific and not guessable (a .glb's scene importer uses
    "nodes/root_type", "meshes/generate_lods", "skins/use_named_skins", ...).

    Args:
        path: res:// path to the SOURCE asset, e.g. "res://Content/Player/Rig.fbx"
            — not the .import file itself.
    """
    return await _script_json(_GET_IMPORT_GD.replace("__PATH__", _gd(path)))


@mcp.tool()
async def set_import_settings(
    path: str, options: dict[str, Any], section: str = "params", reimport: bool = True
) -> dict:
    """Write import options into an asset's .import file and reimport it.

    This is how you retype a .glb's imported root (options={"nodes/root_type":
    "StaticBody3D"}) or change mesh/skin import behaviour. A wrapper scene that
    instances the asset picks up the new type automatically — there is no way to
    retype it from the wrapper's own .tscn text.

    Reimporting prints progress-dialog errors ("Do not use progress dialog (task)
    while flushing the message queue", "Attempted to call reimport_files()
    recursively"). Those are inherent to the plugin's deferred dispatch, not a
    failure — verify the result by reading back the asset, not the error log.

    Args:
        path: res:// path to the source asset.
        options: Option keys and values to set. Read get_import_settings first for
            the valid keys; unknown keys are written and then ignored by the
            importer rather than rejected.
        section: .import section to write into. Leave as "params" unless you know
            you need "remap" or "deps".
        reimport: Reimport immediately after writing. False just edits the file.
    """
    code = (
        _SET_IMPORT_GD.replace("__PATH__", _gd(path))
        .replace("__SECTION__", _gd(section))
        .replace("__OPTIONS__", _gd(json.dumps(options)))
        .replace("__REIMPORT__", "true" if reimport else "false")
    )
    # ConfigFile.save trips the plugin's file-write guard. That guard exists to
    # stop a script clobbering a resource the editor has open and unsaved; an
    # .import file is never an open editor resource, so writing one is exactly
    # the case the guard is meant to let through deliberately.
    return await _script_json(code, allow_unsafe_editor_io=True)


# ── Resource creation (script-class aware) ────────────────────────────────
#
# Deliberately supersedes the plugin's own create_resource, which gates on
# ClassDB.class_exists() (resource_commands.gd:117). ClassDB knows engine
# classes only, so a C# [GlobalClass] Resource — or a GDScript class_name one —
# is rejected as "not a Resource type". add_node already handles this correctly
# via get_global_class_list(); create_resource was never updated to match.

_CREATE_RESOURCE_GD = """
var p: String = __PATH__
var t: String = __TYPE__
var props: Dictionary = JSON.parse_string(__PROPERTIES__)
var overwrite: bool = __OVERWRITE__
var out := {}
var res: Resource = null
var via := ""
if FileAccess.file_exists(p) and not overwrite:
	out = {"ok": false, "error": "Resource already exists: " + p, "hint": "Pass overwrite=true to replace it."}
elif ClassDB.class_exists(t) and ClassDB.is_parent_class(t, "Resource"):
	res = ClassDB.instantiate(t)
	via = "ClassDB"
else:
	var by_name := {}
	for e in ProjectSettings.get_global_class_list():
		by_name[str(e.get("class", ""))] = e
	if not by_name.has(t):
		out = {"ok": false, "error": "Unknown resource type: " + t, "hint": "Not an engine class and not a registered global class_name. For a C# type, run 'dotnet build' first — the global class list is only populated once the assembly loads."}
	else:
		var entry: Dictionary = by_name[t]
		# A script class may extend another script class, so walk up until the
		# base is something ClassDB can actually instantiate.
		var base: String = str(entry.get("base", ""))
		var guard := 0
		while by_name.has(base) and guard < 32:
			base = str(by_name[base].get("base", ""))
			guard += 1
		if not ClassDB.class_exists(base) or not ClassDB.is_parent_class(base, "Resource"):
			out = {"ok": false, "error": t + " resolves to base '" + base + "', which is not a Resource."}
		else:
			var scr: Script = load(str(entry.get("path", "")))
			if scr == null:
				out = {"ok": false, "error": "Could not load the script for " + t + " at " + str(entry.get("path", ""))}
			else:
				# Instantiate the engine base and attach the script, rather than
				# calling scr.new(). Only GDScript exposes new(); this path works
				# for CSharpScript too.
				res = ClassDB.instantiate(base)
				res.set_script(scr)
				via = "global_class:" + str(entry.get("language", "?"))

if res != null:
	var applied := []
	var skipped := []
	for k in props:
		if k in res:
			var v: Variant = props[k]
			var cur: Variant = res.get(k)
			# JSON cannot carry a Vector3/Color, so accept its GDScript literal
			# form as a string and convert against the property's real type.
			if typeof(v) == TYPE_STRING and typeof(cur) != TYPE_STRING and typeof(cur) != TYPE_NIL:
				var parsed: Variant = str_to_var(v)
				if parsed != null:
					v = parsed
			res.set(k, v)
			applied.append(k)
		else:
			skipped.append(k)
	var dir_abs := ProjectSettings.globalize_path(p.get_base_dir())
	if not DirAccess.dir_exists_absolute(dir_abs):
		DirAccess.make_dir_recursive_absolute(dir_abs)
	var err := ResourceSaver.save(res, p)
	if err != OK:
		out = {"ok": false, "error": "Failed to save " + p + ": " + error_string(err)}
	else:
		EditorInterface.get_resource_filesystem().update_file(p)
		out = {"ok": true, "path": p, "type": t, "instantiated_via": via, "properties_applied": applied, "properties_skipped": skipped}
_mcp_print(JSON.stringify(out))
"""


@mcp.tool()
async def create_resource(
    path: str, type: str, properties: dict[str, Any] | None = None, overwrite: bool = False
) -> dict:
    """Create a .tres Resource file — including one whose type is a C# [GlobalClass]
    or a GDScript class_name, which the plugin's own create_resource cannot do.

    For a C# type the assembly must already be built and loaded by the editor;
    otherwise the class is not in the global class list yet and this fails with
    "Unknown resource type". Run dotnet build and let the editor pick it up first.

    Property names for C# exports are the Godot-facing names (PascalCase, as
    declared), not the C# field names. Anything unmatched comes back in
    properties_skipped rather than failing the call — check it.

    Args:
        path: res:// destination, must end in .tres or .res.
        type: Engine Resource class, or a registered class_name / [GlobalClass].
        properties: Initial values. For Vector3/Color and similar, pass the
            GDScript literal as a string, e.g. {"Offset": "Vector3(0, 1, 0)"}.
        overwrite: Replace the file if it already exists.
    """
    code = (
        _CREATE_RESOURCE_GD.replace("__PATH__", _gd(path))
        .replace("__TYPE__", _gd(type))
        .replace("__PROPERTIES__", _gd(json.dumps(properties or {})))
        .replace("__OVERWRITE__", "true" if overwrite else "false")
    )
    # ResourceSaver.save / DirAccess mutation trip the plugin's file-write guard.
    # Writing a new .tres at a path the caller named is the intended operation.
    return await _script_json(code, allow_unsafe_editor_io=True)


async def _main_async() -> None:
    async with asyncio.TaskGroup() as tg:
        tg.create_task(bridge.serve_forever())
        tg.create_task(mcp.run_stdio_async())


def main() -> None:
    asyncio.run(_main_async())
