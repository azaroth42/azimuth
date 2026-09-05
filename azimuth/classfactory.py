"""Runtime composition of entity classes: base + mixins -> class.

Instead of hand-writing one class per combination of capabilities (the
combinatorial explosion -- 8 composites out of the dozens of possible mixin
sets), an object records the entity *base* it is and the *mixins* it has:

    {"class": "Object", "mixins": ["Containable", "Openable"]}

and the class is looked up (or built) once, at world init.  Resolving a
combination:

1. **Normalize** the mixin set -- validate the names, drop any mixin that
   another one in the set already derives from (``Lockable`` implies
   ``Openable``), and sort.  That yields a canonical key.
2. **Name** it -- ``CANON`` when a hand-written class already covers the
   combination, otherwise ``<Mixins...><Base>``.
3. **Prefer code** -- if a class of that name exists (in the base's own module,
   or in ``azimuth.entities``), use it.  That is how a combination needing
   extra Python keeps its overrides: ``OpenableExit``'s closed-door check,
   ``PositionableObject.take_ok``.  A hand-written class that covers only
   *part* of the mixin set gets the remainder mixed in on top of it rather
   than silently losing those capabilities.
4. **Otherwise build it** with ``type()``, **mixins first** so their
   cooperative ``__init__``/``to_dict``/``look_at``/``state_summary`` precede
   ``BaseThing`` in the MRO (see ARCHITECTURE.md 6.2).

The resolved class is stamped with ``_az_base``/``_az_mixins`` in its own
``__dict__``, which is what ``BaseThing.to_dict`` writes back out -- so the
stored form is always base + mixins, never a synthesized class name that no
module could import.  (Read with ``cls.__dict__.get``, never plain attribute
access: a hand-written subclass of a stamped class must not inherit its
ancestor's identity and get persisted as it.)
"""

import inspect
import logging

from . import entities
from .mixins import (
    Containable,
    Enterable,
    Holdable,
    Lockable,
    Openable,
    Positionable,
    Switchable,
    Vehicle,
    Wearable,
)

logger = logging.getLogger(__name__)

# The mixin vocabulary a stored object may name.  Anything outside this is
# rejected rather than imported: `mixins` comes from the database, and an
# arbitrary importable name would be an arbitrary-base-class injection.
MIXINS = {
    "Containable": Containable,
    "Enterable": Enterable,
    "Holdable": Holdable,
    "Lockable": Lockable,
    "Openable": Openable,
    "Positionable": Positionable,
    "Switchable": Switchable,
    "Vehicle": Vehicle,
    "Wearable": Wearable,
}

# Hand-written classes that *are* a base+mixins combination.  Their names
# follow no algorithm -- OpenableExit puts the mixin first, PositionableObject
# last, Container mentions neither, LockableExit names the derived mixin -- so
# the mapping has to be explicit.  It does double duty: it keeps every existing
# db record, test and agent prompt resolving by its old name, and it is how a
# combination that needs extra Python finds its hand-written class.
CANON = {
    ("Exit", ("Openable",)): "OpenableExit",
    ("Exit", ("Lockable",)): "LockableExit",
    ("Object", ("Containable",)): "Container",
    ("Object", ("Containable", "Openable")): "OpenableContainer",
    ("Object", ("Containable", "Wearable")): "WearableObject",
    ("Object", ("Holdable",)): "HeldObject",
    ("Object", ("Positionable",)): "PositionableObject",
    ("Object", ("Switchable",)): "SwitchableObject",
}

# name -> (base, mixins), so a legacy record that stores only "Container"
# resolves down the same path as an explicit base+mixins one (and so gets the
# same stamp, and persists in the same form).
LEGACY = {name: key for key, name in CANON.items()}


class UnknownMixin(Exception):
    """A stored object named a mixin that is not in the vocabulary."""


