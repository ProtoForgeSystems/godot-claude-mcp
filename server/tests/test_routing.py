"""Project-path routing: which connected editor a bridge talks to.

The addon connects to every listener in 6505-6514, so every editor on the
machine reaches every bridge. These tests pin the behaviour that keeps a
session's commands inside its own checkout — see transport.py's module
docstring for the failure mode they exist to prevent.
"""

import asyncio
import json

import pytest
from websockets.asyncio.client import connect

from godot_mcp.transport import PROJECT_DIR_ENV, GodotBridge, resolve_project_dir

PORT = 6598  # outside the plugin's 6505-6514 range, and distinct from test_transport's


def make_project(root, *parts):
    """Create a directory containing a project.godot and return it."""
    project = root.joinpath(*parts)
    project.mkdir(parents=True, exist_ok=True)
    (project / "project.godot").write_text('config/name="Test"\n')
    return project


async def _wait_for_port(port: int, timeout: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        try:
            probe = await connect(f"ws://127.0.0.1:{port}")
            await probe.close()
            return
        except (ConnectionRefusedError, OSError):
            if asyncio.get_running_loop().time() > deadline:
                raise
            await asyncio.sleep(0.02)


class FakeEditor:
    """A Godot editor with `project_path` open, answering identity probes.

    Requests other than get_export_info land in `inbox` for the test to
    inspect and answer, which is how these tests observe *which* editor a
    command was routed to.
    """

    def __init__(self, ws, project_path):
        self._ws = ws
        # The addon reports globalize_path("res://"), which carries a trailing
        # separator — normalisation has to cope with it.
        self._reported = f"{project_path}/"
        self.inbox: asyncio.Queue = asyncio.Queue()
        self.identified = asyncio.Event()
        self._task = asyncio.create_task(self._pump())

    async def _pump(self):
        async for raw in self._ws:
            message = json.loads(raw)
            if message.get("method") == "get_export_info":
                await self._ws.send(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": message["id"],
                            "result": {"project_path": self._reported},
                        }
                    )
                )
                self.identified.set()
            else:
                await self.inbox.put(message)

    async def reply(self, message, result):
        await self._ws.send(
            json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result})
        )

    async def send_raw(self, payload):
        await self._ws.send(json.dumps(payload))

    async def recv_raw(self):
        return json.loads(await self._ws.recv())

    async def close(self):
        self._task.cancel()
        await self._ws.close()


@pytest.fixture
async def project_bridge():
    """Factory: a bridge serving a given project, plus a way to attach editors."""
    tasks = []
    editors = []

    async def make(project):
        bridge = GodotBridge(base_port=PORT, port_range_size=1, project_dir=project)
        task = asyncio.create_task(bridge.serve_forever())
        tasks.append(task)
        await _wait_for_port(PORT)
        return bridge

    async def attach(project_path):
        editor = FakeEditor(await connect(f"ws://127.0.0.1:{PORT}"), project_path)
        editors.append(editor)
        return editor

    try:
        yield make, attach
    finally:
        for editor in editors:
            await editor.close()
        for task in tasks:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task


# --------------------------------------------------------------------------
# resolve_project_dir
# --------------------------------------------------------------------------


def test_resolve_finds_project_at_the_starting_directory(tmp_path):
    project = make_project(tmp_path, "game")
    assert resolve_project_dir(project) == project


def test_resolve_walks_up_to_the_enclosing_project(tmp_path):
    project = make_project(tmp_path, "game")
    nested = project / "Content" / "Player"
    nested.mkdir(parents=True)
    assert resolve_project_dir(nested) == project


def test_resolve_scans_down_from_a_repo_root(tmp_path):
    """The normal case here: the session's cwd is the repo, the project is game/."""
    project = make_project(tmp_path, "game")
    assert resolve_project_dir(tmp_path) == project


def test_resolve_scans_down_more_than_one_level(tmp_path):
    """Fendrel's layout: godot/client/project.godot under the repo root."""
    project = make_project(tmp_path, "godot", "client")
    assert resolve_project_dir(tmp_path) == project


def test_resolve_ignores_projects_inside_worktrees(tmp_path):
    """Worktrees live inside the repo, so without skipping hidden directories
    the main checkout would see every branch's project.godot and give up."""
    project = make_project(tmp_path, "game")
    make_project(tmp_path, ".worktrees", "feature-x", "game")
    assert resolve_project_dir(tmp_path) == project


def test_resolve_returns_none_when_ambiguous(tmp_path):
    make_project(tmp_path, "game_a")
    make_project(tmp_path, "game_b")
    assert resolve_project_dir(tmp_path) is None


def test_resolve_returns_none_when_there_is_no_project(tmp_path):
    assert resolve_project_dir(tmp_path) is None


def test_resolve_honours_the_env_override(tmp_path, monkeypatch):
    make_project(tmp_path, "game_a")
    make_project(tmp_path, "game_b")  # ambiguous without the override
    chosen = make_project(tmp_path, "elsewhere")
    monkeypatch.setenv(PROJECT_DIR_ENV, str(chosen))
    assert resolve_project_dir(tmp_path) == chosen


def test_resolve_ignores_an_env_override_that_is_not_a_project(tmp_path, monkeypatch):
    project = make_project(tmp_path, "game")
    monkeypatch.setenv(PROJECT_DIR_ENV, str(tmp_path / "nonexistent"))
    assert resolve_project_dir(tmp_path) == project


def test_bridge_without_a_resolvable_project_does_not_filter(tmp_path):
    """Started outside any Godot project, the bridge cannot discriminate, so
    it must not try — that would break every non-project working directory."""
    bridge = GodotBridge(base_port=PORT, port_range_size=1)
    bridge._project_dir = None  # what resolve_project_dir returns there
    assert bridge.project_dir is None


