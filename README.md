# AZIMUTH

Azaroth's Intelligent Multi-User Textual Habitat

## Background

A LambdaMOO-inspired Python M* server, initially as an experiment in vibe coding / AI
assisted development, and to update my skills with the latest Python 3 features.

The design goals that fell out of that:

- **Data-driven objects** — every object is a plain JSON dict with a `class` field.
  Objects are rehydrated dynamically at load time (see `World.import_class`), so an
  object's class can even be *reassigned at runtime* via `@chparent`.
- **A MOO-style programmer tier** — a `Programmer` player gets `eval`, `@create`,
  `@dig`, `@message` and friends for building and editing the world from inside the MUD.
- **AI-native** — the world is exposed over MCP so LLM agents can inspect it, and an
  LLM-driven Room Builder agent can generate whole environments from a single sentence.

## Architecture

| Component | File | Role |
|---|---|---|
| Server | [azimuth/main.py](azimuth/main.py) | FastAPI + Socket.IO ASGI app, plus MCP |
| World engine | [azimuth/world.py](azimuth/world.py) | Object cache, lazy loading, login, command dispatch |
| Command system | [azimuth/command_decorator.py](azimuth/command_decorator.py) | `@make_command` registration and resolution |
| Object model | [azimuth/entities.py](azimuth/entities.py), [azimuth/mixins.py](azimuth/mixins.py) | `Place`/`Exit`/`Object`/`Player` + capability mixins |
| Persistence | [azimuth/persistence.py](azimuth/persistence.py) | JSON-file or MarkLogic backends |
| AI agents | [azimuth/agents/](azimuth/agents/) | LLM room builder (in-process) |
| Text client | [client.py](client.py) | Socket.IO + prompt_toolkit terminal client |
| Web client | [azimuth/templates/index.html](azimuth/templates/index.html) | Browser terminal at `/` |

**Server.** FastAPI wrapped in a `socketio.ASGIApp`, served with uvicorn. It exposes
three surfaces:

1. **Socket.IO** — real-time player I/O (`connect` sends the MOTD + login prompt;
   `command` dispatches to the world; `disconnect` persists the player's location).
2. **Web client** — `GET /` serves a terminal-style browser client.
3. **MCP** — `fastapi_mcp` is mounted, exposing `GET /data/{id}` (`get_record`) and
   `GET /search/{name}` (`search_record`) as MCP tools so AI agents can look up
   world objects by UUID or name. (This is why the server moved from Flask to FastAPI.)

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
`Holdable` (wield), `Wearable` — combined into `Container`, `Clothing`, `Furniture`,
`HeldObject`, `OpenableExit`, etc.

**Persistence.** `AZIMUTH_DB_TYPE` selects the backend:

- `file` (default) — one JSON file per object in `db/` (gitignored; world state is
  local). Name search shells out to `grep`.
- `marklogic` — documents in a MarkLogic database via its REST API (CTQ queries).

All active objects are dumped back to storage on server shutdown and on disconnect.

## Running

```bash
pip install -r requirements.txt
python run.py        # server on 0.0.0.0:5001 (uvicorn, with --reload)
```

Then connect with either client:

```bash
python client.py     # text client (prompt_toolkit; not in requirements.txt yet)
```

or open <http://localhost:5001/> for the browser terminal.

Log in as **wizard / wizard** to get a Programmer, or `register <username>
<password> <email>` for a regular player. (The wizard's hash is baked in when a new
world is created; an existing `db/` will use whatever was registered there.)

### Configuration (`.env`)

| Variable | Default | Purpose |
|---|---|---|
| `AZIMUTH_WORLD_ID` | `WORLD1` | World name/id |
| `AZIMUTH_DB_TYPE` | `file` | `file` or `marklogic` |
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

## AI Room Builder

[azimuth/agents/room_builder.py](azimuth/agents/room_builder.py) runs **in-process**
(it takes the live `World` and drives the `wizard` player) and talks to LM Studio's
OpenAI-compatible chat API in two phases:

1. **Plan** — the LLM is given the current room plus a list of all existing rooms
   and must emit strict JSON: 12–15 rooms on an x/y/z coordinate grid with exits
   (`dir`/`return`, `Exit` or `OpenableExit`), connected in a regular grid pattern.
   The plan is rendered as an ASCII map for review.
2. **Describe** — per room, the LLM writes an atmospheric description and up to 5
   objects (class-restricted: `Container`, `Clothing`, `HeldObject`, `Furniture`,
   `Item`).

The agent then instantiates and saves everything through the normal entity
constructors, so the result is indistinguishable from hand-built content.

Entry points: [run-repl.py](run-repl.py) wires up world + agent **but currently has
no main block** (stub). [run-agent.py](run-agent.py) is **stale** — leftover from the
earlier design where the agent was a remote Socket.IO client; it no longer matches
the in-process API and will not run. See *Known issues* below.

## Known issues

- `run-agent.py` references methods that no longer exist on `RoomBuilderAgent`
  (`connect_to_mud`, `login`, `send_command`, …) and imports from the wrong path.
- `run-repl.py` builds the agent but never invokes it (no `__main__` block).
- `World.handle_login` raises `KeyError` on unknown usernames instead of replying
  "Username and password do not match" — a bad login gets no response.
- `prompt_toolkit` (needed by the text client) and the spaCy/bagpipes deps for the
  parser experiment are missing from `requirements.txt`.
- `MlStorage` bakes its own web API path (`http://localhost:5001/data/`) into the
  MarkLogic document URIs.

## Ongoing work

- ~~Use uvicorn or other non-sucky server framework~~ — done (uvicorn + FastAPI)
- Finish the stub commands (whisper, positioning, `Positionable.look_at`, …)
- Wire the spaCy parser in for robust command interpretation
- MCP: read tools work; write actions (create/modify objects) still to come
- Clean up the agent scripts (`run-agent.py`, `run-repl.py`) around the in-process design
- More robust persistence — Redis, Postgres, or other real databases
- AI agents that play, not just build

## Testing

A serverless in-process test harness runs commands directly against a `World`
(no FastAPI, uvicorn, or Socket.IO needed). Each test gets an isolated copy of
`db/` in a temp dir, so tests never touch your real world:

```bash
python run-tests.py              # run all tests
python run-tests.py sword        # only tests whose name contains "sword"
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
