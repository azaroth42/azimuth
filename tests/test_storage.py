"""Backend-agnostic storage contract tests.

Every scenario runs against BOTH SimpleFileStorage and SqliteStorage so the
backends can't drift apart. These exercise the storage layer directly, not
the game logic (for that, run the rest of the suite with `--db sqlite`).
"""

import os
import shutil
import tempfile

from azimuth.persistence import SimpleFileStorage, SqliteStorage

from .framework import AzimuthTest


class StorageContractTest(AzimuthTest):
    def _backends(self):
        """One of each backend in a fresh temp dir; cleaned up afterwards."""
        d = tempfile.mkdtemp(prefix="az_storage_")
        file_db = SimpleFileStorage(os.path.join(d, "db"))
        sqlite_db = SqliteStorage(os.path.join(d, "w.sqlite3"))
        try:
            yield "file", file_db
            yield "sqlite", sqlite_db
        finally:
            sqlite_db.close()
            shutil.rmtree(d, ignore_errors=True)

    def test_save_load_roundtrip(self):
        for label, db in self._backends():
            db.save(
                {"id": "x1", "class": "Object", "name": "A thing", "aliases": ["at"]}
            )
            got = db.load("x1")
            assert got is not None, label
            assert got["name"] == "A thing", label
            assert got["aliases"] == ["at"], label
            db.save({"id": "x1", "class": "Object", "name": "Renamed"})
            assert db.load("x1")["name"] == "Renamed", f"{label}: overwrite"

    def test_load_missing(self):
        for label, db in self._backends():
            assert db.load("no-such-id") is None, label

    def test_delete(self):
        for label, db in self._backends():
            db.save({"id": "x1", "name": "A"})
            db.delete("x1")
            assert db.load("x1") is None, label
            db.delete("no-such-id")  # must not raise

    def test_get_by_id_prefix(self):
        for label, db in self._backends():
            db.save({"id": "abcdef-1", "class": "Place", "name": "P1"})
            db.save({"id": "abcdef-2", "class": "Place", "name": "P2"})
            unique = db.get_object_by_id("abcdef-1")
            assert isinstance(unique, dict) and unique["id"] == "abcdef-1", label
            ambiguous = db.get_object_by_id("abcdef")
            assert isinstance(ambiguous, list) and len(ambiguous) == 2, label
            assert db.get_object_by_id("zzzz") is None, label

    def test_get_by_id_class_filter(self):
        for label, db in self._backends():

            class Place:
                pass

            class Object:
                pass

            class Exit:
                pass

            db.save({"id": "k-1", "class": "Place", "name": "R"})
            db.save({"id": "k-2", "class": "Object", "name": "O"})
            # "k" is a prefix of BOTH ids; the class filter disambiguates
            got = db.get_object_by_id("k", Place)
            assert isinstance(got, dict) and got["id"] == "k-1", label
            got = db.get_object_by_id("k", Object)
            assert isinstance(got, dict) and got["id"] == "k-2", label
            # a filter that matches nothing returns None
            assert db.get_object_by_id("k", Exit) is None, label

    def test_get_by_name(self):
        for label, db in self._backends():
            db.save({"id": "n1", "class": "Place", "name": "Glittering Cave"})
            db.save({"id": "n2", "class": "Object", "name": "Gem", "aliases": ["shiny gem"]})
            db.save({"id": "n3", "class": "Object", "name": "Other"})
            # exact, case-insensitive
            got = db.get_object_by_name("glittering cave")
            assert got is not None and got["id"] == "n1", label
            # alias
            got = db.get_object_by_name("SHINY GEM")
            assert got is not None and got["id"] == "n2", label
            # no match at all
            assert db.get_object_by_name("nope") is None, label
            # NOTE: a partial name ("glittering") is intentionally NOT part of
            # the shared contract: the file backend greps substrings (so it
            # matches), sqlite does exact name/alias matching (so it doesn't).

    def test_get_by_name_ambiguous_and_class(self):
        for label, db in self._backends():

            class Place:
                pass

            db.save({"id": "a1", "class": "Place", "name": "Twin"})
            db.save({"id": "a2", "class": "Object", "name": "Twin"})
            assert db.get_object_by_name("twin") is None, f"{label}: ambiguous"
            got = db.get_object_by_name("twin", Place)
            assert got is not None and got["id"] == "a1", label

    def test_get_all_objects(self):
        for label, db in self._backends():

            class Place:
                pass

            class Object:
                pass

            db.save({"id": "b1", "class": "Place", "name": "R1"})
            db.save({"id": "b2", "class": "Place", "name": "R2"})
            db.save({"id": "b3", "class": "Object", "name": "O"})
            all_ids = {o["id"] for o in db.get_all_objects()}
            assert all_ids == {"b1", "b2", "b3"}, label
            places = db.get_all_objects(Place)
            assert {o["id"] for o in places} == {"b1", "b2"}, label
            assert db.get_all_objects(Object) == [db.load("b3")], label
