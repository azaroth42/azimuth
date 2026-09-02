# Azimuth — Architecture & Handoff Notes

This document is written for the next person (or agent) picking up development.
It captures the architecture, the current state of the code, the known bugs,
and the design quirks that look like bugs but aren't.

---

## 1. Project overview

Azimuth is a **LambdaMOO-inspired M\* (MetaMUD) server in modern Python**, started
as a "vibe coding" experiment and grown into a working engine with an AI layer:

- **Data-driven objects** — every object is a plain JSON dict with a `class`
  string, rehydrated dynamically at load time (`World.import_class` uses
  `importlib`), so a class can even be *reassigned at runtime* via `@chparent`.
- **A MOO-style programmer tier** — `Programmer` players get `eval` (with `#name`
  object references), `@create`, `@dig`, `@chparent`, `@message`, … to build and
  edit the world from inside the MUD.
- **AI-native** — an LLM room-builder agent generates environments in-process,
  and a (currently disabled) MCP mount exposes world objects for agent inspection.

### State as of writing

- Tests: **28/28 passing** on the file backend (`python run-tests.py`) and on
  sqlite (`python run-tests.py --db sqlite`): 20 game-logic + 8 storage-contract
  tests (the contract tests run against *both* backends in-process).
- The working tree may have uncommitted changes (user + agent edits interleave);
  run `git status` first. Do not revert changes you did not make.
- `db/` (world state) and `.env` are **gitignored** — they do not survive a git
  clone. If you are moving machines, **copy `db/` and `.env/` out of band**.

## 2. Repository map

| Path | What it is |
|---|---|
| `azimuth/main.py` | FastAPI + Socket.IO ASGI app; REST endpoints; MCP mount **commented out** (see §6.1) |
| `azimuth/world.py` | `World` class: object cache, lazy loading, login/register, **the command dispatcher**, `setup_world` bootstrap |
| `azimuth/command_decorator.py` | `@make_command` decorator + global `commands` registry |
| `azimuth/entities.py` | `BaseThing`/`Place`/`Exit`/`Object`/`Player`/`Programmer` + composite classes (`Container`, `Clothing`, `Furniture`, `HeldObject`, `OpenableExit`, `LockableExit`, `OpenableContainer`) |
| `azimuth/mixins.py` | Capability mixins: `StateToggle`→`Openable`/`Lockable`/`Switchable`, `Containable`, `Positionable`, `Holdable`, `Wearable` |
| `azimuth/persistence.py` | `Storage` ABC, `SimpleFileStorage` (default), `SqliteStorage`, `MlStorage` (MarkLogic) |
| `azimuth/agents/` | `RoomBuilderAgent` (in-process LLM world-builder), `config.py` (env-driven config + system prompts) |
| `azimuth/templates/index.html` | Browser terminal client (socket.io from CDN) |
| `client.py` | Text client (python-socketio + prompt_toolkit) |
| `run.py` | Server entry: uvicorn on `0.0.0.0:5001`, `--reload` |
| `run-repl.py` | In-process world + agent seed — **no `__main__` block (stub)** |
| `run-agent.py` | **Stale** — old remote-socket-client agent design; will not run |
| `run-tests.py` + `tests/` | Serverless test framework + suite (see §6.4) |
| `run-migrate-sqlite.py` | One-way port of a file world (`db/*.json`) into the SQLite backend |
| `experiments/spacy_parser.py` | Unwired NLP parser experiment (spaCy + bagpipes_spacy) |
| `db/` | World state (gitignored): one JSON file per object on the file backend, or `azimuth.db` on sqlite |
| `requirements.txt` | Server deps; note the `mcp<2` pin (see §6.1) |

## 3. Setting up on a new machine

```bash
# 1. Get the code (plus db/ and .env copied out of band — they are gitignored)
git clone <repo> && cd azimuth

# 2. Fresh venv (venvs don't move)
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt prompt_toolkit   # prompt_toolkit: needed by client.py, not yet in requirements

# 3. Verify
.venv/bin/python run-tests.py      # expect 28/28 passed (add --db sqlite for the sqlite backend)

# 4. Run
.venv/bin/python run.py            # server on 0.0.0.0:5001
# then: .venv/bin/python client.py  OR  open http://localhost:5001/
# log in:  login wizard wizard      (Programmer; hash is baked into fresh worlds)
# or:      register <user> <password> <email>
```

