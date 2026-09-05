"""Regression tests for dispatch order on exits with state.

The verb-shadowing bug: `get_commands` merges class
commands shallowest-ancestor-first, and the dispatcher used to try entries
in list order (first match wins) -- so the *generic* ancestor handler ran
before the *specialized* one. On a door that meant `open` ran the lock-unaware
`Openable.open` and `go` ran the closed-unaware `Exit.use`.

The fix is in `World.process_player_command`: it now iterates the verb
entries in reverse (deepest matching handler first). These tests pin that.
"""

from azimuth.entities import LockableExit, Place

from .framework import AzimuthTest


class DoorTest(AzimuthTest):
    def setup_door(self):
        """Two throwaway rooms joined by a closed, unlocked lockable door;
        the wizard stands at the source side."""
        wiz = self.wizard()
        w = self.tw.world
        src = Place(None, w, {"name": "Door Hall", "description": "A hall with a door."})
        dst = Place(None, w, {"name": "Door Beyond", "description": "The other side."})
        door = LockableExit(
            None,
            w,
            {
                "name": "heavy door",
                "source": src.id,
                "destination": dst.id,
                "open": False,
            },
        )
        wiz.player.move_to(src)
        return wiz, door, dst

    def test_closed_door_blocks_go(self):
        """A closed door must not be walkable through (OpenableExit.use's
        closed check must beat Exit.use)."""
        wiz, door, dst = self.setup_door()
        self.assert_msg(
            wiz.send("go heavy door"), "You cannot go through that, it's closed."
        )
        assert wiz.location_name == "Door Hall"

    def test_locked_door_blocks_open(self):
        """A locked door must not open (Lockable.open's lock check must beat
        the plain Openable.open). Lock state is set directly because the
        `lock` command is separately broken (see ARCHITECTURE.md §8)."""
        wiz, door, dst = self.setup_door()
        door.is_locked = True  # closed AND locked
        self.assert_msg(wiz.send("open heavy door"), "You must unlock heavy door first")
        assert not door.is_open  # the failed attempt must not have opened it

    def test_open_unlocked_door_allows_go(self):
        """The normal path must keep working: an open, unlocked door passes."""
        wiz, door, dst = self.setup_door()
        door.is_open = True
        self.assert_msg(wiz.send("go heavy door"), "You go through")
        assert wiz.location_name == "Door Beyond"

    def test_lock_when_closed(self):
        """Locking a closed door must lock it silently -- no bogus
        'must close first' (that came from self.open resolving to the
        OpenableExit.open method, which is always truthy)."""
        wiz, door, dst = self.setup_door()
        msgs = wiz.send("lock heavy door")
        assert door.is_locked
        self.assert_msg(msgs, absent=("You must close",))

    def test_lock_refuses_while_open(self):
        wiz, door, dst = self.setup_door()
        door.is_open = True
        self.assert_msg(wiz.send("lock heavy door"), "You must close heavy door first")
        assert not door.is_locked

    def test_open_announces_to_destination(self):
        """Open/close must reach the other side (OpenableExit's
        *_destination announcements must beat the silent Openable toggle)."""
        wiz, door, dst = self.setup_door()
        bystander = self.tw.register("bystander", "pw123")
        bystander.player.move_to(dst)
        self.assert_msg(wiz.send("open heavy door"), "You open")
        assert any(
            "opens heavy door from the other side" in str(m) for m in bystander.messages()
        )
        self.assert_msg(wiz.send("close heavy door"), "You close")
        assert any(
            "closes heavy door from the other side" in str(m) for m in bystander.messages()
        )
