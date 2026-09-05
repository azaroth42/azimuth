# Azimuth Out-of-Band (OOB) Protocol — Design

A structured state channel between server and client, parallel to the existing
`message` text stream. Version **1** — **implemented** (see §12 and the
`OOBTest` suite in `tests/test_oob.py`).

---

## 1. Why

Today the TUI client (`tui_client.py`) learns about the world by *regex-parsing
the human-readable `message` stream*: `ROOM_RE` / `SEEH_RE` / `EXITS_RE` /
`CARRY_RE` / `WHO_RE` feed the side panel, and a **hardcoded** `VERBS` tuple
plus harvested names feed Tab completion. This is fragile (any wording change
breaks the client), laggy (the client only learns about changes when it re-issues
`look`/`inv`/`@who`), and dumb (no notion of *which verbs apply to which object*).

The OOB channel fixes all three by pushing **structured state**:

| Client need | Today | With OOB |
|---|---|---|
| `@who` list (connected players) | retype `@who`, regex rows | live `players` section |
| Inventory | retype `inv`, regex the line | live `inventory` section |
| Room / exits / visible things | retype `look`, regex 3 line formats | live `room` section |
| Verb completion (first word) | hardcoded `VERBS` tuple | server-issued `self.verbs` |
| Object completion | harvested from room/inventory text | `room.things` + `inventory` (incl. aliases) |
| Per-object verb menu (dropdown) | impossible | `verbs` summary on every thing |

## 2. What "out of band" means here

**One connection, two channels.** The existing Socket.IO connection already
carries `command` (client→server) and `message` (server→client). We add two
*named events* on the same connection:

- `state` (server→client) — structured world state (this document)
- `data`  (client→server) — small client operations (`hello`, `resync`)

No second connection, no HTTP polling: session identity (sid) is already
established, and both Socket.IO transports (websocket and polling) are
**reliable and in-order per connection**, which the sequencing rules in §8
rely on.

**In-band stays in-band.** The `message` stream is narrative (descriptions,
flavor, say/emote, command results). The OOB channel is *state only* — it never
replaces or duplicates prose; it just keeps the client's model of the world
fresh.

## 3. Design principles

1. **Sections, not operations.** An update carries *complete* copies of the
   sections that changed (whole room, whole inventory, whole player list).
   MUDs are small (a room is dozens of things at most); a full-section
   replacement is trivially correct, immune to ordering/replay bugs, and
   cheap. Per-thing add/remove ops are a future optimization (§11), not a
   requirement.
2. **Emit on flush, never mid-command.** `process_player_command` runs
   synchronously inside one async handler tick. Changes made during a command
   are *marked dirty*; one coalesced `state` event is emitted when the command
   finishes. Ten objects moving during one command = one event.
3. **Conservative dirty-marking, precise payloads.** Marking over-approximates
   *who* is affected; at flush time we recompute sections and **diff against
   what we last sent** — only sections that actually changed go on the wire.
4. **Never send what the player can't see or do.** The channel reuses the
   *exact* gates the dispatcher already uses: `can_see()` for presence,
   `okay_for_verb()` for capability (§6).
5. **Backwards compatible.** Old clients ignore an unknown event name. New
   clients detect an old server (no `state` after login) and fall back to the
   current text-parsing pipeline (§7.3, §9).

## 4. Wire protocol

### 4.1 Envelope (server→client, event `state`)

```json
{
  "v": 1,
  "kind": "init" | "update",
  "seq": 42,
  "self": { ... },        // present in init (and on identity changes)
  "room": { ... },        // present when the player's room changed
  "inventory": [ ... ],   // present when the player's carried things changed
  "players": [ ... ]      // present when the connected-player list changed
}
```

- `v` — protocol version (1). Clients ignore unknown keys; breaking changes bump `v`.
- `kind` — `init` = full snapshot (all sections present; sent on login and on
  `resync`); `update` = only the sections that changed (any may be absent).
- `seq` — per-connection, monotonically increasing, starts at 1, increments by
  1 per `state` event (init or update). Used for gap detection (§8).
- Section **order is irrelevant**; clients apply by key.

### 4.2 Client operations (client→server, event `data`)

