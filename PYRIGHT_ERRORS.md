# Pyright Error Inventory — Azimuth code base

Generated 2026-08-30 with **pyright 1.1.413** (default `basic` type-checking mode), run with the project virtualenv interpreter (`.venv/bin/python`).

**20 files analyzed → 131 errors, 0 warnings.** The repo has no `pyrightconfig.json`, so the run used pyright defaults; adding one (pointing at `.venv`, `exclude: [.venv, db]`) would make these results reproducible in CI.

## 1. Summary by error type

| Error type (pyright rule) | What it means | Count | Fix complexity |
|---|---|---|---|
| `reportAttributeAccessIssue` | attribute access on unresolved/mistyped object | 68 | Medium for the 59 mixin-interface errors (one systemic fix, cluster M1); Simple for the socketio/config and isolated ones; 4 are **latent bugs** (B1) |
| `reportIncompatibleMethodOverride` | incompatible method override | 17 | Medium — entities.py override cluster + `Storage` base annotations (E2); check `make_command` keyword dispatch first |
| `reportOptionalMemberAccess` | member access on possible None | 11 | Simple — add `None` guards/asserts; the one in entities.py (`source.announce`) needs a real None-handling decision |
| `reportPossiblyUnboundVariable` | possibly unbound variable | 10 | Simple — initialize the variable before the branch that may skip its assignment |
| `reportUndefinedVariable` | undefined name | 7 | Medium — spacy_parser.py is half-pasted experiment code; refactor into a function or exclude the file (S1) |
| `reportMissingImports` | unresolvable import | 4 | Simple for venv deps; Medium for spacy/bagpipes_spacy (never in requirements) and the broken `agents.*` path in run-agent.py |
| `reportOptionalSubscript` | subscript on possible None | 5 | Simple — guard the `None` result of `_read_file` / `config` subscripting |
| `reportArgumentType` | argument type mismatch | 2 | Simple — annotate `config: AgentConfig | None` etc. in room_builder.py |
| `reportCallIssue` | bad call signature | 2 | Medium — clears with cluster M1 (unresolved `super()` chain in mixins) |
| `reportOperatorIssue` | operator type mismatch | 2 | Simple — one is the `requests` stub gap (T1), one a `float | None` comparison guard |
| `reportReturnType` | missing/incorrect return | 1 | Simple — add the missing return path in `build_environment` |
| `reportOptionalCall` | call on possible None | 1 | Medium — `world.socketio` is genuinely `None` until injected; guard + annotation (W1) |
| `general` | syntax/structure error | 1 | Medium — stray module-level `return` in the experiment file (S1) |

**Total: 131 errors** (43 Simple / 88 Medium). Complexity legend: **Simple** = isolated one-line/mechanical fix, no design decision. **Medium** = needs a design decision, touches several call sites, or risks runtime behavior (command dispatch, textual internals).

## 2. Root-cause clusters (recommended fix order)

Most errors trace back to a few systemic root causes; fixing a root cause clears a whole group. Each error is assigned to exactly one cluster.

