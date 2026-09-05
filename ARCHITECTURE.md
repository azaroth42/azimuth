# Azimuth — Architecture & Handoff Notes

This document is written for the next person (or agent) picking up development.
It captures the architecture, the current state of the code, the known bugs,
and the design quirks that look like bugs but aren't.

---

## 1. Project overview

Azimuth is a **LambdaMOO-inspired M\* (MetaMUD) server in modern Python**, started
as a "vibe coding" experiment and grown into a working engine with an AI layer:

- **Data-driven objects** — every object is a plain JSON dict recording an
  entity *base* plus a set of *mixins*; the class for that combination is
  looked up or composed at runtime (`azimuth/classfactory.py`), so an object's
  capabilities can change from inside the MUD (`@addmixin`) and its class be
  reassigned (`@chparent`).
- **Verbs in the database** — Python source stored in the world and compiled
  onto a composed class at init (`@verb`), so a combination needing a dozen
  lines does not need a class in the codebase.
- **A MOO-style programmer tier** — `Programmer` players get `eval` (with `#name`
  object references), `@create`, `@dig`, `@chparent`, `@message`, … to build and
  edit the world from inside the MUD.
- **AI-native** — an LLM room-builder agent generates environments in-process,
  and a (currently disabled) MCP mount exposes world objects for agent inspection.

### State as of writing

- Tests: **136/136 passing** on the file backend (`python run-tests.py`) and on
  sqlite (`python run-tests.py --db sqlite`) — game logic, storage contract
  (run against *both* backends in-process), the out-of-band state channel, and
  class composition (`tests/test_compose.py`).
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
| `azimuth/entities.py` | `BaseThing`/`Place`/`Exit`/`Object`/`Player`/`Programmer` + the hand-written base+mixin combinations (`Container`, `OpenableContainer`, `PositionableObject`, `WearableObject`, `SwitchableObject`, `HeldObject`, `OpenableExit`, `LockableExit`) and `EdibleThing` |
| `azimuth/mixins.py` | Capability mixins: `StateToggle`→`Openable`/`Lockable`/`Switchable`, `Containable`, `Positionable`, `Holdable`, `Wearable`, `Vehicle`, `Enterable`. All **cooperative**: each chains `super()` in `__init__`/`to_dict`/`look_at`/`state_summary` |
| `azimuth/classfactory.py` | base + mixins → class (`CANON` names, normalization, `type()` synthesis, eager build at init); stored-verb compilation |
| `azimuth/persistence.py` | `Storage` ABC, `SimpleFileStorage` (default), `SqliteStorage`, `MlStorage` (MarkLogic) |
| `azimuth/agents/` | `RoomBuilderAgent` (in-process LLM world-builder), `config.py` (env-driven config + system prompts) |
| `azimuth/templates/index.html` | Browser client (socket.io from CDN): speaks the OOB protocol, renders a live side panel, every name clickable |
| `client.py` | Text client (python-socketio + prompt_toolkit) |
| `run.py` | Server entry: uvicorn on `0.0.0.0:5001`, `--reload` |
| `run-repl.py` | In-process world + agent seed — **no `__main__` block (stub)** |
| `run-agent.py` | **Stale** — old remote-socket-client agent design; will not run |
| `run-tests.py` + `tests/` | Serverless test framework + suite (see §6.4) |
| `run-migrate-sqlite.py` | One-way port of a file world (`db/*.json`) into the SQLite backend |
| `experiments/spacy_parser.py` | Unwired NLP parser experiment (spaCy + bagpipes_spacy) |
| `OOB-PROTOCOL.md` | **Implemented** design (v1) of the out-of-band `state`/`data` socket channel — live who/inventory/room state + per-object verb summaries feeding the TUI's completion/dropdowns (`tests/test_oob.py` pins it) |
| `db/` | World state (gitignored): one JSON file per object on the file backend, or `azimuth.db` on sqlite |
| `requirements.txt` | Server **and client** deps; note the `mcp<2` pin (§6.1) and `websocket-client`, without which the socket.io clients silently fall back to HTTP long-polling (OOB-PROTOCOL.md §8) |

## 3. Setting up on a new machine

