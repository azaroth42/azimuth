"""Tests for Switchable (mixins.Switchable / entities.SwitchableObject).

A Switchable has an on/off power state a player can toggle.  Covers:
  * the many phrasings: turn/switch/power on|off (leading and trailing),
    activate/deactivate, and bare on/off
  * the "already on / already off" no-op messages
  * a paired object (a wall switch drives the lamp it controls)
  * ``look at`` reflecting the state
  * the OOB state tag (on/off)
  * is_on persisting across a save / reload
"""

import azimuth.world as worldmod

from .framework import AzimuthTest, Session
from azimuth.entities import SwitchableObject


class _ApplianceMixin:
    def _lamps(self, where=None):
        """A lamp and a wall switch that drives it, in the (start) room."""
        w = self.tw.world
        start = where or w.get_object(w.config["start_room_id"])

        def mk(clss, name, extra=None):
            existing = w.get_object_by_name(name)
            if existing is not None:
                return existing
            d = {"name": name, "description": f"A {name}.", "location": start.id, "is_on": False}
            d.update(extra or {})
            o = clss(None, w, d)
            o._save()
            w.mark_room_dirty(start)
            return o

        lamp = mk(SwitchableObject, "lamp")
        sw = mk(SwitchableObject, "switch", {"on_paired_object": lamp.id})
        w.flush_state()
        return lamp, sw


class ToggleThingTest(AzimuthTest, _ApplianceMixin):
    def test_turn_on_then_off(self):
        wiz = self.wizard()
        lamp, _ = self._lamps()
        self.assert_msg(wiz.send("turn on lamp"), "You turn lamp on.")
        assert lamp.is_on is True
        self.assert_msg(wiz.send("turn lamp off"), "You turn lamp off.")
        assert lamp.is_on is False

    def test_already_on_and_off(self):
        wiz = self.wizard()
        lamp, _ = self._lamps()
        wiz.send("turn on lamp")
        self.assert_msg(wiz.send("turn on lamp"), "already on")
        self.assert_msg(wiz.send("turn off lamp"), "You turn lamp off.")
        self.assert_msg(wiz.send("turn off lamp"), "already off")

    def test_activate_deactivate(self):
        wiz = self.wizard()
        lamp, _ = self._lamps()
        self.assert_msg(wiz.send("activate lamp"), "You turn lamp on.")
        assert lamp.is_on is True
        self.assert_msg(wiz.send("deactivate lamp"), "You turn lamp off.")
        assert lamp.is_on is False

    def test_bare_on_off(self):
        wiz = self.wizard()
        lamp, _ = self._lamps()
        wiz.send("on lamp")
        assert lamp.is_on is True
        wiz.send("off lamp")
        assert lamp.is_on is False

    def test_articles_ignored(self):
        wiz = self.wizard()
        lamp, _ = self._lamps()
        self.assert_msg(wiz.send("turn on the lamp"), "You turn lamp on.")
        assert lamp.is_on is True

    def test_look_reflects_state(self):
        wiz = self.wizard()
        lamp, _ = self._lamps()
        wiz.send("turn on lamp")
        self.assert_msg(wiz.send("look at lamp"), "It is on.")
        wiz.send("turn off lamp")
        self.assert_msg(wiz.send("look at lamp"), "It is off.")


class PairedObjectTest(AzimuthTest, _ApplianceMixin):
    def test_switch_drives_lamp(self):
        wiz = self.wizard()
        lamp, sw = self._lamps()
        self.assert_msg(wiz.send("switch on switch"), "You turn switch on.")
        assert sw.is_on is True
        assert lamp.is_on is True, "turning the switch on turns the lamp on"
        wiz.send("turn off switch")
        assert sw.is_on is False
        assert lamp.is_on is False, "turning the switch off turns the lamp off"

    def test_paired_only_follows_on_toggle(self):
        wiz = self.wizard()
        lamp, sw = self._lamps()
        # Toggling the lamp directly must not drive the switch.
        wiz.send("turn on lamp")
        assert lamp.is_on is True
        assert sw.is_on is False, "the switch is not the lamp's pair"


class SwitchOOBTest(AzimuthTest, _ApplianceMixin):
    def test_state_tag_on_off(self):
        s = Session(self.tw)
        self.tw.oob(s)
        s.login("wizard", "wizard")
        w = self.tw.world
        start = w.get_object(w.config["start_room_id"])
        if s.player.location is not start:
            s.player.move_to(start)
            w.flush_state()
        self._lamps(start)
        s.send("turn on lamp")
        w.flush_state()
        thing = next(t for t in s.states()[-1]["room"]["things"] if t["name"] == "lamp")
        assert thing["state"] == ["on"], thing
        s.send("turn off lamp")
        w.flush_state()
        thing = next(t for t in s.states()[-1]["room"]["things"] if t["name"] == "lamp")
        assert thing["state"] == ["off"], thing


class SwitchPersistenceTest(AzimuthTest, _ApplianceMixin):
    def test_is_on_survives_reload(self):
        w = self.tw.world
        wiz = self.wizard()
        lamp, sw = self._lamps()
        wiz.send("turn on lamp")
        wiz.send("switch on switch")
        assert lamp.is_on and sw.is_on

        w.dump_database()
        w2 = worldmod.setup_world(self.tw.storage, w.id)
        l2 = w2.get_object_by_name("lamp")
        s2 = w2.get_object_by_name("switch")
        assert l2 is not None and l2.is_on is True, "lamp is_on lost on reload"
        assert s2 is not None and s2.is_on is True, "switch is_on lost on reload"
        # the pairing link is re-established
        assert s2.on_paired_object is l2