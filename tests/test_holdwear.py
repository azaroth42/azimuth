"""Tests for held/worn display.

Covers the three surfaces a held/worn object appears in:
  * ``inventory`` splits its output into carried / holding / wearing sections
  * ``look at <player>`` names what the player is holding and wearing
  * the out-of-band state (OOB-PROTOCOL.md) tags each carried thing with a
    ``held`` / ``worn`` state, which is what the TUI renders as (held)/(worn)

The demo world provides a HeldObject (rusty sword), a Wearable
(chainmail armor) and a plain Object (iron key), one for each section.
"""

from .framework import AzimuthTest, Session


def _reset(obj):
    """Clear held/worn state on an object (the copied real world may carry it)."""
    for attr in ("held_by", "worn_by"):
        if hasattr(obj, attr):
            setattr(obj, attr, None)
    return obj


def find_thing(section, name):
    for t in section or []:
        if t.get("name") == name:
            return t
    return None


class InventorySectionsTest(AzimuthTest):
    def _gear(self):
        wiz = self.wizard()
        start = self.tw.world.get_object(self.tw.world.config["start_room_id"])
        # Start from a known-empty inventory: drop whatever the (copied) world
        # gave the wizard, so the sections are deterministic.
        for x in list(wiz.player.contents):
            _reset(x).move_to(start)
        self.tw.world.flush_state()
        sword = _reset(self.place_object("rusty sword", start))
        armor = _reset(self.place_object("chainmail armor", start))
        key = _reset(self.place_object("iron key", start))
        for c in ("get sword", "get armor", "get key"):
            wiz.send(c)
        self.tw.world.flush_state()
        return wiz, start, sword, armor, key

    def test_carried_held_worn_get_separate_sections(self):
        wiz, _, sword, armor, key = self._gear()
        wiz.send("wield sword")
        wiz.send("wear armor")
        self.tw.world.flush_state()
        out = self.assert_msg(
            wiz.send("inventory"),
            "You are carrying: iron key",
            "You are holding: rusty sword",
            "You are wearing: chainmail armor",
        )
        # The held/worn items must not leak into the plain carrying line.
        assert "You are carrying: rusty sword" not in out
        assert "You are carrying: chainmail armor" not in out
        assert "You are holding: chainmail armor" not in out
        assert "You are wearing: rusty sword" not in out

    def test_nothing_carried(self):
        wiz = self.wizard()
        start = self.tw.world.get_object(self.tw.world.config["start_room_id"])
        for x in list(wiz.player.contents):
            _reset(x).move_to(start)
        self.tw.world.flush_state()
        self.assert_msg(wiz.send("inventory"), "You are not carrying anything")


class LookAtPlayerTest(AzimuthTest):
    def _wielded_wizard(self):
        wiz = self.wizard()
        start = self.tw.world.get_object(self.tw.world.config["start_room_id"])
        _reset(self.place_object("rusty sword", start))
        _reset(self.place_object("chainmail armor", start))
        wiz.send("get sword")
        wiz.send("get armor")
        wiz.send("wield sword")
        wiz.send("wear armor")
        self.tw.world.flush_state()
        return wiz, start

    def test_look_at_self(self):
        wiz, _ = self._wielded_wizard()
        self.assert_msg(
            wiz.send("look at wizard"),
            "You are holding: rusty sword",
            "You are wearing: chainmail armor",
        )

    def test_look_at_other_player(self):
        wiz, start = self._wielded_wizard()
        other = self.tw.register()
        other.player.move_to(start)
        self.tw.world.flush_state()
        out = self.assert_msg(
            other.send("look at wizard"),
            "They are holding: rusty sword",
            "They are wearing: chainmail armor",
        )
        # Plain carried items are not revealed on another player.
        assert "iron key" not in out


class OOBHeldWornStateTest(AzimuthTest):
    def _oob_wizard(self):
        s = Session(self.tw)
        self.tw.oob(s)
        s.login("wizard", "wizard")
        assert s.player is not None
        start = self.tw.world.get_object(self.tw.world.config["start_room_id"])
        if s.player.location is not start:
            s.player.move_to(start)
            self.tw.world.flush_state()
        return s, start

    def test_inventory_states_tag_held_and_worn(self):
        s, start = self._oob_wizard()
        _reset(self.place_object("rusty sword", start))
        _reset(self.place_object("chainmail armor", start))
        for c in ("get sword", "get armor"):
            s.send(c)
        s.send("wield sword")
        s.send("wear armor")
        self.tw.world.flush_state()

        inv = s.states()[-1]["inventory"]
        assert find_thing(inv, "rusty sword")["state"] == ["held"]
        assert find_thing(inv, "chainmail armor")["state"] == ["worn"]

    def test_unwield_and_remove_clear_state(self):
        s, start = self._oob_wizard()
        _reset(self.place_object("rusty sword", start))
        _reset(self.place_object("chainmail armor", start))
        for c in ("get sword", "get armor", "wield sword", "wear armor"):
            s.send(c)
        self.tw.world.flush_state()
        inv = s.states()[-1]["inventory"]
        assert find_thing(inv, "rusty sword")["state"] == ["held"]
        assert find_thing(inv, "chainmail armor")["state"] == ["worn"]

        s.send("unwield sword")
        s.send("remove armor")
        self.tw.world.flush_state()
        inv = s.states()[-1]["inventory"]
        # Cleared: a plain object has no state.
        assert find_thing(inv, "rusty sword")["state"] is None
        assert find_thing(inv, "chainmail armor")["state"] is None