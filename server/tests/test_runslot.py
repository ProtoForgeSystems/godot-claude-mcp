"""The exclusive game-run slot.

Godot keys user:// on the project name, so every worktree of a game shares one
directory and only one of them may have the game running. See runslot.py.
"""

import json

import pytest

from godot_mcp.runslot import (
    UNCONFIRMED_GRACE_SECONDS,
    GameProcess,
    GameRunSlot,
    parse_ps_output,
    read_project_name,
)

# A real line from `ps -axo pid=,command=`, captured from a game launched by
# EditorInterface.play_main_scene() on 2026-08-06.
REAL_GAME_LINE = (
    " 157603 /home/u/Applications/godot/Godot_v4.7.1-stable_mono_linux.x86_64 "
    "--path /repo/game --remote-debug tcp://127.0.0.1:6007 --editor-pid 79579 "
    "--scene res://Engine/AppRoot.tscn --wid 123732227 --display-driver x11 "
    "--position 320,159 --resolution 1920x1080"
)
REAL_EDITOR_LINE = (
    "  79579 /home/u/Applications/godot/Godot_v4.7.1-stable_mono_linux.x86_64 "
    "--editor --path /repo/game"
)


def make_project(root, *parts, name="Test Game"):
    project = root.joinpath(*parts)
    project.mkdir(parents=True, exist_ok=True)
    (project / "project.godot").write_text(
        f'config_version=5\n\n[application]\n\nconfig/name="{name}"\n'
    )
    return project


class FakeClock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


def slot_for(project, tmp_path, games=(), clock=None):
    """A slot whose view of the process table is whatever the test says."""
    live = list(games)
    return (
        GameRunSlot(
            project,
            lock_dir=tmp_path / "locks",
            scan=lambda: list(live),
            clock=clock or FakeClock(),
        ),
        live,
    )


# --------------------------------------------------------------------------
# process table parsing
# --------------------------------------------------------------------------


def test_a_real_game_line_is_recognised():
    games = parse_ps_output(REAL_GAME_LINE)
    assert len(games) == 1
    assert games[0].pid == 157603
    assert games[0].project_dir.name == "game"


def test_an_editor_is_not_mistaken_for_a_game():
    """Regression: "--editor" is a prefix of "--editor-pid", so a substring test
    reports every game as an editor and the whole guard silently never fires.
    That bug shipped once."""
    assert parse_ps_output(REAL_EDITOR_LINE) == []


def test_a_game_launched_from_a_command_line_counts():
    """The mirror-image bug: classifying on the presence of --editor-pid misses
    every game the editor did not spawn. This is the project's documented way of
    rendering a clip, and it shares user:// exactly the same way."""
    line = (
        " 160001 /home/u/Applications/godot/Godot_v4.7.1-stable_mono_linux.x86_64 "
        "--path /repo/game -- --driver=crouch --write-movie /tmp/out.avi"
    )
    games = parse_ps_output(line)
    assert [g.pid for g in games] == [160001]


@pytest.mark.parametrize(
    "flag",
    ["--build-solutions", "--export-release", "--export-debug", "--script", "--doctool"],
)
def test_tool_invocations_are_not_running_games(flag):
    """A compile or an export names a project path without running it. Counting
    those would make every build look like a collision."""
    line = (
        f" 160002 /home/u/Applications/godot/Godot_v4.7.1-stable_mono_linux.x86_64 "
        f"--headless --path /repo/game {flag} probe.gd --quit"
    )
    assert parse_ps_output(line) == []


def test_both_lines_together_yield_only_the_game():
    games = parse_ps_output(f"{REAL_EDITOR_LINE}\n{REAL_GAME_LINE}")
    assert [g.pid for g in games] == [157603]


def test_non_godot_processes_are_ignored():
    assert parse_ps_output("  42 /usr/bin/python3 --editor-pid 1 --path /repo/game") == []


def test_a_game_without_a_path_is_ignored():
    assert parse_ps_output("  42 /opt/godot --editor-pid 1 --scene res://a.tscn") == []


# --------------------------------------------------------------------------
# project name = the collision domain
# --------------------------------------------------------------------------


def test_read_project_name(tmp_path):
    project = make_project(tmp_path, "game", name="Meat Mutant")
    assert read_project_name(project) == "Meat Mutant"