| Cluster | Root cause | Errors cleared | Effort |
|---|---|---|---|
| M1 | Concrete mixins in `azimuth/mixins.py` (`StateToggle`, `Openable`, `Lockable`, `Containable`, `Positionable`, `Holdable`, `Wearable`) call `self.get_message()`, `self.name`, `self.contents`, `super().to_dict()`, … — members that only exist on `BaseThing`, which they do not inherit from. They work at runtime only because the composite classes in `entities.py` (e.g. `Container(Object, Containable)`) supply them. Declare the mixins against a `Protocol` / `typing.Self` bound to the `BaseThing` interface. | 59 | Medium — one pattern change across ~7 mixin classes, then mechanical; do this before touching individual lines. |
| E2 | Base-class methods in `entities.py` / `persistence.py` have no return annotations, so pyright infers literal types (e.g. `Literal[True]`, `Literal[""]`, `None`) and rejects overrides returning `bool`/`str`. Also parameter-name drift (`who` vs `player`, `docid` vs `what_id`). Add explicit annotations (`-> str`, `-> bool`, `-> dict | None`) and align parameter names. **Check `make_command` first — command dispatch may pass arguments as kwargs by name.** | 15 | Medium — one or two files, many mechanical edits; one real risk (keyword dispatch). |
| W1 | `World.socketio` and `World.config` are assigned `None` in `__init__` and inferred as type `None`; later code assigns, calls, and subscripts them. Annotate both attributes (`AsyncServer | None`, `dict | None`) and add guards at the call sites where `None` is actually reachable. | 7 | Simple–Medium — mostly annotations; a few call sites need a product decision (fail vs. skip). |
| S1 | `experiments/spacy_parser.py` is a half-finished experiment: `spacy`/`bagpipes_spacy` are not in `requirements.txt`, and the bottom half of the file is a command-handler body pasted at module level (`player`, `search_order` undefined; bare `return`). Finish it as a real function and add the deps, or exclude it from the typed tree. | 17 | Medium — requires deciding the file's fate. |
| B1 | Latent runtime bugs pyright surfaced: `Player.home` is both a method (entities.py:735) and an instance attribute (611/749) — the attribute shadows the method; `Clothing.contained_look_at` calls `Wearable.look_at`, which `Wearable` never defines; `DictStorage.load` calls `self.get(...)`, which does not exist (should be `self.data.get`); `tui_client._on_resize` shadows textual's `App._on_resize` with a different signature. | 6 | Medium — small edits, but each needs a code-path review. |
| T1 | Untyped third-party packages fall back to minimal typeshed stubs: `requests.auth` (persistence.py:142) and `socketio.exceptions` (client.py:94). Add `types-requests` / `types-socketio` to requirements (dev) or restructure the imports. | 2 | Simple |
| I1 | Everything else: isolated one-liners — `m` unbound in `import_class`, `agent` unbound in `finally` (run-agent.py), unbound `info` in the experiment, `float | None` comparison, `_cmd.focus()` on None, test-side None access, `config: AgentConfig = None` annotations, missing return path in `build_environment`, dead frame code in `command_decorator.get_my_info`, broken `agents.*` import path in run-agent.py. | 25 | Simple — batch in one PR. |

(The seven clusters partition all 131 errors.)

## 3. Complete error list (all 131)

### azimuth/agents/room_builder.py — 3 errors

| Line | Error type | Message | Fix estimate |
|---|---|---|---|
| 29 | argument type mismatch | Expression of type "None" cannot be assigned to parameter of type "AgentConfig"   "None" is not assignable to "AgentConfig" | **Simple** — isolated one-liner |
| 91 | argument type mismatch | Expression of type "None" cannot be assigned to parameter of type "str"   "None" is not assignable to "str" | **Simple** — isolated one-liner |
| 291 | missing/incorrect return | Function with declared return type "bool" must return value on all code paths   "None" is not assignable to "bool" | **Simple** — isolated one-liner |

### azimuth/command_decorator.py — 3 errors

| Line | Error type | Message | Fix estimate |
|---|---|---|---|
| 5 | member access on possible None | "f_back" is not a known attribute of "None" | **Simple** — isolated one-liner |
| 5 | member access on possible None | "f_code" is not a known attribute of "None" | **Simple** — isolated one-liner |
| 11 | attribute access on unresolved/mistyped object | Cannot access attribute "_commands" for class "None"   Attribute "_commands" is unknown | **Simple** — isolated one-liner |

### azimuth/entities.py — 20 errors