def normalize(mixin_names):
    """Canonical, de-duplicated, sorted mixin tuple.

    Drops any mixin that another named mixin already derives from, so
    ``["Openable", "Lockable"]`` and ``["Lockable"]`` are the same
    combination and cannot produce two different classes.
    """
    names = []
    for n in mixin_names or []:
        if n not in MIXINS:
            raise UnknownMixin(n)
        if n not in names:
            names.append(n)
    keep = []
    for n in names:
        cls = MIXINS[n]
        if any(n != o and issubclass(MIXINS[o], cls) for o in names):
            continue  # another named mixin is a subclass of this one
        keep.append(n)
    return tuple(sorted(keep))


def canonical_name(base_name, mixins):
    """The class name for a combination: the hand-written one when there is
    one, else ``<Mixins alphabetically><Base>`` (the style of the existing
    PositionableObject / WearableObject names)."""
    short = base_name.rsplit(".", 1)[-1]
    named = CANON.get((short, mixins))
    if named is not None:
        return named
    return "".join(mixins) + short


class ClassFactory:
    """Per-world registry of composed entity classes.

    Per *world*, not global, so that the stored verbs a world attaches to a
    combination (see ``attach_verbs``) cannot leak into another world -- which
    matters for the test suite, where many worlds exist in one process.
    """

    def __init__(self, world):
        self.world = world
        self.registry = {}  # canonical name -> class
        self.combos = {}    # canonical name -> (base_name, mixins)
        self._prime()

    def _prime(self):
        """Resolve every hand-written combination up front.

        Two reasons.  It stamps ``_az_base``/``_az_mixins`` on classes that
        code constructs *directly* (setup_world's bootstrap, the room-builder
        agent, the tests) rather than through make_instance, so those objects
        persist in the same base+mixins form as everything else.  And it is
        where this world's stored verbs get attached to them.
        """
        for (base, mixins) in CANON:
            try:
                self.resolve(base, mixins)
            except Exception as e:  # a broken CANON entry must not stop boot
                logger.error(f"cannot prime {base}+{list(mixins)}: {e}")

    # --- resolution ------------------------------------------------------

    def resolve(self, base_name, mixin_names):
        """The class for a base plus a set of mixins, building it if needed."""
        mixins = normalize(mixin_names)
        name = canonical_name(base_name, mixins)
        if name in self.registry:
            return self.registry[name]

        base = self.world.import_class(base_name)
        if base is None:
            raise ValueError(f"Unknown base class {base_name!r}")
        cls = self._find_static(base, name) if mixins else base
        if cls is None:
            cls = self._synthesize(base, mixins, name)
        else:
            cls = self._complete(cls, mixins, name)

        # Stored verbs last, so they sit deepest in the MRO and the
        # dispatcher (which tries each verb's entries deepest-first) reaches
        # them before anything they shadow.
        verbs = (self.world.class_verbs.get(name) or {}).get("verbs")
        if verbs:
            cls = self.attach_verbs(cls, name, verbs)

        self._stamp(cls, base_name.rsplit(".", 1)[-1], mixins)
        self.registry[name] = cls
        self.combos[name] = (base_name, mixins)
        return cls

    def resolve_from_data(self, data):
        """The class for a stored object dict.  Accepts both forms: an
        explicit ``{"class": base, "mixins": [...]}`` and a legacy
        ``{"class": "OpenableContainer"}``, which routes through the same
        path so both persist identically afterwards."""
        name = data["class"]
        mixins = data.get("mixins")
        if mixins:
            return self.resolve(name, mixins)
        (base, named) = self.split_name(name)
        if named:
            return self.resolve(base, named)
        return self.world.import_class(name)

    # --- building --------------------------------------------------------

    def _find_static(self, base, name):
        """A hand-written class of this name: the base's own module first (so
        a custom entity module can supply its own composites), then
        azimuth.entities."""
        for mod in (getattr(base, "__module__", None), "azimuth.entities"):
            if mod is None:
                continue
            m = entities if mod == "azimuth.entities" else __import__(
                mod, fromlist=["*"]
            )
            cls = getattr(m, name, None)
            if isinstance(cls, type) and issubclass(cls, base):
                return cls
        return None

    def _complete(self, cls, mixins, name):
        """A hand-written class that covers only part of the mixin set gets
        the rest mixed in on top, rather than silently dropping them."""
        missing = tuple(
            MIXINS[m] for m in mixins if not issubclass(cls, MIXINS[m])
        )
        if not missing:
            return cls
        logger.info(
            f"{name} does not cover {[m.__name__ for m in missing]}; "
            f"mixing them in on top of it"
        )
        return type(name, missing + (cls,), {})

    def _synthesize(self, base, mixins, name):
        bases = tuple(MIXINS[m] for m in mixins) + (base,)
        logger.debug(f"synthesizing {name} from {[b.__name__ for b in bases]}")
        return type(name, bases, {})

    @staticmethod
    def _stamp(cls, base_name, mixins):
        """Record the combination on the class itself; BaseThing.to_dict reads
        it back to persist base + mixins instead of a class name."""
        cls._az_base = base_name
        cls._az_mixins = mixins

    # --- stored verbs -----------------------------------------------------

    def attach_verbs(self, cls, name, verbs):
        """A per-world subclass of *cls* carrying this world's stored verbs.

        A *subclass*, never the class itself: a hand-written class is shared
        process-wide, so writing verbs onto it would leak them into every
        other world in the process -- including every other test.  The
        subclass declares only the stored verbs in its own
        ``default_commands``, which is exactly what BaseThing.get_commands
        merges last.
        """
        ns, default_commands, loaded = {}, {}, []
        for vname, spec in verbs.items():
            try:
                shapes = verb_shapes(spec)
                fn = compile_verb(cls, vname, spec)
            except Exception as e:
                # One bad verb must not make a world unloadable: log it and
                # leave the inherited behaviour in place.
                logger.error(f"stored verb {name}.{vname} not loaded: {e}")
                continue
            ns[vname] = fn
            declared = set()
            for info in shapes:
                info["func"] = fn
                declared.add(
                    (info["dobj"], tuple(info["prep"] or ()), info["iobj"])
                )
                for vb in info["verb"]:
                    default_commands.setdefault(vb, []).append(info)
            named = {v for s in shapes for v in s["verb"]}
            missing = _inherited_shapes(cls, named) - declared
            if missing:
                logger.warning(
                    f"stored verb {name}.{vname} declares "
                    f"{_fmt_shapes(declared)} but shadows {_fmt_shapes(missing)}"
                    f" -- those phrasings still reach the inherited handler"
                )
            loaded.append(vname)
        if not loaded:
            return cls
        ns["default_commands"] = default_commands
        logger.info(f"{name}: stored verbs {', '.join(sorted(loaded))}")
        return type(name, (cls,), ns)

    def stored_verbs(self, name):
        """This world's stored verbs for a canonical class name."""
        return (self.world.class_verbs.get(name) or {}).get("verbs") or {}

    # --- editing a combination from inside the MUD ------------------------

    def mixin_names(self):
        """The mixin vocabulary, for @mixins and error messages."""
        return sorted(MIXINS)

    def split_name(self, name):
        """A class name -> (base, mixins).

        Three forms resolve, so a composed class can be *named* anywhere a
        class name is accepted -- `@create ... as`, `@chparent`, an agent
        prompt, a hand-written db record:

        * a hand-written composite (``Container``) expands into the
          combination it stands for, so `@chparent thing to Container` and
          `@addmixin Containable to thing` land on the same class;
        * a generated name (``PositionableVehicleObject``) is parsed back by
          peeling known mixin names off the front -- it is the canonical
          identity of that combination, so it has to round-trip;
        * anything else is a plain base with no mixins.
        """
        short = name.rsplit(".", 1)[-1]
        legacy = LEGACY.get(short)
        if legacy is not None:
            return legacy
        rest, found = short, []
        while rest:
            # Longest first: no mixin name is a prefix of another today, but
            # relying on that silently would be a trap for the next one added.
            for m in sorted(MIXINS, key=len, reverse=True):
                if rest.startswith(m) and rest != m:
                    found.append(m)
                    rest = rest[len(m):]
                    break
            else:
                break
        if found and rest:
            return (rest, normalize(found))
        return (name, ())

    def combine(self, current, add=(), remove=()):
        """The normalized mixin tuple after adding/removing names.

        Raises UnknownMixin for a name outside the vocabulary and ValueError
        when asked to remove one that is not there -- including the case where
        it is only there by implication (removing Openable from a Lockable
        thing has to name Lockable instead)."""
        names = list(normalize(current))
        for n in add:
            if n not in MIXINS:
                raise UnknownMixin(n)
            if n not in names:
                names.append(n)
        for n in remove:
            if n not in MIXINS:
                raise UnknownMixin(n)
            if n in names:
                names.remove(n)
                continue
            implied = [o for o in names if issubclass(MIXINS[o], MIXINS[n])]
            if implied:
                raise ValueError(f"{n} comes with {implied[0]}; remove {implied[0]}")
            raise ValueError(f"not {n}")
        return normalize(names)

    # --- eager build at init ---------------------------------------------

    def build_from_database(self):
        """Resolve every combination the stored world actually uses, up front.

        Cheaper than it sounds (one type() per distinct combination) and the
        point is not speed: it turns a typo'd mixin name or a base that no
        longer exists into one error at boot, instead of a mystery failure
        hours later when a player finally walks into that room.  Lazy
        resolution in make_instance stays as the backstop -- @addmixin and the
        room-builder agent can invent a combination that was not in the
        database when the server started.

        Returns (built, problems).
        """
        iter_ids = getattr(self.world.db, "iter_ids", None)
        if iter_ids is None:
            # DictStorage / MlStorage cannot enumerate; lazy resolution covers it.
            logger.debug(
                f"{type(self.world.db).__name__} cannot enumerate ids; "
                f"skipping eager class build"
            )
            return ([], [])
        seen, problems = set(), []
        for oid in iter_ids():
            data = self.world.db.load(oid)
            if not data or "class" not in data:
                continue
            key = (data["class"], tuple(data.get("mixins") or []))
            if key in seen:
                continue
            seen.add(key)
            try:
                self.resolve_from_data(data)
            except Exception as e:
                problems.append((oid, key, e))
                logger.error(f"cannot build class for {oid} {key}: {e}")
        # Also resolve any combination that only exists because it has stored
        # verbs -- compiling them at boot is the point of the eager pass.
        for name in list(self.world.class_verbs):
            if name in self.registry:
                continue
            combo = LEGACY.get(name)
            if combo is None:
                logger.warning(
                    f"stored verbs for {name}: no live object uses that "
                    f"combination, and the name is not a known one"
                )
                continue
            try:
                self.resolve(*combo)
            except Exception as e:
                problems.append((None, combo, e))
                logger.error(f"cannot build {name} for its stored verbs: {e}")

        built = sorted(self.registry)
        # Only the *synthesized* ones are news; resolving to a hand-written
        # class is the uninteresting case and a world init is frequent (every
        # test builds one).
        made = sorted(n for n in built if n not in vars(entities))
        if made:
            logger.info(f"composed classes built at init: {', '.join(made)}")
        logger.debug(f"composed classes in use: {', '.join(built)}")
        return (built, problems)


