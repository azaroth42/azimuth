# AZIMUTH

Azaroth's Intelligent Multi-User Textual Habitat

## Background

A LambdaMOO-inspired Python M* server, initially as an experiment in vibe coding / AI
assisted development, and to update my skills with the latest Python 3 features.

The design goals that fell out of that:

- **Data-driven objects** — every object is a plain JSON dict recording an entity
  *base* and a set of *mixins*. The class for a combination is looked up or
  **composed at runtime** (see `azimuth/classfactory.py`), so an object's
  capabilities can be changed from inside the MUD with `@addmixin` / `@rmmixin`,
  and its class reassigned with `@chparent`.
- **Verbs in the database** — a combination that needs a dozen lines of Python
  doesn't need a class in the codebase: `@verb` stores the function source in the
  world and compiles it onto the class at init.
- **A MOO-style programmer tier** — a `Programmer` player gets `eval`, `@create`,
  `@dig`, `@message` and friends for building and editing the world from inside the MUD.
- **AI-native** — an LLM-driven Room Builder agent can generate whole environments
  from a single sentence; an MCP mount for agent inspection is present but
  currently disabled (see below).

**Deep-dive:** [ARCHITECTURE.md](ARCHITECTURE.md) documents the subsystems, the
current state, the known bugs, and how to set up on a new machine. Start there
before making non-trivial changes.

## Architecture

| Component | File | Role |
|---|---|---|
| Server | [azimuth/main.py](azimuth/main.py) | FastAPI + Socket.IO ASGI app (MCP mount currently disabled) |
| World engine | [azimuth/world.py](azimuth/world.py) | Object cache, lazy loading, login, command dispatch |
| Command system | [azimuth/command_decorator.py](azimuth/command_decorator.py) | `@make_command` registration and resolution |
| Object model | [azimuth/entities.py](azimuth/entities.py), [azimuth/mixins.py](azimuth/mixins.py) | `Place`/`Exit`/`Object`/`Player` + capability mixins |
| Class composition | [azimuth/classfactory.py](azimuth/classfactory.py) | base + mixins → class, at runtime; stored verbs |
| Persistence | [azimuth/persistence.py](azimuth/persistence.py) | file, SQLite, or MarkLogic backends |
| AI agents | [azimuth/agents/](azimuth/agents/) | LLM room builder (in-process) |
| Text client | [client.py](client.py) | Socket.IO + prompt_toolkit terminal client |
| TUI client | [tui_client.py](tui_client.py) | Textual terminal client (status bar, live room panel, completion) |
| Web client | [azimuth/templates/index.html](azimuth/templates/index.html) | Browser terminal at `/` |

**Server.** FastAPI wrapped in a `socketio.ASGIApp`, served with uvicorn. It exposes
three surfaces:

