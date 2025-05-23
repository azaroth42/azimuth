import uuid
from werkzeug.security import check_password_hash
import inspect
import copy
from mixins import Openable, Lockable, Containable
from decorator import make_command
from flask_socketio import disconnect
import time

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
        """Returns a dictionary representation of the base thing."""
        return {
            "id": self.id,
            "class": self.__class__.__name__,
            "name": self.name,
            "aliases": self.aliases,
            "description": self.description,
            "location": self.location.id if self.location else None,
            "contents": [x.id for x in self.contents],
            "messages": self._messages,
        }

    def _save(self):
        d = self.to_dict()
        self.world.save(d)

    def look_at(self, who=None):
        """Returns the description of the thing."""
        return self.description

    def move_to(self, where):
        """Update location and contents"""
        if self.location is not None:
            self.location.contents.remove(self)
            self.location.on_leave(self)
        self.location = where
        if self.location is not None:
            self.location.contents.append(self)
            where.on_enter(self)

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

    def match_object(self, name, player):
        # does name match this object?
        if name == "me" and player == self:
            return 1
        elif name == "here" and player.location == self:
            return 1

        names = [self.name.lower()]
        for a in self.aliases:
            names.append(a.lower())
        if ' ' in self.name:
            namebits = self.name.lower().split()
            if not namebits[-1] in names:
                names.append(namebits[-1])

        if type(name) == str:
            name = name.lower()
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
            self.default_messages.get(which, self.world.default_messages.get(which, "")),
        )
        # FIXME: allow more flexible messages
        if what is not None:
            # player has used key on chest
            return msg.format(**{"player": who.name, "self": self.name, "object": what.name})
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
                for (vb,info) in c.default_commands.items():
                    try:
                        cmds[vb].extend(copy.deepcopy(info))
                    except:
                        cmds[vb] = copy.deepcopy(info)
            for k, v in self.commands.items():
                try:
                    cmds[k].extend(v)
                except:
                    cmds[k] = v
            self.commands_cached = cmds
        if match is None:
            return cmds
        else:
            c = cmds.get(match, [])
            return {match: c}

    # for custom commands
    def register_command(self, info):
        verbs = info["verb"]
        if type(verbs) is str:
            verbs = [verbs]
        for verb in verbs:
            try:
                self.commands[verb].append(info)
            except:
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
            self.world.join_room(self, what)

    def on_leave(self, what):
        super().on_leave(what)
        if isinstance(what, Player):
            self.world.leave_room(self, what)

    def announce(self, msg):
        self.world.announce(msg, self)

    def announce_all_but(self, msg, who):
        if who is None:
            self.announce(msg)
        elif isinstance(who, Player):
            self.world.announce(msg, self, who)
        elif isinstance(who, list):
            # need to tell all contents if not in who
            for c in self.contents:
                if c not in who and hasattr(c, 'tell'):
                    c.tell(msg)


    def look_at(self, player):
        """Generates a description of the place, its contents, and exits"""
        desc = []
        desc.append(f"--- {self.name} ---")
        desc.append(super().look_at(player))
        desc.append("")

        # List visible contents (excluding the player looking)
        visible_content_names = []
        for item in self.contents:
            if item != player:  # Don't list the player themselves
                if player.can_see(item):
                    visible_content_names.append(item.name)
        if visible_content_names:
            desc.append(f"You see here: {', '.join(visible_content_names)}.\n")
        else:
            desc.append("The place looks empty.\n")

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
        dest = world.get_object(data["destination"])
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
            player.tell(self.get_message("leave", player))
            player.move_to(dest)
            if player.location == dest:
                self.source.announce(self.get_message("leave_others", player))
                self.destination.announce_all_but(self.get_message("arrive_others", player), player)
            else:
                player.tell(self.get_message("leave_fail_destination", player))

    def to_dict(self):
        """Returns a dictionary representation of the exit."""
        data = super().to_dict()
        data.update(
            {
                "source": self.source.id if self.source else None,
                "destination": self.destination.id if self.destination else None,
            }
        )
        return data



