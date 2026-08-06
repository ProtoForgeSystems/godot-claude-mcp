# godot-claude-mcp

A free, self-hosted Python replacement for the paid Node.js relay bundled with
[godot-mcp-pro](https://github.com/youichi-uda/godot-mcp-pro). Lets Claude
Code drive the Godot 4 editor — scenes, nodes, scripts, arbitrary editor
scripting — through godot-mcp-pro's free, MIT-licensed GDScript addon,
without buying the bundled bridge.

## How It Works

```
Claude Code  <--stdio/MCP-->  this server (server/)  <--ws://127.0.0.1:6505 (JSON-RPC 2.0)-->  Godot editor (godot_mcp addon)
```

The Godot-side addon is unmodified, third-party code — it's a WebSocket
*client* that dials out to `127.0.0.1:6505` and retries every 3s. This
server is that something: a WebSocket *server* speaking the same bare
JSON-RPC 2.0 framing, reverse-engineered by reading the addon's own
`websocket_server.gd` / `command_router.gd` (there's no published spec —
see `godot_mcp/transport.py` for the documented protocol notes).

This repo no longer ships its own Godot plugin. The `addon/godot_claude_mcp/`
folder is the original (pre-rewrite) custom plugin and is no longer used —
install [godot-mcp-pro's addon](https://github.com/youichi-uda/godot-mcp-pro)
in your Godot project instead.

### Running more than one editor

The addon doesn't pick a port — it opens a peer on **every** port in
6505-6514 at once. So every Godot editor on the machine connects to every
server on the machine, which matters as soon as you have two: one editor per
git worktree, or a second unrelated game that also has the addon installed.

A server that just kept the most recent connection would execute its commands
against an arbitrary editor, and since the addon force-reconnects after a 30s
heartbeat gap, *which* editor changes during a session. The symptom isn't a
failed call — it's a scene edit silently landing in the wrong checkout.

So this server figures out which project it belongs to (the nearest
`project.godot` at or above its working directory, else a shallow scan below
it), asks each editor that connects which project it has open via the stock
`get_export_info` command, and talks only to the one that matches. Other
editors stay connected and idle. No addon changes are needed.

Set `GODOT_MCP_PROJECT_DIR` to override the detection. If the project can't be
determined at all — the server was started outside any Godot project — it
can't discriminate, so filtering is off and it talks to whoever connects.

## Prerequisites

- Godot 4.x with the [godot-mcp-pro](https://github.com/youichi-uda/godot-mcp-pro) addon installed and enabled (`addons/godot_mcp/`, MIT licensed, free — only its Node.js bridge is paid, and this server replaces that)
- Python 3.11+
- [uv](https://docs.astral.sh/uv/getting-started/installation/) (recommended) or pip

## Setup

### 1. Install the Godot addon

Copy `addons/godot_mcp/` from the [godot-mcp-pro](https://github.com/youichi-uda/godot-mcp-pro) repo into your Godot project, then enable it: **Project → Project Settings → Plugins → Godot MCP Pro → Enable**.

### 2. Install this server's Python dependencies

```sh
git clone https://github.com/cmcginnis/godot-claude-mcp
cd godot-claude-mcp/server
uv sync          # or: python -m venv .venv && .venv/bin/pip install -e ".[dev]"
```

### 3. Register the MCP server in Claude Code

```sh
claude mcp add godot -- uv run --project /path/to/godot-claude-mcp/server godot-mcp
```

Or edit `~/.claude.json` directly:

```json
{
  "mcpServers": {
    "godot": {
      "command": "/path/to/godot-claude-mcp/server/.venv/bin/python",
      "args": ["-m", "godot_mcp"]
    }
  }
}
```

Restart Claude Code, open your Godot project with the plugin enabled, and confirm the server appears under `/mcp`. Godot reconnects automatically within ~3s of either side starting.

## Available Tools

A curated subset of godot-mcp-pro's ~172 commands — see "Follow-up" below.

| Tool | Description |
|------|-------------|
| `get_scene_tree(max_depth)` | Live node tree of whichever scene is open in the editor |
| `get_scene_file_content(path)` | Raw `.tscn` text of a scene file, doesn't need to be open |
| `open_scene(path)` / `save_scene(path)` | Open/save scenes |
| `add_scene_instance(scene_path, parent_path, name)` | Instance a `.tscn` as a child node |
| `play_scene(mode)` / `stop_scene()` | Run/stop a scene ("main", "current", or a res:// path) |
| `add_node(type, parent_path, name, properties)` | Add a node (undo-able) |
| `delete_node(node_path)` | Delete a node (undo-able) |
| `move_node(node_path, new_parent_path)` | Reparent via Godot's own reparent logic, not manual transform math (undo-able) |
| `update_property(node_path, property, value)` | Set any property, type-parsed against its current value (undo-able) |
| `get_node_properties(node_path, category)` | Read all editor-visible properties of a node |
| `add_resource(node_path, property, resource_type, resource_properties)` | Create and assign a Resource, e.g. a collision shape (undo-able) |
| `execute_editor_script(code, allow_unsafe_editor_io)` | Run arbitrary GDScript in the editor — use for batch operations across many files instead of one tool call per file |
| `get_editor_errors()` | Recent editor errors and stack traces |
| `get_editor_screenshot(save_path)` | Capture the editor's main viewport — whatever tab/view is currently active, no way to pick one |
| `get_game_screenshot(save_path)` | Capture a frame from the running game; requires `play_scene` first |
| `get_editor_camera()` / `set_editor_camera(position, rotation_degrees, look_at, fov)` | Read/move the 3D viewport camera, so a following `get_editor_screenshot` is framed deterministically |

All mutating tools go through Godot's `EditorUndoRedoManager` — `Ctrl+Z` works as expected.

### Tools not backed by a plugin command

These four have no equivalent in godot-mcp-pro at all — they generate GDScript
and run it through `execute_editor_script`. Called out separately because their
failure modes are the *script's*, not a plugin command's.

| Tool | Why it exists |
|------|---------------|
| `get_skeleton_bones(node_path, filter, include_pose)` | The plugin has **zero** skeleton/bone commands. Lists bones, rests, poses, and `SkeletonModifier3D` children with their `active`/`influence` — the last of which is where silently-dead IK rigs get diagnosed. **The reported pose is pre-modifier**; verify IK from a rendered frame, not from these numbers. |
| `get_import_settings(path)` | Nothing in the plugin reads `.import` files. |
| `set_import_settings(path, options, section, reimport)` | Nothing in the plugin writes `.import` files or calls `reimport_files()`. This is how you retype a `.glb`'s imported root — impossible from a wrapper scene's own `.tscn` text. |
| `create_resource(path, type, properties, overwrite)` | Deliberately supersedes the plugin's `create_resource`, which gates on `ClassDB.class_exists()` and therefore **cannot create a C# `[GlobalClass]` or GDScript `class_name` Resource**. (`add_node` handles script classes correctly; `create_resource` was never updated to match.) |

`set_import_settings` and `create_resource` pass `allow_unsafe_editor_io=true`,
because `ConfigFile.save` / `ResourceSaver.save` are on the plugin's refusal
list. That guard exists to stop a script clobbering a resource the editor has
open and unsaved — writing an `.import` file or a new `.tres` at a
caller-named path is the case it is meant to let through.

## Follow-up: wrapping the rest

Addon 1.16.0 registers **177 commands** across 26 files (animation, audio,
tilemap, shaders, navigation, particles, physics, profiling, testing, batch
refactoring, etc.) — see its [README's tool tables](https://github.com/youichi-uda/godot-mcp-pro#all-172-tools).
This server wraps a curated subset. Anything else is reachable today by passing its exact method name
and params dict through `execute_editor_script`'s GDScript escape hatch, or
by calling `bridge.call("<method>", {...})` directly — but it doesn't have a
typed, documented `@mcp.tool()` wrapper yet.

Wrapping is on-demand by design: each wrapper costs a Claude Code session
restart before it is callable, and has to be re-verified against the GDScript
signature. Wrap when a command is used repeatedly and is awkward to express
ad-hoc; otherwise use the escape hatch.

Some plugin commands are worth actively avoiding:

- **`export_project` does not export.** It returns a shell command string and
  the message *"Direct export from editor plugin is not supported in Godot 4."*
- **`validate_script` is GDScript-only.** It accepts a `.cs` path through its
  extension guard, then loads it with `ResourceLoader.load(path, "GDScript")`.
  Use `dotnet build` for C#.
- **`batch_add_nodes` has the same ClassDB-only bug as `create_resource`** and
  cannot add nodes of a script class, though single `add_node` can.

Add one as each is actually needed: confirm the exact `params` keys by
reading the corresponding handler in `commands/*.gd` in the addon (don't
guess from the README's one-line descriptions — they don't include param
schemas), then add a typed wrapper in `server.py` following the existing
pattern. No need to wrap all 172 up front.

## Development

```sh
cd server
uv run pytest
```

`tests/test_transport.py` spins up a real `GodotBridge` WebSocket server and
drives it with a fake Godot client to exercise the actual wire protocol.
`tests/test_server.py` mocks the bridge to test tool-wrapper param passthrough
and error formatting.