| Line | Error type | Message | Fix estimate |
|---|---|---|---|
| 369 | member access on possible None | "announce" is not a known attribute of "None" | **Medium** — isolated one-liner |
| 436 | incompatible method override | Base classes for class "LockableExit" define method "look_at" in incompatible way   Parameter 2 name mismatch: base parameter is named "player", override parameter is named "who" | **Medium** — cluster E2 — base annotations/param names |
| 474 | incompatible method override | Method "okay_for_verb" overrides class "BaseThing" in an incompatible manner   Return type mismatch: base method returns type "Literal[True]", override returns type "bool"     "bool" is not assignable to type "Literal[True]" | **Simple** — cluster E2 — base annotations/param names |
| 546 | incompatible method override | Method "look_at" overrides class "Containable" in an incompatible manner   Parameter 2 name mismatch: base parameter is named "player", override parameter is named "who" | **Medium** — cluster E2 — base annotations/param names |
| 564 | incompatible method override | Method "look_at" overrides class "Positionable" in an incompatible manner   Parameter 2 name mismatch: base parameter is named "player", override parameter is named "who"   Return type mismatch: base method returns type "Literal['']", override returns type "LiteralString"     "str" is not assignable to "Literal['']" | **Medium** — cluster E2 — base annotations/param names |
| 572 | incompatible method override | Method "take_ok" overrides class "Object" in an incompatible manner   Return type mismatch: base method returns type "Literal[True]", override returns type "Literal[False]"     "Literal[False]" is not assignable to type "Literal[True]" | **Simple** — cluster E2 — base annotations/param names |
| 583 | incompatible method override | Method "look_at" overrides class "Containable" in an incompatible manner   Parameter 2 name mismatch: base parameter is named "player", override parameter is named "who" | **Medium** — cluster E2 — base annotations/param names |
| 587 | attribute access on unresolved/mistyped object | Cannot access attribute "look_at" for class "type[Wearable]"   Attribute "look_at" is unknown | **Medium** — cluster B1 — **latent runtime bug** |
| 590 | incompatible method override | Method "contained_look_at" overrides class "BaseThing" in an incompatible manner   Return type mismatch: base method returns type "Literal['']", override returns type "str"     "str" is not assignable to type "Literal['']" | **Medium** — cluster E2 — base annotations/param names |
| 590 | incompatible method override | Method "contained_look_at" overrides class "Wearable" in an incompatible manner   Parameter 2 mismatch: base parameter has default argument value, override parameter does not | **Medium** — cluster E2 — base annotations/param names |
| 599 | incompatible method override | Method "contained_look_at" overrides class "BaseThing" in an incompatible manner   Return type mismatch: base method returns type "Literal['']", override returns type "str"     "str" is not assignable to type "Literal['']" | **Medium** — cluster E2 — base annotations/param names |
| 611 | attribute access on unresolved/mistyped object | Cannot assign to attribute "home" for class "Player*"   Type "None" is not assignable to type "(player: Unknown, target: Unknown ¦ None = None, prep: Unknown ¦ None = None, verb: Unknown ¦ None = None) -> None" | **Medium** — cluster B1 — **latent runtime bug** |
| 629 | attribute access on unresolved/mistyped object | Cannot access attribute "id" for class "MethodType"   Attribute "id" is unknown | **Medium** — cluster B1 — **latent runtime bug** |
| 670 | member access on possible None | "contents" is not a known attribute of "None" | **Simple** — isolated one-liner |
| 671 | member access on possible None | "exits" is not a known attribute of "None" | **Simple** — isolated one-liner |
| 749 | attribute access on unresolved/mistyped object | Cannot assign to attribute "home" for class "Player*"   Type "Unknown ¦ None" is not assignable to type "(player: Unknown, target: Unknown ¦ None = None, prep: Unknown ¦ None = None, verb: Unknown ¦ None = None) -> None"     Type "None" is not assignable to type "(player: Unknown, target: Unknown ¦ None = None, prep: Unknown ¦ None = None, verb: Unknown ¦ None = None) -> None" | **Medium** — cluster B1 — **latent runtime bug** |
| 824 | member access on possible None | "add_exit" is not a known attribute of "None" | **Simple** — isolated one-liner |
| 900 | member access on possible None | "messages" is not a known attribute of "None" | **Simple** — isolated one-liner |
| 901 | member access on possible None | "name" is not a known attribute of "None" | **Simple** — isolated one-liner |
| 943 | incompatible method override | Method "describe" overrides class "Player" in an incompatible manner   Parameter 3 name mismatch: base parameter is named "target", override parameter is named "what"   Parameter 4 name mismatch: base parameter is named "prep", override parameter is named "desc"   Parameter 5 name mismatch: base parameter is named "verb", override parameter is named "prep" | **Medium** — cluster E2 — base annotations/param names |