If `db/` is absent, `setup_world` bootstraps a small demo world (three rooms,
wizard, sword, armor, bread, key, chest+gem) on first start.

Caveat: `run.py` runs uvicorn with `--reload`, whose watcher covers the whole
project directory **including `db/` and `.venv/`** — file churn there restarts
the server.

## 4. Server stack (`azimuth/main.py`)

FastAPI wrapped in `socketio.ASGIApp`, served by uvicorn:

1. **Socket.IO** — `connect` (MOTD + login prompt), `command` (→
   `world.process_player_command`), `disconnect` (persists `last_location`).
   Commands are processed synchronously inside the async handler ("for now").
2. **Web client** — `GET /` serves the browser terminal (Jinja2).
3. **REST** — `GET /data/{id}` and `GET /search/{name}` (world inspection).
4. **MCP — currently disabled**: the `FastApiMCP(...)` + `mcp.mount()` lines are
   commented out in `main.py`. Re-enabling is two uncomments. Historical note:
   the server moved Flask→FastAPI specifically to get `fastapi_mcp`, which is why
   `requirements.txt` pins `mcp>=1.12.0,<2` (fastapi-mcp 0.4.0 is incompatible
   with the mcp 2.x `Server()` signature — it crashed at startup with
   `TypeError: Server.__init__() takes 2 positional arguments but 3 were given`).
   Keep the pin unless fastapi-mcp is upgraded.

## 5. World engine (`azimuth/world.py`)

- `World.active_objects` — in-memory cache `{id: instance}`; `active_sids`
  `{socket sid: player id}`; `players` `{username: id}` (loaded from
  `{WORLD_ID}_players.json`).
- **Lazy loading** — `get_object(id)` checks the cache, else `load(id)` →
  `db.load` → `make_instance` (reads the `class` field, `import_class`, builds).
  `Exit` destinations load with `recursive=False` (a string id until first use).
- **Persistence** — `dump_database()` saves all active objects (called on server
  shutdown); disconnect saves the player's `last_location`. Players also persist
  on register/login.
- **Login** — `handle_login` verifies against the `players` map +
  `werkzeug` password hashes, then `World.login` attaches the sid and moves the
  player to `last_location` (or the start room). Unknown username → graceful
  "do not match" (was a `KeyError`, fixed).
- **Bootstrap** — `setup_world(db, world_id)` creates the demo world only when
  no `{WORLD_ID}.json` config exists.

## 6. Subsystems

### 6.1 Command system (the heart of it)

Registration → merge → dispatch, three stages:

1. **Registration** — `@make_command(verb, dobj, prep, iobj)` (in
   `command_decorator.py`) appends an info dict
   `{"verb": [...], "dobj", "prep", "iobj"}` to the function's `_commands` list
   and to a **module-level `commands` registry** keyed by `(module, ClassName)`.
   `World.register_commands()` (called once from `setup_world`) assigns each such
   class its own `default_commands = {verb: [info, ...]}` **instance attribute**
   (this is deliberate — see §6.2) and stamps `info["func"] = fn`.
2. **Merge** — `BaseThing.get_commands(match=None)` walks
   `list(inspect.getmro(cls))[:-1]` **reversed** (shallowest ancestor first) and
   merges each class's `default_commands`, then the instance's data-driven
   `self.commands`, into a per-instance `commands_cached` dict.
   **Important fix (already in):** the walk uses
   `c.__dict__.get("default_commands")` and skips `None` — plain attribute access
   would make a command-less subclass (HeldObject, Container, Clothing,
   Furniture, LockableExit) inherit its parent's dict and merge it twice.
   Each *distinct* handler appears exactly once. The merged list is
   shallowest-ancestor-first, which is why the dispatcher (step 3) walks it in
   `reversed(...)` order — otherwise a generic ancestor handler would shadow
   the specialized subclass's (the verb-shadowing bug; fixed, pinned by
   `tests/test_exits.py`).