```bash
# 1. Get the code (plus db/ and .env copied out of band — they are gitignored)
git clone <repo> && cd azimuth

# 2. Fresh venv (venvs don't move)
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 3. Verify
.venv/bin/python run-tests.py      # expect 136/136 passed (add --db sqlite for the sqlite backend)

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
   Plus the **out-of-band state channel** (`OOB-PROTOCOL.md`): clients announce
   capability with a `data` `hello`; the server pushes structured
   `state` events (room/inventory/players sections + verb summaries) —
   dirty-marked in the world, coalesced, and flushed at the end of each
   `command` handler. Old clients that never hello get none of it.
2. **Web client** — `GET /` serves the browser client (Jinja2). It is a
   full OOB client: `hello` on connect, a `WorldModel` kept from `state`
   events, a side panel (exits / room / carrying / players) rendered from
   that model, and a verb menu on every name built from the object's own
   `verbs` table. Clicking sends a command on the normal `command` channel —
   the server has no idea a click happened, so nothing about dispatch or
   authority moves to the client.
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
   would make a command-less subclass (Container, OpenableContainer,
   WearableObject, HeldObject, LockableExit -- and *every* synthesized class,
   which declares none of its own) inherit its parent's dict and merge it
   twice.  This is also what makes a composed class work with no changes to
   the command system at all.
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
│   ├── Interior  — the inside of an Enterable; see §6.2.3
│   └── (rooms created by setup_world / agent / @dig)
├── Exit    (source/destination, use() = move + announces; lazy destination)
│   ├── OpenableExit   (Openable, Exit) — closed blocks travel; announces across
│   └── LockableExit   (Lockable, OpenableExit)
├── Object  (get/drop/use + take_ok/drop_ok/use_*_ok + *_effect hooks)
│   ├── Container            (Containable, Object)  — put/take-from/look-in
│   ├── OpenableContainer    (Openable, Container)
│   ├── PositionableObject   (Positionable, Object) — take_ok = False
│   ├── WearableObject       (Wearable, Containable, Object)
│   ├── SwitchableObject     (Switchable, Object)
│   ├── HeldObject           (Holdable, Object)     — wield/unwield
│   └── EdibleThing          (Object)               — eat, destroy
(composed at runtime, no class in the codebase:
     PositionableVehicleObject — a bike you sit on
     EnterableVehicleObject    — a car you sit in)
└── Player  (connection sid, username, password_hash, last_location, home,
    │        say/emote/whisper(stub)/who/@quit/@desc/@home/@sethome/inv)
    └── Programmer  (eval with #refs, @dig, @create, @chparent, @rename,
                     @teleport, @dumpdb, @messages/@message,
                     @mixins/@addmixin/@rmmixin, @verbs/@verb/@rmverb)
```

Every class on the Exit/Object branches except `EdibleThing` is just a
base + mixins combination; they exist as hand-written classes only where they
carry extra Python (or, for the empty ones, as names old worlds already use).
Anything else is composed at runtime — see §6.2.1.

Mixins (`mixins.py`) provide the state machinery: `StateToggle.toggle_on/off`
drives `Openable` (is_open + paired object) / `Lockable` (is_locked,
locked_by_object/player) / `Switchable` (is_on).

**Mixins come first in the bases, and chain cooperatively.** Every composite
above is declared mixins-first (`class OpenableExit(Openable, Exit)`), so:

```
LockableExit → Lockable → OpenableExit → Openable → StateToggle → Exit → BaseThing
```

`BaseThing` is *last*, which is what makes ordinary `super()` work. Each mixin
implements `__init__` / `to_dict` / `look_at` / `state_summary` as
`super()` first, then its own contribution; `BaseThing` terminates the chain.
That removed three hacks that the old mixins-*last* diamond forced:

- the explicit `Openable.__init__(self, ...)` call in every composite,
- the hardcoded mixin tuple with unbound calls in `BaseThing.to_dict`,
- the same tuple again in `BaseThing.state_summary`.

Dispatch order still matters, and still works the same way: the dispatcher
walks each verb's entries deepest-first, so `Lockable.open`'s lock check beats
`Openable.open`, and `OpenableExit.open`'s far-side announcement sits between
them.

**Two traps if you add a mixin.** (1) Its `__init__` must default
`recursive=True`, matching `BaseThing`/`Place`/`Exit` — a mixin now *leads* the
MRO, so its signature is the one direct construction hits, and a `False`
default silently left an `OpenableExit` with an unresolved `destination`.
(2) Register it in `classfactory.MIXINS`, or objects cannot name it.

### 6.2.1 Composed classes (`classfactory.py`)

An object records `{"class": "Object", "mixins": ["Containable", "Openable"]}`.
Resolving that:

1. **Normalize** — validate names against the `MIXINS` vocabulary (anything
   else is rejected: `mixins` is database content, and an arbitrary importable
   name would be an arbitrary-base-class injection), drop any mixin another
   already derives from (`Lockable` implies `Openable`), sort.
2. **Name** — `CANON` when a hand-written class covers the combination, else
   `<Mixins alphabetically><Base>`. The existing names follow no algorithm
   (`OpenableExit` mixin-first, `PositionableObject` base-last, `Container`
   neither), so `CANON` is explicit — and doubles as the compatibility map
   that keeps old db records, tests and agent prompts resolving by name.