def test_read_project_name_missing_file(tmp_path):
    assert read_project_name(tmp_path / "nowhere") is None


def test_worktrees_of_one_game_share_a_lock(tmp_path):
    """The lock is keyed by project NAME, because user:// is."""
    main = make_project(tmp_path, "repo", "game", name="Meat Mutant")
    tree = make_project(tmp_path, "repo", ".worktrees", "x", "game", name="Meat Mutant")
    a, _ = slot_for(main, tmp_path)
    b, _ = slot_for(tree, tmp_path)
    assert a.lock_path == b.lock_path


def test_a_different_game_gets_a_different_lock(tmp_path):
    mine = make_project(tmp_path, "mine", "game", name="Meat Mutant")
    other = make_project(tmp_path, "other", "client", name="Fendrel")
    a, _ = slot_for(mine, tmp_path)
    b, _ = slot_for(other, tmp_path)
    assert a.lock_path != b.lock_path


# --------------------------------------------------------------------------
# acquire / release
# --------------------------------------------------------------------------


def test_acquire_succeeds_when_free(tmp_path):
    project = make_project(tmp_path, "game")
    slot, _ = slot_for(project, tmp_path)
    assert slot.acquire() is None
    assert slot.lock_path.exists()


def test_a_second_worktree_is_refused(tmp_path):
    main = make_project(tmp_path, "repo", "game", name="Meat Mutant")
    tree = make_project(tmp_path, "repo", ".worktrees", "x", "game", name="Meat Mutant")
    clock = FakeClock()
    a, a_games = slot_for(main, tmp_path, clock=clock)
    assert a.acquire() is None
    a_games.append(GameProcess(pid=4242, project_dir=main))
    a.confirm_started()

    b, b_games = slot_for(tree, tmp_path, clock=clock)
    b_games.append(GameProcess(pid=4242, project_dir=main))
    blocker = b.acquire()
    # A visible process outranks the lock as the reason for the refusal — it is
    # the more direct evidence, and it is the only evidence when the game was
    # started outside MCP.
    assert isinstance(blocker, GameProcess)
    assert blocker.project_dir == main
    assert blocker.pid == 4242


def test_acquire_is_refused_by_a_game_that_never_claimed_the_slot(tmp_path):
    """Only play_scene claims the lock, so a game started by F5 in the
    architect's own editor — or from a shell — holds nothing. The process table
    is what makes those routes count; the lock alone would let us launch a
    second game straight into the shared user://."""
    main = make_project(tmp_path, "repo", "game", name="Meat Mutant")
    tree = make_project(tmp_path, "repo", ".worktrees", "x", "game", name="Meat Mutant")
    slot, games = slot_for(tree, tmp_path)
    games.append(GameProcess(pid=777, project_dir=main))

    blocker = slot.acquire()
    assert isinstance(blocker, GameProcess)
    assert blocker.pid == 777
    assert not slot.lock_path.exists()  # nothing claimed on a refusal


def test_reacquiring_our_own_slot_is_not_a_conflict(tmp_path):
    project = make_project(tmp_path, "game")
    slot, _ = slot_for(project, tmp_path)
    assert slot.acquire() is None
    assert slot.acquire() is None


def test_a_dead_holder_is_reclaimed(tmp_path):
    """A session that died between acquire and release must not wedge the
    machine — the process table, not the lock, decides what is running."""
    main = make_project(tmp_path, "repo", "game", name="Meat Mutant")
    tree = make_project(tmp_path, "repo", ".worktrees", "x", "game", name="Meat Mutant")
    clock = FakeClock()
    dead, dead_games = slot_for(main, tmp_path, clock=clock)
    dead.acquire()
    dead_games.append(GameProcess(pid=4242, project_dir=main))
    dead.confirm_started()

    # The game is gone; nobody released the lock.
    survivor, _ = slot_for(tree, tmp_path, clock=clock)
    assert survivor.acquire() is None


def test_an_unconfirmed_holder_is_respected_during_the_grace_period(tmp_path):
    """A launch takes time. Reclaiming instantly would let a second session
    barge in while the first game is still starting."""
    main = make_project(tmp_path, "repo", "game", name="Meat Mutant")
    tree = make_project(tmp_path, "repo", ".worktrees", "x", "game", name="Meat Mutant")
    clock = FakeClock()
    starting, _ = slot_for(main, tmp_path, clock=clock)
    starting.acquire()  # no pid recorded yet, no process visible yet

    other, _ = slot_for(tree, tmp_path, clock=clock)
    assert other.acquire() is not None

    clock.now += UNCONFIRMED_GRACE_SECONDS + 1
    assert other.acquire() is None


