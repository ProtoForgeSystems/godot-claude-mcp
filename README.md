# godot-claude-mcp

Let Claude control the Godot 4 editor — place scenes, query node trees, move and remove nodes — all in real time with undo support.

## How It Works

Two components:

1. **Godot editor plugin** (`addon/godot_claude_mcp/`) — runs a local HTTP server inside the Godot editor
2. **Python MCP server** (`server/`) — exposes 5 MCP tools that Claude calls, forwarding them to the plugin

## Setup

### 1. Enable the Godot Plugin

Copy the addon folder into your Godot project:

```
cp -r addon/godot_claude_mcp/ /path/to/your/godot-project/addons/
```

In Godot: **Project → Project Settings → Plugins → Godot Claude MCP → Enable**

You should see in the Output panel:
```
[GodotMCP] Listening on localhost:6400
```

### 2. Register the MCP Server

Add to your Claude Code MCP config (`~/.claude/mcp_settings.json` or via `/mcp`):

```json
{
  "mcpServers": {
    "godot": {
      "command": "uvx",
      "args": ["godot-claude-mcp"]
    }
  }
}
```

Or if running from source:

```json
{
  "mcpServers": {
    "godot": {
      "command": "python",
      "args": ["-m", "godot_mcp"],
      "cwd": "/path/to/godot-claude-mcp/server"
    }
  }
}
```

Restart Claude Code. Confirm the server is listed in `/mcp`.

## Available Tools

| Tool | Description |
|------|-------------|
| `get_scene_tree(scene_path)` | Returns full node tree with transforms |
| `list_scenes(directory)` | Lists all .tscn files under a res:// directory |
| `place_scene(scene_path, x, y, z, ...)` | Instantiates a scene node (undo-able) |
| `remove_node(scene_path, node_path)` | Removes a node (undo-able) |
| `set_node_transform(scene_path, node_path, ...)` | Moves/rotates/scales a node (undo-able) |

## Example Prompt

> "List the available debris props under res://Assets/Kits/AbandonedWorld/, then open Room4.tscn and scatter 5–8 of them around the room at floor level with varied Y rotations."

## Port

Default port is 6400. To change it, edit the `PORT` constant in `addon/godot_claude_mcp/plugin.gd` and restart the plugin.