3. **Prefer code** — a class of that name in the base's module or
   `azimuth.entities` wins, keeping its overrides. One that covers only *part*
   of the mixin set gets the remainder mixed in on top rather than losing it.
4. **Otherwise `type()`**, mixins first.

The resolved class is stamped with `_az_base`/`_az_mixins` **in its own
`__dict__`**, and `BaseThing.to_dict` writes those back out — read with
`cls.__dict__.get`, never attribute access, or a hand-written subclass would
inherit its ancestor's identity and be persisted as it. `ClassFactory` is
**per-world** (stored verbs must not leak between worlds in one process) and
primes every `CANON` entry at construction, so classes built directly by
`setup_world` or the agent are stamped too.

`build_from_database()` resolves every combination the stored world uses, at
init. The point is not speed (one `type()` per combination): it turns a typo'd
mixin name into one error at boot instead of a mystery hours later. Lazy
resolution in `make_instance` remains the backstop for combinations invented
after startup. Backends that cannot enumerate (`DictStorage`, `MlStorage` —
neither has `iter_ids`) skip the eager pass and rely on it.

### 6.2.3 Vehicles

A vehicle is a room-bound thing you ride and can drive through exits. It is
**two mixins, no new entity class** — the first thing built entirely on the
composition machinery:

| | mixins | riding |
|---|---|---|
| bicycle | `Positionable` + `Vehicle` | you sit **on** it; riders keep their own location |
| car | `Enterable` + `Vehicle` | you sit **in** it; riders stand in the vehicle's own `Interior` |

`Vehicle` supplies `take_ok → False`, size, `drive`/`dismount`, and the travel
itself; which kind of riding is in play is read off the sibling mixin's own
state (`positioned` / `interior`), the same duck-typing `Containable.look_at`
uses to notice an `is_open`.

**The interior is the point of the car.** `Enterable` owns a `Interior`
(a `Place`), and a passenger standing in it does not move when the car does —
so `travel_to` has nothing to carry. A *bicycle's* rider really does change
location, and `move_to` drops any position a thing holds, so the seating is
taken down and put back up around the move (`tests/test_vehicles.py` pins
this: without it you arrive standing beside the bike).

**Which exits take which vehicle** is a size gate, so the rule reads the way
the world does. Every `Exit` has `max_vehicle_size`; a plain exit (a road, an
archway) is `VEHICLE_LARGE`, an `OpenableExit` is `VEHICLE_SMALL` — a doorway
you can wheel a bike through but not drive a car through. **A garage door is
that same class with `max_vehicle_size` set wide**, not a new class.
`VEHICLE_NONE` bars vehicles outright. A closed door still stops everything.

**Riding changes three lookups**, all for the same reason — from inside a car,
the car is in the street, not in the room you are standing in:

- `Player.can_see` — you can see the street, its contents and its exits
  (a car has windows);
- `Player.my_match_object` and the dispatcher's search order — your vehicle
  and what is outside it are addressable, with `okay_for_verb` still gating
  what you can *do* (you cannot pick up a kerbstone from the driving seat);
- `World.state_room` — the OOB room section reports the *vehicle's* ways out,
  so a driver's panel has something to click.

`Exit.use` drives when you are aboard, so `go north`, a bare `north`, and a
click on the exit in a client's panel all mean the same thing. Get off first
if you want to leave the vehicle behind.

### 6.2.2 Stored verbs

`{WORLD_ID}_classes` holds Python source keyed by canonical class name;
`attach_verbs` compiles it onto a **per-world subclass** of the resolved class
(never the shared class itself) whose own `default_commands` carries the verbs,
so `get_commands` merges them last and the dispatcher reaches them first.
Three things that are not obvious:

- **No zero-argument `super()`** — `exec`'d code has no `__class__` closure
  cell. The class is injected as `cls`; stored code writes `super(cls, self)`.
- **The signature is data, not a decorator** — `@make_command` inside stored
  source registers into the *global* registry under a bogus `(None, name)` key,
  and the next `register_commands()` dies with `AttributeError` on `NoneType`.
  Hence the `shapes` field.
- **A verb name does not cover a verb's argument shapes** — `Exit` registers
  `use` under two structures; a stored `use` declaring only one leaves `go
  through <door>` on the inherited handler. `attach_verbs` logs a warning
  naming exactly which shapes are still shadowed.

A compile failure is logged and the verb skipped, so one bad verb cannot make a
world unloadable; `@verb` also compiles before committing, so a syntax error is
a message to the programmer, not a broken world. **This is `exec` of database
content running in the server process for every player who triggers the verb.**
The namespace is restricted (`SAFE_BUILTINS`) but that is not a sandbox —
dunder traversal walks straight out of it. Writing is Programmer-tier and each
verb records its author and timestamp.