```json
{"op": "hello",  "v": 1}                 // on (re)connect, before login
{"op": "resync"}                         // client suspects desync (seq gap)
```

- `hello` — tags the sid as OOB-capable. The server keeps `world.oob_sids: set`
  and sends `state` **only** to sids that hello'd (old clients are untouched,
  and pre-login sids get no state at all until they log in).
- `resync` — server replies with a fresh `kind:"init"` snapshot. Cheap; no
  special error if the client spams it (rate-limit: ignore if one came within
  the last second).

### 4.3 Section schemas

**`self`** — the player's own identity and capability table:

```json
{
  "id": "3f2c…", "name": "wizard", "username": "wizard",
  "verbs": [
    {"verb": ["look", "l"],  "dobj": "self",  "prep": null,        "iobj": null},
    {"verb": ["i","inv","inventory"], "dobj": null, "prep": null,  "iobj": null},
    {"verb": ["say"],          "dobj": "any",  "prep": null,        "iobj": null},
    {"verb": ["emote"],        "dobj": "any",  "prep": null,        "iobj": null},
    {"verb": ["wh","whisper"],"dobj": "any",  "prep": ["to"],       "iobj": "Player"},
    {"verb": ["@who"],         "dobj": null,  "prep": null,         "iobj": null}
  ]
}
```

**`room`** — the player's current place:

```json
{
  "id": "8a1d…",
  "name": "The Starting Chamber",
  "exits": [
    {"id": "…", "name": "north", "aliases": ["n"], "cls": "Exit",
     "state": null,
     "verbs": [
       {"verb": ["go","walk"], "dobj": "self", "prep": null, "iobj": null}
     ]}
  ],
  "things": [
    {"id": "…", "name": "rusty sword", "aliases": [], "cls": "HeldObject",
     "state": null,
     "verbs": [
       {"verb": ["look","l"],           "dobj": "self", "prep": null,         "iobj": null},
       {"verb": ["get","take","pick"],  "dobj": "self", "prep": null,         "iobj": null},
       {"verb": ["use"],                "dobj": "self", "prep": null,         "iobj": null},
       {"verb": ["wield","hold"],       "dobj": "self", "prep": null,         "iobj": null},
       {"verb": ["drop"],               "dobj": "self", "prep": null,         "iobj": null}
     ]},
    {"id": "…", "name": "wizard", "aliases": [], "cls": "Player", "state": null,
     "verbs": [
       {"verb": ["look","l"], "dobj": "self", "prep": null, "iobj": null}
     ]}
  ]
}
```

- `things` includes **other players** (the same set the in-band `look` lists:
  `item != who and who.can_see(item)`). Their `cls` is `"Player"`, which the
  client uses to build in-room whisper/say targets.
- `exits` are the room's exits, with the same thing-summary shape (an Openable
  Exit gets `"state": "closed"` etc.).

**`inventory`** — a list of thing-summaries (everything in `player.contents`:
carried, wielded, worn).

**`players`** — the live `@who`:

```json
[
  {"id": "…", "name": "wizard", "loc": "The Starting Chamber", "seen": 1727750123, "self": true}
]
```

- One entry per logged-in player (`world.active_sids`). `loc` is the room
  **name** (or `null` if the player is in no room); `seen` is
  `last_active_time` as epoch seconds — the client renders "N seconds ago"
  *locally*, so entries don't need re-pushing as time passes. `self` marks the
  requesting player (for styling / "you" suffix).
- Pushed only on membership or location change; `seen` is refreshed in-band
  with those pushes.

**Thing summary** (shared by `room.things`, `room.exits`, `inventory`):

| field | meaning |
|---|---|
| `id` | object uuid (stable identity across updates) |
| `name` | primary name |
| `aliases` | alias list (empty if none) — lets the client complete prefixes against *all* names |
| `cls` | Python class name (`"HeldObject"`, `"Container"`, `"Player"`, …) — display + behavior hints |
| `state` | `null` or a list of short state strings, e.g. `["closed"]`, `["locked","closed"]`, `["held"]`, `["worn"]` — see `state_summary()` below |
| `verbs` | the object's **effective command table for this player** (§5.3) |
| `contents` | *optional*: list of thing-summaries of what is inside — present only when the player may see in (§6.2). Enables `get <gem> from <chest>` completion |

