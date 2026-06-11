import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("godot-editor")
GODOT_URL = "http://localhost:6400"


def _call(method: str, path: str, **kwargs) -> dict:
    """Make an HTTP call to the Godot plugin. Returns a dict with 'ok' key."""
    try:
        r = httpx.request(method, f"{GODOT_URL}{path}", timeout=10.0, **kwargs)
        return r.json()
    except httpx.ConnectError:
        return {
            "ok": False,
            "error": (
                "Cannot connect to Godot editor. "
                "Make sure Godot is running with the godot_claude_mcp plugin enabled."
            ),
        }
    except httpx.TimeoutException:
        return {"ok": False, "error": "Request to Godot editor timed out."}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
def get_scene_tree(scene_path: str) -> dict:
    """Return the full node tree of a Godot scene including transforms.

    Args:
        scene_path: The res:// path to the scene, e.g. res://Content/Worlds/Room4.tscn
    """
    return _call("GET", "/scene/tree", params={"path": scene_path})


@mcp.tool()
def list_scenes(directory: str) -> dict:
    """List all .tscn files under a res:// directory.

    Args:
        directory: The res:// directory to search, e.g. res://Assets/Props/
    """
    return _call("GET", "/scene/list", params={"dir": directory})


@mcp.tool()
def place_scene(
    scene_path: str,
    x: float,
    y: float,
    z: float,
    rot_x: float = 0.0,
    rot_y: float = 0.0,
    rot_z: float = 0.0,
    parent_path: str = ".",
    name: str = "",
) -> dict:
    """Instantiate a .tscn file as a child node in the currently edited scene.

    Args:
        scene_path: res:// path to the scene to instantiate
        x: World X position
        y: World Y position (elevation in Godot)
        z: World Z position
        rot_x: X rotation in degrees (default 0)
        rot_y: Y rotation in degrees (default 0)
        rot_z: Z rotation in degrees (default 0)
        parent_path: Node path of the parent (default "." = scene root)
        name: Override the instance name (default = scene filename)
    """
    return _call("POST", "/scene/place", json={
        "scene_path": scene_path,
        "x": x, "y": y, "z": z,
        "rot_x": rot_x, "rot_y": rot_y, "rot_z": rot_z,
        "parent_path": parent_path,
        "name": name,
    })


@mcp.tool()
def remove_node(scene_path: str, node_path: str) -> dict:
    """Remove a node from the currently edited scene by its node path.

    Args:
        scene_path: res:// path of the scene (used as a safety check)
        node_path: Node path within the scene, e.g. "Layout/Barrel"
    """
    return _call("DELETE", "/scene/node", json={
        "scene_path": scene_path,
        "node_path": node_path,
    })


@mcp.tool()
def set_node_transform(
    scene_path: str,
    node_path: str,
    x: float,
    y: float,
    z: float,
    rot_x: float,
    rot_y: float,
    rot_z: float,
    scale_x: float = 1.0,
    scale_y: float = 1.0,
    scale_z: float = 1.0,
) -> dict:
    """Update position, rotation (degrees), and scale of an existing Node3D.
    All values are required. Read current values from get_scene_tree first
    if you only want to change one axis.

    Args:
        scene_path: res:// path of the scene (safety check)
        node_path: Node path within the scene, e.g. "Layout/Barrel"
        x, y, z: New world position
        rot_x, rot_y, rot_z: New rotation in degrees (Euler XYZ)
        scale_x, scale_y, scale_z: New scale (default 1.0)
    """
    return _call("PUT", "/scene/transform", json={
        "scene_path": scene_path,
        "node_path": node_path,
        "x": x, "y": y, "z": z,
        "rot_x": rot_x, "rot_y": rot_y, "rot_z": rot_z,
        "scale_x": scale_x, "scale_y": scale_y, "scale_z": scale_z,
    })


def main() -> None:
    mcp.run()
