import copy
import inspect
import time
import uuid

from werkzeug.security import check_password_hash

from azimuth.command_decorator import make_command
from azimuth.mixins import (
    Containable,
    Holdable,
    Lockable,
    Openable,
    Positionable,
    Switchable,
    Wearable,
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
        """Dictionary for persistence: the base fields merged with the fields
        each state mixin contributes.  The mixins are consulted directly
        (unbound calls, exactly as ``state_summary`` does) because the diamond
        MROs would otherwise let BaseThing shadow their ``to_dict`` overrides
        -- which silently dropped open/locked/held/worn/positioned state on
        every save.  Each mixin's ``to_dict`` returns its own fields only."""
        data = {
            "id": self.id,
            "class": self.__class__.__name__,
            "name": self.name,
            "aliases": self.aliases,
            "description": self.description,
            "location": self.location.id if self.location else None,
            "contents": [x.id for x in self.contents],
            "messages": self._messages,
        }
        for mixin in (Openable, Lockable, Switchable, Holdable, Wearable, Positionable):
            if isinstance(self, mixin):
                data.update(mixin.to_dict(self))
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
        on/off, held/worn).  Mixins are consulted directly (unbound calls)
        because the diamond MROs would otherwise let BaseThing shadow them.
        Returns None when the thing has no state."""
        states = []
        for mixin in (Openable, Lockable, Switchable, Holdable, Wearable):
            if isinstance(self, mixin):
                s = mixin.state_summary(self)
                if s:
                    states.extend(s)
        return states or None

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
        if isinstance(self, (Player, WearableObject)):
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
            "state": self.state_summary(),
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

    def __init__(self, id, world, data, recursive=True):
        self.source = None
        self.destination = None
        super().__init__(id, world, data, recursive)
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

    @make_command(["go", "walk"], "self")
    @make_command(["go", "walk"], None, ["through"], "self")
    def use(self, player, prep=None, verb=None):
        """Moves a player through the exit to the destination."""
        if not player.location == self.source:
            player.tell(self.get_message("leave_fail_location", player))
        elif self.destination is None:
            player.tell(self.get_message("leave_fail_destination", player))
        else:
            dest = self.destination
            # Load up the next room
            if type(dest) is str:
                dest = self.world.get_object(dest)
                if dest is None:
                    player.tell(self.get_message("leave_fail_destination", player))
                    return
                else:
                    self.destination = dest

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


class OpenableExit(Exit, Openable):
    default_messages = {
        "go_fail_closed": "You cannot go through that, it's closed.",
        "open_destination": "{player} opens {self} from the other side.",
        "close_destination": "{player} closes {self} from the other side.",
    }

    def __init__(self, id, world, data, recursive=True):
        super().__init__(id, world, data, recursive)
        Openable.__init__(self, id, world, data, recursive)

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
        if self.open and self.destination:
            self.destination.announce(self.get_message("open_destination", player))

    @make_command("close", "self")
    def close(self, player, prep=None, verb=None):
        super().close(player)
        if self.open and self.destination:
            self.destination.announce(self.get_message("close_destination", player))

    def look_at(self, who):
        # Make contents visible if open
        # super() will call on the object hierarchy
        desc = super().look_at(who)
        # Now call the mixins independently
        d2 = Openable.look_at(self, who)
        return "\n".join([desc, d2])


# Need the overrides for open/close for exits
class LockableExit(OpenableExit, Lockable):
    def __init__(self, id, world, data, recursive=True):
        super().__init__(id, world, data, recursive)
        # super() only chains OpenableExit -> Exit; Lockable's own __init__
        # (is_locked, locked_by_*, ...) is never called through the diamond,
        # so invoke it explicitly -- same pattern as OpenableContainer.
        Lockable.__init__(self, id, world, data, recursive)


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
                print("failed match for location")
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
class Container(Object, Containable):
    """Represents an object that can hold other objects."""

    def look_at(self, who):
        # Make contents visible if open
        # super() will call on the object hierarchy
        desc = super().look_at(who)
        # Now call the mixins independently
        d2 = Containable.look_at(self, who)
        return "\n".join([desc, d2])


class OpenableContainer(Container, Openable):
    def __init__(self, id, world, data, recursive=False):
        super().__init__(id, world, data, recursive)
        Openable.__init__(self, id, world, data, recursive)


class PositionableObject(Object, Positionable):
    """Represents a furniture (or similar) object relative to which players and objects can
    be positioned (sit on a chair, lie under a table, put a plate on it...)."""

    def __init__(self, id, world, data, recursive=False):
        super().__init__(id, world, data, recursive)
        # super() only chains Object -> BaseThing; Positionable's own __init__
        # (self.positioned, ...) is never reached through the diamond, so call
        # it explicitly -- same pattern as OpenableContainer / HeldObject.
        Positionable.__init__(self, id, world, data, recursive)

    def look_at(self, who):
        desc = super().look_at(who)
        d2 = Positionable.look_at(self, who)
        if d2:
            desc += f"\n{d2}"
        return desc

    def take_ok(self, player):
        return False


class WearableObject(Object, Containable, Wearable):
    """Represents a clothing object that can be worn, with pockets."""

    def __init__(self, id, world, data, recursive=False):
        super().__init__(id, world, data, recursive)
        Wearable.__init__(self, id, world, data, recursive)

    def look_at(self, who):
        """If wearing the clothing, then show its contents."""
        desc = super().look_at(who)
        # Now call the mixins independently
        d2 = Wearable.contained_look_at(self, who)
        if d2:
            desc += f"\n{d2}"
        return desc

    def contained_look_at(self, who):
        return Wearable.contained_look_at(self, who)


class HeldObject(Object, Holdable):
    def __init__(self, id, world, data, recursive=False):
        super().__init__(id, world, data, recursive)
        Holdable.__init__(self, id, world, data, recursive)

    def contained_look_at(self, who=None):
        return Holdable.contained_look_at(self, who)


# --- Switchable Class ---
class SwitchableObject(Object, Switchable):
    """A lamp (or other light/electronic) that can be turned on and off."""

    def __init__(self, id, world, data, recursive=False):
        super().__init__(id, world, data, recursive)
        # super() only chains Object -> BaseThing; call Switchable.__init__
        # explicitly so is_on / on_paired_object are set (see PositionableObject).
        Switchable.__init__(self, id, world, data, recursive)

    def look_at(self, who):
        desc = super().look_at(who)
        d2 = Switchable.look_at(self, who)
        if d2:
            desc += f"\n{d2}"
        return desc


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
        elif isinstance(what, Exit) and what.source == self.location:
            return True
        else:
            return False

    def my_match_object(self, name):
        if name in ["me", "myself"]:
            return self
        elif name == "here":
            return self.location
        else:
            for x in [
                *self.contents,
                *self.location.contents,
                *self.location.exits.values(),
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

    @make_command("@chparent", "any", "to", "any")
    def change_parent(self, player, what, new_class, prep=None, verb=None):
        if not what:
            player.tell("You need to specify an object.")
        elif not new_class:
            player.tell("You need to specify a new class.")
        else:
            nc = self.world.import_class(new_class)
            if nc is not None:
                # get "what" --> object
                target = player.my_match_object(what)
                data = target.to_dict()
                data["class"] = new_class
                self.world.save(data)
                wid = target.id
                del self.world.active_objects[wid]
                del target
                target = self.world.load(wid)
                self.world.mark_thing_changed(target)
                player.tell(f"You change the parent of {what} to {new_class}.")
            else:
                player.tell(f"Could not find class {new_class}")

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
            # directly change the class
            nc = self.world.import_class(new_class)
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