Deliberately **absent**: `description` (narrative; stays in-band), any
credentials, `location` id (the section membership *is* the location), and
`messages`/`functions`/`properties` (programmer-tier; never leak).

## 5. Server side

### 5.1 Capability gate (`azimuth/main.py`)

```python
@sio.event
async def connect(sid, environ):
    ...  # unchanged: MOTD + login prompt

@sio.event
async def data(sid, body):
    if body.get("op") == "hello" and body.get("v") == 1:
        world.oob_sids.add(sid)
    elif body.get("op") == "resync":
        player_id = world.active_sids.get(sid)
        if player_id:
            world.push_init(world.active_objects[player_id])
```

`world.oob_sids` is pruned on `disconnect` and on `@quit`.

### 5.2 Section builders (`azimuth/world.py`)

```python
def state_self(self, p):            # {id,name,username,verbs: p.verbs_summary(p)}
def state_room(self, p):            # {id,name,exits:[exit.thing_summary(p)...],
                                    #  things:[x.thing_summary(p) for x in p.location.contents
                                    #          if x is not p and p.can_see(x)]}
def state_inventory(self, p):       # [x.thing_summary(p) for x in p.contents if p.can_see(x)]
def state_players(self, viewer):    # [{id,name,loc,seen,self} for every active player]
```

`thing_summary(who)` on `BaseThing`:

```python
def thing_summary(self, who):
    return {
        "id": self.id, "name": self.name, "aliases": self.aliases,
        "cls": self.__class__.__name__,
        "state": self.state_summary(),
        "verbs": self.verbs_summary(who),
    }

def state_summary(self):
    """None by default; Openable/Lockable/Switchable/Holdable/Wearable
    contribute short strings (incl. "held"/"worn")."""
    return None

def verbs_summary(self, who, include_argless=False):
    """The object's merged command table, restricted to what *who* may do now.
    Serializes the @make_command info dicts, minus the unserializable 'func'.
    Duplicate shapes collapse to one entry; with include_argless=False
    (things) only entries carrying a 'self' slot are listed — argless or
    'dobj any'-style entries can't be aimed at a remote object (a bare verb
    always resolves to the speaker first)."""
    ...
```

Notes:

- `get_commands()` is already cached per instance (`commands_cached`), so this
  is a few dict copies; `okay_for_verb` is a handful of comparisons. Building
  a full snapshot is well under a millisecond for MUD-scale rooms.
- **The gate is the same one dispatch uses.** If `okay_for_verb("take", p)` is
  False, `take` is not listed — so the summary can never advertise a verb the
  dispatcher would reject, and a hidden object is both unlisted and
  unadvertised. This is the core anti-leakage property (§6).
- `okay_for_verb(verb, player)` currently takes one verb string; entries carry
  an alias list, hence `any(...)`.
