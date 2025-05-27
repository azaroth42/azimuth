### Vase: containable, not openable
### Bag: containable, openable, not lockable
### Chest: containable, openable, lockable
### Small Window: not containable, openable, lockable?

from azimuth.decorator import make_command


class StateToggle:
    # Can we abstract open, locked, etc as state toggles

    default_commands = {}
    default_messages = {}

    def toggle_on(self, which, player):
        if not player.can_see(self):
            player.tell(self.get_message("fail_visible", player))
        elif getattr(self, f"is_{which}"):
            player.tell(self.get_message(f"toggle_{which}_fail_true", player))
        else:
            setattr(self, f"is_{which}", True)
            player.tell(self.get_message(f"toggle_{which}_on", player))
            player.location.announce_all_but(self.get_message(f"toggle_{which}_on_others", player), player)
            return True
        return False

    def toggle_off(self, which, player):
        if not player.can_see(self):
            player.tell(self.get_message("fail_visible", player))
        elif not getattr(self, f"is_{which}"):
            player.tell(self.get_message(f"toggle_{which}_fail_false", player))
        else:
            setattr(self, f"is_{which}", False)
            player.tell(self.get_message(f"toggle_{which}_off", player))
            player.location.announce_all_but(self.get_message(f"toggle_{which}_off_others", player), player)
            return True
        return False

    def register_message(self, which, msg):
        self.default_messages[which] = msg


### FIXME
# open self with <str>
# pull lever / raise lever / push button = some other toggle on paired object


class Openable(StateToggle):
    """A mixin for openable/closable things, eg container, exit, some spaces)"""

    default_messages = {
        "open_look_at": "It is open.",
        "closed_look_at": "It is closed.",
        "toggle_open_fail_true": "That is already open.",
        "toggle_open_fail_false": "That is already closed.",
        "toggle_open_off": "You close {self}.",
        "toggle_open_off_others": "{player} closes {self}",
        "toggle_open_on": "You open {self}.",
        "toggle_open_on_others": "{player} opens {self}.",
    }

    def __init__(self, id, world, data, recursive=False):
        self.is_open = data.get("open", True)
        lpo = data.get("open_paired_object", None)
        if lpo is not None:
            lpo = world.get_object(lpo)
        self.open_paired_object = lpo

    @make_command("open", "self")
    def open(self, player, prep=None, verb=None):
        ok = self.toggle_on("open", player)
        if ok and self.open_paired_object is not None:
            self.open_paired_object.is_open = True

    @make_command("close", "self")
    def close(self, player, prep=None, verb=None):
        """Allow a player to close this."""
        ok = self.toggle_off("open", player)
        if ok and self.open_paired_object is not None:
            self.open_paired_object.is_open = False

    def look_at(self, player):
        if self.is_open:
            return self.get_message("open_look_at", player)
        else:
            return self.get_message("closed_look_at", player)

    def to_dict(self):
        """Returns a dictionary representation of the exit."""
        data = super().to_dict()
        data.update(
            {
                "open": self.is_open,
                "open_paired_object": self.open_paired_object.id if self.open_paired_object else None,
            }
        )
        return data


