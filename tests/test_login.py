"""Tests for the login/register flow (World.handle_login / handle_register)."""

from .framework import AzimuthTest


class LoginTest(AzimuthTest):
    def test_wizard_login(self):
        s = self.tw.login("wizard", "wizard")
        assert s.player is not None
        assert s.player.username == "wizard"

    def test_bad_password(self):
        s = self.tw.login("wizard", "wrong-password")
        assert s.player is None

    def test_unknown_user(self):
        """An unknown username must reply, not raise."""
        s = self.tw.login("definitely-not-a-user", "nope")
        assert s.player is None

    def test_register(self):
        s = self.tw.register("newbie", "pw123")
        assert s.player is not None
        assert s.player.username == "newbie"
        assert s.location_name == "The Starting Chamber"
