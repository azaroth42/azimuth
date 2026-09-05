### Vase: containable, not openable
### Bag: containable, openable, not lockable
### Chest: containable, openable, lockable
### Small Window: not containable, openable, lockable?

from azimuth.command_decorator import make_command


def join_look(*parts):
    """Join the fragments a cooperative ``look_at`` chain contributes, dropping
    the empty ones.  Every mixin below appends its own line to whatever its
    ``super()`` produced, so a synthesized class composes its description with
    no per-combination glue (see ARCHITECTURE.md 6.2)."""
    return "\n".join([p for p in parts if p])


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
            player.location.announce_all_but(
                self.get_message(f"toggle_{which}_on_others", player), player
            )
            self.world.mark_thing_changed(self)
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
            player.location.announce_all_but(
                self.get_message(f"toggle_{which}_off_others", player), player
            )
            self.world.mark_thing_changed(self)
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

    def __init__(self, id, world, data, recursive=True):
        # Cooperative: run the rest of the MRO (ultimately BaseThing.__init__)
        # first, so self.id/location exist before this mixin resolves its own
        # object references, then set this mixin's fields.
        #
        # `recursive` defaults to True to match BaseThing/Place/Exit/Object.
        # A mixin now leads the MRO, so *its* signature is the one direct
        # construction hits -- a False default here silently stopped an
        # OpenableExit from resolving its destination (it stayed a bare id
        # string, and open/close then blew up announcing to it).
        super().__init__(id, world, data, recursive)
        self.is_open = data.get("open", True)
        lpo = data.get("open_paired_object", None)
        if lpo is not None:
            lpo = world.get_object(lpo)
        self.open_paired_object = lpo

    def state_summary(self):
        return super().state_summary() + [
            "open" if getattr(self, "is_open", True) else "closed"
        ]

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
            self.world.mark_thing_changed(self.open_paired_object)

    def look_at(self, who):
        return join_look(
            super().look_at(who),
            self.get_message("open_look_at" if self.is_open else "closed_look_at", who),
        )

    def to_dict(self):
        data = super().to_dict()
        data.update(
            {
                "open": self.is_open,
                "open_paired_object": self.open_paired_object.id
                if self.open_paired_object
                else None,
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
        # StateToggle.toggle_on/off("locked") emits these; without them
        # get_message fell back to "" and lock/unlock answered with a blank
        # line (ARCHITECTURE.md 8).
        "toggle_locked_fail_true": "That is already locked.",
        "toggle_locked_fail_false": "That is already unlocked.",
        "toggle_locked_on": "You lock {self}.",
        "toggle_locked_on_others": "{player} locks {self}.",
        "toggle_locked_off": "You unlock {self}.",
        "toggle_locked_off_others": "{player} unlocks {self}.",
    }

    def __init__(self, id, world, data, recursive=True):
        super().__init__(id, world, data, recursive)
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

    def state_summary(self):
        return super().state_summary() + [
            "locked" if self.is_locked else "unlocked"
        ]

    @make_command("open", "self")
    def open(self, player, prep=None, verb=None):
        if self.is_locked:
            player.tell(self.get_message("open_fail_locked", player))
        else:
            return super().open(player)

    @make_command("lock", "self")
    def lock(self, player, prep=None, verb=None):
        # Note: self.open must be self.is_open -- on a LockableExit the name
        # 'open' resolves to the OpenableExit.open *method* (always truthy).
        if self.is_open:
            player.tell(self.get_message("lock_fail_open", player))
            return
        elif self.locked_by_player not in [None, player]:
            player.tell(self.get_message("lock_fail_player", player))
            return
        self.toggle_on("locked", player)

    @make_command("unlock", "self")
    def unlock(self, player, prep=None, verb=None):
        """Allow a player to close this."""
        if self.locked_by_player not in [None, player]:
            player.tell(self.get_message("unlock_fail_player", player))
        self.toggle_off("locked", player)

    @make_command("lock", "self", ["with", "using"], "Object")
    def lock_with(self, player, prep=None, verb=None):
        if self.is_open:
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

    def look_at(self, who):
        # `self.is_locked`, not `self.locked` -- this method was unreachable
        # under the old diamond MRO, so the typo never raised.  Now that the
        # chain runs it, the attribute has to be the real one.
        return join_look(
            super().look_at(who),
            self.get_message(
                "locked_look_at" if self.is_locked else "unlocked_look_at", who
            ),
        )

    def to_dict(self):
        data = super().to_dict()
        data.update(
            {
                "is_locked": self.is_locked,
                "locked_by_object": self.locked_by_object.id
                if self.locked_by_object
                else None,
                "locked_by_player": self.locked_by_player.id
                if self.locked_by_player
                else None,
                "lock_paired_object": self.lock_paired_object.id
                if self.lock_paired_object
                else None,
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

    def my_match_object(self, name, player):
        for x in self.contents:
            if x.match_object(name, player):
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
            player.location.announce_all_but(
                self.get_message("put_in_others", player, what), player
            )

    @make_command(["take", "get", "remove"], "Object", "from", "self")
    def take_from(self, player, target, prep=None, verb=None):
        # move target from self to player
        what = self.my_match_object(target, player)
        if not what:
            player.tell(f"You can't see anything matching {target} in {self.name}")
        else:
            what.move_to(player)
            player.tell(self.get_message("take_from", player, what))
            player.location.announce_all_but(
                self.get_message("take_from_others", player, what), player
            )

    @make_command(["look", "l"], "any", "in", "self")
    def look_at_in(self, player, target=None, prep=None, verb=None):
        if not hasattr(self, "is_open") or self.is_open:
            # okay to look
            pass
        else:
            player.tell(self.get_message("look_in_fail_closed", player))

    def look_at(self, who):
        desc = super().look_at(who)
        # A closed container (Openable mixed in alongside) reveals nothing.
        if hasattr(self, "is_open") and not self.is_open:
            return desc
        conts = ", ".join([x.name for x in self.contents])
        return join_look(desc, f"Inside there is: {conts}")


# Mixin for things that can be turned on or off: a lamp, light switch, radio,
# phone, ...  The flip itself reuses StateToggle (can_see check, already-on/off
# handling, announcement to the room, dirty marking) exactly as Openable and
# Lockable do.
class Switchable(StateToggle):
    default_messages = {
        "toggle_on_fail_true": "{self} is already on.",
        "toggle_on_fail_false": "{self} is already off.",
        "toggle_on_on": "You turn {self} on.",
        "toggle_on_on_others": "{player} turns {self} on.",
        "toggle_on_off": "You turn {self} off.",
        "toggle_on_off_others": "{player} turns {self} off.",
        "on_look_at": "It is on.",
        "off_look_at": "It is off.",
    }

    def __init__(self, id, world, data, recursive=True):
        super().__init__(id, world, data, recursive)
        self.is_on = data.get("is_on", False)
        # Optional: a device this one drives when toggled (e.g. a wall switch
        # paired with the lamp it controls), mirroring Openable.open_paired_object.
        po = data.get("on_paired_object", None)
        self.on_paired_object = world.get_object(po) if po else None

    def to_dict(self):
        data = super().to_dict()
        data.update(
            {
                "is_on": self.is_on,
                "on_paired_object": self.on_paired_object.id
                if self.on_paired_object
                else None,
            }
        )
        return data

    def state_summary(self):
        return super().state_summary() + ["on" if self.is_on else "off"]

    # --- commands ---
    # Many natural phrasings for the same action; the handler (power) decides
    # on vs off from the preposition or the verb:
    @make_command(["turn", "switch", "power"], "", ["on", "off"], "self")  # turn on the lamp
    @make_command(["turn", "switch", "power"], "self", ["on", "off"])       # turn the lamp on
    @make_command(["activate", "deactivate"], "self")                       # activate the lamp
    @make_command(["on", "off"], "self")                                    # on the lamp
    def power(self, player, target=None, prep=None, verb=None):
        """Turn this thing on or off (and drive its paired device, if any)."""
        if prep == "off" or verb in ("deactivate", "off"):
            want = False
        elif prep == "on" or verb in ("activate", "on"):
            want = True
        else:
            return  # unreachable: every registered shape names on or off
        ok = self.toggle_on("on", player) if want else self.toggle_off("on", player)
        if ok and self.on_paired_object is not None:
            self.on_paired_object.is_on = want
            self.world.mark_thing_changed(self.on_paired_object)

    def look_at(self, who):
        return join_look(
            super().look_at(who),
            self.get_message("on_look_at" if self.is_on else "off_look_at", who),
        )


# --- Positionable ----------------------------------------------------------
# Relative positions a thing can take with respect to a Positionable, and the
# prepositions that map to each (the parser hands us the raw preposition).
POSITIONS = {
    "on": "on",
    "at": "at",
    "against": "against",
    "under": "under",
    "beside": "beside",
    "next to": "beside",
}
POSITION_PREPS = sorted(POSITIONS.keys())

# posture verb -> progressive form, for "Bob is sitting on the table"
# (consumed by Positionable.posture_ing)
_POSTURE_ING = {
    "sit": "sitting",
    "stand": "standing",
    "lean": "leaning",
    "kneel": "kneeling",
    "crouch": "crouching",
    "lie": "lying",
}


class Positionable:
    """Mixin for things that act as an anchor/surface you can be positioned
    relative to: a table, chair, bed, wall, platform.  A player may *sit /
    stand / lean / kneel / crouch / lie* **on / at / against / under /
    beside** it, and any object may be *put / placed* there.

    Positioning is a **display relation**, distinct from containment: a
    positioned thing keeps its real location (its room or carrier) and is
    merely *described* relative to this thing, so it shows up in the room
    description.  A thing is positioned relative to at most one Positionable,
    and only while it shares that Positionable's location.  (A chest of
    drawers, a wardrobe, a window can be a Positionable *and* a Container /
    Exit at the same time.)
    """

    default_messages = {
        "position_fail_location": "You can't do that to {self} from here.",
    }

    def __init__(self, id, world, data, recursive=True):
        super().__init__(id, world, data, recursive)
        self.positioned = {}  # position -> list of [thing, verb_or_None]
        for prep, entries in (data.get("positioned") or {}).items():
            pos = POSITIONS.get(prep, prep)
            lst = []
            for e in entries or []:
                if not isinstance(e, dict):
                    continue
                t = world.get_object(e.get("id")) if e.get("id") else None
                if t is not None:
                    lst.append([t, e.get("verb")])
            if lst:
                self.positioned[pos] = lst
        # Re-establish each child's back-reference so move_to() cleanup and
        # find_position() keep working after a load.
        for entries in self.positioned.values():
            for (t, _v) in entries:
                t._position_parent = self

    # --- persistence / state (consulted by BaseThing.to_dict) ---

    def to_dict(self):
        data = super().to_dict()
        data.update(
            {
                "positioned": {
                    pos: [{"id": t.id, "verb": v} for (t, v) in entries]
                    for pos, entries in self.positioned.items()
                }
            }
        )
        return data

    def state_summary(self):
        # A Positionable has no state of its own -- the position is reported
        # from the *child* side (see BaseThing.thing_summary).
        return super().state_summary()

    # --- positioning operations ---

    def _entries(self, pos):
        return self.positioned.setdefault(pos, [])

    def has_positioned(self, thing, pos=None):
        for p, entries in self.positioned.items():
            if pos is not None and p != pos:
                continue
            if any(t is thing for (t, _v) in entries):
                return True
        return False

    def add_positioned(self, thing, pos, verb):
        if self.has_positioned(thing, pos):
            return
        self._entries(pos).append([thing, verb])
        thing._position_parent = self
        self.world.mark_thing_changed(self)

    def remove_positioned(self, thing, pos=None):
        changed = False
        for p, entries in list(self.positioned.items()):
            if pos is not None and p != pos:
                continue
            keep = [(t, v) for (t, v) in entries if t is not thing]
            if len(keep) != len(entries):
                self.positioned[p] = keep if keep else []
                if not keep:
                    del self.positioned[p]
                changed = True
        if getattr(thing, "_position_parent", None) is self:
            thing._position_parent = None
        if changed:
            self.world.mark_thing_changed(self)

    # --- commands ---

    @make_command(
        ["sit", "stand", "lean", "kneel", "crouch", "lie"],
        "",
        POSITION_PREPS,
        "self",
    )
    def position_self(self, player, prep=None, verb=None):
        """The player takes a position relative to this thing: 'sit on the
        table', 'lie under the table', 'stand at the desk', ..."""
        if player.location is not self.location:
            player.tell(self.get_message("position_fail_location", player))
            return
        pos = POSITIONS.get(prep, prep)
        player._unposition()  # a thing occupies at most one spot
        self.add_positioned(player, pos, verb)
        player.tell(f"You {verb} {pos} {self.name}.")
        self.location.announce_all_but(
            f"{player.name} {verb} {pos} {self.name}.", player
        )

    @make_command(
        ["put", "place", "position", "set"],
        "any",
        POSITION_PREPS,
        "self",
    )
    def position_object(self, player, target, prep=None, verb=None):
        """Place an object relative to this thing: 'put the plate on the
        table', 'set the book under the table', ..."""
        if player.location is not self.location:
            player.tell(self.get_message("position_fail_location", player))
            return
        what = player.my_match_object(target)
        if what is None:
            player.tell(f"You can't see anything matching {target}.")
            return
        if what.location is not self.location and what.location is not player:
            player.tell(self.get_message("position_fail_location", player))
            return
        pos = POSITIONS.get(prep, prep)
        what._unposition()
        self.add_positioned(what, pos, None)
        player.tell(f"You put {what.name} {pos} {self.name}.")
        self.location.announce_all_but(
            f"{player.name} puts {what.name} {pos} {self.name}.", player
        )

    # --- display ---

    def posture_ing(self, verb):
        """'sit' -> 'sitting', 'lie' -> 'lying', etc.  Falls back to a naive
        'verb + ing' for verbs we don't special-case.  Override in a subclass
        to rephrase how postures read relative to this thing (e.g. a cushion:
        'sit' -> 'sprawled on')."""
        if not verb:
            return ""
        return _POSTURE_ING.get(verb, verb)

    def position_line(self, thing, pos, verb):
        """One room-description line for *thing* positioned relative to this
        thing: a positioned player gets their posture ('Bob is sitting on the
        table'), a placed object just its relation ('A plate is on the
        table').  Override in a subclass to change the phrasing."""
        if verb:
            return f"{thing.name} is {self.posture_ing(verb)} {pos} the {self.name}."
        return f"{thing.name} is {pos} the {self.name}."

    def look_at(self, who):
        """What is positioned relative to this thing (when you look at it)."""
        lines = [super().look_at(who)]
        for pos, entries in self.positioned.items():
            for (t, verb) in entries:
                lines.append(self.position_line(t, pos, verb))
        return join_look(*lines)


class Holdable:
    default_messages = {
        "wield": "You hold {self}.",
        "wield_others": "{player} holds {self}.",
        "wield_failed_wielding": "You cannot hold {self}, as you are already holding it.",
        "wield_failed_not_in_contents": "You cannot wield or remove {self}, as you are not carrying it.",
        "unwield": "You put away {self}.",
        "unwield_others": "{player} puts away {self}.",
        "unwield_failed_not_wielding": "You cannot put away {self}, as you are not holding it.",
    }

    def __init__(self, id, world, data, recursive=True):
        super().__init__(id, world, data, recursive)
        hb = data.get("held_by", None)
        self.held_by = world.get_object(hb) if hb else None

    def to_dict(self):
        data = super().to_dict()
        data.update({"held_by": self.held_by.id if self.held_by else None})
        return data

    def state_summary(self):
        s = super().state_summary()
        if getattr(self, "held_by", None) is not None:
            s = s + ["held"]
        return s

    def contained_look_at(self, who=None):
        return join_look(
            super().contained_look_at(who),
            f"Held: {self.name}" if self.held_by is not None else "",
        )

    @make_command(["wield", "hold"], "self")
    def wield(self, player, prep=None, verb=None):
        if self not in player.contents:
            player.tell(self.get_message("weild_failed_not_in_contents", player))
        elif self.held_by is not None:
            player.tell(self.get_message("wield_failed_wielding", player))
        else:
            self.held_by = player
            player.tell(self.get_message("wield", player))
            player.location.announce_all_but(
                self.get_message("wield_others", player), player
            )
            # Held state changed: the carrier's inventory (and, via the room
            # branch of mark_thing_changed, other players' view) must refresh.
            self.world.mark_thing_changed(self)

    @make_command(["unwield", "remove"], "self")
    def unwield(self, player, prep=None, verb=None):
        if self not in player.contents:
            player.tell(self.get_message("weild_failed_not_in_contents", player))
        elif self.held_by != player:
            player.tell(self.get_message("unwield_failed_not_wielding", player))
        else:
            self.held_by = None
            player.tell(self.get_message("unwield", player))
            player.location.announce_all_but(
                self.get_message("unwield_others", player), player
            )
            self.world.mark_thing_changed(self)


class Wearable:
    default_messages = {
        "wear": "You wear {self}.",
        "wear_others": "{player} puts on {self}.",
        "wear_failed_wearing": "You cannot put on {self}, as you are already wearing it.",
        "wear_failed_not_in_contents": "You cannot wear or remove {self}, as you are not carrying it.",
        "remove": "You take off {self}.",
        "remove_others": "{player} takes off {self}.",
        "remove_failed_not_wearing": "You cannot take off {self}, as you are not wearing it.",
    }

    def __init__(self, id, world, data, recursive=True):
        super().__init__(id, world, data, recursive)
        wb = data.get("worn_by", None)
        self.worn_by = world.get_object(wb) if wb else None

    def to_dict(self):
        data = super().to_dict()
        data.update({"worn_by": self.worn_by.id if self.worn_by else None})
        return data

    def contained_look_at(self, who=None):
        return join_look(
            super().contained_look_at(who),
            f"Worn: {self.name}" if self.worn_by is not None else "",
        )

    def state_summary(self):
        s = super().state_summary()
        if getattr(self, "worn_by", None) is not None:
            s = s + ["worn"]
        return s

    def look_at(self, who):
        # Wearable alone reports the worn line on `look at <garment>`; Holdable
        # deliberately does not (a held thing reads as held only from the
        # carrier's side), which is the behaviour the old composites had.
        return join_look(super().look_at(who), self.contained_look_at(who))

    @make_command("wear", "self")
    def wear(self, player, prep=None, verb=None):
        # Need to be in inventory to wear
        if self not in player.contents:
            player.tell(self.get_message("wear_failed_not_in_contents", player))
        elif self.worn_by is not None:
            player.tell(self.get_message("wear_failed_wearing", player))
        else:
            self.worn_by = player
            player.tell(self.get_message("wear", player))
            player.location.announce_all_but(
                self.get_message("wear_others", player), player
            )
            # Worn state changed: refresh the carrier's inventory and the room.
            self.world.mark_thing_changed(self)

    @make_command("remove", "self")
    def remove(self, player, prep=None, verb=None):
        if self not in player.contents:
            player.tell(self.get_message("wear_failed_not_in_contents", player))
        elif self.worn_by != player:
            player.tell(self.get_message("remove_failed_not_wearing", player))
        else:
            self.worn_by = None
            player.tell(self.get_message("remove", player))
            player.location.announce_all_but(
                self.get_message("remove_others", player), player
            )
            self.world.mark_thing_changed(self)
