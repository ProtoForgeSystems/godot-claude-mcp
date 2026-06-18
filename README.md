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

All mutating tools go through Godot's `EditorUndoRedoManager` — `Ctrl+Z` works as expected.

## Follow-up: wrapping the rest

godot-mcp-pro exposes ~172 commands across 23 categories (animation, audio,
tilemap, shaders, navigation, particles, physics, profiling, testing, batch
refactoring, etc.) — see its [README's tool tables](https://github.com/youichi-uda/godot-mcp-pro#all-172-tools).
This server only wraps the 16 needed for editor-scripted scene/node work and
basic screenshot capture so far. Anything else is reachable today by passing its exact method name
and params dict through `execute_editor_script`'s GDScript escape hatch, or
by calling `bridge.call("<method>", {...})` directly — but it doesn't have a
typed, documented `@mcp.tool()` wrapper yet.

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