### azimuth/main.py — 1 errors

| Line | Error type | Message | Fix estimate |
|---|---|---|---|
| 45 | attribute access on unresolved/mistyped object | Cannot assign to attribute "socketio" for class "World"   Expression of type "AsyncServer" cannot be assigned to attribute "socketio" of class "World"     "AsyncServer" is not assignable to "None" | **Simple** — cluster W1 — socketio/config optional |

### azimuth/mixins.py — 59 errors

| Line | Error type | Message | Fix estimate |
|---|---|---|---|
| 17 | attribute access on unresolved/mistyped object | Cannot access attribute "get_message" for class "StateToggle*"   Attribute "get_message" is unknown | **Medium** — cluster M1 — mixin interface |
| 19 | attribute access on unresolved/mistyped object | Cannot access attribute "get_message" for class "StateToggle*"   Attribute "get_message" is unknown | **Medium** — cluster M1 — mixin interface |
| 22 | attribute access on unresolved/mistyped object | Cannot access attribute "get_message" for class "StateToggle*"   Attribute "get_message" is unknown | **Medium** — cluster M1 — mixin interface |
| 24 | attribute access on unresolved/mistyped object | Cannot access attribute "get_message" for class "StateToggle*"   Attribute "get_message" is unknown | **Medium** — cluster M1 — mixin interface |
| 31 | attribute access on unresolved/mistyped object | Cannot access attribute "get_message" for class "StateToggle*"   Attribute "get_message" is unknown | **Medium** — cluster M1 — mixin interface |
| 33 | attribute access on unresolved/mistyped object | Cannot access attribute "get_message" for class "StateToggle*"   Attribute "get_message" is unknown | **Medium** — cluster M1 — mixin interface |
| 36 | attribute access on unresolved/mistyped object | Cannot access attribute "get_message" for class "StateToggle*"   Attribute "get_message" is unknown | **Medium** — cluster M1 — mixin interface |
| 38 | attribute access on unresolved/mistyped object | Cannot access attribute "get_message" for class "StateToggle*"   Attribute "get_message" is unknown | **Medium** — cluster M1 — mixin interface |
| 88 | attribute access on unresolved/mistyped object | Cannot access attribute "get_message" for class "Openable*"   Attribute "get_message" is unknown | **Medium** — cluster M1 — mixin interface |
| 90 | attribute access on unresolved/mistyped object | Cannot access attribute "get_message" for class "Openable*"   Attribute "get_message" is unknown | **Medium** — cluster M1 — mixin interface |
| 94 | attribute access on unresolved/mistyped object | Cannot access attribute "to_dict" for class "object"   Attribute "to_dict" is unknown | **Medium** — cluster M1 — mixin interface |
| 136 | attribute access on unresolved/mistyped object | Cannot access attribute "get_message" for class "Lockable*"   Attribute "get_message" is unknown | **Medium** — cluster M1 — mixin interface |
| 143 | attribute access on unresolved/mistyped object | Cannot access attribute "get_message" for class "Lockable*"   Attribute "get_message" is unknown | **Medium** — cluster M1 — mixin interface |
| 145 | attribute access on unresolved/mistyped object | Cannot access attribute "get_message" for class "Lockable*"   Attribute "get_message" is unknown | **Medium** — cluster M1 — mixin interface |
| 152 | attribute access on unresolved/mistyped object | Cannot access attribute "get_message" for class "Lockable*"   Attribute "get_message" is unknown | **Medium** — cluster M1 — mixin interface |
| 158 | attribute access on unresolved/mistyped object | Cannot access attribute "get_message" for class "Lockable*"   Attribute "get_message" is unknown | **Medium** — cluster M1 — mixin interface |
| 160 | attribute access on unresolved/mistyped object | Cannot access attribute "get_message" for class "Lockable*"   Attribute "get_message" is unknown | **Medium** — cluster M1 — mixin interface |
| 162 | attribute access on unresolved/mistyped object | Cannot access attribute "get_message" for class "Lockable*"   Attribute "get_message" is unknown | **Medium** — cluster M1 — mixin interface |
| 166 | attribute access on unresolved/mistyped object | Cannot access attribute "get_message" for class "Lockable*"   Attribute "get_message" is unknown | **Medium** — cluster M1 — mixin interface |
| 173 | attribute access on unresolved/mistyped object | Cannot access attribute "get_message" for class "Lockable*"   Attribute "get_message" is unknown | **Medium** — cluster M1 — mixin interface |
| 175 | attribute access on unresolved/mistyped object | Cannot access attribute "get_message" for class "Lockable*"   Attribute "get_message" is unknown | **Medium** — cluster M1 — mixin interface |
| 179 | incompatible method override | Method "look_at" overrides class "Openable" in an incompatible manner   Parameter 2 name mismatch: base parameter is named "who", override parameter is named "player" | **Medium** — cluster M1 — mixin interface |
| 180 | attribute access on unresolved/mistyped object | Cannot access attribute "locked" for class "Lockable*"   Attribute "locked" is unknown | **Medium** — cluster M1 — mixin interface |
| 181 | attribute access on unresolved/mistyped object | Cannot access attribute "get_message" for class "Lockable*"   Attribute "get_message" is unknown | **Medium** — cluster M1 — mixin interface |
| 183 | attribute access on unresolved/mistyped object | Cannot access attribute "get_message" for class "Lockable*"   Attribute "get_message" is unknown | **Medium** — cluster M1 — mixin interface |
| 216 | attribute access on unresolved/mistyped object | Cannot access attribute "contents" for class "Containable*"   Attribute "contents" is unknown | **Medium** — cluster M1 — mixin interface |
| 229 | attribute access on unresolved/mistyped object | Cannot access attribute "get_message" for class "Containable*"   Attribute "get_message" is unknown | **Medium** — cluster M1 — mixin interface |
| 231 | attribute access on unresolved/mistyped object | Cannot access attribute "get_message" for class "Containable*"   Attribute "get_message" is unknown | **Medium** — cluster M1 — mixin interface |
| 239 | attribute access on unresolved/mistyped object | Cannot access attribute "name" for class "Containable*"   Attribute "name" is unknown | **Medium** — cluster M1 — mixin interface |
| 242 | attribute access on unresolved/mistyped object | Cannot access attribute "get_message" for class "Containable*"   Attribute "get_message" is unknown | **Medium** — cluster M1 — mixin interface |
| 244 | attribute access on unresolved/mistyped object | Cannot access attribute "get_message" for class "Containable*"   Attribute "get_message" is unknown | **Medium** — cluster M1 — mixin interface |
| 249 | attribute access on unresolved/mistyped object | Cannot access attribute "is_open" for class "Containable*"   Attribute "is_open" is unknown | **Medium** — cluster M1 — mixin interface |
| 253 | attribute access on unresolved/mistyped object | Cannot access attribute "get_message" for class "Containable*"   Attribute "get_message" is unknown | **Medium** — cluster M1 — mixin interface |
| 257 | attribute access on unresolved/mistyped object | Cannot access attribute "contents" for class "Containable*"   Attribute "contents" is unknown | **Medium** — cluster M1 — mixin interface |
| 258 | attribute access on unresolved/mistyped object | Cannot access attribute "is_open" for class "Containable*"   Attribute "is_open" is unknown | **Medium** — cluster M1 — mixin interface |
| 268 | bad call signature | Expected 0 positional arguments | **Medium** — cluster M1 — mixin interface |
| 277 | bad call signature | Expected 0 positional arguments | **Medium** — cluster M1 — mixin interface |
| 287 | attribute access on unresolved/mistyped object | Cannot access attribute "name" for class "Positionable*"   Attribute "name" is unknown | **Medium** — cluster M1 — mixin interface |
| 296 | attribute access on unresolved/mistyped object | Cannot access attribute "name" for class "Positionable*"   Attribute "name" is unknown | **Medium** — cluster M1 — mixin interface |
| 321 | attribute access on unresolved/mistyped object | Cannot access attribute "to_dict" for class "object"   Attribute "to_dict" is unknown | **Medium** — cluster M1 — mixin interface |
| 331 | attribute access on unresolved/mistyped object | Cannot access attribute "name" for class "Holdable*"   Attribute "name" is unknown | **Medium** — cluster M1 — mixin interface |
| 338 | attribute access on unresolved/mistyped object | Cannot access attribute "get_message" for class "Holdable*"   Attribute "get_message" is unknown | **Medium** — cluster M1 — mixin interface |
| 340 | attribute access on unresolved/mistyped object | Cannot access attribute "get_message" for class "Holdable*"   Attribute "get_message" is unknown | **Medium** — cluster M1 — mixin interface |
| 343 | attribute access on unresolved/mistyped object | Cannot access attribute "get_message" for class "Holdable*"   Attribute "get_message" is unknown | **Medium** — cluster M1 — mixin interface |
| 345 | attribute access on unresolved/mistyped object | Cannot access attribute "get_message" for class "Holdable*"   Attribute "get_message" is unknown | **Medium** — cluster M1 — mixin interface |
| 351 | attribute access on unresolved/mistyped object | Cannot access attribute "get_message" for class "Holdable*"   Attribute "get_message" is unknown | **Medium** — cluster M1 — mixin interface |
| 353 | attribute access on unresolved/mistyped object | Cannot access attribute "get_message" for class "Holdable*"   Attribute "get_message" is unknown | **Medium** — cluster M1 — mixin interface |
| 356 | attribute access on unresolved/mistyped object | Cannot access attribute "get_message" for class "Holdable*"   Attribute "get_message" is unknown | **Medium** — cluster M1 — mixin interface |
| 358 | attribute access on unresolved/mistyped object | Cannot access attribute "get_message" for class "Holdable*"   Attribute "get_message" is unknown | **Medium** — cluster M1 — mixin interface |
| 378 | attribute access on unresolved/mistyped object | Cannot access attribute "to_dict" for class "object"   Attribute "to_dict" is unknown | **Medium** — cluster M1 — mixin interface |
| 388 | attribute access on unresolved/mistyped object | Cannot access attribute "name" for class "Wearable*"   Attribute "name" is unknown | **Medium** — cluster M1 — mixin interface |
| 396 | attribute access on unresolved/mistyped object | Cannot access attribute "get_message" for class "Wearable*"   Attribute "get_message" is unknown | **Medium** — cluster M1 — mixin interface |
| 398 | attribute access on unresolved/mistyped object | Cannot access attribute "get_message" for class "Wearable*"   Attribute "get_message" is unknown | **Medium** — cluster M1 — mixin interface |
| 401 | attribute access on unresolved/mistyped object | Cannot access attribute "get_message" for class "Wearable*"   Attribute "get_message" is unknown | **Medium** — cluster M1 — mixin interface |
| 403 | attribute access on unresolved/mistyped object | Cannot access attribute "get_message" for class "Wearable*"   Attribute "get_message" is unknown | **Medium** — cluster M1 — mixin interface |
| 409 | attribute access on unresolved/mistyped object | Cannot access attribute "get_message" for class "Wearable*"   Attribute "get_message" is unknown | **Medium** — cluster M1 — mixin interface |
| 411 | attribute access on unresolved/mistyped object | Cannot access attribute "get_message" for class "Wearable*"   Attribute "get_message" is unknown | **Medium** — cluster M1 — mixin interface |
| 414 | attribute access on unresolved/mistyped object | Cannot access attribute "get_message" for class "Wearable*"   Attribute "get_message" is unknown | **Medium** — cluster M1 — mixin interface |
| 416 | attribute access on unresolved/mistyped object | Cannot access attribute "get_message" for class "Wearable*"   Attribute "get_message" is unknown | **Medium** — cluster M1 — mixin interface |

