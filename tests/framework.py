"""
Azimuth in-process test framework.

Run MUD commands directly against a World -- no server, no FastAPI, no
uvicorn, no Socket.IO, no separate process. This generalizes the in-process
wiring from run-repl.py (db -> world -> player) into a reusable harness.

Isolation: every TestWorld gets its own throwaway copy of the database in a
temp dir, so tests never touch the real db/. If the real db/ doesn't exist,
setup_world bootstraps a fresh demo world into the temp dir instead.

Quick start (no runner, just poke at a world):

    from tests.framework import TestWorld

    tw = TestWorld()
    wiz = tw.login("wizard", "wizard")
    print(wiz.send("look"))
    print(wiz.send("get sword"))
    print(wiz.inventory())
    tw.clean()

Suite runner: python run-tests.py [name filter]
"""

import contextlib
import importlib
import io
import os
import pkgutil
import shutil
import sys
import tempfile
import time
import traceback
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from azimuth.persistence import SimpleFileStorage
from azimuth.world import setup_world

WORLD_ID = os.getenv("AZIMUTH_WORLD_ID", "WORLD1")
REAL_DB = os.path.join(ROOT, "db")


class FakeSocketIO:
    """Stands in for the injected socketio server: records every emit."""

    def __init__(self):
        self.emitted = []  # (event, data, to)
        self.disconnected = []  # positional args of disconnect()

    def emit(self, event, data, to=None, **kwargs):
        self.emitted.append((event, data, to))

    def disconnect(self, *args, **kwargs):
        self.disconnected.append(args)


class Session:
    """One fake player connection.

    Everything the game sends to this connection (player.tell, room
    announcements, ...) is captured; .send() returns the messages a command
    produced.
    """

    def __init__(self, tw):
        self.tw = tw
        self.sid = f"test-{uuid.uuid4().hex[:12]}"
        self.player = None

    def login(self, username, password):
        """Go through the real World.handle_login. Returns the reply string."""
        reply = self.tw.world.handle_login(
            self.sid, {"username": username, "password": password}
        )
        pid = self.tw.world.active_sids.get(self.sid)
        self.player = self.tw.world.active_objects.get(pid) if pid else None
        return reply

    def register(self, username, password, email="test@example.com"):
        """Go through the real World.handle_register. Returns the reply string."""
        reply = self.tw.world.handle_register(
            self.sid,
            {"username": username, "password": password, "email": email},
        )
        pid = self.tw.world.active_sids.get(self.sid)
        self.player = self.tw.world.active_objects.get(pid) if pid else None
        return reply

    def messages(self):
        """All messages the game has sent to this connection so far."""
        return [
            data
            for (event, data, to) in self.tw.fake.emitted
            if event == "message" and to == self.sid
        ]

    def send(self, command):
        """Run a command as this player; return the messages it produced."""
        if self.player is None:
            raise RuntimeError(f"session {self.sid} is not logged in")
        start = len(self.messages())
        self.tw.world.process_player_command(self.player.id, command)
        return self.messages()[start:]

    # --- convenience queries ---
    @property
    def location_name(self):
        loc = self.player.location if self.player is not None else None
        return loc.name if loc is not None else None

    def inventory(self):
        if self.player is None:
            return []
        return [x.name for x in self.player.contents]

    def close(self):
        if self.sid in self.tw.world.active_sids:
            self.tw.world.on_disconnect(self.sid)


class TestWorld:
    """A fresh world backed by a throwaway copy of the database."""

    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix="azimuth_test_")
        db_dir = os.path.join(self.dir, "db")
        if os.path.isdir(REAL_DB):
            # Seed from the real world so tests see the same rooms/objects/
            # players the user's running world has.
            shutil.copytree(REAL_DB, db_dir, dirs_exist_ok=True)
        else:
            os.makedirs(db_dir, exist_ok=True)
        self.storage = SimpleFileStorage(db_dir)
        self.world = setup_world(self.storage, WORLD_ID)
        # The real server injects an async socketio server; in tests we use
        # the fake, whose methods are sync, so bypass the async plumbing.
        self.fake = FakeSocketIO()
        self.world.socketio = self.fake
        self.world.call_async_partial = lambda func: func()
        self.sessions = []

    def login(self, username, password):
        s = Session(self)
        s.login(username, password)
        self.sessions.append(s)
        return s

    def register(self, username=None, password="secret", email=None):
        """Register a fresh throwaway player. Returns a logged-in Session."""
        username = username or f"tester-{uuid.uuid4().hex[:8]}"
        email = email or f"{username}@example.com"
        s = Session(self)
        s.register(username, password, email)
        self.sessions.append(s)
        return s

    def clean(self):
        shutil.rmtree(self.dir, ignore_errors=True)


