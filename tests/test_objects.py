"""Tests for object interaction commands: get/take/drop, containers.

The demo world (bootstrapped by setup_world) has:
  - The Starting Chamber: rusty sword, chainmail armor, loaf of bread
  - Narrow Hallway: iron key
  - Glittering Cave: sturdy chest (containing a shiny gem)
"""

from azimuth.entities import LockableExit

from .framework import AzimuthTest


def _make_lockable_exit(tw):
    """The demo world has no lockable exit, so make a throwaway one."""
    return LockableExit(
        None,
        tw.world,
        {"name": "test door", "source": None, "destination": None},
        recursive=False,
    )


class GetObjectTest(AzimuthTest):
    def test_get_sword(self):
        """The reported bug: 'get sword' did nothing for a logged-in player."""
        wiz = self.wizard()
        self.place_object("rusty sword", "The Starting Chamber")
        self.assert_msg(wiz.send("look"), "The Starting Chamber")
        self.assert_msg(wiz.send("get sword"), "You take rusty sword.")
        assert "rusty sword" in wiz.inventory()

    def test_get_alias_take(self):
        wiz = self.wizard()
        self.place_object("rusty sword", "The Starting Chamber")
        self.assert_msg(wiz.send("take sword"), "You take rusty sword.")
        assert "rusty sword" in wiz.inventory()

    def test_get_invisible(self):
        """An object in another room must not match."""
        wiz = self.wizard()
        self.place_object("iron key", "Narrow Hallway")  # not visible from here
        assert "iron key" not in wiz.inventory()
        self.assert_msg(wiz.send("get key"), "I don't understand that.")

    def test_take_from_container(self):
        wiz = self.wizard()
        self.place_object("sturdy chest", "Glittering Cave")
        self.place_object("shiny gem", "sturdy chest")
        self.assert_msg(
            wiz.send("@teleport Glittering Cave"), "Teleporting to Glittering Cave"
        )
        self.assert_msg(wiz.send("take gem from chest"), "shiny gem")
        assert "shiny gem" in wiz.inventory()


class DropObjectTest(AzimuthTest):
    def test_take_then_drop(self):
        wiz = self.wizard()
        self.place_object("rusty sword", "The Starting Chamber")
        self.assert_msg(wiz.send("get sword"), "You take rusty sword.")
        self.assert_msg(wiz.send("drop sword"), "You drop rusty sword.")
        assert "rusty sword" not in wiz.inventory()
        # And it's back on the floor of the room, where 'get' finds it again.
        self.assert_msg(wiz.send("get sword"), "You take rusty sword.")

    def test_bare_drop_is_not_implemented(self):
        """Document current semantics: 'drop' requires an explicit object;
        a bare 'drop' does not auto-target the carried item."""
        wiz = self.wizard()
        self.place_object("rusty sword", "The Starting Chamber")
        self.assert_msg(wiz.send("get sword"), "You take rusty sword.")
        self.assert_msg(wiz.send("drop"), "I don't understand that.")
        assert "rusty sword" in wiz.inventory()  # still carrying it


class CommandRegistrationTest(AzimuthTest):
    """The MRO merge in get_commands must not double-register commands.

    Classes that declare no commands of their own (HeldObject, Container,
    Clothing, Furniture, LockableExit) used to inherit a parent's
    default_commands dict, so the walk merged the same entries twice.
    """

    def test_no_duplicate_entries(self):
        wiz = self.wizard()
        self.place_object("rusty sword", "The Starting Chamber")
        sword = wiz.send("get sword")  # now carried; exercises its full MRO
        for thing in [self.tw.world, wiz.player, wiz.player.location, *wiz.player.contents]:
            cmds = thing.get_commands()
            for verb, infos in cmds.items():
                self.assert_msg(sword, "You take rusty sword.")
                dupes = [i for i in infos if infos.count(i) > 1]
                assert not dupes, (
                    f"duplicate {verb!r} entries on {thing!r}: {infos}"
                )

    def test_lockable_exit_no_duplicate_open(self):
        door = _make_lockable_exit(self.tw)
        infos = door.get_commands().get("open", [])
        # 'open' is defined by three distinct classes (Openable, Lockable,
        # OpenableExit) -- each exactly once, none of them twice.
        funcs = [i["func"] for i in infos]
        assert len(funcs) == len(set(funcs)), f"duplicate open handlers: {infos}"
        assert len(funcs) == 3, f"expected 3 distinct open handlers, got {funcs}"
        # NB: dispatch tries entries in merge order (shallowest first), so a
        # *locked* door currently opens via the plain Openable.open. If that
        # ever matters, the fix belongs in the dispatcher, not here.


class SayTest(AzimuthTest):
    def test_quote_say(self):
        wiz = self.wizard()
        self.assert_msg(wiz.send("'hello there'"), "You say,")

    def test_emote(self):
        wiz = self.wizard()
        self.assert_msg(wiz.send(";waves at the ceiling"), "waves at the ceiling")
