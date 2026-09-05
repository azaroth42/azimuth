"""Tests for EdibleThing.

``eat``/``drink``/``quaff <this>`` consumes the object and fires the
overridable ``_on_eaten`` hook -- the seam that lets later potions and food
carry effects when eaten.

The demo world has no edibles, so each test builds a throwaway one in the
start room (or in the wizard's pack).
"""

from azimuth.entities import EdibleThing

from .framework import AzimuthTest


class _HealingPotion(EdibleThing):
    """A potion that heals when quaffed -- proves the ``_on_eaten`` hook runs
    (while the object still exists) right before it is destroyed."""

    default_messages = {"eat": "You quaff the {self}."}

    def _on_eaten(self, player):
        # Record where we were when the effect fired: it must still be in a
        # real location (i.e. not already destroyed).
        self._location_at_effect = self.location
        player.tell("You feel your wounds close.")


def _edible(tw, name, where):
    """Create a throwaway ``EdibleThing``, place it in ``where``, persist it."""
    e = EdibleThing(None, tw.world, {"name": name})
    e.move_to(where)
    e._save()
    return e


class EdibleThingTest(AzimuthTest):
    def test_eat_from_room(self):
        wiz = self.wizard()
        food = _edible(self.tw, "apple", wiz.player.location)
        self.assert_msg(wiz.send("eat apple"), "You eat apple.")
        # Destroyed: gone from the room, the in-memory world, and the db.
        assert food not in wiz.player.location.contents
        assert food.id not in self.tw.world.active_objects
        assert self.tw.world.db.load(food.id) is None

    def test_eat_from_inventory(self):
        wiz = self.wizard()
        food = _edible(self.tw, "apple", wiz.player)  # carried
        assert "apple" in wiz.inventory()
        self.assert_msg(wiz.send("eat apple"), "You eat apple.")
        assert "apple" not in wiz.inventory()
        assert food.id not in self.tw.world.active_objects
        assert self.tw.world.db.load(food.id) is None

    def test_drink_and_quaff_aliases(self):
        wiz = self.wizard()
        tonic = _edible(self.tw, "tonic", wiz.player.location)
        self.assert_msg(wiz.send("drink tonic"), "You eat tonic.")
        ale = _edible(self.tw, "ale", wiz.player.location)
        self.assert_msg(wiz.send("quaff ale"), "You eat ale.")
        assert tonic.id not in self.tw.world.active_objects
        assert ale.id not in self.tw.world.active_objects

    def test_on_eaten_hook_fires_before_destroy(self):
        wiz = self.wizard()
        potion = _HealingPotion(
            None, self.tw.world, {"name": "healing potion"}
        )
        potion.move_to(wiz.player)
        potion._save()
        self.assert_msg(
            wiz.send("drink potion"),
            "You quaff the healing potion.",
            "You feel your wounds close.",
        )
        # The effect ran while the potion was still in the player's pack.
        assert potion._location_at_effect is wiz.player
        assert potion.id not in self.tw.world.active_objects

    def test_cannot_eat_other_room(self):
        wiz = self.wizard()
        hallway = self.tw.world.get_object_by_name("Narrow Hallway")
        assert hallway is not None
        food = _edible(self.tw, "poison apple", hallway)  # not in this room
        self.assert_msg(
            wiz.send("eat apple"), "I don't understand that.", absent=("You eat",)
        )
        assert food.id in self.tw.world.active_objects  # untouched