### azimuth/persistence.py — 10 errors

| Line | Error type | Message | Fix estimate |
|---|---|---|---|
| 27 | attribute access on unresolved/mistyped object | Cannot access attribute "get" for class "DictStorage*"   Attribute "get" is unknown | **Simple** — cluster B1 — **latent runtime bug** |
| 92 | incompatible method override | Method "get_object_by_id" overrides class "Storage" in an incompatible manner   Return type mismatch: base method returns type "None", override returns type "Any ¦ dict[str, str] ¦ list[str] ¦ None"     Type "Any ¦ dict[str, str] ¦ list[str] ¦ None" is not assignable to type "None"       "dict[str, str]" is not assignable to "None" | **Medium** — cluster E2 — base annotations/param names |
| 102 | subscript on possible None | Object of type "None" is not subscriptable | **Simple** — isolated one-liner |
| 111 | incompatible method override | Method "get_object_by_name" overrides class "Storage" in an incompatible manner   Return type mismatch: base method returns type "None", override returns type "Any ¦ dict[str, str] ¦ None"     Type "Any ¦ dict[str, str] ¦ None" is not assignable to type "None"       "dict[str, str]" is not assignable to "None" | **Medium** — cluster E2 — base annotations/param names |
| 130 | operator type mismatch | Operator "in" not supported for types "Literal['class']" and "Any ¦ dict[str, str] ¦ None"   Operator "in" not supported for types "Literal['class']" and "None" | **Simple** — isolated one-liner |
| 130 | subscript on possible None | Object of type "None" is not subscriptable | **Simple** — isolated one-liner |
| 142 | attribute access on unresolved/mistyped object | "auth" is not a known attribute of module "requests" | **Simple** — cluster T1 — third-party stubs |
| 145 | incompatible method override | Method "load" overrides class "Storage" in an incompatible manner   Parameter 2 name mismatch: base parameter is named "what_id", override parameter is named "docid" | **Simple** — cluster E2 — base annotations/param names |
| 205 | incompatible method override | Method "get_object_by_id" overrides class "Storage" in an incompatible manner   Parameter 2 name mismatch: base parameter is named "id", override parameter is named "docid"   Return type mismatch: base method returns type "None", override returns type "Any ¦ list[Unknown] ¦ None"     Type "Any ¦ list[Unknown] ¦ None" is not assignable to type "None"       "list[Unknown]" is not assignable to "None" | **Medium** — cluster E2 — base annotations/param names |
| 214 | incompatible method override | Method "get_object_by_name" overrides class "Storage" in an incompatible manner   Return type mismatch: base method returns type "None", override returns type "Any ¦ list[Unknown] ¦ None"     Type "Any ¦ list[Unknown] ¦ None" is not assignable to type "None"       "list[Unknown]" is not assignable to "None" | **Medium** — cluster E2 — base annotations/param names |