# --- stored verbs ---------------------------------------------------------
# A combination that needs a dozen lines of Python does not have to have a
# hand-written class in the codebase: the function *source* can live in the
# database and be compiled onto the class at world init.  This is the
# LambdaMOO-style extensibility point (see OOB/ARCHITECTURE notes).
#
# Stored shape, under the world's `{WORLD_ID}_classes` record, keyed by
# canonical class name:
#
#   "OpenableExit": {"verbs": {"use": {
#        "code": "def use(self, player, prep=None, verb=None): ...",
#        "shapes": [{"verb": ["go", "walk"], "dobj": "self"},
#                   {"verb": ["go", "walk"], "prep": ["through"], "iobj": "self"}],
#        "author": "wizard", "created": "..."}}}
#
# Three rules that only became apparent by trying it:
#
# 1. **No zero-argument `super()`.**  Code from `exec` has no `__class__`
#    closure cell, so `super()` raises.  The class is injected into the
#    namespace as `cls`; stored code calls `super(cls, self)`.
# 2. **The signature is data, not a decorator.**  Using @make_command inside
#    stored source registers into the *global* command registry under a bogus
#    (None, name) key -- the next register_commands() then dies with
#    AttributeError on NoneType.  Hence `shapes`.
# 3. **A verb name does not cover a verb's argument *shapes*.**  `Exit`
#    registers `use` under two structures; a stored `use` declaring only the
#    first leaves `go through <door>` dispatching to the inherited handler.
#    attach_verbs warns when the declared shapes do not cover the shadowed
#    ones.
#
# On the trust boundary: this is `exec` of database content, running in the
# server process, for every player who triggers the verb.  The namespace below
# is a restriction, not a sandbox -- dunder traversal walks straight out of it.
# Writing verbs is Programmer-tier (the @verb command lives on Programmer) and
# each verb records its author; treat a stored verb as server code.