class AzimuthTest:
    """Base class for tests. Add test_* methods; each runs on a fresh TestWorld."""

    def __init__(self, tw: TestWorld):
        self.tw = tw
        self.session = None

    def wizard(self) -> Session:
        """Log in (once) as the built-in wizard programmer.

        The wizard is then moved to the start room: the real (copied) world
        remembers whatever room the wizard last played in, and tests assume
        the start room.
        """
        if self.session is None:
            self.session = self.tw.login("wizard", "wizard")
            if self.session.player is not None:
                start = self.tw.world.get_object(
                    self.tw.world.config["start_room_id"]
                )
                if start is not None and self.session.player.location is not start:
                    self.session.player.move_to(start)
        return self.session

    def assert_msg(self, msgs, *want, absent=()):
        """Assert substrings are present (and, with absent=, not present).

        Returns the joined text so tests can do further checks on it.
        """
        text = "\n".join(str(m) for m in msgs)
        for w in want:
            assert w in text, f"expected {w!r} in output:\n{text}"
        for a in absent:
            assert a not in text, f"did not expect {a!r} in output:\n{text}"
        return text

    def place_object(self, name, where=None):
        """Test precondition: move a named object to `where`, wherever it is.

        The test world is a copy of the real db/, which actual play may have
        rearranged (the sword might be in someone's inventory). Tests that
        assume a layout should restore their preconditions with this rather
        than relying on the copied state. `where` may be an object or the
        name of one. Returns the moved object.
        """
        w = self.tw.world
        obj = None
        for o in w.active_objects.values():
            if o.name == name:
                obj = o
                break
        if obj is None:
            for data in w.db.get_all_objects():
                if data.get("name") == name:
                    obj = w.make_instance(data)
        assert obj is not None, f"could not find object {name!r} in test world"
        if where is not None:
            if isinstance(where, str):
                where_name = where
                for o in w.active_objects.values():
                    if o.name == where:
                        where = o
                        break
                else:
                    where = w.get_object_by_name(where)
                assert where is not None, f"could not resolve {where_name!r}"
            obj.move_to(where)
        return obj


def run_tests(pattern=None, keep_db=False):
    """Discover and run every AzimuthTest in the tests package."""
    import tests

    failures = []
    total = 0
    for mod_info in pkgutil.iter_modules(tests.__path__):
        if mod_info.name == "framework":
            continue
        mod = importlib.import_module(f"tests.{mod_info.name}")
        for name, cls in sorted(vars(mod).items()):
            if not (
                isinstance(cls, type)
                and issubclass(cls, AzimuthTest)
                and cls is not AzimuthTest
            ):
                continue
            for mname in sorted(dir(cls)):
                if not mname.startswith("test_"):
                    continue
                label = f"{name}.{mname}"
                if pattern and pattern.lower() not in label.lower():
                    continue
                total += 1
                tw = TestWorld()
                buf = io.StringIO()
                t0 = time.time()
                try:
                    with contextlib.redirect_stdout(buf):
                        getattr(cls(tw), mname)()
                    status = "PASS"
                except Exception:
                    status = "FAIL"
                    failures.append(label)
                finally:
                    if not keep_db:
                        tw.clean()
                dt = time.time() - t0
                print(f"[{status}] {label} ({dt:.2f}s)")
                if status == "FAIL":
                    print(traceback.format_exc())
                    out = buf.getvalue()
                    if out:
                        print("---- game output ----")
                        print(out)

    print(
        f"\n{total - len(failures)}/{total} passed"
        + (f" ({len(failures)} failed)" if failures else "")
    )
    return 1 if failures else 0