### 6.3 Persistence (`azimuth/persistence.py`)

The `Storage` base class declares only `load`/`save`/`get_object_by_name`/
`get_object_by_id`; `delete`/`get_all_objects`/`iter_ids`/`close` exist on the
backends that support them (deliberately not in the base — `MlStorage` still
lacks `get_all_objects`). All methods take/return **plain dicts**; `World`
rehydrates them via `make_instance` → `ClassFactory.resolve_from_data`, which
accepts both the current `{"class": base, "mixins": [...]}` form and a legacy
`{"class": "OpenableContainer"}` name (routed through the same path, so both
persist identically afterwards).

**Class filtering is `issubclass` now, not name equality.** A `Container` is
stored as `Object` + `Containable`, so `data["class"] == clss.__name__` could
not match it. The filter still runs *in* the backend (it has to apply before
the unique/ambiguous decision), but the decision is delegated:
`Storage.matches_class` calls a `class_matcher` that `World` installs, which
composes the class and asks `issubclass` — the same semantics
`World.get_object_by_name` already applied to its in-memory cache. The default
stays name equality so a backend used standalone (as the contract tests do,
with local dummy classes) still works. `SqliteStorage` therefore filters
fetched rows in Python rather than with `AND class = ?`.

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
  filename (`/` → `___`). Name search is a field-accurate, case-insensitive
  exact match on `name` or any alias, mirroring sqlite (it once shelled out to
  `grep` over whole files, which also matched descriptions and class names).
  Returns `None` when multiple files match — expect the "Multiple files found"
  print; a *missing* file is an ordinary outcome and is now `logger.debug`, not
  stdout noise, since optional world records like `{WORLD_ID}_classes` are
  absent in most worlds. Ambiguous id lookups return a **list of ids** (they
  used to return full file paths).
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

- ~~**`Lockable` message keys missing**~~ — **fixed**: the
  `toggle_locked_*` keys are in `Lockable.default_messages` now (`@addmixin
  Lockable to <thing>` made the blank replies impossible to ignore).
- **`unlock` shares the old pattern** — failure message then unconditional
  `toggle_off` (no `return`); only bites when `locked_by_player` is set, which
  nothing in the current world does. Mirror the `lock` fix if you wire up
  lock-keys.
- **The `open` name collision is a live hazard** — anywhere code does
  `if self.open` on an exit it gets a truthy *method*. Use `self.is_open`.
  (`OpenableExit.open`/`close` had exactly this bug — both announced to the far
  side unconditionally — and are fixed; `Lockable.lock`/`lock_with` were fixed
  earlier. Renaming the command methods to e.g. `do_open` would defuse it for
  good.)
- **`Positionable` is a stub** — `sit/stand/…` handlers just print `saw: …`.
- **`whisper` is an empty stub** on `Player` (registered, does nothing).
- **`run-agent.py` is stale** (see §6.5); `run-repl.py` has no main block.
- **spaCy/bagpipes_spacy are missing from `requirements.txt`** (only needed by
  `experiments/`); `prompt_toolkit`/`textual` for the clients *are* present.
- **`handle_register`** has `except: raise` with an unreachable `return` after it
  (harmless; tidy up if touching the file).
- **`Positionable`/`Switchable` `StateToggle` machinery**: `Switchable` has no
  commands yet (levers/buttons unimplemented).
- **`MlStorage` class filtering is partial** — it has no `iter_ids`/
  `get_all_objects`, so it skips the eager class build, and its server-side
  `clss` filter matches the stored *base* only; the mixin half is applied
  locally to whatever comes back. Indexing `mixins` would fix it properly if
  that backend comes back into use.
- **`--reload` watches `db/` and `.venv/`** — expect server restarts when those
  change; consider narrowing uvicorn's watch dirs in `run.py`.

## 9. Suggested next steps (roughly in order)

1. Tidy the remaining §8 door nits (`unlock`'s missing `return`, optionally
   rename the `open` command methods so `self.open` stops being a trap).
2. Narrow `run.py`'s reload watch dirs.
3. Rewrite-or-delete `run-agent.py`; give `run-repl.py` a real main loop.
4. Re-enable the MCP mount (§6.1) and add *write* actions (create/modify) —
   read tools are the two GET endpoints only.
5. Finish `Positionable`/`Switchable` (sit/stand, levers) and `whisper`.
6. Wire in `experiments/spacy_parser.py` to replace the preposition-split parser.
7. More robust persistence (Redis/Postgres) if file+SQLite+MarkLogic feel
   limiting.
