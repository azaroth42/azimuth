
import os
import json


class Storage:

    def load(self, what_id):
        raise NotImplementedError()

    def save(self, what):
        raise NotImplementedError()


class DictStorage(Storage):
    def __init__(self):
        self.data = {}

    def load(self, what_id):
        return self.get(what_id, None)

    def save(self, what):
        self.data[what['id']] = what


class SimpleFileStorage(Storage):
    def __init__(self):
        self.directory = "db"
        self.suffix = ".json"
        self.slash_replacement = "___"
        self.key = "id"
        if not os.path.exists(self.directory):
            os.mkdir(self.directory)


    def _read_file(self, fn):
        fn = os.path.join(self.directory, fn)
        if os.path.exists(fn):
            with open(fn) as fh:
                data = fh.read()
            try:
                js = json.loads(data)
            except:
                js = {"file": data}
            return js
        else:
            return None

    def load(self, what_id):
        fn = what_id.replace('/', self.slash_replacement)
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
        key = what['id']
        key = key.replace('/', self.slash_replacement)
        if not key.endswith(self.suffix):
            key = key + self.suffix
        fn = os.path.join(self.directory, key)

        with open(fn, "w") as fh:
            fh.write(json.dumps(what))

