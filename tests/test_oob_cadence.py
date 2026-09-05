"""How often the out-of-band state channel actually pushes.

Two rules (OOB-PROTOCOL.md 3.3, 8): a section is sent when its *meaningful*
content changed, and otherwise no more than once per STATE_RESYNC_SECONDS.

The bug these pin: `seen` (last_active_time) is part of the `players` section
and moves on every command by any player, so including it in the change
comparison made the section permanently unequal -- every movement pushed the
whole roster to every connected client, and the periodic pass would have done
the same on every tick.  The client re-renders relative ages locally, so the
timestamp never needs a push of its own.
"""

import time

from azimuth import world as world_mod

from .framework import AzimuthTest, Session


class StateCadenceTest(AzimuthTest):
    def oob_players(self, n=3):
        """`n` OOB-capable players, settled together in the start room.

        OOB capability is announced *before* login, as a real client does
        (its `data` hello precedes the login command), so each one gets the
        `init` snapshot -- without which every section reads as never-sent and
        the periodic pass would legitimately push a full refresh.
        """
        start = self.tw.world.get_object(self.tw.world.config["start_room_id"])
        out = []
        for i in range(n):
            s = Session(self.tw)
            self.tw.oob(s)
            s.register(f"cadence{i}", "pw123456", f"cadence{i}@example.com")
            assert s.player is not None
            self.tw.sessions.append(s)
            s.player.move_to(start)
            out.append(s)
        self.tw.world.flush_state()
        return out

    def age(self, session, section, seconds):
        """Pretend a section was last sent `seconds` ago."""
        sent = self.tw.world.state_sent_at.setdefault(session.player.id, {})
        sent[section] = time.monotonic() - seconds

    # -- only on a real change --------------------------------------------

    def test_idle_commands_push_nothing(self):
        (a, b) = self.oob_players(2)
        before = len(b.states())
        a.send("look")
        a.send("'hello")
        self.tw.world.flush_state()
        assert b.states()[before:] == []

    def test_seen_alone_does_not_push(self):
        """A player acting moves their `seen`; that must not, on its own,
        reach anyone."""
        (a, b) = self.oob_players(2)
        time.sleep(1.1)  # cross a whole second, so `seen` really differs
        a.send("look")
        stale = self.tw.world.state_players(b.player)
        assert stale != self.tw.world.state_last[b.player.id]["players"], (
            "precondition: the players section should differ by `seen` here"
        )
        before = len(b.states())
        self.tw.world.mark_players_dirty()
        self.tw.world.flush_state()
        assert b.states()[before:] == [], "a `seen`-only difference was pushed"

    def test_real_change_still_pushes_immediately(self):
        (a, b) = self.oob_players(2)
        before = len(b.states())
        a.send("north")  # a's `loc` changes: everyone's roster really changed
        self.tw.world.flush_state()
        pushed = b.states()[before:]
        assert len(pushed) == 1, pushed
        assert "players" in pushed[0]

    # -- and a resync once per minute -------------------------------------

    def test_periodic_resync_is_quiet_when_nothing_is_stale(self):
        (a, b) = self.oob_players(2)
        before = len(a.states()) + len(b.states())
        assert self.tw.world.periodic_resync() == 2
        assert len(a.states()) + len(b.states()) == before

    def test_periodic_resync_refreshes_a_stale_section(self):
        (a, b) = self.oob_players(2)
        time.sleep(1.1)
        a.send("look")  # only `seen` moves
        self.tw.world.flush_state()
        before = len(b.states())
        self.age(b, "players", world_mod.STATE_RESYNC_SECONDS + 1)
        self.tw.world.periodic_resync()
        pushed = b.states()[before:]
        assert len(pushed) == 1, pushed
        assert "players" in pushed[0]

    def test_resync_does_not_repeat_within_the_interval(self):
        (a, b) = self.oob_players(2)
        time.sleep(1.1)
        a.send("look")
        self.tw.world.flush_state()
        self.age(b, "players", world_mod.STATE_RESYNC_SECONDS + 1)
        self.tw.world.periodic_resync()
        before = len(b.states())
        self.tw.world.periodic_resync()  # immediately again
        assert b.states()[before:] == []

    # -- sequence numbers --------------------------------------------------

    def test_seq_is_only_spent_on_a_real_send(self):
        """The client treats a gap in `seq` as a dropped event and asks for a
        full resync (OOB-PROTOCOL.md 8), so a flush that sends nothing must
        not consume a number."""
        (a,) = self.oob_players(1)
        a.send("north")
        self.tw.world.flush_state()
        seqs = [s["seq"] for s in a.states() if "seq" in s]
        for _ in range(5):  # flushes that change nothing
            self.tw.world.mark_players_dirty()
            self.tw.world.flush_state()
        a.send("south")
        self.tw.world.flush_state()
        seqs = [s["seq"] for s in a.states() if "seq" in s]
        assert seqs == list(range(1, len(seqs) + 1)), seqs