class Lockable(Openable):
    """A mixin for lockable/unlockable Openables"""

    default_messages = {
        "lock_fail_open": "You must close {self} first before locking it.",
        "open_fail_locked": "You must unlock {self} first before opening it.",
        "lock_fail_player": "You cannot lock {self}",
        "locked_look_at": "It is locked.",
        "unlocked_look_at": "It is unlocked.",
    }

    def __init__(self, id, world, data, recursive=False):
        super().__init__(world, data, data, recursive)
        self.is_locked = data.get("is_locked", False)
        lbo = data.get("locked_by_object", None)
        if lbo is not None:
            lbo = world.get_object(lbo)
        self.locked_by_object = lbo
        lbp = data.get("locked_by_player", None)
        if lbp is not None:
            lbp = world.get_object(lbp)
        self.locked_by_player = lbp
        lpo = data.get("lock_paired_object", None)
        if lpo is not None:
            lpo = world.get_object(lpo)
        self.lock_paired_object = lpo

    @make_command("open", "self")
    def open(self, player, prep=None, verb=None):
        if self.locked:
            player.tell(self.get_message("open_fail_locked", player))
        else:
            return super().open(player)

    @make_command("lock", "self")
    def lock(self, player, prep=None, verb=None):
        if self.open:
            player.tell(self.get_message("lock_fail_open", player))
        elif self.locked_by_player not in [None, player]:
            player.tell(self.get_message("lock_fail_player", player))
        self.toggle_on("locked", player)

    @make_command("unlock", "self")
    def unlock(self, player, prep=None, verb=None):
        """Allow a player to close this."""
        if self.locked_by_player not in [None, player]:
            player.tell(self.get_message("unlock_fail_player", player))
        self.toggle_off("locked", player)

    @make_command("lock", "self", ["with", "using"], "Object")
    def lock_with(self, player, prep=None, verb=None):
        if self.open:
            player.tell(self.get_message("lock_fail_open", player))
        elif self.locked_by_player not in [None, player]:
            player.tell(self.get_message("lock_fail_player", player))
        elif self.locked_by_object is None:
            player.tell(self.get_message("lock_fail_no_object", player))
        else:
            # is iobj == self.locked_by_object

            player.tell(self.get_message("lock_fail_object", player))
            self.toggle_on("locked", player)

    @make_command("unlock", "self", ["with", "using"], "Object")
    def unlock_with(self, player, prep=None, verb=None):
        """Allow a player to close this."""
        if self.locked_by_player not in [None, player]:
            player.tell(self.get_message("unlock_fail_player", player))
        elif self.locked_by_object is None:
            player.tell(self.get_message("unlock_fail_no_object", player))
        else:
            self.toggle_off("locked", player)

    def look_at(self, player):
        if self.locked:
            return self.get_message("locked_look_at", player)
        else:
            return self.get_message("unlocked_look_at", player)

    def to_dict(self):
        """Returns a dictionary representation of the exit."""
        data = super().to_dict()
        data.update(
            {
                "is_locked": self.is_locked,
                "locked_by_object": self.locked_by_object.id if self.locked_by_object else None,
                "locked_by_player": self.locked_by_player.id if self.locked_by_player else None,
                "lock_paired_object": self.lock_paired_object.id if self.lock_paired_object else None,
            }
        )
        return data


class Containable:
    ### Mixin for object that can contain things

    default_messages = {
        "take_from": "You take {object} from {self}",
        "take_from_others": "{player} takes {object} from {self}.",
        "put_in": "You put {object} in {self}.",
        "put_in_others": "{player} puts {object} in {self}",
    }

    def my_match_object(self, name):
        for x in self.contents:
            if x.match_object(name):
                return x
        return None

    @make_command(["put"], "Object", "in", "self")
    def put_in(self, player, target, prep=None, verb=None):
        # move target from (player|here) to self
        what = player.my_match_object(target)
        if not what:
            player.tell(f"You can't see anything matching {target}")
        else:
            what.move_to(self)
            player.tell(self.get_message("put_in", player, what))
            player.location.announce_all_but(self.get_message("put_in_others", player, what), player)

    @make_command(["take", "get", "remove"], "Object", "from", "self")
    def take_from(self, player, target, prep=None, verb=None):
        # move target from self to player
        what = self.my_match_object(target)
        if not what:
            player.tell(f"You can't see anything matching {target} in {self.name}")
        else:
            what.move_to(player)
            player.tell(self.get_message("take_from", player, what))
            player.location.announce_all_but(self.get_message("take_from_others", player, what), player)

    @make_command(["look", "l"], "any", "in", "self")
    def look_at_in(self, player, target=None, prep=None, verb=None):
        if not hasattr(self, "is_open") or self.is_open:
            # okay to look
            pass
        else:
            player.tell(self.get_message("look_in_fail_closed", player))

    def look_at(self, player):
        # contents
        conts = ", ".join([x.name for x in self.contents])
        if not hasattr(self, "is_open") or self.is_open:
            return f"Inside there is: {conts}"
        else:
            return ""


# Mixin for things that can be somehow on or off.
# e.g. lever, switch, button, curtains (?), tv (??)
class Switchable(StateToggle):
    def __init__(self, id, world, data, recursive=False):
        super().__init__(world, data, data, recursive)
        self.is_on = data.get("is_on", False)


# table, bike/horse, chair, platform etc.


class Positionable:
    def position_self(self, player, prep=None, verb=None):
        pass

    def position_object(self, player, target, prep=None, verb=None):
        pass
