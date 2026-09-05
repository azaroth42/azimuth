"""Composed classes: base + mixins, resolved (or built) at runtime.

Covers the four moving parts of classfactory.py -- normalization and naming,
what gets persisted, the storage class filter, and the two things a programmer
can do from inside the MUD (@addmixin / @rmmixin and @verb) -- plus the traps
that made each of them non-obvious.
"""

from azimuth.classfactory import CANON, normalize
from azimuth.entities import Container, Object, Place
from azimuth.mixins import Containable, Lockable, Openable

from .framework import AzimuthTest, TestWorld


class ComposeTest(AzimuthTest):
    """Shared fixture.  The test world is a copy of the real db/, which play
    rearranges and can recompose, so these tests build their own subject in
    the start room rather than borrowing the demo gem or chest."""

    def subject(self, name="test widget", cls_name="Object", **data):
        w = self.tw.world
        start = w.get_object(w.config["start_room_id"])
        obj = w.compose(cls_name)(
            None, w, {"name": name, "description": "A test widget.",
                      "location": start.id, **data}
        )
        obj._save()
        w.flush_state()
        return obj


class NormalizeTest(ComposeTest):
    def test_redundant_ancestor_dropped(self):
        """Lockable implies Openable, so naming both is the same combination
        as naming Lockable -- otherwise one thing could have two classes."""
        assert normalize(["Openable", "Lockable"]) == ("Lockable",)
        assert normalize(["Lockable", "Openable"]) == ("Lockable",)

    def test_sorted_and_deduplicated(self):
        assert normalize(["Wearable", "Containable", "Wearable"]) == (
            "Containable",
            "Wearable",
        )

    def test_unknown_mixin_rejected(self):
        """`mixins` comes from the database; an arbitrary importable name
        would be an arbitrary-base-class injection."""
        w = self.tw.world
        try:
            w.classes.resolve("Object", ["os.system"])
        except Exception:
            return
        raise AssertionError("an unknown mixin name must not resolve")


class ResolutionTest(ComposeTest):
    def test_handwritten_class_wins(self):
        """A combination with a class in the codebase uses it, keeping its
        overrides, rather than being synthesized."""
        w = self.tw.world
        assert w.classes.resolve("Object", ["Containable"]) is Container
        for (base, mixins), name in CANON.items():
            cls = w.classes.resolve(base, mixins)
            assert cls.__name__ == name, f"{base}+{list(mixins)} -> {cls.__name__}"

    def test_new_combination_is_built(self):
        """A combination with no hand-written class is composed on the spot,
        mixins first, and inherits every mixin's verbs."""
        w = self.tw.world
        cls = w.classes.resolve("Object", ["Containable", "Lockable"])
        assert cls.__name__ == "ContainableLockableObject"
        assert issubclass(cls, Containable) and issubclass(cls, Lockable)
        mro = [c.__name__ for c in cls.__mro__]
        assert mro.index("Containable") < mro.index("BaseThing"), mro
        verbs = cls(None, w, {"name": "strongbox"}).get_commands()
        for v in ("put", "take", "lock", "unlock", "open", "close"):
            assert v in verbs, f"{v} missing from {sorted(verbs)}"

    def test_same_combination_is_the_same_class(self):
        w = self.tw.world
        a = w.classes.resolve("Exit", ["Lockable"])
        b = w.classes.resolve("Exit", ["Openable", "Lockable"])
        assert a is b


