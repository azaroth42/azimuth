import copy
import inspect
import time
import uuid

from werkzeug.security import check_password_hash

from azimuth.command_decorator import make_command
from azimuth.mixins import (
    VEHICLE_LARGE,
    VEHICLE_SMALL,
    Containable,
    Enterable,
    Holdable,
    Lockable,
    Openable,
    Positionable,
    Switchable,
    Vehicle,
    Wearable,
    join_look,
)


# --- Base Class ---
class BaseThing:
    """Base class for all things in the MUD."""

    default_messages = {}

    def __init__(self, id, world, data, recursive=True):
        self.world = world
        self.data = data

        if not id:
            self.id = str(uuid.uuid4())  # Unique identifier
        else:
            self.id = id
        self.world.register_active(self)

        self.name = data.get("name", "Unnamed Object")
        self.aliases = data.get("aliases", [])
        self.description = data.get("description", "")
        self._messages = data.get("messages", {})
        # default_messages is a class property
        # that subclass definitions should set

        self.commands = data.get("commands", {})
        self.properties = data.get("properties", {})
        self.functions = data.get("functions", {})
        self.commands_cached = {}

        location = data.get("location", None)
        self.location = None
        if location is not None:
            loc = world.get_object(data["location"])
            self.move_to(loc)

        self.contents = []
        for c in data.get("contents", []):
            co = world.get_object(c)
            co.move_to(self)

    def __repr__(self):
        return f"<{self.__class__.__name__}: {self.name} ({self.id})>"

    def to_dict(self):
        """Base fields for persistence.  This is the *end* of a cooperative
        chain: every mixin and subclass overrides ``to_dict`` as
        ``data = super().to_dict(); data.update(...)``, and mixins precede the
        entity bases in the MRO, so each contributes its own fields on the way
        down.  (This used to consult a hardcoded mixin tuple with unbound
        calls, because the old mixins-last MRO let BaseThing shadow them.)"""
        cls = self.__class__
        # `cls.__dict__.get`, not attribute access: only the class the factory
        # actually stamped may claim a combination.  A hand-written subclass of
        # a stamped class (class BigChest(OpenableContainer)) would otherwise
        # inherit its ancestor's identity and be persisted *as* that ancestor.
        data = {
            "id": self.id,
            "class": cls.__dict__.get("_az_base", cls.__name__),
            "name": self.name,
            "aliases": self.aliases,
            "description": self.description,
            "location": self.location.id if self.location else None,
            "contents": [x.id for x in self.contents],
            "messages": self._messages,
        }
        mixins = cls.__dict__.get("_az_mixins")
        if mixins:
            data["mixins"] = list(mixins)
        return data

    def _save(self):
        d = self.to_dict()
        self.world.save(d)

    def look_at(self, who):
        """Returns the description of the thing."""
        return self.description

    def contained_look_at(self, who):
        return ""

    def move_to(self, where):
        """Update location and contents"""
        if self.location is not None:
            self.location.contents.remove(self)
            self.location.on_leave(self)
        old = self.location
        # Positioning is a same-room display relation: the moment this thing
        # changes location (walks away, is picked up, is dropped elsewhere)
        # its position relative to a Positionable is over.
        if getattr(self, "_position_parent", None) is not None:
            self._unposition()
        self.location = where
        if self.location is not None:
            self.location.contents.append(self)
            where.on_enter(self)
        # Out-of-band: let the world mark state sections dirty for observers.
        self.world.mark_moved(self, old, where)

    # --- Positioning (display relation to a Positionable; see Positionable) ---

    def find_position(self):
        """(Positionable, position, verb) this thing currently occupies, or
        None.  Derived from the back-reference the parent Positionable keeps
        in its ``positioned`` lists -- that dict is the single source of
        truth, so this costs no extra stored state."""
        parent = getattr(self, "_position_parent", None)
        if parent is None:
            return None
        for pos, entries in parent.positioned.items():
            for t, verb in entries:
                if t is self:
                    return (parent, pos, verb)
        return None

    def _unposition(self):
        """Drop this thing's position, if any (the parent also drops it from
        its lists and re-marks the room dirty)."""
        parent = getattr(self, "_position_parent", None)
        if parent is not None:
            parent.remove_positioned(self)

    def enter_ok(self, what):
        # Can what be moved to self?
        return True

    def leave_ok(self, what):
        # Can what leave self, if present?
        return True

    def on_enter(self, what):
        pass

    def on_leave(self, what):
        pass

    def okay_for_verb(self, verb, player):
        return True

    def match_object(self, name, player, verb=None):
        # does name match this object?
        if verb is not None:
            # Test if we should match for the verb
            if not self.okay_for_verb(verb, player):
                return 0

        if name == "me" and player == self:
            return 1
        elif name == "here" and player and player.location == self:
            return 1

        names = [self.name.lower()]
        for a in self.aliases:
            names.append(a.lower())
        if " " in self.name:
            namebits = self.name.lower().split()
            if namebits[-1] not in names:
                names.append(namebits[-1])

        if type(name) is str:
            name = name.lower().strip()
            # Ignore a single leading article so "the table" / "a chair" match
            # the same as "table" / "chair".
            for _art in ("the ", "a ", "an "):
                if name.startswith(_art):
                    name = name[len(_art) :].strip()
                    break
            if name in names:
                return 1
            else:
                for n in names:
                    if n.startswith(name):
                        return 2
            return 0

    @property
    def messages(self):
        msgs = {}
        msgs.update(self.world.default_messages)
        classes = list(inspect.getmro(self.__class__)[:-1])
        classes.reverse()
        for c in classes:
            msgs.update(c.default_messages)
        msgs.update(self._messages)
        return msgs

    def get_message(self, which, who, what=None):
        # x
        # x_others
        # x_fail
        # x_fail_reason

        msg = self.messages.get(
            which,
            self.default_messages.get(
                which, self.world.default_messages.get(which, "")
            ),
        )
        # FIXME: allow more flexible messages
        if what is not None:
            # player has used key on chest
            return msg.format(
                **{"player": who.name, "self": self.name, "object": what.name}
            )
        else:
            # player has left through north
            return msg.format(**{"player": who.name, "self": self.name})

    def get_commands(self, match=None, allow_cached=True):
        if allow_cached and self.commands_cached:
            cmds = self.commands_cached
        else:
            cmds = {}
            classes = list(inspect.getmro(self.__class__)[:-1])
            classes.reverse()
            for c in classes:
                # Only merge commands the class declares itself. Attribute
                # access (c.default_commands) would also return a parent's
                # dict for classes that declare none, merging the same
                # entries a second time further up the MRO.
                own = c.__dict__.get("default_commands")
                if own is None:
                    continue
                for vb, info in own.items():
                    try:
                        cmds[vb].extend(copy.deepcopy(info))
                    except Exception:
                        cmds[vb] = copy.deepcopy(info)
            for k, v in self.commands.items():
                try:
                    cmds[k].extend(v)
                except Exception:
                    cmds[k] = v
            self.commands_cached = cmds
        if match is None:
            return cmds
        else:
            c = cmds.get(match, [])
            return {match: c}

    # --- Out-of-band state channel (see OOB-PROTOCOL.md) ---

    def state_summary(self):
        """Short state strings for the client (open/closed, locked/unlocked,
        on/off, held/worn).  End of a cooperative chain: each mixin returns
        ``super().state_summary() + [...]``, so a plain thing has none.
        Callers that need the old "None when empty" form use ``or None``
        (see thing_summary)."""
        return []

    def verbs_summary(self, who, include_argless=False):
        """This thing's merged command table as JSON-safe entries, restricted
        to what *who* may do right now -- the same okay_for_verb gate the
        dispatcher applies, so the summary never advertises a verb dispatch
        would reject.  `func` refs are dropped; duplicate shapes collapse to
        one entry.

        With include_argless=False (things), only entries that carry a
        'self' slot are listed: argless entries on a *remote* thing can't be
        aimed at it (a bare verb always resolves to the speaker first), and
        'dobj any'-style entries (say/emote) run on the speaker regardless.
        """
        if who is None:
            return []
        seen = set()
        out = []
        for entries in self.get_commands().values():
            for info in entries:
                if not any(self.okay_for_verb(v, who) for v in info["verb"]):
                    continue
                key = (
                    tuple(info["verb"]),
                    info["dobj"],
                    tuple(info["prep"]) if info["prep"] else None,
                    info["iobj"],
                )
                if key in seen:
                    continue
                seen.add(key)
                targetable = info["dobj"] == "self" or info["iobj"] == "self"
                if not targetable and not include_argless:
                    continue
                out.append(
                    {
                        "verb": list(info["verb"]),
                        "dobj": info["dobj"],
                        "prep": list(info["prep"]) if info["prep"] else None,
                        "iobj": info["iobj"],
                    }
                )
        return out

    def _contents_summary(self, who):
        """What *who* may see inside this thing -- mirrors in-band `look`:
        open (or not openable) containers list their contents; players and
        worn clothing only reveal held/worn items."""
        if who is None or not who.can_see(self):
            return None
        if isinstance(self, (Player, Wearable)):
            items = [x for x in self.contents if x.contained_look_at(who)]
        elif hasattr(self, "is_open") and not self.is_open:
            return None
        else:
            items = list(self.contents)
        return [x.thing_summary(who) for x in items] or None

    def thing_summary(self, who):
        """Compact, visibility-safe description of this thing for *who*."""
        s = {
            "id": self.id,
            "name": self.name,
            "aliases": list(self.aliases or []),
            "cls": self.__class__.__name__,
            "state": self.state_summary() or None,
            "verbs": self.verbs_summary(who),
        }
        p = self.find_position()
        if p is not None:
            parent, pos, verb = p
            s["position"] = (
                f"{parent.posture_ing(verb)} " if verb else ""
            ) + f"{pos} the {parent.name}"
        c = self._contents_summary(who)
        if c is not None:
            s["contents"] = c
        return s

    # for custom commands
    def register_command(self, info):
        verbs = info["verb"]
        if type(verbs) is str:
            verbs = [verbs]
        for verb in verbs:
            try:
                self.commands[verb].append(info)
            except Exception:
                self.commands[verb] = [info]

    @make_command(["look", "l"], "self")
    @make_command(["look", "l"], None, "at", "self")
    def look(self, player, prep=None, verb=None):
        desc = self.look_at(player)
        player.tell(desc)