- **State-dependent soft failures stay listed.** `okay_for_verb` is a
  location/visibility gate, not a state gate: a *locked* door still lists
  `open` (dispatch accepts the verb; the handler then replies "must unlock
  first" — normal MUD feedback). The `state` strings let the client
  de-prioritize or annotate such rows in the dropdown. Verbs the gate
  *rejects* (e.g. `get` on a thing in another room, `drop` on an uncarried
  thing) are **removed** — that is what makes the list live. (Pinned by
  `OOBTest.test_verb_summary_gating`.)
- `StateToggle` gets `state_summary()` support: `Openable` contributes
  `"open"`/`"closed"`, `Lockable` adds `"locked"`/`"unlocked"`, `Switchable`
  `"on"`/`"off"`. The carry-state mixins are consulted too: `Holdable` adds
  `"held"` (when `held_by` is set) and `Wearable` adds `"worn"` (when
  `worn_by` is set) — this is what lets a client annotate a carried item with
  `(held)` / `(worn)` (the TUI does so in its CARRYING panel). (Requires the
  known-safe `self.is_open` accessor discipline already noted in
  ARCHITECTURE.md §8.)

### 5.3 Example: the chest

`sturdy chest` (Container = Object + Containable) for a player standing in the
room shows, from the mixins' real command table:

```json
"verbs": [
  {"verb": ["look","l"],              "dobj": "self",   "prep": null,     "iobj": null},
  {"verb": ["get","take","pick"],     "dobj": "self",   "prep": null,     "iobj": null},
  {"verb": ["drop"],                  "dobj": "self",   "prep": null,     "iobj": null},
  {"verb": ["use"],                   "dobj": "self",   "prep": null,     "iobj": null},
  {"verb": ["put"],                   "dobj": "Object", "prep": ["in"],   "iobj": "self"},
  {"verb": ["take","get","remove"],   "dobj": "Object", "prep": ["from"], "iobj": "self"},
  {"verb": ["look","l"],              "dobj": "any",    "prep": ["in"],   "iobj": "self"},
  {"verb": ["open"],                  "dobj": "self",   "prep": null,     "iobj": null},
  {"verb": ["close"],                 "dobj": "self",   "prep": null,     "iobj": null}
]
```

This is exactly the shape the client needs for both completion (§7.4) and the
dropdown (§7.5): each entry says *which slots exist* and *what kind of thing
goes in each slot* (`"self"` = this object, `"Object"` = any object pool,
`"Player"` = player pool, `"any"` = free text).

### 5.4 Dirty marking and flush

`World` gains:

```python
self.state_dirty = {}      # player_id -> {"room","inventory","players"}  (set)
self.state_last  = {}      # player_id -> last sections dict actually sent
```

**Marking** (superset is fine — the diff makes it exact):

| Mutation | Mark |
|---|---|
| `login` | send `init` to the new player (no dirty) |
| `BaseThing.move_to` (the single funnel for take/drop/put/wear/travel) | players in *old* location → `room`; players in *new* location → `room`; if the mover is a `Player` → `players` for **all**; if the *new* location is a Player (i.e. someone took it) → that player's `inventory` |
| `StateToggle.toggle_on/off` (open/close/lock/unlock) | `room` for every player who `can_see` the object (and `inventory` if the object is carried by a player) — a closed→open flip can also change `contents` visibility |
| another player `login` / `on_disconnect` | `players` for all logged-in players |
| `@rename` / `@create` / `@dig` / `@chparent` | same rules as `move_to` / room change for the affected room's occupants |

**Flushing** — one call at the end of each handler in `main.py`:

```python
# in the `command` sio event, after world.process_player_command(...):
await world.flush_state()

async def flush_state(self):
    for pid, sections in self.state_dirty.items():
        p = self.active_objects.get(pid)
        if not p or p.connection not in self.oob_sids:
            continue
        payload = {"v": 1, "kind": "update", "seq": next_seq(p.connection)}
        changed = False
        for name in sections:
            fresh = getattr(self, f"state_{name}")(p)
            if fresh != self.state_last.get(pid, {}).get(name):
                payload[name] = fresh
                changed = True
        if changed:
            self.state_last[pid] = {**self.state_last.get(pid, {}), **payload}
            await self.socketio.emit("state", payload, to=p.connection)
    self.state_dirty.clear()
```

`push_init(p)` is the same loop with all sections, `kind:"init"`, and resets
`state_last`. Because commands process synchronously, **flushing at the end of
the handler is perfect coalescing**: no mid-command bursts, no duplicate
events, and the flush also covers `on_disconnect` (mark-all + flush there).

## 6. Security invariants

1. **Whitelist fields, never "to_dict minus secrets".** `thing_summary` emits
   exactly `id/name/aliases/cls/state/verbs(/contents)`. Player `to_dict`
   carries `password_hash` — it must *never* be the basis of a summary. (The
   existing unauthenticated `GET /data/{id}` endpoint returning raw dicts —
   including player hashes — is a separate pre-existing hole; fix it, but it is
   out of scope for this channel, which by construction sends no such fields.)
2. **Presence = `can_see`.** Anything failing `player.can_see(x)` is omitted
   from `room`/`inventory`/`contents`. Hidden/invisible objects neither appear
   nor are hinted (their verbs are never computed).
3. **Capability = `okay_for_verb`.** Verbs are filtered with the dispatcher's
   own gate, per current state. A locked door lists `unlock` but not `open`;
   once unlocked, the next flush flips it.
4. **`players` is the `@who` scope** — exactly what `@who` already prints to
   anyone (name, place name, staleness). No ids of other players' *locations*
   (names only), no email, no location coordinates.
5. **`contents` mirrors in-band knowledge** (§6.2): only when the player can
   see the container *and* it is open (or not openable) — the same condition
   `Containable.look_at` uses for its "Inside there is: …" line. No deeper
   recursion: `contents` is one level; the client follows nesting via
   `look <x> in <y>` in-band output if it wants more.
6. **Pre-login: no state.** `state` is emitted only to `oob_sids` *and* only
   after that sid has a logged-in player.
7. **Programmer-tier data stays out.** `messages`/`functions`/`properties` and
   `@messages`-style tables are never on the channel.

## 7. Client side (`tui_client.py`)

### 7.1 `SocketSession`

- In `_make_client`, register:

  ```python
  @client.event
  def state(data) -> None:   # server → client
      session._publish(ServerEvent("state", json.dumps(data)))
  ```

  (`ServerEvent` gains kind `"state"`; the JSON string travels the existing
  `outbox` → `_pump` → app, matching how messages already cross the thread
  boundary.)
- On each successful transport `connect`, emit `data` `{"op":"hello","v":1}`.
- Expose `session.request_resync()` → emits `{"op":"resync"}` (debounced).

### 7.2 `WorldModel` (new small class in the client)

```python
class WorldModel:
    self_ = None; room = None; inventory = []; players = []
    def apply(self, payload):      # init: replace all; update: replace present keys
    def thing(self, id) / thing_by_name(self, prefix)   # across room+inventory+contents
    def object_pool(self)          # room things + carried + "me"/"here", deduped
    def player_pool(self)          # players names + "me"
    def verbs_for(self, thing)     # the thing summary's verb entries
```

The app holds one model; `_dispatch` routes `state` events into it (the payload
is JSON-decoded) and then calls `_refresh_panel()` — which is *already* a
method; it just reads the model instead of (see 7.3) the parsed-text fields.

### 7.3 Feature negotiation & fallback

- The app starts with `_text_harvest = True` and `_oob = False`: the existing
  regex pipeline runs out of the box (old-server behavior is unchanged).
- On the **first** `state` event (init or update) `_oob` flips True and
  `_text_harvest` flips False — the channel becomes the sole source of truth
  for panel/completion; the harvesting assignments in `_style_line` are gated
  on `_text_harvest` (styling still applies). No grace timer is needed:
  against a new server the init arrives within milliseconds of login, and a
  stray harvested line before that is instantly overwritten by the model.
- On any disconnect: the model is reset and both flags revert, so a
  reconnect re-logins and re-negotiates (a fresh `init` arrives, or the text
  fallback takes over against an old server).

### 7.4 Model-driven completion

- **First word (verb):** if `oob`, the verb pool is the *server's* verb list —
  the `self.verbs` entries (display alias = longest; all aliases accepted as
  prefixes) — replacing the hardcoded `VERBS` tuple. Otherwise today's tuple.
- **Object arguments:** for a verb whose entry has object-ish slots (`dobj`
  `"self"`/`"Object"`, `iobj` `"Object"`), the pool is
  `model.object_pool()` — which now includes **aliases** and, for prepositional
  forms, the *selected container's* `contents` (so `get <gem> from chest`
  completes `gem`). `iobj "Player"` → `model.player_pool()`.
