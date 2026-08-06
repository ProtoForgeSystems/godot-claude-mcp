"""One running game at a time, per game, across every bridge on the machine.

WHY THIS EXISTS
---------------
Godot derives `user://` from the project's *name* (`application/config/name`),
not its path. Every git worktree of the same game therefore resolves to one
directory — on Linux `~/.local/share/godot/app_userdata/<name>`.

The godot_mcp addon talks to a running game through files in that directory:
`mcp_screenshot_request`, `mcp_input_commands`, `mcp_game_request/response`.
Two games running at once answer each other's requests, so a session gets a
perfectly plausible screenshot of somebody else's build. Worse, `stop_scene`
cleans those files up, so one session stopping its game deletes the other
session's in-flight channel.

There is no way to split `user://`: Godot 4.7 has no CLI override for it, and
while `override.cfg` moves it for the runtime, the *editor* ignores
`override.cfg` entirely — so using it would put the two halves of that channel
in different directories. Serializing the runs is the only fix available.

This does NOT gate on a session whose own editor simply isn't playing: the
addon already refuses those (`EditorInterface.is_playing_scene()` guards every
live-game command). The window this closes is two editors playing at once.

WHY A LOCK FILE *AND* A PROCESS SCAN
------------------------------------
Neither alone is sufficient, and the failure modes are opposite:

- A process scan alone races. Two agents calling play_scene in the same instant
  both scan, both see nothing, and both launch.
- A lock file alone leaks. A session that dies between acquire and release
  blocks the machine forever, and there is nobody to clean up after it.

So the lock provides atomicity (O_EXCL create) and the process table provides
truth: a held lock whose game is gone is debris and gets reclaimed. That keeps
"a game is running" defined by exactly one thing — a live process — while still
closing the start race.

WHAT COUNTS AS A RUNNING GAME
-----------------------------
Any Godot process running a project that is not the editor itself. Two traps,
both of which have bitten:

- `--editor` must be matched as a **whole field**. A substring test also matches
  an editor-spawned game, whose command line carries `--editor-pid`, so every
  game reads as an editor and the guard never fires.
- Do **not** invert that into "a game is a process carrying `--editor-pid`".
  That flag appears only on a game the *editor* spawned. A game launched from a
  command line — `godot --path game -- --driver=<name> --write-movie …`, which
  is how this project renders clips — carries neither flag, and it shares
  `user://` in exactly the same way. Missing it misses the collision.

Tool invocations (`--build-solutions`, `--export-*`, `--script`, …) name a
project path without running the game, so they are skipped rather than counted.
"""

from __future__ import annotations

import errno
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

#: A game that never reports a pid is treated as debris after this long, so a
#: failed launch cannot wedge the slot. Generous: the editor has to build C#
#: and spawn a process before anything appears in the process table.
UNCONFIRMED_GRACE_SECONDS = 45.0

#: How long a scan of the process table is reused. The live-game tools check the
#: slot on every call, and `ps` is far too expensive to run per call.
SCAN_TTL_SECONDS = 2.0

#: Godot modes that name a project path without ever running the game. A build
#: or an export must not read as a running game, or every compile would look
#: like a collision.
_TOOL_MODE_FLAGS = frozenset(
    {
        "--build-solutions",
        "--export-release",
        "--export-debug",
        "--export-pack",
        "--script",
        "--doctool",
        "--dump-extension-api",
    }
)

_CONFIG_NAME = re.compile(r'^\s*config/name\s*=\s*"(?P<name>.*)"\s*$', re.MULTILINE)
_UNSAFE_IN_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True)
class GameProcess:
    """A Godot game process spawned by an editor."""

    pid: int
    project_dir: Path


@dataclass(frozen=True)
class SlotHolder:
    """Whoever currently owns the right to run this game."""

    project_dir: Path
    game_pid: int | None
    acquired_at: float

    def describe(self) -> str:
        where = f"the checkout at {self.project_dir}"
        if self.game_pid is not None:
            return f"{where} (game pid {self.game_pid})"
        return f"{where} (game still starting)"


def read_project_name(project_dir: Path) -> str | None:
    """The `application/config/name` that decides which games share user://."""
    try:
        text = (project_dir / "project.godot").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = _CONFIG_NAME.search(text)
    if match is None:
        return None
    name = match.group("name").strip()
    return name or None


def default_lock_dir() -> Path:
    cache = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(cache) / "godot-claude-mcp"