# --- Place Class ---
class Place(BaseThing):
    """Represents a location in the MUD (e.g., a room)."""

    def __init__(self, id, world, data, recursive=True):
        self.exits = {}  # exit command -> Exit Object
        super().__init__(id, world, data, recursive)
        self.coordinates = data.get("coordinates", [0, 0, 0])
        if not self.exits and "exits" in data:
            for ex in data.get("exits", []):
                xo = world.get_object(ex, recursive)
                self.add_exit(xo)

    def add_exit(self, exit_obj):
        """Adds an exit ID to the place's exits"""
        self.exits[exit_obj.name.lower()] = exit_obj
        exit_obj.source = self

    def add_entrance(self, exit_obj):
        """if we need to track entrances, e.g. to bless entering"""
        pass

    def on_enter(self, what):
        super().on_enter(what)
        if isinstance(what, Player):
            what.tell(self.look_at(what))

    def on_leave(self, what):
        super().on_leave(what)

    def announce(self, msg):
        for who in self.contents:
            if hasattr(who, "tell"):
                who.tell(msg)

    def announce_all_but(self, msg, who):
        if who is None:
            self.announce(msg)
            return
        elif isinstance(who, Player):
            who = [who]
        elif not isinstance(who, list):
            return
        # need to tell all contents if not in who
        for c in self.contents:
            if c not in who and hasattr(c, "tell"):
                c.tell(msg)

    def look_at(self, who):
        """Generates a description of the place, its contents, and exits"""
        desc = []
        desc.append(f"--- {self.name} ---")
        desc.append(super().look_at(who))
        desc.append("")

        # Collect visible contents, splitting out anything positioned relative
        # to a Positionable in this room -- those are described by position
        # ("a plate on the table") rather than in the plain "you see here" list.
        positioned = {}  # thing -> (parent, pos, verb); includes the looker
        for item in self.contents:
            if isinstance(item, Positionable):
                for pos, entries in item.positioned.items():
                    for t, verb in entries:
                        if who.can_see(t) and who.can_see(item):
                            positioned[t] = (item, pos, verb)

        plain_names = []
        for item in self.contents:
            if item == who or not who.can_see(item):
                continue
            if item in positioned:
                continue
            plain_names.append(item.name)
        position_lines = []
        for t, (parent, pos, verb) in positioned.items():
            if t is who:
                where = (
                    f"{parent.posture_ing(verb)} {pos}" if verb else pos
                ) + f" the {parent.name}"
                position_lines.append(f"You are {where}.")
            else:
                position_lines.append(parent.position_line(t, pos, verb))

        if plain_names:
            desc.append(f"You see here: {', '.join(plain_names)}.")
        elif not position_lines:
            desc.append("The place looks empty.")
        if position_lines:
            desc.extend(position_lines)

        # List exits
        exit_names = []
        for exit in self.exits.keys():
            exit_names.append(exit)
        if exit_names:
            desc.append(f"Exits: {', '.join(sorted(exit_names))}.")
        else:
            desc.append("There are no obvious exits.")
        return "\n".join(desc)

    @make_command(["look", "l"])
    def look(self, player, prep=None, verb=None):
        desc = self.look_at(player)
        player.tell(desc)

    def to_dict(self):
        """Returns a dictionary representation of the place (current in-memory state)."""
        data = super().to_dict()
        data.update(
            {
                "exits": [x.id for x in self.exits.values()],  # List of Exit IDs
                "coordinates": self.coordinates,
            }
        )
        return data