1. **Socket.IO** — real-time player I/O (`connect` sends the MOTD + login prompt;
   `command` dispatches to the world; `disconnect` persists the player's location).
2. **Web client** — `GET /` serves a terminal-style browser client.
3. **MCP — currently disabled** — the `FastApiMCP(...)` / `mcp.mount()` lines in
   `main.py` are commented out; re-enabling is two uncomments. (This is why the
   server moved from Flask to FastAPI.) `requirements.txt` pins `mcp<2` because
   fastapi-mcp 0.4.x is incompatible with the mcp 2.x `Server()` signature. The
   underlying REST endpoints (`GET /data/{id}`, `GET /search/{name}`) still work
   standalone.

**World.** `World` keeps an in-memory cache of active objects and lazily loads the
rest from the persistence layer. On first start it bootstraps a demo world (three
rooms, a wizard, a sword, a chest, a gem…), which is what you explore.

**Command system.** Handlers are declared with the `@make_command(verb, dobj, prep,
iobj)` decorator and registered per class (MRO-walked at startup). At runtime the
dispatcher matches the verb against a search order — the player, their room, what
they carry, what the room contains, its exits, then the world itself — and splits
arguments on prepositions (`put gong on long bong` → direct object `gong`, indirect
object `long bong`). Object names match by full name or prefix. A real NLP parser
(spaCy) exists in [experiments/spacy_parser.py](experiments/spacy_parser.py) but has
not been wired in yet.

**Object model.** `BaseThing` (uuid, location/contents graph, MRO-merged message
system with `{player}`/`{self}`/`{object}` formatting) → `Place` (exits, room
announcements) → `Exit` (movement, lazy destination) → `Object` (take/drop/use with
`*_ok`/`*_effect` hooks). Capability mixins implement the usual MUD semantics:
`Openable`/`Lockable` (state toggles), `Containable`, `Switchable`, `Positionable`,
`Holdable` (wield), `Wearable`.

**Composition, not a class per combination.** An object stores what it *is* —
`{"class": "Object", "mixins": ["Containable", "Openable"]}` — and
[classfactory.py](azimuth/classfactory.py) turns that into a class: a
hand-written one when the combination has one (`Container`, `OpenableExit`,
`PositionableObject`, … keep their overrides and their old names), otherwise
built with `type()`, mixins first so their cooperative `__init__` / `to_dict` /
`look_at` / `state_summary` chain ahead of `BaseThing`. Combinations in use are
resolved eagerly at init; anything invented later (`@addmixin`, the agent)
resolves lazily. A lockable container with pockets needs no new Python.

**Persistence.** `AZIMUTH_DB_TYPE` selects the backend:

- `file` (default) — one JSON file per object in `db/` (gitignored; world state is
  local). Name search shells out to `grep`.
- `sqlite` — one row per object in a single database file (`db/azimuth.db`,
  override with `AZIMUTH_SQLITE_PATH`), using stdlib `sqlite3` — no extra
  dependency. Name/class lookups are indexed SQL queries instead of `grep`.
- `marklogic` — documents in a MarkLogic database via its REST API (CTQ queries).

All active objects are dumped back to storage on server shutdown and on
disconnect.

An existing file world can be ported to SQLite with
[run-migrate-sqlite.py](run-migrate-sqlite.py) (it upserts by id and never
deletes the JSON files — re-run it any time after playing on the file backend;
switching the server over is just `AZIMUTH_DB_TYPE=sqlite` in `.env`).

## Running

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt prompt_toolkit
.venv/bin/python run-tests.py      # verify: 28/28 passed
.venv/bin/python run.py            # server on 0.0.0.0:5001 (uvicorn, --reload)
```

Then connect with either client:

```bash
.venv/bin/python client.py      # plain text client
.venv/bin/python tui_client.py  # TUI client (richer UI)
```

or open <http://localhost:5001/> for the browser terminal.

The TUI client takes an optional server URL argument
(`.venv/bin/python tui_client.py http://mud.example:5001`) or the
`AZIMUTH_SERVER_URL` environment variable. It shows a live status bar
(connection phase, server URL, player name), a side panel tracking the
current room / exits / things / inventory (parsed from `look` output), and
lightly styled output. In-client commands (``/help /clear /connect
/disconnect /server <url> /log [path] /quit``) and the keys Up/Down
(history), Tab (completion), F1 (help), Ctrl+Q (quit) work offline.

**Moving machines?** `db/` (world state), `.env`, and `.venv` are all gitignored —
copy `db/` and `.env` out of band, and recreate the venv on the new box. If `db/`
is absent, a fresh demo world is bootstrapped on first start.

Log in as **wizard / wizard** to get a Programmer, or `register <username>
<password> <email>` for a regular player. (The wizard's hash is baked in when a new
world is created; an existing `db/` will use whatever was registered there.)

### Configuration (`.env`)

| Variable | Default | Purpose |
|---|---|---|
| `AZIMUTH_WORLD_ID` | `WORLD1` | World name/id |
| `AZIMUTH_DB_TYPE` | `file` | `file`, `sqlite`, or `marklogic` |
| `AZIMUTH_SQLITE_PATH` | `db/azimuth.db` | SQLite database file |
| `AZIMUTH_ML_URL` | `http://localhost:8000` | MarkLogic REST endpoint |
| `AZIMUTH_ML_USER` | `admin` | MarkLogic user |
| `AZIMUTH_ML_PASSWORD` | — | MarkLogic password |
| `AZIMUTH_ML_DB` | `azimuth` | MarkLogic database name |
| `LM_STUDIO_URL` | `http://localhost:1234` | LLM endpoint for the agent |
| `LM_STUDIO_MODEL` | `openai/gpt-oss-20b` | Model to use |
| `LM_STUDIO_TEMPERATURE` | `0.7` | Sampling temperature |
| `LM_STUDIO_MAX_TOKENS` | `4000` | Max response tokens |
| `LM_STUDIO_TIMEOUT` | `3000` | Request timeout (s) |
| `AGENT_*` | see [agents/config.py](azimuth/agents/config.py) | Build delays, log file, etc. |

## Commands

Special prefixes (anywhere at the prompt):

| Input | Effect |
|---|---|
| `'text'` | Say *text* in the room |
| `:text` / `;text` | Emote *text* |
| `|code` | Programmer-only: `eval` *code* |
| `n`, `s`, `e`, `w`, `ne`, …, `up`, `down` | Move through the matching exit |

Players: `look`/`l` (also `look at <x>`), `inventory`/`inv`/`i`, `say`, `emote`,
`whisper` (stub), `wh` (list of online players), `@desc as <text>`, `@home`,
`@sethome`, `@quit`.

Objects: `take`/`get`/`pick`, `drop`, `use [on <x>]`, `put <x> in <container>`,
`take <x> from <container>`, `open`/`close`, `lock`/`unlock [with <key>]`,
`wield`/`unwield`, `wear`/`remove`.

Programmers (`wizard`): `eval`/`@eval`/`|` (`#name` in the code resolves to that
object), `@dig <exits> to <room|#id>` (comma-separated aliases, `|` for the reverse
exit), `@create <name> as <class>`, `@chparent <obj> to <class>`, `@rename <obj> to
<name>`, `@teleport <place|#id>`, `@desc <obj> as <text>`, `@messages <obj>` /
`@message <name> on <obj> as "<text>"` (runtime text editing), `@dumpdb`.

Composition and verbs: `@mixins [<obj>]` (what a thing is made of, and the
vocabulary), `@addmixin <obj> to <Mixin>`, `@rmmixin <obj> from <Mixin>`,
`@verbs [<Class|obj>]`, `@verb <Class|obj> <name> [<verbs/dobj/prep/iobj>]
<code>`, `@rmverb <Class|obj> <name>`. Stored verb source is written on one
line with `\n` for newlines, and calls `super(cls, self)` rather than a bare
`super()` (there is no closure cell for it). A stored verb is server-process
code: it is Programmer-tier, and it records its author.

## AI Room Builder

[azimuth/agents/room_builder.py](azimuth/agents/room_builder.py) runs **in-process**
(it takes the live `World` and drives the `wizard` player) and talks to LM Studio's
OpenAI-compatible chat API in two phases:

1. **Plan** — the LLM is given the current room plus a list of all existing rooms
   and must emit strict JSON: 12–15 rooms on an x/y/z coordinate grid with exits
   (`dir`/`return`, `Exit` or `OpenableExit`), connected in a regular grid pattern.
   The plan is rendered as an ASCII map for review.
2. **Describe** — per room, the LLM writes an atmospheric description and up to 5
   objects (class-restricted: `Container`, `WearableObject`, `HeldObject`, `PositionableObject`,
   `Item`).

The agent then instantiates and saves everything through the normal entity
constructors, so the result is indistinguishable from hand-built content.

Entry points: [run-repl.py](run-repl.py) wires up world + agent **but currently has
no main block** (stub). [run-agent.py](run-agent.py) is **stale** — leftover from the
earlier design where the agent was a remote Socket.IO client; it no longer matches
the in-process API and will not run. See *Known issues* below.

## Known issues

- Door-state nits remain after the verb-shadowing fix (blank `lock`/`unlock`
  replies, the `open` name collision) — see [ARCHITECTURE.md §8](ARCHITECTURE.md).
- `run-agent.py` references methods that no longer exist on `RoomBuilderAgent`
  (`connect_to_mud`, `login`, `send_command`, …) and imports from the wrong path.
- `run-repl.py` builds the agent but never invokes it (no `__main__` block).
- The spaCy/bagpipes deps for the parser experiment are missing from
  `requirements.txt` (`prompt_toolkit` and `textual` for the clients are present).
- `MlStorage` bakes its own web API path (`http://localhost:5001/data/`) into the
  MarkLogic document URIs.
- See [ARCHITECTURE.md §8](ARCHITECTURE.md) for the rest (stub commands, the
  grep-based name search, the reload watcher, …).

## Ongoing work

- ~~Use uvicorn or other non-sucky server framework~~ — done (uvicorn + FastAPI)
- ~~`handle_login` KeyError on unknown usernames~~ — done (`.get()`)
- ~~`get_commands` double-merging inherited `default_commands`~~ — done (`__dict__` guard + tests)
- ~~Verb-shadowing dispatch bug~~ — done (`reversed(...)` dispatch + `LockableExit.__init__` chain + `lock` command)
- ~~SQLite persistence backend~~ — done (`AZIMUTH_DB_TYPE=sqlite`; `run-migrate-sqlite.py` ports an existing file world)
- Finish the stub commands (whisper, positioning, `Positionable.look_at`, levers)
- Wire the spaCy parser in for robust command interpretation
- Re-enable the MCP mount; add write actions (create/modify objects)
- Clean up the agent scripts (`run-agent.py`, `run-repl.py`) around the in-process design
- More robust persistence — Redis, Postgres, or other real databases (SQLite is in, but a server-class DB would be the next step)
- AI agents that play, not just build

## Testing

A serverless in-process test harness runs commands directly against a `World`
(no FastAPI, uvicorn, or Socket.IO needed). Each test gets an isolated copy of
`db/` in a temp dir, so tests never touch your real world:

```bash
python run-tests.py              # run all tests (file backend)
python run-tests.py sword        # only tests whose name contains "sword"
python run-tests.py --db sqlite  # run the whole suite on the sqlite backend
python run-tests.py --keep-db    # keep the temp test databases for inspection
```

To add a test, create a class in `tests/` that subclasses `AzimuthTest` and
add `test_*` methods (see `tests/test_objects.py`). For ad-hoc poking around:

```python
from tests.framework import TestWorld
tw = TestWorld()
wiz = tw.login("wizard", "wizard")
print(wiz.send("get sword"))
print(wiz.inventory())
```

## Contributing

Contributions are welcome but not expected!
