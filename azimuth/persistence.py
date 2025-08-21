import os
import json
import glob
import requests
import subprocess


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
        if len(files) == 1:
            fn = files[0].replace(f"{self.directory}/", "").replace(".json", "")
            return self.load(fn)
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

    def get_object_by_name(self, name, clss=None):
        # Use system `grep` to search files for name
        cmd = f"grep '{name}' {self.directory}/*"
        output = subprocess.check_output(cmd, shell=True).decode("utf-8")
        files = output.split("\n")
        files = [f.split(":")[0] for f in files if f]
        print(f"grep: {files}")
        if len(files) == 1:
            fn = files[0].replace(f"{self.directory}/", "").replace(".json", "")
            return self.load(fn)
        elif len(files) > 1:
            raise ValueError(f"Multiple files found for name '{name}'")

        # Brute force search of all objects
        files = os.listdir(self.directory)
        for fn in files:
            fn = os.path.join(self.directory, fn)
            with open(fn) as fh:
                data = json.load(fh)
                if data["name"] == name:
                    if clss:
                        if data["class"] == clss.__name__:
                            return self.load(fn)
                    else:
                        return self.load(fn)
        return None


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