### azimuth/world.py — 6 errors

| Line | Error type | Message | Fix estimate |
|---|---|---|---|
| 82 | possibly unbound variable | "m" is possibly unbound | **Simple** — isolated one-liner |
| 108 | call on possible None | Object of type "None" cannot be called | **Medium** — cluster W1 — socketio/config optional |
| 183 | attribute access on unresolved/mistyped object | Cannot access attribute "emit" for class "None"   Attribute "emit" is unknown | **Simple** — cluster W1 — socketio/config optional |
| 192 | attribute access on unresolved/mistyped object | Cannot access attribute "disconnect" for class "None"   Attribute "disconnect" is unknown | **Simple** — cluster W1 — socketio/config optional |
| 227 | subscript on possible None | Object of type "None" is not subscriptable | **Simple** — cluster W1 — socketio/config optional |
| 282 | subscript on possible None | Object of type "None" is not subscriptable | **Simple** — cluster W1 — socketio/config optional |

### client.py — 1 errors

| Line | Error type | Message | Fix estimate |
|---|---|---|---|
| 94 | attribute access on unresolved/mistyped object | "exceptions" is not a known attribute of module "socketio" | **Simple** — cluster T1 — third-party stubs |

### experiments/spacy_parser.py — 17 errors

| Line | Error type | Message | Fix estimate |
|---|---|---|---|
| 1 | unresolvable import | Import "spacy" could not be resolved | **Medium** — cluster S1 — experiment file |
| 2 | unresolvable import | Import "bagpipes_spacy" could not be resolved | **Medium** — cluster S1 — experiment file |
| 68 | undefined name | "player" is not defined | **Medium** — cluster S1 — experiment file |
| 69 | syntax/structure error | "return" can be used only within a function | **Medium** — cluster S1 — experiment file |
| 109 | possibly unbound variable | "info" is possibly unbound | **Simple** — cluster S1 — experiment file |
| 111 | undefined name | "search_order" is not defined | **Medium** — cluster S1 — experiment file |
| 116 | possibly unbound variable | "info" is possibly unbound | **Simple** — cluster S1 — experiment file |
| 116 | possibly unbound variable | "info" is possibly unbound | **Simple** — cluster S1 — experiment file |
| 133 | possibly unbound variable | "info" is possibly unbound | **Simple** — cluster S1 — experiment file |
| 135 | possibly unbound variable | "info" is possibly unbound | **Simple** — cluster S1 — experiment file |
| 143 | possibly unbound variable | "info" is possibly unbound | **Simple** — cluster S1 — experiment file |
| 145 | possibly unbound variable | "info" is possibly unbound | **Simple** — cluster S1 — experiment file |
| 168 | undefined name | "player" is not defined | **Medium** — cluster S1 — experiment file |
| 170 | undefined name | "player" is not defined | **Medium** — cluster S1 — experiment file |
| 172 | undefined name | "player" is not defined | **Medium** — cluster S1 — experiment file |
| 177 | undefined name | "player" is not defined | **Medium** — cluster S1 — experiment file |
| 183 | undefined name | "player" is not defined | **Medium** — cluster S1 — experiment file |