# --------------------------------------------------------------------------
# routing
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_routes_to_the_editor_with_this_project_open(project_bridge, tmp_path):
    mine = make_project(tmp_path, "mine", "game")
    theirs = make_project(tmp_path, "theirs", "game")
    make, attach = project_bridge
    bridge = await make(mine)

    foreign = await attach(theirs)
    await asyncio.sleep(0.1)  # let the foreign editor be identified and rejected
    ours = await attach(mine)

    call = asyncio.create_task(bridge.call("save_scene", {}))
    request = await asyncio.wait_for(ours.inbox.get(), timeout=2)
    assert request["method"] == "save_scene"
    await ours.reply(request, {"saved": True})
    assert await call == {"saved": True}
    assert foreign.inbox.empty()


@pytest.mark.asyncio
async def test_a_foreign_editor_connecting_last_does_not_steal_the_route(
    project_bridge, tmp_path
):
    """The regression this whole mechanism exists for: the addon reconnects on
    a 30s heartbeat gap, so the most recent connection is arbitrary and must
    not decide where commands go."""
    mine = make_project(tmp_path, "mine", "game")
    theirs = make_project(tmp_path, "theirs", "game")
    make, attach = project_bridge
    bridge = await make(mine)

    ours = await attach(mine)
    await asyncio.wait_for(ours.identified.wait(), timeout=2)
    foreign = await attach(theirs)
    await asyncio.wait_for(foreign.identified.wait(), timeout=2)
    await asyncio.sleep(0.1)

    call = asyncio.create_task(bridge.call("delete_node", {}))
    request = await asyncio.wait_for(ours.inbox.get(), timeout=2)
    await ours.reply(request, {"ok": True})
    assert await call == {"ok": True}
    assert foreign.inbox.empty()


@pytest.mark.asyncio
async def test_only_a_foreign_editor_reports_not_connected(project_bridge, tmp_path):
    mine = make_project(tmp_path, "mine", "game")
    theirs = make_project(tmp_path, "theirs", "game")
    make, attach = project_bridge
    bridge = await make(mine)

    foreign = await attach(theirs)
    await asyncio.wait_for(foreign.identified.wait(), timeout=2)

    with pytest.raises(ConnectionError) as excinfo:
        await bridge.call("save_scene", {}, connect_timeout=0.5)
    # The message has to name both projects, or the failure looks like the
    # editor simply not running and the real cause stays invisible.
    assert str(mine) in str(excinfo.value)
    assert str(theirs) in str(excinfo.value)


@pytest.mark.asyncio
async def test_pings_are_answered_on_the_socket_that_sent_them(project_bridge, tmp_path):
    """An ignored editor still needs its pongs: the addon force-reconnects
    after 30s of silence, so answering on the active socket instead would put
    every foreign editor into a permanent redial loop."""
    mine = make_project(tmp_path, "mine", "game")
    theirs = make_project(tmp_path, "theirs", "game")
    make, attach = project_bridge
    bridge = await make(mine)
    await attach(mine)
    foreign = await attach(theirs)
    await asyncio.wait_for(foreign.identified.wait(), timeout=2)

    await foreign.send_raw({"jsonrpc": "2.0", "method": "ping", "params": {}})
    reply = await asyncio.wait_for(foreign.inbox.get(), timeout=2)
    assert reply["method"] == "pong"
    assert bridge.project_dir == mine


@pytest.mark.asyncio
async def test_a_foreign_editor_disconnecting_does_not_fail_our_pending_calls(
    project_bridge, tmp_path
):
    mine = make_project(tmp_path, "mine", "game")
    theirs = make_project(tmp_path, "theirs", "game")
    make, attach = project_bridge
    bridge = await make(mine)
    ours = await attach(mine)
    foreign = await attach(theirs)
    await asyncio.wait_for(foreign.identified.wait(), timeout=2)

    call = asyncio.create_task(bridge.call("get_scene_tree", {}))
    request = await asyncio.wait_for(ours.inbox.get(), timeout=2)

    await foreign.close()
    await asyncio.sleep(0.1)

    await ours.reply(request, {"tree": []})
    assert await call == {"tree": []}


@pytest.mark.asyncio
async def test_a_replacement_editor_is_elected_after_ours_drops(project_bridge, tmp_path):
    """Restarting the editor mid-session has to reattach without restarting
    the Claude session."""
    mine = make_project(tmp_path, "mine", "game")
    make, attach = project_bridge
    bridge = await make(mine)

    first = await attach(mine)
    await asyncio.wait_for(first.identified.wait(), timeout=2)
    await first.close()
    await asyncio.sleep(0.1)

    second = await attach(mine)
    call = asyncio.create_task(bridge.call("get_scene_tree", {}))
    request = await asyncio.wait_for(second.inbox.get(), timeout=2)
    await second.reply(request, {"tree": ["reattached"]})
    assert await call == {"tree": ["reattached"]}


@pytest.mark.asyncio
async def test_an_editor_that_cannot_be_identified_is_not_routed_to(
    project_bridge, tmp_path
):
    """A probe failure must not fall back to routing blindly — that is exactly
    the behaviour being removed."""
    mine = make_project(tmp_path, "mine", "game")
    make, attach = project_bridge
    bridge = await make(mine)

    silent = await connect(f"ws://127.0.0.1:{PORT}")
    try:
        probe = json.loads(await asyncio.wait_for(silent.recv(), timeout=2))
        assert probe["method"] == "get_export_info"
        await silent.send(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": probe["id"],
                    "error": {"code": -32601, "message": "Unknown command"},
                }
            )
        )
        with pytest.raises(ConnectionError):
            await bridge.call("save_scene", {}, connect_timeout=0.5)
    finally:
        await silent.close()
