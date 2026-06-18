# Godot Claude MCP — Editor Plugin

This plugin runs a local HTTP server inside the Godot editor so Claude can query and modify your scene in real time.

**It requires a companion Python MCP server** that Claude Code connects to. The plugin alone does nothing without it.

## Enabling the Plugin

In Godot: **Project → Project Settings → Plugins → Godot Claude MCP → Enable**

You should see in the Output panel:

```
[GodotMCP] Listening on localhost:6400
```

Check status anytime via **Tools → Godot MCP: Status**.

## Setting Up the MCP Server

The MCP server lives in the [godot-claude-mcp](https://github.com/cmcginnis/godot-claude-mcp) repository.

### 1. Clone the repo

```sh
git clone https://github.com/cmcginnis/godot-claude-mcp
cd godot-claude-mcp
```

### 2. Install dependencies

With [uv](https://docs.astral.sh/uv/getting-started/installation/) (recommended):

```sh
cd server && uv sync
```

With pip:

```sh
cd server
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
```

### 3. Register with Claude Code

```sh
claude mcp add godot -- uv run --project /path/to/godot-claude-mcp/server godot-mcp
```

Or add to `~/.claude.json` manually:

```json
{
  "mcpServers": {
    "godot": {
      "command": "uv",
      "args": ["run", "--project", "/path/to/godot-claude-mcp/server", "godot-mcp"]
    }
  }
}
```

Restart Claude Code and confirm the server appears under `/mcp`.

## How It Works

```
Claude Code  ──MCP──►  Python server (port stdio)  ──HTTP──►  This plugin (port 6400)
```

The plugin accepts HTTP requests on `localhost:6400` and translates them into editor API calls — opening scenes, placing nodes, reading the scene tree, etc. All mutations are registered with Godot's undo/redo system.

## Port

Default is 6400. To change it, edit the `PORT` constant at the top of `plugin.gd` and re-enable the plugin.
