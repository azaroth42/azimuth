"""Tests for Positionable (mixins.Positionable / entities.PositionableObject).

A thing positioned relative to a Piece of furniture is a *display* relation:
it keeps its real location (the room) and is merely described relative to the
furniture, so it shows up in the room look ("a plate on the table", "Bob is
sitting at the table").

Surfaces covered:
  * ``sit/stand/lie ... on/at/under/beside <furniture>`` positions the player
  * ``put/place <object> ... <furniture>`` positions an object
  * the room look renders positions -- first person for the looker, third
    person for other players -- and removes positioned things from the plain
    "You see here:" list
  * ``look at <furniture>`` lists what is positioned on it
  * ``rise`` and leaving the room clear a position
  * position survives a save / reload
  * the OOB thing_summary tags a positioned thing with its position
"""

import azimuth.world as worldmod

from .framework import AzimuthTest, Session
from azimuth.entities import PositionableObject, Object


class _FurnishMixin:
    def _furniture(self, where=None):
        """Create a table, a chair and a plate in the (start) room."""
        w = self.tw.world
        start = where or w.get_object(w.config["start_room_id"])

        def mk(clss, name, desc):
            existing = w.get_object_by_name(name)
            if existing is not None:
                return existing
            o = clss(None, w, {"name": name, "description": desc, "location": start.id})
            o._save()
            w.mark_room_dirty(start)
            return o

        table = mk(PositionableObject, "table", "A wooden table.")
        chair = mk(PositionableObject, "chair", "A wooden chair.")
        plate = mk(Object, "plate", "A plate.")
        w.flush_state()
        return table, chair, plate

    def _you_see_here(self, text):
        """Return just the 'You see here:' line of a room look (or None)."""
        for line in text.splitlines():
            if line.strip().startswith("You see here:"):
                return line
        return None


class PositionSelfTest(AzimuthTest, _FurnishMixin):
    def test_sit_on_table_confirms_and_renders(self):
        wiz = self.wizard()
        self._furniture()
        self.assert_msg(wiz.send("sit on table"), "You sit on table.")
        out = wiz.send("look")
        self.assert_msg(out, "You are sitting on the table.")

    def test_articles_are_ignored(self):
        wiz = self.wizard()
        table, _, _ = self._furniture()
        out = wiz.send("sit on the table")  # article must not break matching
        self.assert_msg(out, "You sit on table.")
        assert table.has_positioned(wiz.player), "'the table' still matched the table"

    def test_multiple_furniture_targets_the_right_one(self):
        wiz = self.wizard()
        table, chair, _ = self._furniture()
        wiz.send("sit on table")
        assert table.has_positioned(wiz.player), "first sit lands on the table"
        # Now sit at the chair: must leave the table and land on the chair,
        # not (as the old unvalidated parser would) stay on the first furniture.
        self.assert_msg(wiz.send("sit at chair"), "You sit at chair.")
        assert not table.has_positioned(wiz.player), "left the table"
        assert chair.has_positioned(wiz.player), "now at the chair"

    def test_lying_under(self):
        wiz = self.wizard()
        self._furniture()
        self.assert_msg(wiz.send("lie under table"), "You lie under table.")
        out = wiz.send("look")
        self.assert_msg(out, "You are lying under the table.")

    def test_rise_clears_position(self):
        wiz = self.wizard()
        table, _, _ = self._furniture()
        wiz.send("sit on table")
        assert table.has_positioned(wiz.player)
        self.assert_msg(wiz.send("rise"), "You rise to your feet.")
        assert not table.has_positioned(wiz.player), "rise unpositions"

    def test_leaving_the_room_clears_position(self):
        wiz = self.wizard()
        table, _, _ = self._furniture()
        wiz.send("sit on table")
        assert table.has_positioned(wiz.player)
        wiz.send("north")  # walk out the door
        assert not table.has_positioned(wiz.player), "moving unpositions"