def parse_ps_output(text: str) -> list[GameProcess]:
    """Pick the editor-spawned Godot games out of `ps -axo pid=,command=`.

    A real line looks like:
        157603 /…/Godot_v4.7.1-stable_mono_linux.x86_64 --path /…/game
               --remote-debug tcp://127.0.0.1:6007 --editor-pid 79579 --scene …
    """
    games: list[GameProcess] = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 3:
            continue
        if "godot" not in fields[1].lower():
            continue
        # Whole-field match, never a substring: see the module docstring for
        # both halves of this and why neither may be "simplified".
        if "--editor" in fields:
            continue
        if not _TOOL_MODE_FLAGS.isdisjoint(fields):
            continue
        try:
            path_index = fields.index("--path")
        except ValueError:
            continue
        if path_index + 1 >= len(fields):
            continue
        try:
            pid = int(fields[0])
        except ValueError:
            continue
        try:
            project_dir = Path(fields[path_index + 1]).resolve()
        except OSError:
            continue
        games.append(GameProcess(pid=pid, project_dir=project_dir))
    return games


def scan_running_games() -> list[GameProcess]:
    """Every editor-spawned Godot game currently running, from the process table.

    Uses `ps -axo pid=,command=` rather than a Python process library so this
    adds no dependency and matches what scripts/godot-editor.sh reports.
    """
    try:
        completed = subprocess.run(
            ["ps", "-axo", "pid=,command="],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return parse_ps_output(completed.stdout)


class GameRunSlot:
    """The exclusive right to have this game running, shared machine-wide.

    Scoped by project *name*, because that is what `user://` is keyed on — two
    worktrees of one game contend, an unrelated Godot project never does.
    """

    def __init__(
        self,
        project_dir: Path,
        *,
        lock_dir: Path | None = None,
        scan=scan_running_games,
        clock=time.time,
    ) -> None:
        self._project_dir = Path(project_dir).resolve()
        self._lock_dir = lock_dir if lock_dir is not None else default_lock_dir()
        self._scan = scan
        self._clock = clock
        self._scan_cache: tuple[float, list[GameProcess]] | None = None

        name = read_project_name(self._project_dir)
        if name is None:
            # Without a name we cannot tell which other checkouts share user://.
            # Fall back to the path, which only ever contends with itself: no
            # cross-worktree protection, but no false refusals either.
            print(
                f"[godot-mcp] no config/name in {self._project_dir}/project.godot; "
                "the game-run slot will not protect against other worktrees",
                file=sys.stderr,
            )
            self._domain = str(self._project_dir)
        else:
            self._domain = name

    @property
    def lock_path(self) -> Path:
        slug = _UNSAFE_IN_FILENAME.sub("-", self._domain).strip("-") or "unnamed"
        return self._lock_dir / f"{slug}.game.lock"

    # -- process table ----------------------------------------------------

    def _games(self, *, fresh: bool = False) -> list[GameProcess]:
        now = self._clock()
        if not fresh and self._scan_cache is not None:
            scanned_at, cached = self._scan_cache
            if now - scanned_at < SCAN_TTL_SECONDS:
                return cached
        games = self._scan()
        self._scan_cache = (now, games)
        return games

    def _shares_user_dir(self, project_dir: Path) -> bool:
        """Would a game in `project_dir` collide with ours in user://?"""
        if project_dir == self._project_dir:
            return True
        other = read_project_name(project_dir)
        return other is not None and other == self._domain

    def foreign_game(self, *, fresh: bool = False) -> GameProcess | None:
        """A running game from a different checkout that shares our user://."""
        for game in self._games(fresh=fresh):
            if game.project_dir != self._project_dir and self._shares_user_dir(game.project_dir):
                return game
        return None

    def our_game(self, *, fresh: bool = False) -> GameProcess | None:
        for game in self._games(fresh=fresh):
            if game.project_dir == self._project_dir:
                return game
        return None

    # -- the lock ---------------------------------------------------------

    def _read_lock(self) -> SlotHolder | None:
        try:
            raw = json.loads(self.lock_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        try:
            pid = raw.get("game_pid")
            return SlotHolder(
                project_dir=Path(raw["project_dir"]),
                game_pid=int(pid) if pid is not None else None,
                acquired_at=float(raw.get("acquired_at", 0.0)),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def _write_lock(self, holder: SlotHolder) -> None:
        self.lock_path.write_text(
            json.dumps(
                {
                    "project_dir": str(holder.project_dir),
                    "game_pid": holder.game_pid,
                    "acquired_at": holder.acquired_at,
                }
            ),
            encoding="utf-8",
        )

    def _is_debris(self, holder: SlotHolder) -> bool:
        """A held slot whose game is not actually running."""
        games = self._games(fresh=True)
        if holder.game_pid is not None:
            return not any(game.pid == holder.game_pid for game in games)
        # Never confirmed a pid. Real if a game for that checkout exists, and
        # otherwise only after the grace period — a launch takes time.
        if any(game.project_dir == holder.project_dir for game in games):
            return False
        return (self._clock() - holder.acquired_at) > UNCONFIRMED_GRACE_SECONDS

    def acquire(self) -> SlotHolder | GameProcess | None:
        """Claim the slot, or return whatever already has it.

        Returns None on success. Returning the blocker rather than raising
        keeps the caller's error message specific about who is in the way.
        """
        # The lock alone is not enough to decide this. Only play_scene claims
        # it, so a game started any other way — F5 in the architect's own
        # editor, or `godot --path … -- --driver=… --write-movie` from a shell —
        # holds no lock at all while sharing user:// exactly the same way.
        # Checking the process table first is what makes those routes count.
        foreign = self.foreign_game(fresh=True)
        if foreign is not None:
            return foreign

        self._lock_dir.mkdir(parents=True, exist_ok=True)
        holder = SlotHolder(
            project_dir=self._project_dir, game_pid=None, acquired_at=self._clock()
        )
        try:
            fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            existing = self._read_lock()
            if existing is not None and existing.project_dir == self._project_dir:
                # Ours already — re-running is not a conflict.
                return None
            if existing is None or self._is_debris(existing):
                # Reclaim and retry once. A competing reclaim loses the O_EXCL
                # race below rather than both proceeding.
                try:
                    self.lock_path.unlink()
                except OSError as e:
                    if e.errno != errno.ENOENT:
                        return existing
                return self._acquire_after_reclaim(holder)
            return existing
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "project_dir": str(holder.project_dir),
                        "game_pid": None,
                        "acquired_at": holder.acquired_at,
                    },
                    handle,
                )
        except OSError:
            self.release()
            raise
        return None

    def _acquire_after_reclaim(self, holder: SlotHolder) -> SlotHolder | None:
        try:
            fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            # Somebody else reclaimed it first and is starting their game.
            return self._read_lock()
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "project_dir": str(holder.project_dir),
                    "game_pid": None,
                    "acquired_at": holder.acquired_at,
                },
                handle,
            )
        return None

    def confirm_started(self) -> int | None:
        """Record the pid of the game we just launched, if it is up yet."""
        game = self.our_game(fresh=True)
        if game is None:
            return None
        holder = self._read_lock()
        if holder is None or holder.project_dir != self._project_dir:
            return game.pid
        self._write_lock(
            SlotHolder(
                project_dir=holder.project_dir,
                game_pid=game.pid,
                acquired_at=holder.acquired_at,
            )
        )
        return game.pid

    def release(self) -> bool:
        """Give up the slot. Only ever removes our own lock."""
        holder = self._read_lock()
        if holder is not None and holder.project_dir != self._project_dir:
            return False
        try:
            self.lock_path.unlink()
        except OSError as e:
            if e.errno != errno.ENOENT:
                return False
        return True

    # -- reporting --------------------------------------------------------

    def blocker(self) -> SlotHolder | GameProcess | None:
        """What is standing in the way of us running the game, if anything.

        Checks the process table as well as the lock so a game started from the
        editor's own UI — never touching MCP — is still detected.
        """
        foreign = self.foreign_game(fresh=True)
        if foreign is not None:
            return foreign
        holder = self._read_lock()
        if holder is None or holder.project_dir == self._project_dir:
            return None
        if self._is_debris(holder):
            return None
        return holder

    def status(self) -> dict:
        holder = self._read_lock()
        games = self._games(fresh=True)
        stale = holder is not None and self._is_debris(holder)
        return {
            "project_dir": str(self._project_dir),
            "shared_user_dir_key": self._domain,
            "lock_path": str(self.lock_path),
            "held_by": None
            if holder is None
            else {
                "project_dir": str(holder.project_dir),
                "game_pid": holder.game_pid,
                "held_for_seconds": round(self._clock() - holder.acquired_at, 1),
                "is_ours": holder.project_dir == self._project_dir,
                "stale": stale,
            },
            "running_games": [
                {
                    "pid": game.pid,
                    "project_dir": str(game.project_dir),
                    "is_ours": game.project_dir == self._project_dir,
                    "shares_our_user_dir": self._shares_user_dir(game.project_dir),
                }
                for game in games
            ],
            "available": self.blocker() is None,
        }