import builtins
import datetime

_SAFE_BUILTIN_NAMES = (
    "abs", "all", "any", "bool", "dict", "divmod", "enumerate", "filter",
    "float", "getattr", "hasattr", "int", "isinstance", "issubclass", "len",
    "list", "map", "max", "min", "print", "range", "repr", "reversed",
    "round", "set", "setattr", "sorted", "str", "sum", "super", "tuple",
    "zip", "AttributeError", "Exception", "KeyError", "TypeError",
    "ValueError",
)
SAFE_BUILTINS = {n: getattr(builtins, n) for n in _SAFE_BUILTIN_NAMES}


def verb_shapes(spec):
    """The (verb, dobj, prep, iobj) structures a stored verb declares.
    Accepts a `shapes` list or the single-shape shorthand of top-level
    verb/dobj/prep/iobj fields."""
    raw = spec.get("shapes")
    if raw is None:
        raw = [{k: spec.get(k) for k in ("verb", "dobj", "prep", "iobj")}]
    out = []
    for s in raw:
        verbs = s.get("verb")
        if isinstance(verbs, str):
            verbs = [verbs]
        prep = s.get("prep")
        if isinstance(prep, str):
            prep = [prep]
        if not verbs:
            raise ValueError("a verb shape must name at least one verb")
        out.append(
            {
                "verb": list(verbs),
                "dobj": s.get("dobj"),
                "prep": list(prep) if prep else None,
                "iobj": s.get("iobj"),
            }
        )
    return out


