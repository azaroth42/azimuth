import asyncio
import copy
import functools
import importlib
import logging
import re
import time

from rich import print
from werkzeug.security import check_password_hash, generate_password_hash

from azimuth.command_decorator import commands

from . import entities
from .classfactory import ClassFactory

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- out-of-band state cadence (see OOB-PROTOCOL.md 3.3, 8) ---------------
# How long a section may go without being sent before the periodic pass
# refreshes it.  Change-driven pushes ignore volatile fields (below), so this
# is what keeps them from drifting -- and it is the repair path for a client
# that missed an update.
STATE_RESYNC_SECONDS = 60.0
# How often the background pass looks; a section is therefore refreshed
# somewhere between STATE_RESYNC_SECONDS and STATE_RESYNC_SECONDS + this.
STATE_RESYNC_TICK_SECONDS = 30.0

# Fields inside a state section that move on their own (wall clock, not player
# action).  `seen` is last_active_time, and it changes on *every* command by
# *any* player -- so including it in the change comparison made the `players`
# section permanently unequal, and every movement pushed the whole roster to
# every connected client.  The client re-renders relative ages locally on a 1s
# tick (OOB-PROTOCOL.md 3.3), so these never need a push of their own: they
# ride along on the next real change, or on the periodic resync.
VOLATILE_STATE_FIELDS = ("seen",)


def stable_state(value):
    """A state section with its volatile fields stripped, for change
    detection only.  What gets *sent* is always the full section."""
    if isinstance(value, dict):
        return {
            k: stable_state(v)
            for k, v in value.items()
            if k not in VOLATILE_STATE_FIELDS
        }
    if isinstance(value, list):
        return [stable_state(v) for v in value]
    return value

# Azimuth
# Azaroth's Intelligent MultiUser Textual Habitat