class PersistenceTest(ComposeTest):
    def _reload(self, obj):
        """Round-trip an object through storage, bypassing the live cache."""
        w = self.tw.world
        obj._save()
        del w.active_objects[obj.id]
        return w.load(obj.id)

    def test_stored_as_base_plus_mixins(self):
        """A synthesized class name is not importable, so what goes to disk is
        the combination, not the class name."""
        w = self.tw.world
        cls = w.classes.resolve("Object", ["Containable", "Lockable"])
        data = cls(None, w, {"name": "strongbox"}).to_dict()
        assert data["class"] == "Object"
        assert data["mixins"] == ["Containable", "Lockable"]

    def test_handwritten_composite_stored_the_same_way(self):
        box = self.subject("test box", "Container")
        data = box.to_dict()
        assert data["class"] == "Object" and data["mixins"] == ["Containable"]

    def test_legacy_class_name_still_loads(self):
        """Worlds on disk record {"class": "OpenableContainer"} with no
        mixins; that has to keep resolving."""
        w = self.tw.world
        w.save({"id": "legacy-1", "class": "OpenableContainer", "name": "old bag"})
        obj = w.load("legacy-1")
        assert obj.__class__.__name__ == "OpenableContainer"
        assert obj.to_dict()["mixins"] == ["Containable", "Openable"]

    def test_mixin_state_survives_a_round_trip(self):
        w = self.tw.world
        cls = w.classes.resolve("Object", ["Containable", "Lockable"])
        box = cls(None, w, {"name": "strongbox", "open": False})
        box.is_locked = True
        again = self._reload(box)
        assert type(again) is cls
        assert again.is_open is False and again.is_locked is True

    def test_subclass_does_not_inherit_the_stamp(self):
        """A hand-written subclass of a composed class must persist as itself,
        not as the ancestor whose combination it inherited."""

        class BigChest(Container):
            pass

        w = self.tw.world
        assert BigChest(None, w, {"name": "big chest"}).to_dict()["class"] == "BigChest"


class ClassFilterTest(ComposeTest):
    """The storage `clss` filter is isinstance semantics now: a stored object
    records a base plus mixins, so a name comparison could not match it."""

    def test_filter_matches_base_and_composite(self):
        w = self.tw.world
        self.subject("test box", "Container")
        assert w.db.get_object_by_name("test box", Container) is not None
        assert w.db.get_object_by_name("test box", Object) is not None
        assert w.db.get_object_by_name("test box", Place) is None

    def test_filter_rejects_a_more_derived_class(self):
        w = self.tw.world
        self.subject("test box", "Container")
        lockbox = w.classes.resolve("Object", ["Containable", "Lockable"])
        assert w.db.get_object_by_name("test box", lockbox) is None

    def test_get_all_objects_is_polymorphic(self):
        w = self.tw.world
        self.subject("test box", "Container")  # Object + Containable
        names = {o["name"] for o in w.db.get_all_objects(Object)}
        assert "test box" in names, names
        assert "The Starting Chamber" not in names


class AddMixinTest(ComposeTest):
    def test_addmixin_grants_the_verbs(self):
        wiz = self.wizard()
        widget = self.subject()
        self.assert_msg(wiz.send("open widget"), "I don't understand that.")
        self.assert_msg(wiz.send("@addmixin Openable to widget"), "OpenableObject")
        self.assert_msg(wiz.send("close widget"), "You close test widget")
        assert self.tw.world.get_object(widget.id).is_open is False

    def test_rebuild_keeps_one_instance_in_the_room(self):
        """Recomposing replaces the instance; the dead one must not be left
        sitting in its room's contents (the room listed it twice)."""
        wiz = self.wizard()
        start = self.tw.world.get_object(self.tw.world.config["start_room_id"])
        widget = self.subject()
        wiz.send("@addmixin Switchable to widget")
        again = self.tw.world.get_object(widget.id)
        assert again is not widget
        assert start.contents.count(again) == 1
        assert widget not in start.contents

    def test_rebuild_reseats_references(self):
        """held_by (and every other back-reference) points at the instance,
        which the rebuild replaces."""
        wiz = self.wizard()
        widget = self.subject("held widget", "HeldObject")
        widget.move_to(wiz.player)
        widget.held_by = wiz.player
        wiz.send("@addmixin Wearable to held widget")
        again = self.tw.world.get_object(widget.id)
        assert again.held_by is wiz.player
        assert wiz.player.contents.count(again) == 1

    def test_rmmixin_names_the_implying_mixin(self):
        wiz = self.wizard()
        self.subject()
        wiz.send("@addmixin Lockable to widget")
        self.assert_msg(
            wiz.send("@rmmixin Openable from widget"),
            "Openable comes with Lockable",
        )
        self.assert_msg(wiz.send("@rmmixin Lockable from widget"), "no longer Lockable")

    def test_unknown_mixin_is_a_message(self):
        wiz = self.wizard()
        self.subject()
        self.assert_msg(wiz.send("@addmixin Nonsense to widget"), "Cannot do that")