class Interior(Place):
    """A Place that is the *inside* of something (see the Enterable mixin).

    It has no exits of its own -- you leave by getting out of the thing it
    belongs to -- and it never moves, which is exactly the point: when a car
    drives away its interior goes along at no cost, so the passengers standing
    in it never change location and nothing has to be carried.
    """

    def __init__(self, id, world, data, recursive=True):
        super().__init__(id, world, data, recursive)
        outside = data.get("outside", None)
        # The owning thing is already registered by the time this runs (its
        # BaseThing.__init__ ran first), so the cycle resolves either way
        # round -- whichever of the two the database happens to load first.
        self.outside = world.get_object(outside) if outside else None

    def outside_room(self):
        """The Place the owning thing is standing in, if any."""
        where = self.outside.location if self.outside else None
        return where if hasattr(where, "exits") else None

    def look_at(self, who):
        desc = super().look_at(who)
        where = self.outside_room()
        if where is None:
            return desc
        lines = [f"Outside you can see {where.name}."]
        if where.exits:
            lines.append(f"Ways out: {', '.join(sorted(where.exits))}.")
        return join_look(desc, *lines)

    def to_dict(self):
        data = super().to_dict()
        data.update({"outside": self.outside.id if self.outside else None})
        return data


# --- Exit Class ---
class Exit(BaseThing):
    """Represents a transition between two Places."""

    default_messages = {
        "leave": "You go through {self}",
        "leave_fail_location": "You are not at that exit's source",
        "leave_fail_destination": "That exit doesn't go anywhere, or you can't enter it",
        "leave_others": "{player} leaves through {self}",
        "arrive_others": "{player} has arrived",
    }

    # The largest vehicle that fits through.  A plain exit is a road, an
    # archway, a gap in the hedge: anything goes.  See OpenableExit for why a
    # door is narrower, and VEHICLE_NONE to bar vehicles outright.
    default_max_vehicle_size = VEHICLE_LARGE

    def __init__(self, id, world, data, recursive=True):
        self.source = None
        self.destination = None
        super().__init__(id, world, data, recursive)
        self.max_vehicle_size = data.get(
            "max_vehicle_size", self.default_max_vehicle_size
        )
        src = world.get_object(data["source"])
        if src is not None:
            self.source = src
            src.add_exit(self)
        # Lazy load destinations after first room
        if not recursive:
            self.destination = data["destination"]
        else:
            dest = world.get_object(data["destination"], recursive=False)
            if dest is not None:
                self.destination = dest
                dest.add_entrance(self)

    def resolve_destination(self):
        """The Place on the far side, loading it if it is still just an id.

        Destinations load lazily (Exit.__init__ with recursive=False leaves a
        bare id), so everything that travels -- players walking, vehicles
        being driven -- has to go through here rather than read the attribute.
        """
        dest = self.destination
        if type(dest) is str:
            dest = self.world.get_object(dest)
            if dest is not None:
                self.destination = dest
        return dest

    def vehicle_ok(self, vehicle, player):
        """May *vehicle* be driven through here?  Tells the player why not.

        Size is the gate, so the rule reads the way the world does: a car does
        not fit through a front door, a bicycle does, and a garage door is
        simply a door with `max_vehicle_size` set wide.
        """
        if getattr(self, "is_open", True) is False:
            player.tell(vehicle.get_message("drive_fail_closed", player, self))
            return False
        if vehicle.vehicle_size > self.max_vehicle_size:
            player.tell(vehicle.get_message("drive_fail_size", player, self))
            return False
        return True

    @make_command(["go", "walk"], "self")
    @make_command(["go", "walk"], None, ["through"], "self")
    def use(self, player, prep=None, verb=None):
        """Moves a player through the exit to the destination.

        Aboard a vehicle this drives the vehicle through instead -- `go north`
        and a click on the exit in a client's panel mean the same thing as
        `drive north` when you are the one at the wheel.  Get off first if you
        want to leave the vehicle behind.
        """
        vehicle = player.current_vehicle()
        if vehicle is not None:
            return vehicle.drive_through(self, player)
        if not player.location == self.source:
            player.tell(self.get_message("leave_fail_location", player))
        elif self.destination is None:
            player.tell(self.get_message("leave_fail_destination", player))
        else:
            dest = self.resolve_destination()
            if dest is None:
                player.tell(self.get_message("leave_fail_destination", player))
                return

            player.tell(self.get_message("leave", player))
            player.move_to(dest)
            if player.location == dest:
                self.source.announce(self.get_message("leave_others", player))
                self.destination.announce_all_but(
                    self.get_message("arrive_others", player), player
                )
            else:
                player.tell(self.get_message("leave_fail_destination", player))

    def to_dict(self):
        """Returns a dictionary representation of the exit."""
        data = super().to_dict()
        data.update(
            {
                "source": self.source.id if self.source else None,
                "max_vehicle_size": self.max_vehicle_size,
            }
        )
        if type(self.destination) is str:
            data["destination"] = self.destination
        elif isinstance(self.destination, Place):
            data["destination"] = self.destination.id
        elif not self.destination:
            data["destination"] = None
        else:
            print(f"Tried to serialize {self.destination} on {self}")
        return data