class World:
    def __init__(self, db, world_id):
        # sid: object_id
        self.id = world_id
        self.active_sids = {}
        # object_id: object
        self.active_objects = {}
        self.db = db
        self.config = None
        self.players = {}
        self.motd = "Welcome to Azimuth"
        self.config = None
        self.commands = []
        self.default_commands = {}
        self.default_messages = {
            "fail_visible": "You can't see anything like that here.",
            "fail_command_match": "I don't understand that.",
        }
        self.socketio = None  # Will be injected by main.py

        # Python source kept in the database and compiled onto composed
        # classes (see classfactory.py).  Loaded before the factory can
        # resolve anything, because resolution is what attaches them.
        self.class_verbs = self.db.load(f"{world_id}_classes") or {}
        self.class_verbs.pop("id", None)
        self.class_verbs.pop("class", None)

        # base + mixins -> class, composed at init (see classfactory.py)
        self.classes = ClassFactory(self)
        # Teach the storage layer how to evaluate a `clss` filter now that a
        # stored object records base + mixins rather than a class name.
        self.db.class_matcher = self.class_matches

        # Out-of-band state channel (see OOB-PROTOCOL.md)
        self.oob_sids = set()   # sids that hello'd OOB capability over `data`
        self.state_dirty = {}   # player_id -> set of section names to re-check
        self.state_last = {}    # player_id -> last sections actually sent
        self.state_sent_at = {}  # player_id -> {section: monotonic time sent}
        self._state_seq = {}    # sid -> monotonically increasing seq counter
        self._last_resync = {}  # sid -> monotonic time of last resync

        self.exit_names = {
            "n": "north",
            "s": "south",
            "e": "east",
            "w": "west",
            "ne": "northeast",
            "nw": "northwest",
            "se": "southeast",
            "sw": "southwest",
            "up": "up",
            "down": "down",
        }

        world_config = self.load(self.id)
        if world_config is not None:
            self.config = world_config
        # pre-cache all username:object_ids
        players = self.db.load(f"{self.id}_players")
        if players is not None:
            try:
                del players["id"]
                del players["class"]
            except Exception:
                pass
            self.players = players

    def compose(self, class_name):
        """The composed class for a class name, expanding a hand-written
        composite name into its base + mixins.  The way code outside
        make_instance should reach a class, so it picks up this world's
        stored verbs (see classfactory.ClassFactory.attach_verbs)."""
        return self.classes.resolve(*self.classes.split_name(class_name))

    def class_matches(self, data, clss):
        """Does a stored object dict satisfy a `clss` filter?

        Composes the object's class and asks issubclass, so a filter for
        `Object` matches a Container and a filter for `Container` matches
        anything stored as Object + Containable, however it was written down.
        Installed on the storage backend (see Storage.matches_class); the
        backend's own default is plain name equality.
        """
        if clss is None:
            return True
        if not data or "class" not in data:
            return False  # the players file and other class-less documents
        try:
            cls = self.classes.resolve_from_data(data)
        except Exception as e:
            logger.warning(f"cannot compose class for {data.get('id')}: {e}")
            return False
        return cls is not None and issubclass(cls, clss)

    def import_class(self, objectType):
        if not objectType:
            return None
        if "." not in objectType:
            objectType = f"azimuth.entities.{objectType}"
        (modName, className) = objectType.rsplit(".", 1)

        try:
            m = importlib.import_module(modName)
        except ModuleNotFoundError as mnfe:
            logger.critical(f"Could not find module {modName}: {mnfe}")
        except Exception as e:
            logger.critical(f"Failed to import {modName}: {e}")
        try:
            parentClass = getattr(m, className)
        except AttributeError:
            raise
        return parentClass

    def get_commands(self, match=None, allow_cached=True):
        return {}

    def register_commands(self):
        commands.register_commands()

    def register_active(self, obj):
        self.active_objects[obj.id] = obj

    def persist_class_verbs(self):
        """Write the stored-verb record back (mirrors persist_players)."""
        rec = copy.deepcopy(self.class_verbs)
        rec["id"] = f"{self.id}_classes"
        self.save(rec)

    def reload_class(self, name):
        """Forget a composed class and rebuild every live instance of it, so
        an edited stored verb takes effect without restarting the server.
        Returns the number of instances rebuilt."""
        self.classes.registry.pop(name, None)
        victims = [
            o for o in list(self.active_objects.values())
            if type(o).__name__ == name
        ]
        for obj in victims:
            self.rebuild_instance(obj, obj.to_dict())
        return len(victims)

    def persist_players(self):
        # for now write players to JSON file
        players = copy.deepcopy(self.players)
        players["id"] = f"{self.id}_players"
        self.save(players)

    def make_instance(self, data, recursive=True):
        # The class comes from the factory, which handles both stored forms:
        # {"class": base, "mixins": [...]} and a legacy composite name.
        clss = self.classes.resolve_from_data(data)
        if clss is None:
            raise ValueError(f"Unknown class {data['class']!r} for {data.get('id')}")
        id = data["id"]
        instance = clss(id, self, data, recursive)
        self.active_objects[id] = instance
        return instance

    def load(self, id, recursive=True):
        # fetch entity from persistence layer
        data = self.db.load(id)
        # bootstrap it up from class name in dict
        if data and "class" in data:
            return self.make_instance(data, recursive)
        else:
            return data

    def save(self, data):
        self.db.save(data)

    def dump_database(self):
        for o in self.active_objects.values():
            if isinstance(o, entities.Player):
                o.last_location = o.location
            o._save()

    def get_object(self, id, recursive=True):
        if id is None:
            return None
        elif id in self.active_objects:
            return self.active_objects[id]
        else:
            return self.load(id, recursive)

    def get_object_by_name(self, name, clss=None):
        for what in self.active_objects.values():
            if (clss is None or isinstance(what, clss)) and what.match_object(
                name, None
            ):
                return what
        # Persistence layer might be able to search too
        data = self.db.get_object_by_name(name, clss)
        if data:
            return self.make_instance(data)
        else:
            return data

    def get_all_objects(self, clss=None):
        return [self.make_instance(o) for o in self.db.get_all_objects(clss)]

    def get_object_by_id(self, id, clss=None):
        if not id:
            return None
        elif id in self.active_objects:
            return self.active_objects[id]
        else:
            # search active objects for startswith(id).  The class filter has
            # to apply here too -- this branch used to ignore it, so a #ref
            # could resolve to an object of the wrong class purely by being in
            # the cache while the db branch below would have rejected it.
            for what, obj in self.active_objects.items():
                if what.startswith(id) and (clss is None or isinstance(obj, clss)):
                    return obj
        # Persistence layer might be able to search too
        data = self.db.get_object_by_id(id, clss)
        if data:
            return self.make_instance(data)
        else:
            return data

    # Attributes across entities/mixins that hold a direct reference to
    # another live object.  When an instance is rebuilt under a different
    # composed class its identity (the id) is unchanged, but every one of
    # these still points at the dead instance and has to be reseated.
    BACKREF_ATTRS = (
        "held_by",
        "worn_by",
        "_position_parent",
        "open_paired_object",
        "on_paired_object",
        "lock_paired_object",
        "locked_by_object",
        "locked_by_player",
        "destination",
        "source",
        "home",
        "last_location",
        # An Enterable and its Interior point at each other; rebuilding either
        # would otherwise leave the other talking to the dead instance, and a
        # car whose interior still names the old car cannot be got out of.
        "interior",
        "outside",
    )

    def rebuild_instance(self, obj, data):
        """Replace a live object with a fresh instance of a different composed
        class, keeping its id and its place in the world graph.

        Backs @chparent / @addmixin / @rmmixin.  The old code for this dropped
        the instance from active_objects and reloaded it, which left the dead
        instance sitting in its room's `contents` (the room then listed the
        thing twice) and left every held_by / paired-object reference pointing
        at it.  Detaching first and reseating afterwards is what makes the
        swap safe.
        """
        oid = obj.id
        where = obj.location
        obj.move_to(None)  # leave the room's contents; drop any position held
        data["id"] = oid
        data["location"] = where.id if where else None
        self.active_objects.pop(oid, None)
        self.save(data)
        new = self.load(oid)
        if new is None:
            raise ValueError(f"could not rebuild {oid}")
        self.reseat_references(obj, new)
        self.mark_thing_changed(new)
        return new

    def reseat_references(self, old, new):
        """Point every live reference to *old* at *new* instead."""
        for other in list(self.active_objects.values()):
            if other is new:
                continue
            for attr in self.BACKREF_ATTRS:
                if getattr(other, attr, None) is old:
                    setattr(other, attr, new)
            for entries in (getattr(other, "positioned", None) or {}).values():
                for e in entries:
                    if e[0] is old:
                        e[0] = new
            exits = getattr(other, "exits", None)
            if exits:
                for k, v in list(exits.items()):
                    if v is old:
                        exits[k] = new

    def call_async_partial(self, func):
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(func())
            else:
                loop.run_until_complete(func())
        except Exception:
            pass  # Fail silently if async context issues

    def emit(self, event, data, to=None):
        # Simple emit - handle async in background
        if self.socketio is None:
            return
        func = functools.partial(self.socketio.emit, event, data, to)
        self.call_async_partial(func)

    def tell_player(self, who, msg):
        if msg.endswith("\n"):
            msg = msg[:-1]
        self.emit("message", msg, to=who.connection)

    def disconnect_player(self, who):
        func = functools.partial(self.socketio.disconnect, who.connection)
        self.call_async_partial(func)

    # ------------------------------------------------------------------
    # Out-of-band state channel (see OOB-PROTOCOL.md)
    #
    # Sections: room / inventory / players.  Mutations mark sections dirty
    # (conservatively); flush_state() recomputes, diffs against the last
    # sent snapshot, and emits one coalesced `state` event per affected
    # player.  Only clients that hello'd OOB capability (oob_sids) receive
    # any of it.
    # ------------------------------------------------------------------

    def state_self(self, p):
        s = {
            "id": p.id,
            "name": p.name,
            "username": p.username,
            "verbs": p.verbs_summary(p, include_argless=True),
        }
        pos = p.find_position()
        if pos is not None:
            parent, ppos, verb = pos
            s["position"] = (
                f"{parent.posture_ing(verb)} " if verb else ""
            ) + f"{ppos} the {parent.name}"
        return s

    def state_room(self, p):
        loc = p.location
        if not isinstance(loc, entities.Place):
            return None
        # Inside a vehicle, the ways out are the ones the *vehicle* can take.
        # Without this a driver's panel is empty and there is nothing to click
        # (Exit.use drives when you are aboard, so clicking one works).
        ways_out = getattr(loc, "outside_room", None)
        ways_out = ways_out() if ways_out is not None else None
        if ways_out is None:
            ways_out = loc
        return {
            "id": loc.id,
            "name": loc.name,
            "exits": [
                e.thing_summary(p) for e in ways_out.exits.values() if p.can_see(e)
            ],
            "things": [
                x.thing_summary(p)
                for x in loc.contents
                if x is not p and p.can_see(x)
            ],
        }

    def state_inventory(self, p):
        return [x.thing_summary(p) for x in p.contents if p.can_see(x)]

    def state_players(self, viewer):
        out = []
        for pid in self.active_sids.values():
            pl = self.active_objects.get(pid)
            if pl is None:
                continue
            loc = pl.location
            out.append(
                {
                    "id": pl.id,
                    "name": pl.name,
                    "loc": loc.name if isinstance(loc, entities.Place) else None,
                    "seen": int(pl.last_active_time),
                    "self": pl is viewer,
                }
            )
        return out

    # --- dirty marking (superset is fine: the diff at flush time is exact) ---

    def mark_room_dirty(self, place):
        if place is None:
            return
        for c in place.contents:
            if isinstance(c, entities.Player) and c.connection:
                self.state_dirty.setdefault(c.id, set()).add("room")

    def mark_inventory_dirty(self, player):
        if player is not None and player.connection:
            self.state_dirty.setdefault(player.id, set()).add("inventory")

    def mark_players_dirty(self):
        for pid in self.active_sids.values():
            self.state_dirty.setdefault(pid, set()).add("players")

    def mark_moved(self, thing, old, new):
        for loc in (old, new):
            if isinstance(loc, entities.Place):
                self.mark_room_dirty(loc)
            elif isinstance(loc, entities.Player):
                self.mark_inventory_dirty(loc)
        if isinstance(thing, entities.Player):
            self.mark_players_dirty()

    def mark_thing_changed(self, thing):
        if isinstance(thing, entities.Exit):
            if thing.source is not None:
                self.mark_room_dirty(thing.source)
            return
        loc = thing.location
        if isinstance(loc, entities.Place):
            self.mark_room_dirty(loc)
        elif isinstance(loc, entities.Player):
            self.mark_inventory_dirty(loc)
            # A thing's held/worn state also shows in the holder's summary,
            # which other players see as part of the room; mark the room too
            # so they refresh (superset is fine -- the flush diff is exact).
            room = loc.location
            if isinstance(room, entities.Place):
                self.mark_room_dirty(room)

    # --- emission ---

    def _next_seq(self, sid):
        n = self._state_seq.get(sid, 0) + 1
        self._state_seq[sid] = n
        return n

    def push_init(self, player):
        """Full snapshot (kind=init) to a player, if their client is OOB."""
        sid = player.connection
        if not sid or sid not in self.oob_sids:
            return
        payload = {
            "v": 1,
            "kind": "init",
            "seq": self._next_seq(sid),
            "self": self.state_self(player),
            "room": self.state_room(player),
            "inventory": self.state_inventory(player),
            "players": self.state_players(player),
        }
        self.state_last[player.id] = {
            "room": payload["room"],
            "inventory": payload["inventory"],
            "players": payload["players"],
        }
        now = time.monotonic()
        self.state_sent_at[player.id] = {
            k: now for k in ("room", "inventory", "players")
        }
        self.emit("state", payload, to=sid)

    def flush_state(self):
        """Recompute every marked section; emit one update per player whose
        state actually changed.  Safe to call when nothing is dirty.

        A section is sent when its *stable* content changed (see
        stable_state).  A difference confined to volatile fields is not worth
        a packet on its own -- with many players `seen` alone would have every
        movement push the full roster to every client -- so it waits for the
        next real change or for the periodic pass, whichever comes first.
        """
        if not self.state_dirty:
            return
        now = time.monotonic()
        for pid, sections in self.state_dirty.items():
            p = self.active_objects.get(pid)
            sid = p.connection if p is not None else None
            if not sid or sid not in self.oob_sids:
                continue
            last = self.state_last.setdefault(pid, {})
            sent_at = self.state_sent_at.setdefault(pid, {})
            payload = {"v": 1, "kind": "update"}
            changed = False
            for name in sections:
                fresh = getattr(self, f"state_{name}")(p)
                if fresh is None:
                    continue
                previous = last.get(name)
                if fresh == previous:
                    continue
                if stable_state(fresh) == stable_state(previous) and (
                    now - sent_at.get(name, 0.0) < STATE_RESYNC_SECONDS
                ):
                    continue  # volatile drift only, and recently sent
                payload[name] = fresh
                last[name] = fresh
                sent_at[name] = now
                changed = True
            if changed:
                # Allocate the sequence number only now.  It used to be taken
                # when the payload was built, so a flush that sent nothing
                # still burned one -- and the client's gap check
                # (OOB-PROTOCOL.md 8: seq > last_seq + 1 triggers a resync)
                # would read the next real update as a dropped event.
                payload["seq"] = self._next_seq(sid)
                self.emit("state", payload, to=sid)
        self.state_dirty.clear()

    def periodic_resync(self):
        """Refresh any section a client has not been sent for
        STATE_RESYNC_SECONDS.  Driven by a background task (see main.py),
        because change-driven pushes deliberately skip volatile-only
        differences and an idle world marks nothing dirty at all.

        Marks the stale sections and defers to flush_state, so this cannot
        emit anything flush_state would not: unchanged sections stay silent.
        Returns the number of players considered.
        """
        now = time.monotonic()
        considered = 0
        for sid in list(self.oob_sids):
            pid = self.active_sids.get(sid)
            p = self.active_objects.get(pid) if pid else None
            if p is None:
                continue
            considered += 1
            sent_at = self.state_sent_at.setdefault(p.id, {})
            for name in ("room", "inventory", "players"):
                if now - sent_at.get(name, 0.0) >= STATE_RESYNC_SECONDS:
                    self.state_dirty.setdefault(p.id, set()).add(name)
        self.flush_state()
        return considered

    def handle_register(self, sid, data):
        """Handles player registration."""
        username = data.get("username")
        password = data.get("password")

        if not username or not password:
            return "Registration requires both username and password, please try again."

        # Check if username is already registered using the Redis set
        if username in self.players:
            return (
                f"Username '{username}' is already taken, please try another username."
            )

        if username in ["id", "class"]:
            return f"Username '{username}' is invalid, please try again."

        # --- Create Player Entity ---
        # Player constructor saves the basic entity to Redis
        password_hash = generate_password_hash(password)
        try:
            new_player = entities.Player(
                None,
                self,
                {
                    "name": username,
                    "username": username,
                    "password_hash": password_hash,
                },
            )  # Creates entity with ID
            new_player.password_hash = password_hash
            new_player.username = username
            new_player.last_location = self.get_object(self.config["start_room_id"])
            new_player._save()
            self.players[username] = new_player.id
            self.persist_players()

            print(f"Registered new user: {username} (ID: {new_player.id})")
            self.login(sid, new_player)
            return "Registration successful!"
        except:
            raise
            return "Registration failed due to a server error storing metadata."

    def handle_login(self, sid, data):
        """Handles player login."""
        username = data.get("username")
        password = data.get("password")

        if not username or not password:
            return "Login requires both username and password."

        # Check if already logged in with this sid (HOW??)
        if sid in self.active_sids:
            return "Already logged in?!"

        # Check if user is registered in players
        player_id = self.players.get(username)
        if not player_id:
            return "Username and password do not match."

        player = self.load(player_id)
        if not isinstance(player, entities.Player):
            return (
                f"Username is not associated with a player object: {player.__class__}"
            )

        # Check if password matches
        if not check_password_hash(player.password_hash, password):
            return "Username and password do not match."

        for ap in self.active_sids.values():
            if ap == player_id:
                return f"{username} is already logged in."
                # FIXME: kick the other copy out?

        # --- Login successful ---
        print(f"Player '{username}' attempting login with SID {sid}")
        self.login(sid, player)

    def login(self, sid, player):
        player.connection = sid
        self.active_sids[sid] = player.id
        self.active_objects[player.id] = player

        where = player.last_location
        if where is None:
            where_id = self.config["start_room_id"]
            where = self.get_object(where_id)
        elif type(where) is str:
            where = self.get_object(where)
        player.move_to(where)
        player.tell(f"Welcome back, {player.name}!")
        self.push_init(player)
        self.mark_players_dirty()
        self.flush_state()

    def on_disconnect(self, sid):
        player_id = self.active_sids.get(sid)  # Find player ID from active session map
        player = self.active_objects[player_id]

        # Announce disconnect
        player.location.announce_all_but(f"{player.name} has disconnected", [player])
        player.last_location = player.location
        player.move_to(None)

        # Remove from active caches
        player.connection = None
        del self.active_sids[sid]
        del self.active_objects[player_id]
        player._save()

        # OOB: the world just changed for everyone who was watching it.
        self.oob_sids.discard(sid)
        self._state_seq.pop(sid, None)
        self.state_last.pop(player_id, None)
        self.state_sent_at.pop(player_id, None)
        self.mark_players_dirty()
        self.flush_state()

    def process_player_command(self, player_id, argstr):
        player = self.active_objects.get(player_id, None)
        if player is None:
            # ????!
            return

        player.last_active_time = time.time()
        ch0 = argstr[0]
        words = argstr.split()
        w1 = words[0]

        if argstr in self.exit_names:
            argstr = self.exit_names[argstr]

        if ch0 in ["'", '"']:
            player.say(argstr[1:])
        elif w1 == "say":
            player.say(argstr[4:].strip())
        elif ch0 in [":", ";"]:
            player.emote(argstr[1:])
        elif w1 == "emote":
            player.emote(argstr[6:].strip())
        elif ch0 == "|":
            player.eval(argstr[1:])
        elif w1 == "eval":
            player.eval(argstr[5:].strip())
        elif argstr in self.exit_names.values():
            # Riding something?  A bare direction drives it.  You don't step
            # off the bicycle to walk north, and a car's interior has no exits
            # of its own to walk through in the first place.
            vehicle = player.current_vehicle()
            if vehicle is not None:
                vehicle.drive_direction(player, argstr)
            else:
                exit = player.location.exits.get(argstr, None)
                if exit is not None:
                    exit.use(player)
                else:
                    player.tell("There is no such exit here")
        else:
            argstr = argstr.replace(w1, "", 1).strip()

            # The vehicle you are riding comes first among the objects: it
            # puts a car within reach from inside (it is in the room, not in
            # the interior you are standing in), and it makes `drive north`
            # reach *your* vehicle rather than whichever one is parked
            # nearest.
            vehicle = player.current_vehicle()
            here = player.location
            # Inside a vehicle, what is out of the window is addressable too:
            # its exits (so `go north` and a panel click drive the vehicle
            # through them) and its contents (so you can look at what you are
            # driving past).  Reaching is still gated by okay_for_verb, which
            # compares locations -- you cannot pick up a kerbstone from the
            # driving seat.
            outside_room = getattr(here, "outside_room", None)
            outside_room = outside_room() if outside_room is not None else None
            beyond = (
                [*outside_room.contents, *outside_room.exits.values()]
                if outside_room is not None
                else []
            )
            search_order = [
                player,
                here,
                *([vehicle] if vehicle is not None else []),
                *player.contents,
                *here.contents,
                *here.exits.values(),
                *beyond,
                self,
            ]

            for s in search_order:
                cmds = s.get_commands(w1)
                # Try entries deepest-first: get_commands merges class commands
                # shallowest-ancestor-first, and the first structural match
                # wins, so the list must be walked in reverse. Otherwise a
                # generic ancestor handler (e.g. Exit.use, Openable.open) runs
                # before the specialized override (OpenableExit.use's closed
                # check, Lockable.open's lock check) -- a locked door would
                # open, and a closed door would be walkable.
                for c in reversed(cmds.get(w1, [])):
                    if len(words) == 1 and not any([c["dobj"], c["prep"], c["iobj"]]):
                        c["func"](s, player, prep=None, verb=w1)
                        return
                    elif len(words) == 1:
                        continue
                    elif c["prep"] is not None:
                        for p in c["prep"]:
                            # Split on the preposition as a whole word, so
                            # 'put gong on long bong' splits sanely AND a
                            # preposition that leads the arguments
                            # ('look at wizard' -> 'at wizard') is handled.
                            bits = re.split(
                                r"(?:^|\s+)" + re.escape(p) + r"(?:\s+|$)",
                                argstr,
                            )
                            if len(bits) != 2:
                                continue
                            (d, i) = bits
                            d = d.strip()
                            i = i.strip()
                            if (not c["dobj"] and d) or (c["dobj"] and not d):
                                continue
                            elif (not c["iobj"] and i) or (c["iobj"] and not i):
                                continue
                            else:
                                if s == player:
                                    # allow any * any
                                    if (
                                        c["dobj"] == "any"
                                        and d
                                        and c["iobj"] == "any"
                                        and i
                                    ):
                                        c["func"](s, player, d, i, prep=p, verb=w1)
                                        return
                                if c["dobj"] == "self":
                                    if not s.match_object(d, player):
                                        continue
                                    else:
                                        c["func"](s, player, i, prep=p, verb=w1)
                                        return
                                if c["iobj"] == "self":
                                    if not s.match_object(i, player):
                                        continue
                                    else:
                                        if not d:
                                            c["func"](s, player, prep=p, verb=w1)
                                            return
                                        else:
                                            c["func"](s, player, d, prep=p, verb=w1)
                                            return
                    else:
                        # no prep, so no iobj
                        # and also not none ... so must be dobj
                        if c["dobj"] == "self" and s.match_object(
                            argstr, player, verb=w1
                        ):
                            c["func"](s, player, prep=None, verb=w1)
                            return
                        elif c["dobj"] == "any":
                            c["func"](s, player, argstr, prep=None, verb=w1)
                            return
            player.tell(player.get_message("fail_command_match", player))

            ### meta
            # @set, @prop, @func
            ### objects
            # furniture (sit at/on, stand/leave, say to table)
            ### MUD type commands
            # hold/wield/wear / stow/unwear
            # attack, cast, shoot
            # eat/drink/consume