class OpenableExit(Exit, Openable):

    default_messages = {
        "go_fail_closed": "You cannot go through that, it's closed.",
        "open_destination": "{player} opens {self} from the other side.",
        "close_destination": "{player} closes {self} from the other side."
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
    pass


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
        "use_others": "{player} uses {self}."
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
                player.location.announce_all_but(self.get_message("take_others", player), player)
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
                player.location.announce_all_but(self.get_message("drop_others", player), player)
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
            player.location.announce_all_but(self.get_message("use_others", player), player)


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


# --- Player Class ---
class Player(BaseThing):
    """Represents a player connected and logged into the MUD."""

    connection = None

    def __init__(self, id, world, data, recursive=True):
        self.connection = None
        self.home = None
        super().__init__(id, world, data, recursive)
        self.username = data["username"]
        self.password_hash = data["password_hash"]
        self.last_location = data.get("last_location", None)
        self.command_sets = []
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
                "password_hash": self.password_hash
            }
        )
        if type(self.last_location) is str:
            data["last_location"] = self.last_location
        elif self.last_location:
            data['last_location'] = self.last_location.id
        else:
            data['last_location'] = None
        return data

    def tell(self, message):
        if self.connection:
            self.world.tell_player(self, message)

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
            for x in [*self.contents, *self.location.contents]:
                if x.match_object(name, self):
                    return x
        return None

    @make_command(["i", "inv", "inventory"])
    def inventory(self, player, prep=None, verb=None):
        if player != self:
            player.tell("You can't inventory someone else")
        elif not self.contents:
            player.tell("You are not carrying anything")
        else:
            stuff = ", ".join([x.name for x in self.contents])
            player.tell(f"You are carrying: {stuff}")

    @make_command("say", "all")
    def say(self, argstr, prep=None, verb=None):
        """Allows a player to say something in their current location."""
        if not argstr:
            self.tell("You need to give something to say")
        elif not self.location:
            self.tell("You are not in a place where you can speak.")
        else:
            self.tell(f"You say, \"{argstr}\"")
            self.location.announce_all_but(f"{self.name} says, \"{argstr}\"", self)

    @make_command("emote", "all")
    def emote(self, argstr, prep=None, verb=None):
        """Allows a player to emote something in their current location."""
        if not argstr:
            self.tell("You need to give something to emote")
        elif not self.location:
            self.tell("You are not in a place where you can emote.")
        else:
            self.location.announce(f"{self.name} {argstr}")

    @make_command(["wh", "whisper"], "all", "to", "Player")
    def whisper(self, argstr, target, prep=None, verb=None):
        pass

    @make_command("@who")
    def who(self, player, target=None, prep=None, verb=None):
        for p in self.world.active_sids.values():
            pl = self.world.active_objects[p]
            player.tell(f"{pl.name}    {pl.location.name}   {int(time.time() - pl.last_active_time)} seconds ago")

    @make_command("@quit")
    def quit(self, player, target=None, prep=None, verb=None):
        self.tell("Goodbye!")
        disconnect(player.connection)

    @make_command(["@desc", "@describe"], "self", "as", "any")
    def describe(self, player, target="", prep=None, verb=None):
        if player != self:
            player.tell("You can't describe that.")
        else:
            target = target.replace("\\n", "\n")
            self.description = target
            self.tell(f"Your new description: {self.description}")

    @make_command("@home")
    def home(self, player, target=None, prep=None, verb=None):
        if not self.home:
            player.tell("You don't have a home.")
        elif self.home == self.location:
            player.tell("You are already at home.")
        else:
            self.tell(f"You tap your heels three times...")
            self.move_to(self.home)

    @make_command("@sethome")
    def set_home(self, player, target=None, prep=None, verb=None):
        if self.home == self.location:
            player.tell("You are already at home.")
        else:
            self.home = self.location
            player.tell("You set this location to be your home")


class Programmer(Player):

    @make_command(["eval", "@eval"], "all")
    def eval(self, argstr, prep=None, verb=None):
        player = self
        here = player.location
        res = eval(argstr)
        print(res)
        self.tell(repr(res))

    @make_command(["@dig", "any", "to", "any"])
    def dig(self, player, exits, room, prep=None, verb=None):
        pass

    @make_command("@messages", "any")
    def list_messages(self, player, target, prep=None, verb=None):
        pass

    @make_command("@message", "any", "as", "any")
    def set_message(self, player, target, prep=None, verb=None):
        pass