- **`whisper`** → `model.player_pool()` (same as today's harvested `@who`
  names, but live and not dependent on having typed `@who`).

### 7.5 The verb dropdown

New key **F5** ("verbs for selected object") + mouse click on a name in the
side panel (which sets the *selected object*). Behavior:

1. Resolve the selection: last word of the input buffer, else the clicked/last
   selected thing; match against the model (name or alias, `thing_by_name`).
2. Open a Textual `ModalScreen` (a simple `OptionList`/`DataTable`) listing
   that thing's `verbs` entries, each rendered as the command it would form,
   with the *unfilled* slots shown as placeholders:

   ```
   ├ look                look <rusty sword>
   ├ take                take <rusty sword>
   ├ use                 use <rusty sword>
   ├ use on …            use <rusty sword> on <object>
   ├ open                open <sturdy chest>
   ├ put … in            put <object> in <sturdy chest>
   └ get … from          get <object> from <sturdy chest>
   ```

   (Entry → template rule: slot for `"self"` = the selected thing's name;
   `"Object"`/`"Player"` = a `<…>` placeholder; `"any"` = `<text>`.)
3. Enter on a row: the input buffer is replaced with that command, placeholders
   get a Tab-completion session pre-seeded (first placeholder focused), focus
   returns to the input. Esc closes.

