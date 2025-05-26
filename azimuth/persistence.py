import os
import json
import glob


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
        return self.get(what_id, None)

    def save(self, what):
        self.data[what["id"]] = what


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
            except Exception:
                js = {"file": data}
            return js
        else:
            return None

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

    def get_object_by_id(self, id, clss=None):
        fn = id.replace("/", self.slash_replacement)
        files = glob.glob(os.path.join(self.directory, f"{fn}*"))
        if len(files) == 1:
            return self.load(files[0])
        elif files:
            if clss:
                # read them and filter for 'class' == clss.__name__
                filtered_files = [f for f in files if self._read_file(f)["class"] == clss.__name__]
                if len(filtered_files) == 1:
                    return self.load(filtered_files[0])
                else:
                    return filtered_files
            return files
        else:
            return None