# --- Game World Creation / Initialization ---
def setup_world(db, world_id):
    """Checks if the world exists and creates it if not."""

    world = World(db, world_id)
    # register_commands() first: it resets default_commands on every class that
    # declares decorated verbs, so the factory (which may attach stored verbs
    # to a class) has to run after it.
    world.register_commands()
    world.classes.build_from_database()
    if world.config is not None:
        return world
    else:
        # Initialize Simple World for now

        print("Initializing game world...")

        # constructor is (id, world, data, recursive)
        # main data fields: name, description, location, contents

        from .entities import (
            Container,
            EdibleThing,
            Exit,
            HeldObject,
            Object,
            Place,
            Programmer,
            WearableObject,
        )

        # Places - Keep track of IDs for linking
        start_room = Place(
            None,
            world,
            {
                "name": "The Starting Chamber",
                "description": "A small, damp stone chamber. It feels like the beginning of an adventure.",
                "coordinates": [0, 0, 0],
            },
        )

        wizard = Programmer(
            None,
            world,
            {
                "name": "wizard",
                "description": "A wise old wizard.",
                "username": "wizard",
                "password_hash": "scrypt:32768:8:1$NbFEqSQjMVUSFVTf$66107ca8d22a1eca7c2b338d7ece787f682cfec7274ea7594cae37027029bd3b7cbad6583c76df1e05ca8d8f465313e5246c6067c62ba17443b2ba5d624792da",
            },
        )

        hallway = Place(
            None,
            world,
            {
                "name": "Narrow Hallway",
                "description": "A dark, narrow hallway stretching north and south.",
                "coordinates": [0, 1, 0],
            },
        )
        treasure_room = Place(
            None,
            world,
            {
                "name": "Glittering Cave",
                "description": "A small cave sparkling with veins of quartz. A sturdy chest sits here.",
                "coordinates": [1, 1, 0],
            },
        )

        # Objects - Place them using location_id in constructor
        sword = HeldObject(
            None,
            world,
            {
                "name": "rusty sword",
                "description": "A simple sword, pitted with rust.",
                "location": start_room.id,
            },
        )
        armor = WearableObject(
            None,
            world,
            {
                "name": "chainmail armor",
                "description": "A sturdy chainmail armor.",
                "location": start_room.id,
            },
        )

        key = Object(
            None,
            world,
            {
                "name": "iron key",
                "description": "A heavy iron key.",
                "location": hallway.id,
            },
        )
        bread = EdibleThing(
            None,
            world,
            {
                "name": "loaf of bread",
                "description": "A crusty loaf of bread. Looks edible.",
                "location": start_room.id,
            },
        )

        # Containers
        chest = Container(
            None,
            world,
            {
                "name": "sturdy chest",
                "description": "A solid wooden chest bound with iron.",
                "location": treasure_room.id,
            },
        )
        gem = Object(
            None,
            world,
            {
                "name": "shiny gem",
                "description": "A brightly shining gemstone.",
                "location": chest.id,
            },
        )

        # Exits (Name, Description, Source ID, Destination ID)
        # Constructor automatically adds exit to source place in Redis
        n = Exit(
            None,
            world,
            {
                "name": "north",
                "description": "A dark opening leads north.",
                "source": start_room.id,
                "destination": hallway.id,
            },
        )
        s = Exit(
            None,
            world,
            {
                "name": "south",
                "description": "An archway leads back south.",
                "source": hallway.id,
                "destination": start_room.id,
            },
        )
        e = Exit(
            None,
            world,
            {
                "name": "east",
                "description": "A narrow passage leads east.",
                "source": hallway.id,
                "destination": treasure_room.id,
            },
        )
        w = Exit(
            None,
            world,
            {
                "name": "west",
                "description": "A passage leads back west.",
                "source": treasure_room.id,
                "destination": hallway.id,
            },
        )

        for what in [
            start_room,
            wizard,
            hallway,
            treasure_room,
            sword,
            armor,
            key,
            bread,
            chest,
            gem,
            n,
            s,
            e,
            w,
        ]:
            what._save()

        config = {"id": world_id, "start_room_id": start_room.id}
        world.save(config)
        world.players[wizard.username] = wizard.id
        world.persist_players()
        world.config = config
        return world