### run-agent.py — 4 errors

| Line | Error type | Message | Fix estimate |
|---|---|---|---|
| 17 | unresolvable import | Import "agents.room_builder" could not be resolved | **Simple** — isolated one-liner |
| 18 | unresolvable import | Import "agents.config" could not be resolved | **Simple** — isolated one-liner |
| 82 | possibly unbound variable | "agent" is possibly unbound | **Simple** — isolated one-liner |
| 84 | possibly unbound variable | "agent" is possibly unbound | **Simple** — isolated one-liner |

### tests/framework.py — 2 errors

| Line | Error type | Message | Fix estimate |
|---|---|---|---|
| 144 | attribute access on unresolved/mistyped object | Cannot assign to attribute "socketio" for class "World"   Expression of type "FakeSocketIO" cannot be assigned to attribute "socketio" of class "World"     "FakeSocketIO" is not assignable to "None" | **Simple** — cluster W1 — socketio/config optional |
| 185 | subscript on possible None | Object of type "None" is not subscriptable | **Simple** — isolated one-liner |

### tests/test_objects.py — 2 errors

| Line | Error type | Message | Fix estimate |
|---|---|---|---|
| 89 | member access on possible None | "location" is not a known attribute of "None" | **Simple** — isolated one-liner |
| 89 | member access on possible None | "contents" is not a known attribute of "None" | **Simple** — isolated one-liner |

### tui_client.py — 3 errors

| Line | Error type | Message | Fix estimate |
|---|---|---|---|
| 195 | operator type mismatch | Operator ">=" not supported for types "float" and "float ¦ None"   Operator ">=" not supported for types "float" and "None" | **Simple** — isolated one-liner |
| 499 | incompatible method override | Method "_on_resize" overrides class "App" in an incompatible manner   Parameter 2 mismatch: base parameter "event" is keyword parameter, override parameter is position-only   Return type mismatch: base method returns type "CoroutineType[Any, Any, None]", override returns type "None"     "None" is not assignable to "CoroutineType[Any, Any, None]" | **Medium** — cluster B1 — **latent runtime bug** |
| 912 | member access on possible None | "focus" is not a known attribute of "None" | **Simple** — isolated one-liner |
