"""Tests for the out-of-band state channel (OOB-PROTOCOL.md).

Covers: capability gating (old clients get nothing), the init snapshot
(shape, scope, visibility), live updates (take/drop, who, open/close),
coalescing (one command -> one event), seq monotonicity, verb-summary
gating, and anti-leakage (no credentials, no hidden things).
"""

import json

from azimuth.entities import OpenableContainer

from .framework import AzimuthTest, Session


def thing_names(section):
    return [t["name"] for t in section] if section else []


def find_thing(section, name):
    """Find a thing summary by name in a list of thing summaries."""
    for t in section or []:
        if t.get("name") == name:
            return t
    return None


class OOBTest(AzimuthTest):
    # -- helpers -----------------------------------------------------------

    def start_room(self):
        return self.tw.world.get_object(self.tw.world.config["start_room_id"])

    def oob_wizard(self):
        """Log the wizard in *after* marking the connection OOB-capable, so
        the login pushes the init snapshot (mirrors a client hello).  Then
        settle the wizard in the start room, so later assertions have a
        known layout."""
        s = Session(self.tw)
        self.tw.oob(s)
        reply = s.login("wizard", "wizard")
        # handle_login returns None on success (it announces in-band).
        assert s.player is not None, f"wizard login failed: {reply!r}"
        start = self.start_room()
        if s.player.location is not start:
            s.player.move_to(start)
            self.tw.world.flush_state()
        return s

    # -- capability gating -------------------------------------------------

    def test_old_client_gets_no_state(self):
        # A session that never hello'd OOB plays normally: full message
        # stream, and zero `state` events.
        s = self.wizard()
        s.send("look")
        s.send("inv")
        self.assert_msg(s.messages(), "You are")
        assert s.states() == []

    # -- init snapshot -----------------------------------------------------

    def test_init_on_login(self):
        s = self.oob_wizard()
        states = s.states()
        assert len(states) >= 1
        init = states[0]
        assert init["v"] == 1
        assert init["kind"] == "init"
        assert init["seq"] == 1
        assert "self" in init and "room" in init
        assert "inventory" in init and "players" in init

        assert init["self"]["name"] == "wizard"
        assert init["self"]["username"] == "wizard"
        assert init["self"]["verbs"], "self section should carry the verb table"

        # The snapshot is internally consistent.
        me = [p for p in init["players"] if p["name"] == "wizard"][0]
        assert me["self"] is True
        assert me["loc"] == init["room"]["name"]

        # After the settle move, the latest state matches the player's room.
        last = states[-1]
        assert last["room"]["id"] == s.player.location.id
        assert last["room"]["name"] == self.start_room().name

    def test_init_never_leaks_credentials_or_hidden_thing(self):
        w = self.oob_wizard()
        start = self.start_room()
        self.place_object("sturdy chest", start)
        self.place_object("shiny gem", "sturdy chest")  # inside: not visible
        self.tw.world.flush_state()
        w.send("look")  # no state change: no new event
        last = w.states()[-1]
        text = json.dumps(last)
        assert "password" not in text
        # The gem sits inside the chest: it must not appear as a room thing.
        assert find_thing(last["room"]["things"], "shiny gem") is None
        assert find_thing(last["room"]["things"], "sturdy chest") is not None

    def test_resync_sends_init_and_seq_increments(self):
        s = self.wizard()
        self.tw.oob(s)
        self.tw.world.push_init(s.player)
        self.tw.world.push_init(s.player)
        assert len(s.states()) == 2
        assert [x["kind"] for x in s.states()] == ["init", "init"]
        assert [x["seq"] for x in s.states()] == [1, 2]

    # -- live updates ------------------------------------------------------

    def test_take_updates_inventory_and_room(self):
        w = self.oob_wizard()
        t = self.tw.register()
        self.tw.oob(t)
        self.tw.world.push_init(t.player)  # baseline for the bystander
        self.place_object("rusty sword", self.start_room())
        self.tw.world.flush_state()  # settle any pending marks

        w_states = len(w.states())
        t_states = len(t.states())
        msgs = w.send("get rusty sword")
        self.assert_msg(msgs, "You take")

        # Coalescing: exactly one state event per affected client.
        assert len(w.states()) == w_states + 1
        assert len(t.states()) == t_states + 1

        upd = w.states()[-1]
        assert upd["kind"] == "update"
        assert find_thing(upd["inventory"], "rusty sword") is not None
        assert find_thing(upd["room"]["things"], "rusty sword") is None

        t_upd = t.states()[-1]
        assert find_thing(t_upd["room"]["things"], "rusty sword") is None

    def test_drop_moves_thing_between_sections(self):
        w = self.oob_wizard()
        self.place_object("rusty sword", w.player)  # in hand
        self.tw.world.flush_state()
        w.send("drop rusty sword")
        upd = w.states()[-1]
        assert find_thing(upd["inventory"], "rusty sword") is None
        assert find_thing(upd["room"]["things"], "rusty sword") is not None

    def test_who_liveness(self):
        w = self.oob_wizard()
        t = self.tw.register()
        name = t.player.username
        self.tw.oob(t)
        self.tw.world.push_init(t.player)
        self.place_object("rusty sword", self.start_room())
        self.tw.world.flush_state()

        w.send("who")  # who is in-band text; no state event from it

        def latest(section):
            return [x for x in w.states() if section in x][-1]

        # The register (login) pushed a `players` update; the room update
        # carries the new player as a thing.
        assert name in [p["name"] for p in latest("players")["players"]]
        assert name in thing_names(latest("room")["room"]["things"])

        t.close()  # disconnect: the world changed for w again
        assert [p["name"] for p in latest("players")["players"]] == ["wizard"]
        assert name not in thing_names(latest("room")["room"]["things"])

    def test_no_event_for_pure_conversation(self):
        w = self.oob_wizard()
        before = len(w.states())
        w.send("say hello")
        w.send("emote waves")
        assert len(w.states()) == before  # no state change, no update

    def test_seq_is_monotonic(self):
        w = self.oob_wizard()
        start = self.start_room()
        self.place_object("rusty sword", start)
        self.place_object("iron key", start)
        self.tw.world.flush_state()
        w.send("get rusty sword")
        w.send("get iron key")
        seqs = [x["seq"] for x in w.states()]
        assert seqs == sorted(seqs)
        assert len(set(seqs)) == len(seqs)  # strictly increasing
        assert [x["kind"] for x in w.states()].count("init") == 1

    # -- verb summaries ----------------------------------------------------

    def test_verb_summary_gating(self):
        w = self.oob_wizard()
        self.place_object("rusty sword", self.start_room())
        self.tw.world.flush_state()
        w.send("look")  # settles; no new event

        def sword_verb_forms(state):
            sword = find_thing(state["room"]["things"], "rusty sword")
            if sword is None:
                sword = find_thing(state["inventory"], "rusty sword")
            assert sword is not None
            return {tuple(e["verb"]) for e in sword["verbs"]}

        forms = sword_verb_forms(w.states()[-1])
        assert ("get", "take", "pick") in forms  # same room: pickup offered
        assert ("drop",) not in forms            # not carried: no drop

        w.send("get rusty sword")
        forms = sword_verb_forms(w.states()[-1])
        assert ("drop",) in forms                # carried: drop offered
        assert ("get", "take", "pick") not in forms

    def test_container_contents_gated_by_state(self):
        w = self.oob_wizard()
        start = self.start_room()
        self.place_object("sturdy chest", start)
        gem = self.place_object("shiny gem")
        gem.move_to(w.player)  # stash it out of the room first
        self.tw.world.flush_state()
        bag = OpenableContainer(
            None, self.tw.world, {"name": "canvas bag", "location": start.id}
        )
        bag.is_open = False
        gem.move_to(bag)
        self.tw.world.flush_state()
        w.send("look")

        def bag_in(state):
            return find_thing((state.get("room") or {}).get("things"), "canvas bag")

        bag = bag_in(w.states()[-1])
        assert bag is not None
        assert bag["state"] == ["closed"]
        assert bag.get("contents") is None  # closed: contents not revealed
        assert find_thing(w.states()[-1]["room"]["things"], "shiny gem") is None

        w.send("open canvas bag")
        bag = bag_in(w.states()[-1])
        assert bag["state"] == ["open"]
        assert [c["name"] for c in bag["contents"]] == ["shiny gem"]
