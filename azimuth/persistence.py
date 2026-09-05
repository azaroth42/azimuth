import os
import json
import glob
import sqlite3
import requests


class Storage:
    def get_object_by_name(self, name, clss=None):
        return None

    def get_object_by_id(self, id, clss=None):
        return None

    def load(self, what_id):
        raise NotImplementedError()

    def save(self, what):
        raise NotImplementedError()


class DictStorage(Storage):
    def __init__(self):
        self.data = {}

    def load(self, what_id):
        return self.data.get(what_id, None)

    def save(self, what):
        self.data[what["id"]] = what


class SimpleFileStorage(Storage):
    def __init__(self, directory="db"):
        self.directory = directory
        self.suffix = ".json"
        self.slash_replacement = "___"
        self.key = "id"
        if not os.path.exists(self.directory):
            os.mkdir(self.directory)

    def _file_to_id(self, path):
        """Full file path in self.directory -> object id (filename minus .json)."""
        return os.path.basename(path)[:-len(self.suffix)]

    def _read_file(self, fn):
        fn = os.path.join(self.directory, fn)
        if os.path.exists(fn):
            with open(fn) as fh:
                data = fh.read()
            try:
                js = json.loads(data)
            except Exception:
                js = {"file": data}
            return js
        else:
            return None

    def iter_ids(self):
        for fn in os.listdir(self.directory):
            if fn.endswith(self.suffix):
                yield fn[: -len(self.suffix)]

    def load(self, what_id):
        fn = what_id.replace("/", self.slash_replacement)
        if not fn.endswith(self.suffix):
            fn = fn + self.suffix
        js = self._read_file(fn)
        if js is not None:
            return js
        else:
            print(f"File does not exist: {self.directory}/{fn}")
            return None

    def save(self, what):
        # subst / in identifier to avoid hierarchy
        # identifiers SHOULD always be uuids but ...
        key = what["id"]
        key = key.replace("/", self.slash_replacement)
        if not key.endswith(self.suffix):
            key = key + self.suffix
        fn = os.path.join(self.directory, key)
        with open(fn, "w") as fh:
            fh.write(json.dumps(what))

    def delete(self, what_id):
        key = what_id.replace("/", self.slash_replacement)
        if not key.endswith(self.suffix):
            key = key + self.suffix
        fn = os.path.join(self.directory, key)
        if os.path.exists(fn):
            os.remove(fn)
        else:
            print(f"File does not exist: {self.directory}/{fn}")

    def get_object_by_id(self, id, clss=None):
        fn = id.replace("/", self.slash_replacement)
        files = glob.glob(os.path.join(self.directory, f"{fn}*"))
        print(f"files: {files}")
        if clss is not None:
            # Restrict by class (mirrors the other backends -- they always
            # apply it, even to a single match); the players file and other
            # class-less docs must not break this.
            files = [
                f
                for f in files
                if (self._read_file(f) or {}).get("class") == clss.__name__
            ]
        if len(files) == 1:
            return self.load(self._file_to_id(files[0]))
        elif files:
            # Ambiguous: a list of ids, matching the other backends' contract
            # (used to return full file paths here).
            return [self._file_to_id(f) for f in files]
        else:
            return None

    def get_object_by_name(self, name, clss=None):
        # Field-accurate name search, mirroring SqliteStorage: a
        # case-insensitive exact match on name or on any alias.  The old
        # implementation grepped whole files, which also matched
        # descriptions, ids and even class names -- e.g. "switch" hit a
        # SwitchableObject named "lamp" -- and returned the wrong object.
        ql = name.lower()
        files = []
        for fid in self.iter_ids():
            doc = self._read_file(fid + self.suffix) or {}
            if str(doc.get("name", "")).lower() == ql:
                files.append(fid)
            elif any(str(a).lower() == ql for a in doc.get("aliases", [])):
                files.append(fid)
        if clss is not None:
            # Disambiguate by class (mirrors the other backends); the
            # players file and other class-less docs must not break this.
            files = [
                fid
                for fid in files
                if (self._read_file(fid + self.suffix) or {}).get("class")
                == clss.__name__
            ]
        if len(files) == 1:
            return self.load(files[0])
        elif files:
            print(f"Multiple files found for name '{name}'")
        return None

    def get_all_objects(self, clss=None):
        objs = []
        for id in self.iter_ids():
            obj = self.load(id)
            if clss is None or "class" in obj and obj["class"] == clss.__name__:
                objs.append(obj)
        return objs