This is the concrete payoff of the per-object `verbs` summary: **the client
offers exactly the verbs the server will accept, with the right argument
shapes.**

### 7.6 Side panel

Gains a **PLAYERS** section (live `@who`: `name — <room>  <N s ago>`, a 1 s
tick refreshes the relative times from `seen`). The ROOM/EXITS/IN ROOM/
CARRYING sections render from the model when `oob`, from parsed text
otherwise. Exit rows show their `state` (`closed`, `locked`) when non-null.

## 8. Ordering, loss, resync

Socket.IO (both transports) is reliable and in-order per connection, so no
acks are needed for correctness. Defensively:

- Client tracks `last_seq`; if an update arrives with `seq > last_seq + 1`
  (gap — e.g. an event dropped by a buggy transport layer or a server restart
  race) it emits `resync`.
- Server restart: the client's socket dies and reconnects; the `connect`
  handler re-sends `hello`; the player must re-login (existing behavior), which
  triggers a fresh `init`. The client clears the model on disconnect, so no
  stale state survives a restart.

## 9. Compatibility matrix

| Client | Server | Behavior |
|---|---|---|
| old | old | today's behavior (text parsing) |
| old | new | `state` events ignored (unknown event); `data` never sent; everything as today |
| new | old | no `state` ever arrives → text harvesting stays on → today's text-parsing behavior |
| new | new | full OOB: live panel, model completion, verb dropdown |

No client is ever *worse* off by the upgrade on either side — the in-band
`message` stream is untouched, so the game is fully playable through it.

## 10. What is deliberately NOT on the channel

- Descriptions, room flavor, and any prose (narrative stays in `message`).
- `@messages` / message tables, `functions`, `properties` (programmer tier).
- Per-character animation or sound cues (future; could ride the same envelope
  as a new section — the design anticipates additive sections).
- Anything about rooms/objects the player has never seen or may not see.

## 11. Future extensions (not part of v1)

- **Per-thing ops** (`thing.add/remove/update`) if a room ever holds
  thousands of things — sections are the coarse, correct baseline first.
- **`desc` on demand:** a `data` op `{"op":"detail","id":…}` → one
  `state`-shaped event with the thing's description, for hover/inspect panels
  — pushes narrative through the channel on request (still gated by `can_see`).
- **`GET /state`** REST endpoint returning the init shape for non-socket
  clients (needs the missing REST auth — §6.1).
- Higher protocol `v` as capabilities (sound, avatar state, coordinates for a
  map widget) are added as new *sections* behind the version field.

## 12. Implementation plan

| Phase | Work | Result |
|---|---|---|
| **1** | Server: `oob_sids`, `data` handler, section builders, `thing_summary`/`state_summary` (no `verbs` yet), dirty-marking hooks in `move_to`/`StateToggle`/login/disconnect, `flush_state` at end of `command` handler. Client: `state` handler, `hello`, `WorldModel`, panel from model, PLAYERS panel, fallback negotiation. | Live who / inventory / room; zero text parsing needed; old clients unaffected. |
| **2** | `verbs_summary` on summaries + `self.verbs`; model-driven verb & object completion (aliases, contents for prepositional forms); F5 verb dropdown modal. | Autocomplete and per-object verb menus come from the server. |
| **3** | **Done alongside 1–2:** `contents` summaries on open containers (`OOBTest.test_container_contents_gated_by_state`); `state` strings (open/closed, locked/unlocked, on/off) on every summary incl. exits; client resync trigger (`SocketSession.request_resync()`); the 11-test `OOBTest` suite (snapshot shape/scope, verb gating, coalescing, who-liveness, seq, anti-leakage). | Full feature set, pinned by tests. |

Status: **all three phases implemented.** Remaining nice-to-haves (not done):
client-side seq-gap detection (the client accepts whatever seq it gets; the
server-side `resync` op is its fallback), `desc`-on-demand, and sounds.
