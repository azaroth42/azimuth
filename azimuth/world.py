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

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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

        # Out-of-band state channel (see OOB-PROTOCOL.md)
        self.oob_sids = set()   # sids that hello'd OOB capability over `data`
        self.state_dirty = {}   # player_id -> set of section names to re-check
        self.state_last = {}    # player_id -> last sections actually sent
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

    def persist_players(self):
        # for now write players to JSON file
        players = copy.deepcopy(self.players)
        players["id"] = f"{self.id}_players"
        self.save(players)

    def make_instance(self, data, recursive=True):
        if "." not in data["class"]:
            clss = getattr(entities, data["class"])
        else:
            clss = self.import_class(data["class"])
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
            # search active objects for startswith(id)
            for what in self.active_objects:
                if what.startswith(id):
                    return self.active_objects[what]
        # Persistence layer might be able to search too
        data = self.db.get_object_by_id(id, clss)
        if data:
            return self.make_instance(data)
        else:
            return data

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
        return {
            "id": loc.id,
            "name": loc.name,
            "exits": [
                e.thing_summary(p) for e in loc.exits.values() if p.can_see(e)
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
        self.emit("state", payload, to=sid)

    def flush_state(self):
        """Recompute every marked section; emit one update per player that
        actually changed.  Safe to call when nothing is dirty."""
        if not self.state_dirty:
            return
        for pid, sections in self.state_dirty.items():
            p = self.active_objects.get(pid)
            sid = p.connection if p is not None else None
            if not sid or sid not in self.oob_sids:
                continue
            last = self.state_last.setdefault(pid, {})
            payload = {"v": 1, "kind": "update", "seq": self._next_seq(sid)}
            changed = False
            for name in sections:
                fresh = getattr(self, f"state_{name}")(p)
                if fresh is None:
                    continue
                if fresh != last.get(name):
                    payload[name] = fresh
                    last[name] = fresh
                    changed = True
            if changed:
                self.emit("state", payload, to=sid)
        self.state_dirty.clear()

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
            exit = player.location.exits.get(argstr, None)
            if exit is not None:
                exit.use(player)
            else:
                player.tell("There is no such exit here")
        else:
            argstr = argstr.replace(w1, "", 1).strip()

            search_order = [
                player,
                player.location,
                *player.contents,
                *player.location.contents,
                *player.location.exits.values(),
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
    world.register_commands()
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