def test_release_only_removes_our_own_lock(tmp_path):
    main = make_project(tmp_path, "repo", "game", name="Meat Mutant")
    tree = make_project(tmp_path, "repo", ".worktrees", "x", "game", name="Meat Mutant")
    clock = FakeClock()
    owner, owner_games = slot_for(main, tmp_path, clock=clock)
    owner.acquire()
    owner_games.append(GameProcess(pid=4242, project_dir=main))
    owner.confirm_started()

    intruder, _ = slot_for(tree, tmp_path, clock=clock)
    assert intruder.release() is False
    assert owner.lock_path.exists()

    assert owner.release() is True
    assert not owner.lock_path.exists()


def test_release_is_safe_when_nothing_is_held(tmp_path):
    project = make_project(tmp_path, "game")
    slot, _ = slot_for(project, tmp_path)
    assert slot.release() is True


def test_a_corrupt_lock_is_reclaimed(tmp_path):
    project = make_project(tmp_path, "game")
    slot, _ = slot_for(project, tmp_path)
    slot.lock_path.parent.mkdir(parents=True, exist_ok=True)
    slot.lock_path.write_text("not json")
    assert slot.acquire() is None
    assert json.loads(slot.lock_path.read_text())["project_dir"] == str(project)


# --------------------------------------------------------------------------
# blocker / status — what gates the live-game tools
# --------------------------------------------------------------------------


def test_a_foreign_game_blocks_even_with_no_lock(tmp_path):
    """Covers a game started from the editor's own UI, which never touches MCP."""
    main = make_project(tmp_path, "repo", "game", name="Meat Mutant")
    tree = make_project(tmp_path, "repo", ".worktrees", "x", "game", name="Meat Mutant")
    slot, games = slot_for(tree, tmp_path)
    games.append(GameProcess(pid=99, project_dir=main))
    blocker = slot.blocker()
    assert isinstance(blocker, GameProcess)
    assert blocker.pid == 99


def test_our_own_game_does_not_block_us(tmp_path):
    project = make_project(tmp_path, "game")
    slot, games = slot_for(project, tmp_path)
    games.append(GameProcess(pid=99, project_dir=project))
    assert slot.blocker() is None


def test_an_unrelated_game_does_not_block_us(tmp_path):
    """Fendrel running is none of this game's business."""
    mine = make_project(tmp_path, "mine", "game", name="Meat Mutant")
    other = make_project(tmp_path, "other", "client", name="Fendrel")
    slot, games = slot_for(mine, tmp_path)
    games.append(GameProcess(pid=99, project_dir=other))
    assert slot.blocker() is None


def test_status_reports_availability_and_who_holds_it(tmp_path):
    main = make_project(tmp_path, "repo", "game", name="Meat Mutant")
    tree = make_project(tmp_path, "repo", ".worktrees", "x", "game", name="Meat Mutant")
    clock = FakeClock()
    owner, owner_games = slot_for(main, tmp_path, clock=clock)
    owner.acquire()
    owner_games.append(GameProcess(pid=4242, project_dir=main))
    owner.confirm_started()

    other, other_games = slot_for(tree, tmp_path, clock=clock)
    other_games.append(GameProcess(pid=4242, project_dir=main))
    status = other.status()
    assert status["available"] is False
    assert status["held_by"]["project_dir"] == str(main)
    assert status["held_by"]["is_ours"] is False
    assert status["running_games"][0]["shares_our_user_dir"] is True

    owner.release()
    owner_games.clear()
    other_games.clear()
    assert other.status()["available"] is True


def test_a_project_without_a_name_falls_back_to_its_path(tmp_path):
    """Better to protect nothing than to refuse the wrong sessions."""
    nameless = tmp_path / "game"
    nameless.mkdir()
    (nameless / "project.godot").write_text("config_version=5\n")
    slot, _ = slot_for(nameless, tmp_path)
    assert slot.acquire() is None
    assert str(nameless).replace("/", "-").strip("-") in slot.lock_path.name