class PositionObjectTest(AzimuthTest, _FurnishMixin):
    def test_put_plate_on_table(self):
        wiz = self.wizard()
        table, _, plate = self._furniture()
        self.assert_msg(wiz.send("put plate on table"), "You put plate on table.")
        assert table.has_positioned(plate)
        # display relation: the plate is still physically in the room
        start = self.tw.world.get_object(self.tw.world.config["start_room_id"])
        assert plate.location is start, "positioning does not move the thing"

    def test_room_look_shows_positioned_object(self):
        wiz = self.wizard()
        table, chair, plate = self._furniture()
        wiz.send("put plate on table")
        out = "\n".join(str(m) for m in wiz.send("look"))
        self.assert_msg(wiz.send("look"), "plate is on the table.")
        # ...and the plate drops out of the plain contents list
        ysh = self._you_see_here(out)
        assert ysh is not None
        assert "plate" not in ysh, f"plate should not be in {ysh!r}"

    def test_look_at_furniture_lists_positioned(self):
        wiz = self.wizard()
        table, _, plate = self._furniture()
        wiz.send("put plate on table")
        self.assert_msg(wiz.send("look at table"), "plate is on the table.")

    def test_position_removed_from_plain_list(self):
        wiz = self.wizard()
        table, chair, plate = self._furniture()
        wiz.send("put plate on table")
        out = "\n".join(str(m) for m in wiz.send("look"))
        ysh = self._you_see_here(out)
        # table and chair remain in the plain list; plate does not
        assert "table" in ysh and "chair" in ysh
        assert "plate" not in ysh


class OtherPlayerViewTest(AzimuthTest, _FurnishMixin):
    def test_other_player_sees_third_person(self):
        wiz = self.wizard()
        table, _, plate = self._furniture()
        other = self.tw.register()
        start = self.tw.world.get_object(self.tw.world.config["start_room_id"])
        other.player.move_to(start)
        self.tw.world.flush_state()

        wiz.send("sit on table")
        wiz.send("put plate on table")
        out = "\n".join(str(m) for m in other.send("look"))
        self.assert_msg(
            other.send("look"),
            "wizard is sitting on the table.",
            "plate is on the table.",
        )

    def test_look_at_player_shows_position(self):
        wiz = self.wizard()
        self._furniture()
        other = self.tw.register()
        start = self.tw.world.get_object(self.tw.world.config["start_room_id"])
        other.player.move_to(start)
        self.tw.world.flush_state()
        wiz.send("sit on table")
        self.assert_msg(other.send("look at wizard"), "They are sitting on the table.")


class PersistenceTest(AzimuthTest, _FurnishMixin):
    def test_position_survives_reload(self):
        w = self.tw.world
        wiz = self.wizard()
        table, _, plate = self._furniture()
        wiz.send("sit on table")
        wiz.send("put plate on table")
        assert set(t.name for t, _ in table.positioned["on"]) == {"wizard", "plate"}

        w.dump_database()
        # A fresh World over the same storage, nothing loaded in memory yet.
        w2 = worldmod.setup_world(self.tw.storage, w.id)
        t2 = w2.get_object_by_name("table")
        assert t2 is not None
        names = set(t.name for t, _ in t2.positioned["on"])
        assert names == {"wizard", "plate"}, f"position lost on reload: {names}"

    def test_held_worn_still_serialize(self):
        # Regression guard for the to_dict MRO fix: open/held/worn state now
        # actually lands in the saved doc (it was silently dropped before).
        from azimuth.entities import OpenableContainer, HeldObject

        w = self.tw.world
        start = w.get_object(w.config["start_room_id"])
        box = OpenableContainer(None, w, {"name": "box", "location": start.id, "open": False})
        sword = HeldObject(None, w, {"name": "heldthing", "location": start.id})
        box._save()
        sword._save()
        assert box.to_dict()["open"] is False
        assert "held_by" in sword.to_dict()  # key present (None) even when unwielded


class OOBPositionTest(AzimuthTest, _FurnishMixin):
    def test_thing_summary_carries_position(self):
        s = Session(self.tw)
        self.tw.oob(s)
        s.login("wizard", "wizard")
        assert s.player is not None
        w = self.tw.world
        start = w.get_object(w.config["start_room_id"])
        if s.player.location is not start:
            s.player.move_to(start)
            w.flush_state()
        table, _, plate = self._furniture(start)
        s.send("put plate on table")
        w.flush_state()

        inv_room = s.states()[-1]["room"]
        thing = next(t for t in inv_room["things"] if t["name"] == "plate")
        assert thing.get("position") == "on the table", thing