3. **Dispatch** — `World.process_player_command(player_id, argstr)`:
   - Special prefixes: `'…'`→say, `:`/`;`→emote, `|`→eval, `n/s/e/w/ne/…/up/down`→exits.
   - Otherwise: strip the verb, then for each `s` in
     `[player, player.location, *player.contents, *player.location.contents, *player.location.exits.values(), world]`
     look up `s.get_commands(verb)` and try each entry in **`reversed(...)`
     order (deepest-first), first structural match wins** (`return` on
     dispatch — the `reversed` is the verb-shadowing fix):
     - single word, argless command → run;
     - single word, argumented command → `continue` (see `bare drop`);
     - `prep` present → split argstr on `" <prep> "` into (dobj, iobj), validate
       presence against the declared `dobj`/`iobj`, then resolve `self`-marked
       objects via `match_object`;
     - no prep → `dobj == "self"` requires `s.match_object(argstr, player, verb)`;
       `dobj == "any"` runs as-is.
   - No match anywhere → "I don't understand that."

**Object matching** (`BaseThing.match_object(name, player, verb)`) returns an int
(`1` exact, `2` prefix, `0` none; `None`-ish/falsy = fail). Before name matching
it consults `okay_for_verb(verb, player)` (e.g. `Object`: must be visible;
`get/take/pick` requires same room; `drop` requires being carried — **must end in
`return True`**, a missing one here was the cause of the original "can't get
sword" bug). Names match full, by alias, by **last word** ("sword" matches "rusty
sword"), or by prefix. `"me"`/`"here"` are special.

**Messages** — `BaseThing.messages` merges `world.default_messages` ← each class's
`default_messages` (MRO reversed) ← instance `_messages`, via `dict.update`, so
**deeper classes correctly win** on message-name conflicts. `get_message(key, who,
what=None)` `.format()`s `{player}`/`{self}`/`{object}`. Do not apply the command
merge pattern here — the message semantics are already correct.

**Known-safe quirk** — a verb can legitimately have *multiple* entries with
*different* argument structures (e.g. `Object` "get self" and `Containable`
"get Object from self"). All are kept; structure is what disambiguates.

### 6.2 Object model

```
BaseThing  (uuid, location/contents graph, move_to, look_at, messages, match_object, get_commands)
├── Place   (exits dict keyed by exit name, coordinates, room look/announce)
│   └── (rooms created by setup_world / agent / @dig)
├── Exit    (source/destination, use() = move + announces; lazy destination)
│   ├── OpenableExit   (Exit, Openable) — closed blocks travel; announces across
│   └── LockableExit   (OpenableExit, Lockable)
├── Object  (get/drop/use + take_ok/drop_ok/use_*_ok + *_effect hooks)
│   ├── Container      (Object, Containable) — put/take-from/look-in
│   ├── OpenableContainer
│   ├── Furniture      (Object, Positionable — mostly stubs, prints "saw: …")
│   ├── Clothing       (Object, Containable, Wearable)
│   └── HeldObject     (Object, Holdable — wield/unwield)
└── Player  (connection sid, username, password_hash, last_location, home,
    │        say/emote/whisper(stub)/who/@quit/@desc/@home/@sethome/inv)
    └── Programmer  (eval with #refs, @dig, @create, @chparent, @rename,
                     @teleport, @dumpdb, @messages/@message)
```

Mixins (`mixins.py`) provide the state machinery: `StateToggle.toggle_on/off`
drives `Openable` (is_open + paired object) / `Lockable` (is_locked,
locked_by_object/player) / `Switchable` (is_on, no commands yet).

**Note the mixin MROs are diamonds** — e.g.
`LockableExit.__mro__` = `LockableExit → OpenableExit → Exit → BaseThing → Lockable → Openable → StateToggle`
(`BaseThing` lands *before* `Lockable`!). This is why dispatch order matters
here: the dispatcher walks each verb's entries deepest-first, so the specialized
handler (e.g. `OpenableExit.use`'s closed check, `Lockable.open`'s lock check)
beats the generic ancestor.

### 6.3 Persistence (`azimuth/persistence.py`)

The `Storage` base class declares only `load`/`save`/`get_object_by_name`/
`get_object_by_id`; `delete`/`get_all_objects`/`iter_ids`/`close` exist on the
backends that support them (deliberately not in the base — `MlStorage` still
lacks `get_all_objects`). All methods take/return **plain dicts**; `World`
rehydrates them via `make_instance` (which reads the `class` key).

Shared lookup contract (pinned by `tests/test_storage.py`, which runs every
scenario against **both** file and sqlite backends):

- `get_object_by_id(id, clss)` — **prefix** match on id; returns the dict when
  unique, a **list of ids** when ambiguous, `None` on no match. `clss` always
  filters — including a single match (a `#`-reference to a non-matching class
  must not resolve).
- `get_object_by_name(name, clss)` — case-insensitive; dict when unique,
  `None` when ambiguous (`clss` disambiguates).
- `get_all_objects(clss)` — every stored dict, optionally class-filtered.

- `SimpleFileStorage(directory="db")` — one JSON file per object; `id` is the
  filename (`/` → `___`). **Name search shells out to `grep`** (`shlex.quote`d
  — once a raw shell injection; `subprocess.run(..., capture_output=True)` —
  grep exits 1 on no match, which used to raise `CalledProcessError`). Returns
  `None` when multiple files match. Expect the "File does not exist" /
  "Multiple files found" prints. Ambiguous id lookups return a **list of ids**
  (they used to return full file paths). **Deliberate divergence from sqlite**
  (documented in the test, not "fixed"): the file backend greps *substrings* of
  whole files — a name appearing in another object's description, or a partial
  name, matches — while sqlite does an exact name-or-alias match.
- `SqliteStorage(path="db/azimuth.db")` — one row per object in a single
  database file (stdlib `sqlite3`, no new dependency; selected via
  `AZIMUTH_DB_TYPE=sqlite`, path via `AZIMUTH_SQLITE_PATH`). Schema: `objects(
  id TEXT PRIMARY KEY, class, name, aliases, data)` — the whole object dict is
  the JSON `data` blob; `id`/`class`/`name`/`aliases` are extracted to indexed
  columns (`class`, `name`). `save` is `INSERT ... ON CONFLICT(id) DO UPDATE`
  (upsert — idempotent). Name search: `(lower(name) = lower(?) OR
  lower(aliases) LIKE '%"<name>"%')` — **keep the parens around the OR**, or a
  `clss` filter binds only to the aliases half and name-matched rows slip past
  it (this was a real bug). Id prefix matching escapes `%`/`_`/`\\` LIKE
  wildcards. Call `close()` to release the connection (`TestWorld.clean()`
  does).
- `MlStorage` — MarkLogic REST (CTQ queries). Quirk: document URIs are
  `http://localhost:5001/data/{id}` (its own web path is baked into the URI).
  Selected via `AZIMUTH_DB_TYPE=marklogic`. Still lacks `get_all_objects`.
- The `{WORLD_ID}_players.json` file is a username→id map; `persist_players`
  injects an `id` key that `World.__init__` strips on load (it also tolerates a
  missing `class` key via bare except). **Older worlds have a players file
  without an `id`** — the file backend tolerates that (it addresses docs by
  filename), but `SqliteStorage.save` requires one: the test framework's
  sqlite seed and `run-migrate-sqlite.py` both inject the filename stem.

**Migration:** `run-migrate-sqlite.py [--db-dir db] [--sqlite-path
<db/azimuth.db>]` ports a file world into the SQLite database. Idempotent
(upsert by id); the JSON files are never deleted, so reverting
`AZIMUTH_DB_TYPE` switches the server back to the file world.

### 6.4 Test framework (`tests/`, `run-tests.py`)

Serverless: a `TestWorld` builds a real `World` in-process against a **throwaway
copy of `db/`** (temp dir; pristine copy if no real `db/` exists) with a
`FakeSocketIO` capturing what the game would emit. No FastAPI/uvicorn/socketio.

```bash
python run-tests.py [name filter] [--db <file|sqlite>] [--keep-db]
```

`--db` selects the storage backend every `TestWorld` uses. Each `TestWorld`
seeds from the **real** `db/` so tests see the same world regardless of
backend: the file backend copies the directory; the sqlite backend re-imports
each JSON doc into a throwaway database (injecting an `id` into the players
file, which has none in older worlds). Run the suite on both to verify a
backend change.

Helpers on `AzimuthTest` (base class): `wizard()` (login + move to start room),
`place_object(name, where)` (restore layout preconditions — the copied world
reflects whatever the real world has become), `assert_msg(msgs, *want, absent=)`,
`self.tw` (the `TestWorld`), `Session.send(cmd)` → new messages,
`Session.inventory()`, `Session.location_name`.

Each test method gets a **fresh** `TestWorld`. New test = subclass `AzimuthTest`
in a `tests/` module, add `test_*` methods. To construct exotic objects in tests,
see `_make_lockable_exit()` in `tests/test_objects.py` (exits accept
`source`/`destination: None`).

### 6.5 AI layer (`azimuth/agents/`)

`RoomBuilderAgent(world, config)` runs **in-process** (drives the wizard) and
calls LM Studio's OpenAI-compatible API (`LM_STUDIO_URL`/`MODEL`/…) in two phases:
**Plan** (strict-JSON grid of 12–15 rooms + exits, validated, ASCII-mapped) then
**Describe** (per-room description + ≤5 class-restricted objects), instantiating
through the normal entity constructors. `run-repl.py` is the in-process seed
(no main block); `run-agent.py` is a stale relic of the earlier
remote-socket-client design (wrong imports, methods that no longer exist) —
either rewrite or delete it.

## 8. Other known issues (curated)

- **`Lockable` message keys missing** — `StateToggle.toggle_on/off` emits
  `toggle_locked_on`/`toggle_locked_off`/`*_others`, none of which exist in
  `Lockable.default_messages`; `get_message` falls back to `""`, so `lock`/
  `unlock` reply with a blank line. Adding the keys is a one-liner each.
- **`unlock` shares the old pattern** — failure message then unconditional
  `toggle_off` (no `return`); only bites when `locked_by_player` is set, which
  nothing in the current world does. Mirror the `lock` fix if you wire up
  lock-keys.
- **The `open` name collision is a live hazard** — the mixin fix covers
  `lock`/`lock_with`, but anywhere else code does `if self.open` on an exit it
  gets a truthy method. Use `self.is_open`. (Renaming the command methods to
  e.g. `do_open` would defuse it for good.)
- **`Positionable` is a stub** — `sit/stand/…` handlers just print `saw: …`;
  `Positionable.look_at` returns `""`.
- **`whisper` is an empty stub** on `Player` (registered, does nothing).
- **`run-agent.py` is stale** (see §6.5); `run-repl.py` has no main block.
- **spaCy/bagpipes_spacy are missing from `requirements.txt`** (only needed by
  `experiments/`); `prompt_toolkit`/`textual` for the clients *are* present.
- **`handle_register`** has `except: raise` with an unreachable `return` after it
  (harmless; tidy up if touching the file).
- **`Positionable`/`Switchable` `StateToggle` machinery**: `Switchable` has no
  commands yet (levers/buttons unimplemented).
- **grep-based name search** (`SimpleFileStorage.get_object_by_name`) is fuzzy:
  a name appearing in another object's description/file yields `None`.
- **Debug print left behind**: `Object.okay_for_verb` in entities.py still has
  `print("failed match for location")` — safe to remove.
- **`--reload` watches `db/` and `.venv/`** — expect server restarts when those
  change; consider narrowing uvicorn's watch dirs in `run.py`.

## 9. Suggested next steps (roughly in order)

1. Tidy the §8 door nits (missing `Lockable` message keys, `unlock` pattern,
   optionally rename the `open` command methods).
2. Narrow `run.py`'s reload watch dirs.
3. Rewrite-or-delete `run-agent.py`; give `run-repl.py` a real main loop.
4. Re-enable the MCP mount (§6.1) and add *write* actions (create/modify) —
   read tools are the two GET endpoints only.
5. Finish `Positionable`/`Switchable` (sit/stand, levers) and `whisper`.
6. Wire in `experiments/spacy_parser.py` to replace the preposition-split parser.
7. More robust persistence (Redis/Postgres) if file+SQLite+MarkLogic feel
   limiting.