class OpenableExit(Openable, Exit):
    """An Exit you can open and close.  Mixins come *first* in the bases so
    the mixin's __init__/to_dict/look_at/state_summary sit ahead of BaseThing
    in the MRO and chain cooperatively -- no explicit Openable.__init__ call,
    no hand-spliced look_at.  Only the genuinely Exit-specific behaviour below
    (a closed door blocks travel; open/close carry across to the far side)
    lives here, which is why this class still exists at all."""

    # A doorway you can wheel a bicycle through but not drive a car through.
    # A garage door is this class with `max_vehicle_size` set to VEHICLE_LARGE
    # in its data -- no separate class needed.
    default_max_vehicle_size = VEHICLE_SMALL

    default_messages = {
        "go_fail_closed": "You cannot go through that, it's closed.",
        "open_destination": "{player} opens {self} from the other side.",
        "close_destination": "{player} closes {self} from the other side.",
    }

    @make_command(["go", "walk"], "self")
    @make_command(["go", "walk"], None, ["through"], "self")
    def use(self, player, prep=None, verb=None):
        if not self.is_open:
            player.tell(self.get_message("go_fail_closed", player))
        else:
            super().use(player)

    @make_command("open", "self")
    def open(self, player, prep=None, verb=None):
        super().open(player)
        # `self.is_open`, not `self.open` -- the latter is this very method and
        # is always truthy, so both branches used to announce unconditionally.
        if self.is_open and self.destination:
            self.destination.announce(self.get_message("open_destination", player))

    @make_command("close", "self")
    def close(self, player, prep=None, verb=None):
        super().close(player)
        if not self.is_open and self.destination:
            self.destination.announce(self.get_message("close_destination", player))


class LockableExit(Lockable, OpenableExit):
    """MRO: LockableExit -> Lockable -> OpenableExit -> Openable -> StateToggle
    -> Exit -> BaseThing.  `open` therefore runs the lock check, then the
    far-side announcement, then the toggle -- each layer calling super()."""


# --- Object Class ---
class Object(BaseThing):
    """Represents a generic object in the MUD that can be picked up or dropped."""

    default_messages = {
        "take_fail": "You can't take {self}.",
        "drop_fail": "You can't drop {self}.",
        "take_others": "{player} takes {self}.",
        "drop_others": "{player} drops {self}.",
        "take": "You take {self}.",
        "drop": "You drop {self}.",
        "use_fail": "You can't use {self}.",
        "use": "You use {self}.",
        "use_others": "{player} uses {self}.",
    }

    def take_ok(self, player):
        return True

    def drop_ok(self, player):
        return True

    def use_ok(self, player):
        return True

    def use_on_ok(self, player, target):
        return True

    def use_effect(self, player):
        return None

    def use_on_effect(self, player, target):
        return None

    def okay_for_verb(self, verb, player):
        if not player.can_see(self):
            return False
        if verb in ["get", "take", "pick"]:
            if self.location != player.location:
                return False
        elif verb == "drop":
            if self.location != player:
                return False
        return True

    @make_command(["get", "take", "pick"], "self")
    def get(self, player, prep=None, verb=None):
        """Allows a player to pick up the object"""
        if not player.can_see(self):
            player.tell(self.get_message("fail_visible", player))
        elif not self.take_ok(player):
            player.tell(self.get_message("take_fail", player))
        else:
            self.move_to(player)
            if self.location == player:
                player.tell(self.get_message("take", player))
                player.location.announce_all_but(
                    self.get_message("take_others", player), player
                )
            else:
                player.tell(self.get_message("take_fail", player))

    @make_command("drop", "self")
    def drop(self, player, prep=None, verb=None):
        if self.location != player:
            player.tell(self.get_message("fail_visible", player))
        elif not self.drop_ok(player) or not player.leave_ok(self):
            player.tell(self.get_message("drop_fail", player))
        else:
            self.move_to(player.location)
            if self.location == player.location:
                player.tell(self.get_message("drop", player))
                player.location.announce_all_but(
                    self.get_message("drop_others", player), player
                )
            else:
                player.tell(self.get_message("drop_fail", player))

    @make_command("use", "self")
    @make_command("use", "self", "on", "Object")
    def use(self, player, target=None, prep=None, verb=None):
        """Placeholder for using an object on another object"""

        if self.location not in [player.location, player]:
            player.tell(self.get_message("fail_visible", player))
        elif not self.use_ok(player):
            player.tell(self.get_message("use_fail", player))
        elif not self.use_on_ok(player, target):
            player.tell(self.get_message("use_fail_target", player, target))
        else:
            if target is None:
                self.use_effect(player)
            else:
                self.use_on_effect(player, target)
            # FIXME: How to get good messages with target??
            player.tell(self.get_message("use", player))
            player.location.announce_all_but(
                self.get_message("use_others", player), player
            )


# --- Container Class ---
class Container(Containable, Object):
    """An object that can hold other objects.  Pure composition -- the body is
    empty because Containable now chains through the MRO on its own."""


class OpenableContainer(Openable, Container):
    """A container with a lid.  Also pure composition."""


class PositionableObject(Positionable, Object):
    """Furniture (or similar) relative to which players and objects can be
    positioned (sit on a chair, lie under a table, put a plate on it...).
    Only the "you cannot pick up the furniture" rule is specific to the
    combination; the rest is inherited composition."""

    def take_ok(self, player):
        return False


class WearableObject(Wearable, Containable, Object):
    """A clothing object that can be worn, with pockets."""


class HeldObject(Holdable, Object):
    """An object that can be wielded."""


# --- Switchable Class ---
class SwitchableObject(Switchable, Object):
    """A lamp (or other light/electronic) that can be turned on and off."""