class SqliteStorage(Storage):
    """SQLite-backed storage: one row per object in a single database file.

    The whole object dict is stored as a JSON blob in `data`; `id`/`class`/
    `name`/`aliases` are also extracted into columns so lookups are indexed
    queries rather than file greps.

    Contract (same as the other backends): load/save/delete take/return
    plain dicts; get_object_by_id does a *prefix* match and returns a dict
    (unique), a list of ids (ambiguous) or None; get_object_by_name does a
    case-insensitive exact name-or-alias match and returns None when the
    result is ambiguous.
    """

    def __init__(self, path="db/azimuth.db"):
        self.path = path
        d = os.path.dirname(path)
        if d and not os.path.exists(d):
            os.makedirs(d, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS objects (id TEXT PRIMARY KEY, class TEXT, "
            "name TEXT, aliases TEXT, data TEXT NOT NULL)"
        )
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_objects_class ON objects(class)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_objects_name ON objects(name)")
        self.conn.commit()

    def close(self):
        self.conn.close()

    # --- core CRUD ---
    def load(self, what_id):
        r = self.conn.execute("SELECT data FROM objects WHERE id = ?", (what_id,)).fetchone()
        return json.loads(r[0]) if r else None

    def save(self, what):
        self.conn.execute(
            "INSERT INTO objects (id, class, name, aliases, data) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "class = excluded.class, name = excluded.name, "
            "aliases = excluded.aliases, data = excluded.data",
            (
                what["id"],
                what.get("class"),
                what.get("name"),
                json.dumps(what.get("aliases", [])),
                json.dumps(what),
            ),
        )
        self.conn.commit()

    def delete(self, what_id):
        self.conn.execute("DELETE FROM objects WHERE id = ?", (what_id,))
        self.conn.commit()

    # --- lookups ---
    def iter_ids(self):
        for (id,) in self.conn.execute("SELECT id FROM objects"):
            yield id

    def get_object_by_id(self, id, clss=None):
        # Prefix match; escape LIKE wildcards so ids with them can't widen it.
        prefix = id.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        sql = "SELECT id FROM objects WHERE id LIKE ? ESCAPE '\\'"
        args = [prefix + "%"]
        if clss is not None:
            sql += " AND class = ?"
            args.append(clss.__name__)
        ids = [r[0] for r in self.conn.execute(sql, args)]
        if len(ids) == 1:
            return self.load(ids[0])
        elif len(ids) > 1:
            return ids
        return None

    def get_object_by_name(self, name, clss=None):
        # Case-insensitive exact match on name or any alias (quote-bounded in
        # the JSON aliases column). The file backend greps whole files, which
        # also matches descriptions; this is stricter but unambiguous.
        # The parens wrap the whole OR, so a `clss` filter (appended below as
        # `AND class = ?`) applies to both halves -- without them, AND binds
        # only to the aliases half and rows matched by name slip past it.
        sql = (
            "SELECT id FROM objects WHERE (lower(name) = lower(?)"
            " OR lower(aliases) LIKE ?)"
        )
        args = [name, f'%\"{name.lower()}\"%']
        if clss is not None:
            sql += " AND class = ?"
            args.append(clss.__name__)
        ids = [r[0] for r in self.conn.execute(sql, args)]
        if len(ids) == 1:
            return self.load(ids[0])
        return None

    def get_all_objects(self, clss=None):
        if clss is not None:
            rows = self.conn.execute("SELECT data FROM objects WHERE class = ?", (clss.__name__,))
        else:
            rows = self.conn.execute("SELECT data FROM objects")
        return [json.loads(r[0]) for r in rows]


class MlStorage(Storage):
    def __init__(self, base_url, username, password, database):
        self.data_url = "http://localhost:5001/data"
        self.base_url = base_url
        self.username = username
        self.password = password
        self.database = database
        self.auth = requests.auth.HTTPDigestAuth(username, password)
        self.headers = {"Accept": "application/json"}

    def load(self, docid):
        url = f"{self.base_url}/v1/documents"
        params = {"database": self.database}
        params["uri"] = f"{self.data_url}/{docid}"
        headers = self.headers.copy()
        headers["Content-Type"] = "application/json"
        r = requests.get(url, auth=self.auth, headers=headers, params=params, timeout=3)
        if r.status_code == 404:
            return None
        elif r.status_code != 200:
            raise Exception(f"Unexpected status code: {r.status_code}")
        else:
            js = r.json()
            return js

    def save(self, what):
        url = f"{self.base_url}/v1/documents"
        params = {"database": self.database}
        params["uri"] = f"{self.data_url}/{what['id']}"
        data = json.dumps(what)
        headers = self.headers.copy()
        headers["Content-Type"] = "application/json"
        r = requests.put(url, auth=self.auth, headers=headers, params=params, data=data, timeout=3)
        if r.status_code not in [200, 201]:
            return None
        elif r.status_code >= 300:
            raise Exception(f"Failed to save document: {r.text}")

    def delete(self, docid):
        url = f"{self.base_url}/v1/documents"
        params = {"database": self.database}
        params["uri"] = f"{self.data_url}/{docid}"
        headers = self.headers.copy()
        headers["Content-Type"] = "application/json"
        r = requests.delete(url, auth=self.auth, headers=headers, params=params, timeout=3)
        print(r.status_code)

    def _make_results(self, js):
        results = []
        for doc in js["results"]:
            docid = doc["uri"].rsplit("/")[-1]
            results.append(docid)
        return results

    def do_search(self, query):
        url = f"{self.base_url}/v1/search"
        params = {"database": self.database}
        data = json.dumps(query)
        headers = self.headers.copy()
        headers["Content-Type"] = "application/json"
        r = requests.post(url, auth=self.auth, headers=headers, params=params, data=data, timeout=3)
        js = r.json()
        results = self._make_results(js)
        if len(results) == 1:
            return self.load(results[0])
        elif not results:
            return None
        else:
            return []

    def get_object_by_id(self, docid, clss=None):
        # id is leading fragment, not the full id (otherwise would just use load)
        fwq = {"fieldWordQuery": {"field": "id", "text": f"{docid}*"}}
        if clss is not None:
            cfwq = {"fieldWordQuery": {"field": "class", "text": clss.__name__}}
            fwq = {"andQuery": {"queries": [fwq, cfwq]}}
        cts = {"ctsquery": fwq}
        return self.do_search(cts)

    def get_object_by_name(self, name, clss=None):
        fwq = {"fieldWordQuery": {"field": "name", "text": name}}
        if clss is not None:
            cfwq = {"fieldWordQuery": {"field": "class", "text": clss.__name__}}
            fwq = {"andQuery": {"queries": [fwq, cfwq]}}
        cts = {"ctsquery": fwq}
        return self.do_search(cts)