RUB = (
    "def rub(self, player, prep=None, verb=None):\\n"
    "    player.tell('The %s glows.' % self.name)"
)
LOOK = (
    "def look(self, player, prep=None, verb=None):\\n"
    "    super(cls, self).look(player)\\n"
    "    player.tell('It hums faintly.')"
)


class StoredVerbTest(ComposeTest):
    def _widget(self, wiz):
        self.subject()
        wiz.send("@addmixin Switchable to widget")

    def test_verb_from_the_database_dispatches(self):
        wiz = self.wizard()
        self._widget(wiz)
        self.assert_msg(
            wiz.send(f"@verb SwitchableObject rub rub/self/-/- {RUB}"), "Stored"
        )
        self.assert_msg(wiz.send("rub widget"), "The test widget glows.")

    def test_super_needs_the_injected_cls(self):
        """exec'd code has no __class__ cell, so a bare super() cannot work;
        stored code calls super(cls, self), and it must reach the override
        it shadows."""
        wiz = self.wizard()
        self._widget(wiz)
        wiz.send(f"@verb SwitchableObject look look/self/-/- {LOOK}")
        self.assert_msg(
            wiz.send("look widget"), "A test widget.", "It hums faintly."
        )

    def test_broken_source_is_refused_not_stored(self):
        wiz = self.wizard()
        self._widget(wiz)
        self.assert_msg(
            wiz.send("@verb SwitchableObject bad bad/self/-/- def bad(self, player:"),
            "Verb not stored",
        )
        assert "bad" not in self.tw.world.classes.stored_verbs("SwitchableObject")
        self.assert_msg(wiz.send("rub widget"), "I don't understand that.")

    def test_verb_survives_a_restart(self):
        from azimuth.world import setup_world

        from .framework import WORLD_ID

        wiz = self.wizard()
        self._widget(wiz)
        wiz.send(f"@verb SwitchableObject rub rub/self/-/- {RUB}")
        self.tw.world.dump_database()
        again = setup_world(self.tw.storage, WORLD_ID)
        gem = again.get_object_by_name("test widget")
        assert "rub" in gem.get_commands()
        assert getattr(type(gem).__dict__.get("rub"), "_az_stored", False)

    def test_rmverb_removes_it(self):
        wiz = self.wizard()
        self._widget(wiz)
        wiz.send(f"@verb SwitchableObject rub rub/self/-/- {RUB}")
        self.assert_msg(wiz.send("@rmverb widget rub"), "Removed")
        self.assert_msg(wiz.send("rub widget"), "I don't understand that.")

    def test_verbs_do_not_leak_between_worlds(self):
        """Stored verbs hang on a per-world subclass, never on the shared
        hand-written class -- otherwise one world (or one test) would rewrite
        another's behaviour."""
        wiz = self.wizard()
        self._widget(wiz)
        wiz.send(f"@verb SwitchableObject rub rub/self/-/- {RUB}")
        other = TestWorld(db_type=self.tw.db_type)
        try:
            cls = other.world.classes.resolve("Object", ["Switchable"])
            assert "rub" not in cls.__dict__
            assert "rub" not in cls(None, other.world, {"name": "x"}).get_commands()
        finally:
            other.clean()