# --- Edible Class ---
class EdibleThing(Object):
    """An object that can be consumed -- food, drink, potion, etc.

    ``eat``/``drink``/``quaff <this>`` consumes it: the base class announces
    the consumption and then destroys the object.  Subclasses override
    :meth:`_on_eaten` to apply the object's effect (heal, buff, poison, ...).
    That hook runs while the object still exists -- immediately before it is
    destroyed -- so it may rely on the object and its current location.

    This is the seam that lets later potions and food carry effects, e.g.::

        class HealingPotion(EdibleThing):
            def _on_eaten(self, player):
                player.tell("You feel your wounds close.")
    """

    default_messages = {
        "eat": "You eat {self}.",
        "eat_others": "{player} eats {self}.",
        "eat_fail": "You can't eat {self}.",
    }

    def eat_ok(self, player):
        """Whether *player* may consume this (default: always).

        Reachability is already enforced by ``okay_for_verb``/``can_see`` --
        you can only eat what you can see (carried, or in your room).  Override
        this for finer rules ("must be held", "you're too full", ...).
        """
        return True

    def _on_eaten(self, player):
        """Hook: apply this object's effect when it is consumed.

        Called from :meth:`eat` right before the object is destroyed, so the
        object still exists and sits in its location.  The base implementation
        is a no-op; override in subclasses to apply the effect.
        """

    @make_command(["eat", "drink", "quaff"], "self")
    def eat(self, player, prep=None, verb=None):
        if not player.can_see(self):
            player.tell(self.get_message("fail_visible", player))
        elif not self.eat_ok(player):
            player.tell(self.get_message("eat_fail", player))
        else:
            player.tell(self.get_message("eat", player))
            player.location.announce_all_but(
                self.get_message("eat_others", player), player
            )
            self._on_eaten(player)  # effect while the object still exists
            self.destroy()  # then consume it

    def destroy(self):
        """Remove this object from the world entirely.

        Relocates anything it contains (so nothing is left orphaned), detaches
        it from its current location (room or carrying player), drops it from
        the in-memory world, and deletes it from the persistence layer.  The
        detach marks the affected room / the carrier's inventory dirty for the
        out-of-band state channel, so clients see it disappear.
        """
        parent = self.location
        for c in list(self.contents):  # don't orphan nested things
            c.move_to(parent)
        self.move_to(None)  # leaves parent's contents; marks the OOB section dirty
        self.world.active_objects.pop(self.id, None)
        self.world.db.delete(self.id)


# --- Player Class ---
class Player(BaseThing):
    """Represents a player connected and logged into the MUD."""

    connection = None
    home: Place | None = None

    def __init__(self, id, world, data, recursive=True):
        self.connection = None
        super().__init__(id, world, data, recursive)
        self.username = data["username"]
        self.password_hash = data["password_hash"]
        self.last_location = data.get("last_location", None)
        self.last_active_time = time.time()

    def check_password(self, password):
        """Checks if the provided password matches the stored hash (in memory)."""
        return check_password_hash(self.password_hash, password)

    def to_dict(self):
        """Returns a dictionary representation of the player."""
        data = super().to_dict()
        data.update(
            {
                "username": self.username,
                "password_hash": self.password_hash,
                "home": self.home.id if self.home else None,
            }
        )
        if type(self.last_location) is str:
            data["last_location"] = self.last_location
        elif self.last_location:
            data["last_location"] = self.last_location.id
        else:
            data["last_location"] = None
        return data

    def look_at(self, who=None):
        """Description, plus anything *who* can see the player holding or
        wearing (plain carried items stay hidden -- that's inventory, not
        something on your person)."""
        desc = super().look_at(who)
        held, worn = [], []
        for item in self.contents:
            mode = self._carry_mode(item)
            if mode == "held":
                held.append(item.name)
            elif mode == "worn":
                worn.append(item.name)
        if held or worn:
            subj = "You" if who is self else "They"
            if held:
                desc += f"\n{subj} are holding: {', '.join(held)}."
            if worn:
                desc += f"\n{subj} are wearing: {', '.join(worn)}."
        p = self.find_position()
        if p is not None:
            parent, pos, verb = p
            subj = "You" if who is self else "They"
            where = (
                f"{parent.posture_ing(verb)} {pos}" if verb else pos
            ) + f" the {parent.name}"
            desc += f"\n{subj} are {where}."
        return desc

    def current_vehicle(self):
        """The vehicle this player is riding, or None.

        Either they are standing in its interior (a car) or sitting on it (a
        bike).  The dispatcher puts this first among the objects it offers a
        command to, which is what makes `drive north` reach *your* vehicle
        rather than whichever one happens to be parked nearest."""
        outside = getattr(self.location, "outside", None)
        if isinstance(outside, Vehicle):
            return outside
        position = self.find_position()
        if position is not None and isinstance(position[0], Vehicle):
            return position[0]
        return None

    def _carry_mode(self, item):
        """How an item in this inventory is carried: 'held', 'worn', or plain
        'carried'.  Derived from the item's state (Holdable/Wearable)."""
        st = item.state_summary() or []
        if "worn" in st:
            return "worn"
        if "held" in st:
            return "held"
        return "carried"

    def tell(self, message):
        if self.connection:
            self.world.tell_player(self, message)
        else:
            print(message)

    def can_see(self, what):
        if what.location == self.location or what.location == self:
            return True
        if isinstance(what, Exit) and what.source == self.location:
            return True
        # Riding something has windows: from inside a car you can see the
        # street it is parked in, what is in it, and the ways out of it --
        # otherwise a driver could not even look at their own car, and the
        # exits they are about to drive through would be invisible.
        vehicle = self.current_vehicle()
        if vehicle is not None:
            outside = vehicle.location
            if what is vehicle or what.location == outside:
                return True
            if isinstance(what, Exit) and what.source == outside:
                return True
        return False

    def my_match_object(self, name):
        if name in ["me", "myself"]:
            return self
        elif name == "here":
            return self.location
        else:
            # The vehicle you are riding is referenceable by name even though
            # it is not in the room with you -- from inside a car, the car is
            # in the street outside.  Same reasoning as the dispatcher's
            # search order (World.process_player_command).
            vehicle = self.current_vehicle()
            for x in [
                *self.contents,
                *self.location.contents,
                *self.location.exits.values(),
                *([vehicle] if vehicle is not None else []),
            ]:
                if x.match_object(name, self):
                    return x
        return None

    @make_command(["i", "inv", "inventory"])
    def inventory(self, player, prep=None, verb=None):
        if player != self:
            player.tell("You can't inventory someone else")
            return
        # Group the inventory by how each item is carried: plain "carried"
        # items, held (wielded) items, and worn items get their own sections.
        carried, held, worn = [], [], []
        for x in self.contents:
            mode = self._carry_mode(x)
            if mode == "held":
                held.append(x.name)
            elif mode == "worn":
                worn.append(x.name)
            else:
                carried.append(x.name)
        if not (carried or held or worn):
            player.tell("You are not carrying anything")
            return
        parts = []
        if carried:
            parts.append(f"You are carrying: {', '.join(carried)}")
        if held:
            parts.append(f"You are holding: {', '.join(held)}")
        if worn:
            parts.append(f"You are wearing: {', '.join(worn)}")
        player.tell("\n".join(parts))

    @make_command("say", "any")
    def say(self, argstr, prep=None, verb=None):
        """Allows a player to say something in their current location."""
        if not argstr:
            self.tell("You need to give something to say")
        elif not self.location:
            self.tell("You are not in a place where you can speak.")
        else:
            self.tell(f'You say, "{argstr}"')
            self.location.announce_all_but(f'{self.name} says, "{argstr}"', self)

    @make_command("emote", "any")
    def emote(self, argstr, prep=None, verb=None):
        """Allows a player to emote something in their current location."""
        if not argstr:
            self.tell("You need to give something to emote")
        elif not self.location:
            self.tell("You are not in a place where you can emote.")
        else:
            self.location.announce(f"{self.name} {argstr}")

    @make_command(["wh", "whisper"], "any", "to", "Player")
    def whisper(self, argstr, target, prep=None, verb=None):
        pass

    @make_command(["rise"])
    def rise(self, player, prep=None, verb=None):
        """Get up from whatever this player is positioned relative to.
        ('rise' is single-word on purpose -- the parser keys on the first word,
        so 'stand up' / 'get up' would never dispatch.)"""
        if player != self:
            player.tell("You can only raise yourself.")
            return
        p = self.find_position()
        if p is None:
            player.tell("You are already on your feet.")
            return
        parent, pos, verb = p
        parent.remove_positioned(self)
        player.tell("You rise to your feet.")
        parent.location.announce_all_but(f"{self.name} rises to their feet.", self)

    @make_command("@who")
    def who(self, player, target=None, prep=None, verb=None):
        for p in self.world.active_sids.values():
            pl = self.world.active_objects[p]
            player.tell(
                f"{pl.name}    {pl.location.name}   {int(time.time() - pl.last_active_time)} seconds ago"
            )

    @make_command("@quit")
    def quit(self, player, target=None, prep=None, verb=None):
        self.tell("Goodbye!")
        self.world.disconnect_player(player)

    @make_command(["@desc", "@describe"], "self", "as", "any")
    def describe(self, player, target="", prep=None, verb=None):
        if player != self:
            player.tell("You can't describe that.")
        else:
            target = target.replace("\\n", "\n")
            self.description = target
            self.tell(f"Your new description: {self.description}")

    @make_command("@home")
    def go_home(self, player, target=None, prep=None, verb=None):
        if not self.home:
            player.tell("You don't have a home.")
        elif self.home == self.location:
            player.tell("You are already at home.")
        else:
            self.tell("You tap your heels three times...")
            self.move_to(self.home)

    @make_command("@sethome")
    def set_home(self, player, target=None, prep=None, verb=None):
        if self.home == self.location:
            player.tell("You are already at home.")
        else:
            self.home = self.location
            player.tell("You set this location to be your home")