def compile_verb(cls, name, spec):
    """Compile stored source into a function bound to *cls* by `super(cls, ..)`.

    Raises on bad source or a body that does not define `name`; callers are
    expected to catch, so that one broken verb cannot make a world unloadable.
    """
    code = spec.get("code")
    if not code:
        raise ValueError("verb has no code")
    ns = {"cls": cls, "__builtins__": SAFE_BUILTINS}
    exec(compile(code, f"<verb {cls.__name__}.{name}>", "exec"), ns)
    fn = ns.get(name)
    if not callable(fn):
        raise ValueError(f"stored code does not define a function named {name!r}")
    fn.__qualname__ = f"{cls.__name__}.{name}"
    fn._az_stored = True
    return fn


def _fmt_shapes(shapes):
    """Readable, order-stable rendering of a set of (dobj, prep, iobj)
    structures (they contain Nones, so they are not directly sortable)."""
    return ", ".join(sorted(f"{d}/{'|'.join(p) or '-'}/{i}" for (d, p, i) in shapes))


def _inherited_shapes(cls, verbs):
    """(dobj, prep, iobj) structures the MRO already registers for *verbs* --
    what a stored verb of the same name is about to shadow."""
    out = set()
    for c in inspect.getmro(cls):
        for vb, infos in (c.__dict__.get("default_commands") or {}).items():
            if vb not in verbs:
                continue
            for i in infos:
                out.add((i.get("dobj"), tuple(i.get("prep") or ()), i.get("iobj")))
    return out


def now_stamp():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