class Programmer(Player):
    @make_command(["eval", "@eval"], "any")
    def eval(self, argstr, prep=None, verb=None):
        player = self
        here = player.location  # noqa F841
        me = self  # noqa F841
        if "#" in argstr:
            # would be a comment, but instead treat as obj ref
            idx = argstr.index("#")
            wd = argstr[idx:].split()[0]
            if "." in wd:
                wd = wd.split(".", 1)[0]
            what = self.world.get_object_by_name(wd[1:])
            if what is not None:
                argstr = argstr.replace(wd, "what")
            else:
                player.tell(f"Could not find an object for: {wd}")
                return
        res = eval(argstr)
        print(res)
        self.tell(repr(res))

    def make_exit_from_spec(self, spec):
        if ":" in spec:
            (ex_clss_name, ex1) = spec.split(":", 1)
            ex_clss = self.world.import_class(ex_clss_name)
            if not issubclass(ex_clss, Exit):
                return None
            ex1 = ex1.split(",")
        else:
            ex_clss = Exit
            ex1 = spec.split(",")

        ex_data = {"name": ex1[0], "source": None, "destination": None}
        if len(ex1) > 1:
            ex_data["aliases"] = ex1[1:]
        exit = ex_clss(None, self.world, ex_data, recursive=False)
        exit._save()
        return exit

    @make_command("@dig", "any", "to", "any")
    def dig(self, player, exits, room, prep=None, verb=None):
        """@dig north,n|south,s to name of room
        @dig east to #<room>"""
        if not exits:
            player.tell("You need to specify an exit.")
        elif not room:
            player.tell("You need to specify a room name.")
        else:
            bits = exits.split("|")
            if len(bits) > 2:
                # more than two exits?
                player.tell("You can only create an exit to and from.")
                return

            if room.startswith("#"):
                place = self.world.get_object_by_id(room[1:], Place)
                if place is None:
                    place = self.world.get_object_by_name(room[1:], Place)
            else:
                # Check if looks like a UUID and refuse?
                place = Place(None, self.world, {"name": room}, recursive=False)
                place._save()

            if not place:
                player.tell(f"Could not find a room for {room}")
            else:
                exit1 = self.make_exit_from_spec(bits[0])
                if exit1 is not None:
                    exit1.source = self.location
                    exit1.destination = place
                    self.location.add_exit(exit1)
                else:
                    player.tell(f"Could not make exit from {bits[0]}")
                if len(bits) == 2:
                    exit2 = self.make_exit_from_spec(bits[1])
                    if exit2 is not None:
                        exit2.source = place
                        exit2.destination = self.location
                        place.add_exit(exit2)
                    else:
                        player.tell(f"Could not make exit from {bits[1]}")
            self.world.mark_room_dirty(self.location)
            if place is not None:
                self.world.mark_room_dirty(place)
            player.tell(f"You dig {exits} to {room} ({place}).")

    def _recompose(self, player, what, describe, **kwargs):
        """Shared body of @chparent / @addmixin / @rmmixin: find the object,
        work out its new (class, mixins), and rebuild the instance under it.

        `kwargs` is passed to World.classes.combine (add=/remove=), except for
        `base`, which replaces the entity base outright."""
        target = player.my_match_object(what)
        if target is None:
            player.tell(f"You can't see anything matching {what}")
            return None
        data = target.to_dict()
        base = kwargs.pop("base", None) or data["class"]
        try:
            mixins = self.world.classes.combine(data.get("mixins"), **kwargs)
            data["class"] = base
            data["mixins"] = list(mixins)
            # Resolve before writing anything: an unknown base or a bad
            # combination must fail with a message, not a half-saved object.
            cls = self.world.classes.resolve(base, mixins)
        except Exception as e:
            player.tell(f"Cannot do that to {target.name}: {e}")
            return None
        target = self.world.rebuild_instance(target, data)
        player.tell(f"{target.name} is now {describe} ({cls.__name__}).")
        return target

    @make_command("@chparent", "any", "to", "any")
    def change_parent(self, player, what, new_class, prep=None, verb=None):
        """@chparent <thing> to <Class> -- swap the entity base.

        A hand-written composite name (Container, OpenableExit, ...) is
        expanded into the base + mixins it stands for, so @chparent and
        @addmixin cannot disagree about what a thing is."""
        if not what:
            player.tell("You need to specify an object.")
            return
        if not new_class:
            player.tell("You need to specify a new class.")
            return
        (base, mixins) = self.world.classes.split_name(new_class)
        if self.world.import_class(base) is None:
            player.tell(f"Could not find class {new_class}")
            return
        target = player.my_match_object(what)
        if target is None:
            player.tell(f"You can't see anything matching {what}")
            return
        data = target.to_dict()
        # @chparent replaces the whole identity, mixins included.
        data["mixins"] = []
        try:
            cls = self.world.classes.resolve(base, mixins)
        except Exception as e:
            player.tell(f"Could not use class {new_class}: {e}")
            return
        data["class"] = base
        data["mixins"] = list(mixins)
        target = self.world.rebuild_instance(target, data)
        player.tell(f"You change the parent of {what} to {cls.__name__}.")

    @make_command("@addmixin", "any", "to", "any")
    def add_mixin(self, player, mixin, what, prep=None, verb=None):
        """@addmixin <Mixin> to <thing> -- give an existing object a new
        capability, with no new Python: the class for base + mixins is
        composed on the spot (or a hand-written one is picked up, if the
        combination has one)."""
        if not what or not mixin:
            player.tell("Usage: @addmixin <Mixin> to <thing>")
            return
        self._recompose(player, what, f"also {mixin}", add=[mixin.strip()])

    @make_command("@rmmixin", "any", ["from"], "any")
    def remove_mixin(self, player, mixin, what, prep=None, verb=None):
        """@rmmixin <Mixin> from <thing> -- take a capability away again."""
        if not what or not mixin:
            player.tell("Usage: @rmmixin <Mixin> from <thing>")
            return
        self._recompose(player, what, f"no longer {mixin}", remove=[mixin.strip()])

    # --- stored verbs (see classfactory.py) -------------------------------

    def _verb_target(self, player, cls_name):
        """The canonical class name to hang a stored verb on, resolving a
        thing's name as well as a class name so `@verb #chest ...` works."""
        (base, mixins) = self.world.classes.split_name(cls_name)
        try:
            return self.world.classes.resolve(base, mixins).__name__
        except Exception:
            pass
        target = player.my_match_object(cls_name)
        if target is not None:
            return target.__class__.__name__
        return None

    @make_command("@verb", "any")
    def define_verb(self, player, argstr, prep=None, verb=None):
        """@verb <Class|thing> <name> [<shape>] <code>

        Store a Python verb in the database and compile it onto the class,
        with no change to the codebase.  Newlines are written as \n (commands
        arrive as a single line):

          @verb OpenableExit use go,walk/self def use(self, player, prep=None,
          verb=None):\n    if not self.is_open:\n        ...

        The shape is `verbs/dobj/prep/iobj` (verbs comma-separated, `-` for an
        empty slot); omit it and the verb is registered argless under its own
        name.  Stored code cannot use a bare super() -- there is no closure
        cell for it -- so call super(cls, self) instead; `cls` is provided.
        With no code, prints the stored verb instead of replacing it.
        """
        # Local import: classfactory imports this module, so it cannot be
        # imported at module scope here.
        from .classfactory import compile_verb, now_stamp

        bits = (argstr or "").split(None, 2)
        if len(bits) < 2:
            player.tell(
                "Usage: @verb <Class|thing> <name> [<verbs/dobj/prep/iobj>] <code>"
            )
            return
        name = self._verb_target(player, bits[0])
        if name is None:
            player.tell(f"No such class or object: {bits[0]}")
            return
        vname = bits[1]
        rest = bits[2] if len(bits) > 2 else ""

        stored = self.world.class_verbs.setdefault(name, {}).setdefault("verbs", {})
        if not rest:
            spec = stored.get(vname)
            if spec is None:
                player.tell(f"{name}.{vname} has no stored verb.")
            else:
                player.tell(
                    f"# {name}.{vname} by {spec.get('author')} {spec.get('created')}"
                )
                player.tell(spec.get("code", ""))
            return

        shape = None
        if rest.split(None, 1)[0].count("/") == 3:
            (shape_txt, _, rest) = rest.partition(" ")
            (verbs, dobj, prp, iobj) = shape_txt.split("/")
            clean = lambda v: None if v in ("", "-") else v  # noqa: E731
            shape = {
                "verb": [v for v in verbs.split(",") if v],
                "dobj": clean(dobj),
                "prep": [p for p in prp.split(",") if p and p != "-"] or None,
                "iobj": clean(iobj),
            }
        if shape is None:
            shape = {"verb": [vname], "dobj": None, "prep": None, "iobj": None}

        spec = {
            "code": rest.replace("\\n", "\n"),
            "shapes": [shape],
            "author": self.name,
            "created": now_stamp(),
        }
        previous = stored.get(vname)
        stored[vname] = spec
        try:
            # Compile against the *current* class before committing, so a
            # syntax error is a message, not a world that no longer loads.
            cls = self.world.classes.registry.get(name)
            if cls is not None:
                compile_verb(cls, vname, spec)
        except Exception as e:
            if previous is None:
                stored.pop(vname, None)
            else:
                stored[vname] = previous
            player.tell(f"Verb not stored: {e}")
            return
        self.world.persist_class_verbs()
        n = self.world.reload_class(name)
        player.tell(f"Stored {name}.{vname}; rebuilt {n} live object(s).")

    @make_command("@rmverb", "any")
    def remove_verb(self, player, argstr, prep=None, verb=None):
        """@rmverb <Class|thing> <name> -- drop a stored verb again."""
        bits = (argstr or "").split()
        if len(bits) != 2:
            player.tell("Usage: @rmverb <Class|thing> <name>")
            return
        name = self._verb_target(player, bits[0])
        stored = (self.world.class_verbs.get(name) or {}).get("verbs") or {}
        if bits[1] not in stored:
            player.tell(f"{name}.{bits[1]} has no stored verb.")
            return
        del stored[bits[1]]
        self.world.persist_class_verbs()
        n = self.world.reload_class(name)
        player.tell(f"Removed {name}.{bits[1]}; rebuilt {n} live object(s).")

    @make_command("@verbs", "any")
    @make_command("@verbs")
    def list_verbs(self, player, argstr=None, prep=None, verb=None):
        """@verbs [<Class|thing>] -- stored verbs, for one class or all."""
        if not argstr:
            any_stored = False
            for cname, rec in sorted(self.world.class_verbs.items()):
                vs = (rec or {}).get("verbs") or {}
                if vs:
                    any_stored = True
                    player.tell(f"{cname}: {', '.join(sorted(vs))}")
            if not any_stored:
                player.tell("No stored verbs in this world.")
            return
        name = self._verb_target(player, argstr.strip())
        if name is None:
            player.tell(f"No such class or object: {argstr}")
            return
        from .classfactory import verb_shapes

        stored = self.world.classes.stored_verbs(name)
        if not stored:
            player.tell(f"{name} has no stored verbs.")
            return
        for vname, spec in sorted(stored.items()):
            shapes = ", ".join(
                f"{'|'.join(s['verb'])}/{s.get('dobj') or '-'}"
                f"/{'|'.join(s.get('prep') or []) or '-'}/{s.get('iobj') or '-'}"
                for s in verb_shapes(spec)
            )
            player.tell(f"{name}.{vname} [{shapes}] by {spec.get('author')}")

    @make_command("@mixins", "any")
    @make_command("@mixins")
    def show_mixins(self, player, what=None, prep=None, verb=None):
        """@mixins <thing> -- what a thing is composed of, and what else it
        could be."""
        vocab = ", ".join(self.world.classes.mixin_names())
        if not what:
            player.tell(f"Available mixins: {vocab}")
            return
        target = player.my_match_object(what)
        if target is None:
            player.tell(f"You can't see anything matching {what}")
            return
        cls = target.__class__
        mixins = list(cls.__dict__.get("_az_mixins") or [])
        player.tell(
            f"{target.name} is {cls.__name__}: base "
            f"{cls.__dict__.get('_az_base', cls.__name__)}"
            + (f" + {', '.join(mixins)}" if mixins else " (no mixins)")
        )
        player.tell(f"Available mixins: {vocab}")

    @make_command("@rename", "any", "to", "any")
    def rename(self, player, what, new_name, prep=None, verb=None):
        if not what:
            player.tell("You need to specify an object.")
        elif not new_name:
            player.tell("You need to specify a new name.")
        else:
            # directly change the class
            what.name = new_name
            what._save()
            self.world.mark_thing_changed(what)
            player.tell(f"You rename {what} to {new_name}.")

    @make_command("@create", "any", "as", "any")
    def create(self, player, what, new_class, prep=None, verb=None):
        if not what:
            player.tell("You need to specify an object.")
        elif not new_class:
            player.tell("You need to specify a new class.")
        else:
            # Through the factory, not import_class: a composed class
            # (PositionableVehicleObject, ...) has no module to import from.
            try:
                nc = self.world.compose(new_class)
            except Exception:
                nc = None
            if nc is not None:
                obj = nc(None, self.world, {"name": what}, recursive=False)
                obj.move_to(player)
                obj._save()
                player.tell(f"You create {what} as {new_class}.")
            else:
                player.tell(f"Could not find class {new_class}")

    @make_command("@dumpdb")
    def dump_database(self, player, prep=None, verb=None):
        """@dump database to disk"""
        self.tell("Dumping database...")
        self.world.dump_database()
        self.tell("Done.")

    @make_command("@messages", "any")
    def list_messages(self, player, target, prep=None, verb=None):
        """List all settable messages on target"""
        print(target)
        what = self.my_match_object(target)
        msgs = what.messages
        player.tell(f"Messages on {what.name}:")
        msgl = []
        for msg in msgs.items():
            msgl.append(f'  {msg[0].rjust(18)}:  "{msg[1]}"')
        player.tell("\n".join(msgl))
        player.tell(f"Total messages: {len(msgs)}")

    @make_command("@message", "any", "as", "any")
    def set_message(self, player, target, msg, prep=None, verb=None):
        """@message message_name on target as <string>"""
        (message_name, target) = target.split(" on ", 1)
        message_name = message_name.strip()
        target = target.strip()
        msg = msg.strip()
        if not message_name:
            player.tell("You need to specify a message name.")
            return
        what = self.my_match_object(target)
        if not what:
            player.tell(f"Could not find {target}")
            return
        what.messages[message_name] = msg
        what._save()
        player.tell(f"Set message {message_name} on {what.name} to {msg}")

    @make_command("@teleport", "any")
    def teleport(self, player, target, prep=None, verb=None):
        """@teleport to target location"""

        if target.startswith("#"):
            place = self.world.get_object_by_id(target[1:], Place)
        else:
            place = self.world.get_object_by_name(target, Place)

        if not place:
            player.tell(f"Could not find {target}")
            return
        else:
            player.tell(f"Teleporting to {place.name}")
            player.move_to(place)

    @make_command(["@desc", "@describe"], "any", "as", "any")
    def describe(self, player, what="", desc="", prep=None, verb=None):
        if player != self:
            player.tell("You can't describe that.")
        else:
            target = player.my_match_object(what)
            if not target:
                player.tell(f"You can't see '{what}' to describe it")
            elif not desc:
                player.tell("You need to give a description")
            else:
                target.description = desc
                target._save()
                player.tell(f"Set description of {target.name} to: {desc}")
