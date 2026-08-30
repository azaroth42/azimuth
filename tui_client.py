#!/usr/bin/env python3
"""Azimuth TUI client.

A full-featured terminal client for the Azimuth MUD server, built with
Textual.  Compared to the plain prompt_toolkit client it adds:

* a live status bar (connection phase, server URL, player name)
* a side panel tracking the current room, exits, visible things and your
  inventory (parsed from the server's ``look`` / ``inventory`` output)
* lightly styled output (room headers, exits, items, says, errors)
* command history (Up/Down) and Tab completion (verbs, in-world objects,
  players)
* offline client commands:  /help /clear /connect /disconnect
  /server <url> /log [path] /quit

Usage::

    python tui_client.py                        # http://localhost:5001
    python tui_client.py http://mud.example:5001
    AZIMUTH_SERVER_URL=http://mud.example:5001 python tui_client.py

Keys:  Up/Down history, Tab complete, F1 help, Ctrl+Q quit, Ctrl+X force quit.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import queue
import re
import threading
import textwrap
import time

import socketio
from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Input, Label, RichLog

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_URL = os.getenv("AZIMUTH_SERVER_URL", "http://localhost:5001")
CONNECT_TIMEOUT = 15          # seconds to wait for a connect attempt
RECONNECT_BASE = 2.0         # initial backoff between reconnect attempts
RECONNECT_MAX = 20.0         # maximum backoff
HISTORY_LIMIT = 200
OUTPUT_LINES = 4000
PANEL_WIDTH = 26             # inner width of side-panel text

# Control tokens on the command channel.  The NUL-family bytes never appear
# in real player input, so these are safe sentinels.
CMD_CONNECT = "\x02connect"
CMD_DISCONNECT = "\x02disconnect"
CMD_QUIT = "\x02quit"

# Verbs offered by Tab completion (mirrors the command set in the README).
VERBS = (
    "l", "look", "inv", "inventory", "i",
    "say", "emote", "whisper", "wh",
    "take", "get", "pick", "drop", "put", "use",
    "open", "close", "lock", "unlock",
    "wield", "unwield", "wear", "remove",
    "sit", "stand",
    "north", "south", "east", "west",
    "northeast", "northwest", "southeast", "southwest",
    "n", "s", "e", "w", "ne", "nw", "se", "sw", "up", "down",
    "who", "@who", "@home", "@sethome", "@quit",
    "@desc", "@describe", "@dig", "@create", "@chparent", "@rename",
    "@teleport", "@messages", "@message", "@dumpdb",
    "eval", "@eval",
)

# Verbs whose next word should complete against things in the room / inventory.
OBJECT_VERBS = {
    "take", "get", "pick", "drop", "put", "use", "open", "close",
    "lock", "unlock", "wield", "unwield", "wear", "remove", "look",
    "sit", "stand",
}

# ---------------------------------------------------------------------------
# Output styling / parsing (the server's look format)
# ---------------------------------------------------------------------------

ROOM_RE = re.compile(r"^---\s*(.+?)\s*---\s*$")
SEEH_RE = re.compile(r"^You see here:\s*(.+?)\.?\s*$")
EXITS_RE = re.compile(r"^Exits:\s*(.+?)\.?\s*$")
CARRY_RE = re.compile(r"^You are carrying:\s*(.+?)\.?\s*$")
WELCOME_RE = re.compile(r"^Welcome back,\s*(.+?)!$")
SAY_YOU_RE = re.compile(r'^You say, "(.*)"$')
SAY_OTHER_RE = re.compile(r'^(.+?) says, "(.*)"$')
WHO_RE = re.compile(r"^\S+\s{2,}\S+")  # "@who" rows: name, place, time
ERROR_RE = re.compile(
    r"(?i)(you can't|you don't|you need|you must|i don't understand|"
    r"there is no |could not|do not match|already taken|already logged in)"
)


def split_list(raw: str) -> list[str]:
    return [x.strip() for x in raw.split(",") if x.strip()]


def _dot(line: str) -> str:
    return "." if line.rstrip().endswith(".") else ""


def _wrap(text: str, width: int = PANEL_WIDTH) -> str:
    if not text or text == "—":
        return "—"
    return "\n".join(textwrap.wrap(text, width=width)) or "—"


# ---------------------------------------------------------------------------
# Socket session: one thread owns all Socket.IO I/O
# ---------------------------------------------------------------------------


class ServerEvent:
    """A unit of work bridged from the socket thread into the UI."""

    __slots__ = ("kind", "data", "detail")

    def __init__(self, kind: str, data: str = "", detail: str = ""):
        self.kind = kind      # "message" | "status"
        self.data = data      # message text, or the phase for status events
        self.detail = detail  # extra info (status reason / connect attempt)


class SocketSession:
    """Runs the (sync) Socket.IO client on a daemon thread.

    * inbound:  socket event handlers push :class:`ServerEvent`s onto
      ``outbox``; the Textual app drains it on its own event loop.
    * outbound: the UI puts command strings (or CMD_* sentinels) onto
      ``inbox``; this thread emits them, so all socket calls happen in
      one place.
    """

    def __init__(self, url: str):
        self.url = url
        self.outbox: queue.Queue = queue.Queue()
        self.inbox: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._auto_reconnect = True
        self._user_initiated = False
        self._last_phase = "connecting"
        self._attempts = 0
        self._client: socketio.Client | None = None
        self._thread: threading.Thread | None = None

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name="azimuth-socket", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    # -- internals ---------------------------------------------------------

    def _publish(self, ev: ServerEvent) -> None:
        if ev.kind == "status":
            self._last_phase = ev.data
        self.outbox.put(ev)

    def _take(self, timeout: float) -> str | None:
        try:
            return self.inbox.get(timeout=timeout)
        except queue.Empty:
            return None

    @staticmethod
    def _hard_disconnect(client: socketio.Client) -> None:
        """Disconnect without joining the polling read loop.

        With the polling transport the read loop can be blocked inside a
        long-poll GET for up to ~30s; joining it would stall this thread.
        The read loop is a daemon thread and exits on its own.
        """
        try:
            client.eio.disconnect(abort=True)
        except Exception:
            try:
                client.disconnect()
            except Exception:
                pass

    def _drain_while(self, limit: float | None) -> None:
        """Wait up to *limit* seconds (None = forever), consuming tokens."""
        end = None if limit is None else time.monotonic() + limit
        while not self._stop.is_set():
            tok = self._take(0.1)
            if tok == CMD_CONNECT:
                self._auto_reconnect = True
                return
            if tok == CMD_DISCONNECT:
                self._auto_reconnect = False
            elif tok is not None:
                self._publish(ServerEvent(
                    "message", "(client) not connected — use /connect"
                ))
            if limit is None:
                continue
            if time.monotonic() >= end:
                return

    def _make_client(self) -> socketio.Client:
        client = socketio.Client(logger=False, engineio_logger=False)
        session = self

        @client.event
        def connect() -> None:  # noqa: F811
            session._user_initiated = False
            session._publish(ServerEvent("status", "connected"))

        @client.event
        def connect_error(data) -> None:  # noqa: F811
            if client.connected:
                return  # transient transport hiccup; client keeps going
            msg = getattr(data, "message", None)
            if msg is None:
                msg = data.args[0] if getattr(data, "args", None) else data
            session._publish(
                ServerEvent("status", "disconnected", f"{msg}")
            )

        @client.event
        def disconnect() -> None:  # noqa: F811
            if session._user_initiated:
                detail = "as requested"
            else:
                detail = "closed by server"
            session._publish(
                ServerEvent("status", "disconnected", detail)
            )

        @client.event
        def message(data="") -> None:  # noqa: F811
            if isinstance(data, dict):
                data = data.get("message") or data.get("text") or str(data)
            session._publish(ServerEvent("message", str(data)))

        @client.event
        def disconnect_request() -> None:  # noqa: F811
            session._user_initiated = True
            session._publish(
                ServerEvent("status", "disconnected", "session ended by server")
            )
            session._hard_disconnect(client)

        return client

    def _run(self) -> None:
        backoff = RECONNECT_BASE
        while not self._stop.is_set():
            client = self._make_client()
            self._client = client
            self._attempts += 1
            detail = f"attempt {self._attempts}" if self._attempts > 1 else ""
            self._publish(ServerEvent("status", "connecting", detail))
            try:
                client.connect(self.url, wait_timeout=CONNECT_TIMEOUT)
            except Exception as exc:
                self._client = None
                if self._last_phase != "disconnected":
                    self._publish(
                        ServerEvent("status", "disconnected", f"connect failed: {exc}")
                    )
                if self._auto_reconnect:
                    self._drain_while(backoff)
                    backoff = min(backoff * 2, RECONNECT_MAX)
                else:
                    self._drain_while(None)
                    backoff = RECONNECT_BASE
                if self._stop.is_set():
                    break
                continue

            # --- connected: pump commands until the link drops ---
            backoff = RECONNECT_BASE
            self._attempts = 0
            while not self._stop.is_set():
                token = self._take(0.05)
                if token in (CMD_DISCONNECT, CMD_QUIT):
                    self._user_initiated = True
                    self._auto_reconnect = False
                    self._hard_disconnect(client)
                    break
                elif token == CMD_CONNECT:
                    pass  # already connected
                elif token is not None:
                    try:
                        client.emit("command", {"command": token})
                    except Exception as exc:
                        self._publish(
                            ServerEvent("message", f"(client) send failed: {exc}")
                        )
                if not client.connected:
                    break

            self._client = None
            if self._stop.is_set():
                break
            if not self._auto_reconnect:
                # User asked to disconnect: park until /connect or exit.
                self._drain_while(None)
                if self._stop.is_set():
                    break
            else:
                self._drain_while(backoff)
                backoff = min(backoff * 2, RECONNECT_MAX)

        # Final cleanup: make sure the server knows we are gone.
        client = self._client
        self._client = None
        if client is not None and client.connected:
            self._hard_disconnect(client)


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------


class CommandInput(Input):
    """Input with Up/Down history and Tab completion delegated to the app."""

    async def _on_key(self, event: events.Key) -> None:  # noqa: N802
        app = self.app  # type: ignore[assignment]
        if isinstance(app, AzimuthClient):
            if event.key == "up":
                app.history_back()
                event.stop()
                event.prevent_default()
                return
            if event.key == "down":
                app.history_forward()
                event.stop()
                event.prevent_default()
                return
            if event.key == "tab":
                if app.complete_current_word():
                    event.stop()
                    event.prevent_default()
                    return
        await super()._on_key(event)


# ---------------------------------------------------------------------------
# The app
# ---------------------------------------------------------------------------

PHASE_STYLE = {
    "connected": ("connected", "bold bright_green"),
    "connecting": ("connecting…", "yellow"),
    "disconnected": ("disconnected", "bold red"),
}

HELP_LINES: list[tuple[str, str]] = [
    ("── AZIMUTH CLIENT ─────────────────────────", "bold bright_blue"),
    ("", "default"),
    ("Keys:   Up/Down history · Tab complete · F1 help", "default"),
    ("        Ctrl+Q quit (notifies server) · Ctrl+X force quit", "default"),
    ("", "default"),
    ("Local commands (no server needed):", "bold"),
    ("  /connect   /disconnect   /server <url>   /clear   /log [path]   /quit", "cyan"),
    ("", "default"),
    ("Typing:    'text' say in room · :text emote · |code eval (programmer)", "default"),
    ("Movement:  n s e w ne nw se sw up down · look/l · inv/i", "default"),
    ("Objects:   take get drop · open close · lock unlock · use · put · wield · wear", "default"),
    ("Account:   login <user> <pass> · @home · @sethome · @who · @quit", "default"),
    ("Programmer: @create · @dig · @chparent · @rename · @teleport · @desc · @dumpdb", "default"),
    ("", "default"),
    ("(wizard / wizard logs you in as a programmer)", "dim"),
]


class AzimuthClient(App):
    """Textual TUI client for the Azimuth MUD."""

    TITLE = "Azimuth"

    CSS = """
    Screen {
        background: #0c0f16;
    }

    #status-bar {
        height: 1;
        width: 100%;
        background: #131826;
        padding: 0 1;
    }
    #status-title { width: auto; margin-right: 2; }
    #status-phase { width: auto; min-width: 15; content-align-horizontal: left; margin-right: 2; }
    #status-url   { width: 1fr; content-align-horizontal: right; }
    #status-player{ width: auto; min-width: 12; content-align-horizontal: right; margin-left: 2; }

    #main { height: 1fr; width: 100%; layout: horizontal; }

    #side {
        width: 30;
        height: 100%;
        border: round #222b40;
        background: #0e1219;
        padding: 0 1;
    }
    .panel-head { color: #b6b6b6; margin-top: 1; width: 100%; }
    .panel-body { color: #c8c8c8; width: 100%; }

    #output-pane { width: 1fr; height: 100%; }
    #output {
        width: 1fr;
        height: 1fr;
        border: round #222b40;
        background: #0a0d13;
    }
    #cmd {
        margin-top: 1;
        border: round #2a3550;
        background: #10151f;
    }
    #cmd:focus { border: round #3d6dd8; }
    """

    BINDINGS = [
        Binding("f1", "help", "Help", show=False),
        Binding("ctrl+q", "graceful_quit", "Quit", priority=True),
        Binding("ctrl+x", "hard_quit", "Force quit"),
    ]

    def __init__(self, session: SocketSession):
        super().__init__()
        self.session = session
        self._phase: str | None = None
        self._previous_phase: str | None = None
        self._player_name: str | None = None
        self._room: str | None = None
        self._exits: list[str] = []
        self._room_items: list[str] = []
        self._carry: list[str] = []
        self._players: list[str] = []
        self._history: list[str] = []
        self._hist_idx: int | None = None
        self._hist_saved: str | None = None
        self._completion_cands: list[str] = []
        self._completion_idx = 0
        self._log_lines: list[str] = []
        self._pump_task: asyncio.Task | None = None
        self._cmd: CommandInput | None = None
        self._output: RichLog | None = None
        self._side: Vertical | None = None

    # -- compose -----------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Horizontal(id="status-bar"):
            yield Label(Text("AZIMUTH", "bold bright_blue"), id="status-title")
            yield Label(Text("● starting…", "yellow"), id="status-phase")
            yield Label(Text(self.session.url, "58"), id="status-url")
            yield Label(Text("", "bright_yellow"), id="status-player")
        with Horizontal(id="main"):
            with Vertical(id="side"):
                yield Label("ROOM", classes="panel-head")
                yield Label("—", id="room-name", classes="panel-body")
                yield Label("EXITS", classes="panel-head")
                yield Label("—", id="exit-list", classes="panel-body")
                yield Label("IN ROOM", classes="panel-head")
                yield Label("—", id="room-items", classes="panel-body")
                yield Label("CARRYING", classes="panel-head")
                yield Label("—", id="carry-list", classes="panel-body")
            with Vertical(id="output-pane"):
                yield RichLog(
                    id="output",
                    wrap=True,
                    markup=False,
                    highlight=False,
                    max_lines=OUTPUT_LINES,
                )
                yield CommandInput(
                    id="cmd",
                    placeholder="type a command…  (F1 for help)",
                )
        yield Footer()

    def on_mount(self) -> None:
        self._cmd = self.query_one("#cmd", CommandInput)
        self._output = self.query_one("#output", RichLog)
        self._side = self.query_one("#side", Vertical)
        self._cmd.focus()
        self.watch(self, "size", self._on_resize)
        self._phase = "connecting"
        self._render_phase()
        self._banner()
        self.session.start()
        self._pump_task = asyncio.get_running_loop().create_task(self._pump())

    def _on_resize(self, *args) -> None:
        # Hide the side panel on narrow terminals.
        if self._side is not None:
            self._side.visible = self.size.width >= 96

    async def on_unmount(self) -> None:
        self.session.stop()
        if self._pump_task is not None:
            self._pump_task.cancel()

    # -- bridge pump -------------------------------------------------------

    async def _pump(self) -> None:
        try:
            while True:
                try:
                    ev = self.session.outbox.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(0.04)
                    continue
                self._dispatch(ev)
        except asyncio.CancelledError:
            return

    def _dispatch(self, ev: ServerEvent) -> None:
        if ev.kind == "status":
            self._set_phase(ev.data, ev.detail)
        else:
            self._append_message(ev.data)

    def _set_phase(self, phase: str, detail: str) -> None:  # noqa: D102
        # (kept small and separate so callers read clearly)
        changed = phase != self._phase
        self._previous_phase = self._phase
        self._phase = phase
        self._render_phase()
        if not changed:
            return
        if phase == "connected":
            self._write_line(Text.assemble(
                ("── connected to ", "dim"),
                (self.session.url, "bold green"),
                (" ──", "dim"),
            ))
        elif phase == "disconnected":
            self._write_line(Text(f"── disconnected ──{(' ' + detail) if detail else ''}", "red"))
        elif phase == "connecting" and self._previous_phase == "disconnected":
            self._write_line(
                Text("── reconnecting… ──" + (f" ({detail})" if detail else ""), "yellow")
            )

    def _render_phase(self) -> None:
        label = self.query_one("#status-phase", Label)
        text, style = PHASE_STYLE.get(self._phase or "connecting", ("…", "yellow"))
        label.update(Text(f"● {text}", style))

    def _render_player(self) -> None:
        if self._player_name:
            self.query_one("#status-player", Label).update(
                Text(f"[{self._player_name}]", "bold bright_yellow")
            )

    # -- output ------------------------------------------------------------

    def _write_line(self, renderable) -> None:
        assert self._output is not None
        self._output.write(renderable)

    def _write_local(self, text: str, style: str = "red") -> None:
        self._write_line(Text(f"  {text}", style))
        self._log_lines.append(f"  {text}")

    def _banner(self) -> None:
        assert self._output is not None
        self._output.write(Text("        A   Z   I   M   U   T   H", "bold bright_cyan"))
        self._output.write(
            Text(" Azaroth's Intelligent Multi-User Textual Habitat", "dim")
        )
        self._output.write(Text(f" connecting to {self.session.url} …", "dim"))
        self._output.write(Text(" F1 for help · 'login <user> <pass>' once connected", "dim"))
        self._output.write(Text(""))

    def _help(self) -> None:
        for content, style in HELP_LINES:
            self._write_line(Text(content, style))
        self._write_line(Text(""))

    def _append_message(self, text: str) -> None:
        assert self._output is not None
        self._log_lines.append(text)
        lines = text.splitlines()
        while lines and not lines[-1].strip():
            lines.pop()
        if not lines:
            lines = [""]
        for line in lines:
            styled = self._style_line(line)
            self._write_line(styled)
        self._write_line(Text(""))
        self._refresh_panel()

    def _style_line(self, line: str) -> Text:
        m = ROOM_RE.match(line)
        if m:
            name = m.group(1)
            self._room = name
            width = max(10, self._output.size.width - 2) if self._output else 60
            if width <= len(name) + 4:
                return Text(f"--- {name} ---", "bold bright_cyan")
            rule = "─" * max(0, width - len(name) - 3)
            return Text.assemble(("── ", "dim"), (name, "bold bright_cyan"), (rule, "dim"))

        m = SEEH_RE.match(line)
        if m:
            names = split_list(m.group(1))
            self._room_items = names
            t = Text("You see here: ", "green")
            for i, n in enumerate(names):
                if i:
                    t.append_text(Text("  ", "default"))
                t.append_text(Text(n, "bold green"))
            t.append_text(Text(_dot(line), "default"))
            return t

        if line.strip() == "The place looks empty.":
            self._room_items = []
            return Text("The place looks empty.", "italic dim")

        m = EXITS_RE.match(line)
        if m:
            names = split_list(m.group(1))
            self._exits = names
            t = Text("Exits: ", "magenta")
            for i, n in enumerate(names):
                if i:
                    t.append_text(Text("  ", "default"))
                t.append_text(Text(n, "bold magenta"))
            t.append_text(Text(_dot(line), "default"))
            return t

        m = CARRY_RE.match(line)
        if m:
            names = split_list(m.group(1))
            self._carry = names
            t = Text("You are carrying: ", "yellow")
            for i, n in enumerate(names):
                if i:
                    t.append_text(Text("  ", "default"))
                t.append_text(Text(n, "bold yellow"))
            t.append_text(Text(_dot(line), "default"))
            return t

        if line.strip() == "You are not carrying anything":
            self._carry = []
            return Text(line, "dim")

        m = WELCOME_RE.match(line)
        if m:
            self._player_name = m.group(1)
            self._render_player()
            return Text(line, "bold bright_green")

        if line.strip() == "Registration successful!":
            return Text(line, "bold bright_green")

        if line.strip() == "Goodbye!":
            return Text(line, "bold bright_magenta")

        m = SAY_YOU_RE.match(line)
        if m:
            return Text.assemble(("You say, ", "yellow"), (f'"{m.group(1)}"', "bold yellow"))

        m = SAY_OTHER_RE.match(line)
        if m:
            return Text.assemble((m.group(1), "bold magenta"), (f' says, "{m.group(2)}"', "yellow"))

        # "@who" rows: first column is a player name — harvest it for completion.
        if WHO_RE.match(line) and " seconds ago" in line:
            self._players.append(line.split()[0])
            return Text(line)

        if ERROR_RE.search(line):
            return Text(line, "bright_red")

        if re.match(r"^Welcome to .+$", line):
            return Text(line, "bold bright_blue")

        return Text(line)

    # -- side panel --------------------------------------------------------

    def _refresh_panel(self) -> None:
        if self._output is None:
            return

        def fmt(names: list[str]) -> str:
            return _wrap(" · ".join(names)) if names else "—"

        self.query_one("#room-name", Label).update(
            Text(_wrap(self._room or ""), "bold cyan") if self._room else Text("—", "240")
        )
        self.query_one("#exit-list", Label).update(Text(fmt(self._exits), "magenta"))
        self.query_one("#room-items", Label).update(Text(fmt(self._room_items), "green"))
        self.query_one("#carry-list", Label).update(Text(fmt(self._carry), "yellow"))

    # -- input -------------------------------------------------------------

    def on_input_submitted(self, message: Input.Submitted) -> None:
        if self._cmd is None or message.input is not self._cmd:
            return
        text = message.value.strip()
        if not text:
            self._cmd.focus()
            return
        self._push_history(text)
        self._cmd.value = ""
        self._cmd.focus()
        if text.startswith("/"):
            self._write_line(Text.assemble(("» ", "magenta"), (text, "bright_white")))
            self._log_lines.append(f"» {text}")
            self._client_command(text)
        else:
            self._write_line(Text.assemble(("❯ ", "bright_cyan"), (text, "bright_white")))
            self._log_lines.append(f"❯ {text}")
            if self._phase == "connected":
                self.session.inbox.put(text)
            else:
                self._write_local("not connected — use /connect to try again")

    def _push_history(self, line: str) -> None:
        self._history.append(line)
        if len(self._history) > HISTORY_LIMIT:
            del self._history[: len(self._history) - HISTORY_LIMIT]
        self._hist_idx = None
        self._hist_saved = None

    def history_back(self) -> None:
        if self._cmd is None or not self._history:
            return
        if self._hist_idx is None:
            self._hist_saved = self._cmd.value
            self._hist_idx = len(self._history)
        self._hist_idx = max(0, self._hist_idx - 1)
        self._cmd.value = self._history[self._hist_idx]
        self._cmd.cursor_position = len(self._cmd.value)

    def history_forward(self) -> None:
        if self._cmd is None or self._hist_idx is None:
            return
        self._hist_idx += 1
        if self._hist_idx >= len(self._history):
            self._hist_idx = None
            self._cmd.value = self._hist_saved or ""
        else:
            self._cmd.value = self._history[self._hist_idx]
        self._cmd.cursor_position = len(self._cmd.value)

    def _object_pool(self) -> list[str]:
        pool: list[str] = []
        for name in [*self._room_items, *self._carry, "me", "here"]:
            if name not in pool:
                pool.append(name)
        return pool

    def _player_pool(self) -> list[str]:
        pool: list[str] = []
        for name in [*self._players, "me"]:
            if name not in pool:
                pool.append(name)
        return pool

    def complete_current_word(self) -> bool:
        """Tab completion.  Returns True if the buffer was modified.

        Completing the first word runs against the verb list; completing
        anywhere in the arguments runs against in-world objects (or players
        for whisper), matching the *whole* argument prefix so multi-word
        names like ``a short sword`` work.
        """
        if self._cmd is None:
            return False
        text = self._cmd.value
        pos = min(self._cmd.cursor_position, len(text))

        # Find the end of the first word.
        first_end = len(text)
        for i, ch in enumerate(text):
            if ch in " \t":
                first_end = i
                break

        if pos <= first_end:
            # Completing the first word (the verb).  Only when the cursor is
            # at the end of that word, to avoid duplicating the rest of it.
            if pos != first_end:
                return False
            word = text[:pos].strip("\"'")
            if not word:
                return False
            pool = list(VERBS)
            repl, repl_len = self._pick(word, pool)
            if repl is None:
                return False
            self._cmd.value = text[:0] + repl + text[pos:]
            self._cmd.cursor_position = len(repl)
            return True

        # Completing an argument: the prefix is everything after the verb.
        # Only at the end of the buffer (arguments trail the verb).
        if pos != len(text):
            return False
        typed = text[first_end:pos]
        prefix = typed.strip(" \t\"'")
        words = text.split()
        first = words[0] if words else ""
        if first in ("whisper", "wh"):
            pool = self._player_pool()
        elif first in OBJECT_VERBS:
            pool = self._object_pool()
        else:
            pool = self._object_pool() or list(VERBS)

        repl, repl_len = self._pick(prefix, pool)
        if repl is None:
            return False
        self._cmd.value = text[:first_end] + " " + repl + text[pos:]
        self._cmd.cursor_position = first_end + 1 + len(repl)
        return True

    def _pick(self, word: str, pool: list[str]) -> tuple[str | None, int]:
        """Choose a completion for *word* from *pool*.

        Returns ``(replacement, suffix_len)`` where the replacement is the
        full replacement text for the typed word, or ``(None, 0)`` when
        there is nothing to complete.
        """
        lowered = word.lower()
        cands = [c for c in pool if c.lower().startswith(lowered)]

        # An active completion session (previous Tab) that the current word
        # belongs to is preferred, so repeated Tab cycles through siblings
        # even after one has been fully typed.
        keep = self._completion_cands
        keep_lower = {c.lower() for c in keep}
        in_keep = bool(keep) and len(keep) > 1 and lowered in keep_lower

        if not cands and not in_keep:
            self._completion_cands = []
            self._completion_idx = 0
            return None, 0
        if len(cands) == 1 and cands[0].lower() == lowered and not in_keep:
            # Word is fully typed and unique: nothing left to complete.
            self._completion_cands = []
            self._completion_idx = 0
            return None, 0

        source = keep if in_keep else cands
        suffix_len = len(word)
        common = os.path.commonprefix([c.lower() for c in source])[suffix_len:]
        if common and common.strip():
            self._completion_cands = source
            self._completion_idx = 0
            return word + common, suffix_len

        if in_keep:
            self._completion_idx = ([c.lower() for c in keep].index(lowered) + 1) % len(keep)
        else:
            self._completion_cands = cands
            if lowered in {c.lower() for c in cands}:
                self._completion_idx = ([c.lower() for c in cands].index(lowered) + 1) % len(cands)
            else:
                self._completion_idx = 0
        self._completion_idx %= len(self._completion_cands) or 1
        target = self._completion_cands[self._completion_idx]
        # Siblings may be shorter than the current word: replace, don't append.
        if target.startswith(word):
            return word + target[suffix_len:], suffix_len
        return target, len(target)

    # -- client commands ---------------------------------------------------

    def _client_command(self, text: str) -> None:
        parts = text.split()
        verb = parts[0].lower()
        rest = parts[1:]

        if verb in ("/help",):
            self._help()
        elif verb == "/clear":
            if self._output is not None:
                self._output.clear()
        elif verb == "/connect":
            if self._phase == "connected":
                self._write_local("already connected")
            else:
                self.session.inbox.put(CMD_CONNECT)
                self._write_local("connecting…", "yellow")
        elif verb == "/disconnect":
            if self._phase == "connected":
                self.session.inbox.put(CMD_DISCONNECT)
            else:
                self._write_local("not connected")
        elif verb == "/server":
            if not rest:
                self._write_local("usage: /server <url>")
            else:
                url = rest[0]
                if not url.startswith(("http://", "https://")):
                    url = "http://" + url
                self.session.url = url
                self.query_one("#status-url", Label).update(Text(url, "58"))
                self._write_local(f"server set to {url}", "green")
                self.session.inbox.put(CMD_CONNECT)
        elif verb == "/log":
            self._save_log(rest[0] if rest else None)
        elif verb == "/quit":
            if self._phase == "connected":
                self._write_line(
                    Text.assemble(("❯ ", "bright_cyan"), ("@quit", "bright_white"))
                )
                self._log_lines.append("❯ @quit")
                self.session.inbox.put("@quit")
                self._write_local(
                    "logging out — the session will return to the login prompt", "yellow"
                )
            else:
                self._write_local("not connected — nothing to quit")
        else:
            self._write_local(f"unknown client command {verb} — try /help")

    def _save_log(self, path: str | None) -> None:
        if path is None:
            path = f"azimuth-session-{time.strftime('%Y%m%d-%H%M%S')}.log"
        try:
            with open(path, "w") as fh:
                fh.write("\n".join(self._log_lines) + "\n")
            self._write_local(f"session saved to {path} ({len(self._log_lines)} lines)", "green")
        except OSError as exc:
            self._write_local(f"could not write log: {exc}")

    # -- actions -----------------------------------------------------------

    def action_help(self) -> None:
        self._help()
        self._cmd.focus()

    def action_graceful_quit(self) -> None:
        if self._phase == "connected":
            self._write_line(Text.assemble(("❯ ", "bright_cyan"), ("@quit", "bright_white")))
            self.session.inbox.put("@quit")
            self.session.inbox.put(CMD_QUIT)
            self.set_timer(0.8, self.exit)
        else:
            self.exit()

    def action_hard_quit(self) -> None:
        self.exit()


# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Azimuth TUI client")
    parser.add_argument(
        "url",
        nargs="?",
        default=DEFAULT_URL,
        help=f"server URL (default: {DEFAULT_URL})",
    )
    args = parser.parse_args(argv)

    url = args.url
    if not url.startswith(("http://", "https://")):
        url = "http://" + url

    session = SocketSession(url)
    app = AzimuthClient(session)
    try:
        app.run()
    except KeyboardInterrupt:
        pass
    finally:
        session.stop()


if __name__ == "__main__":
    main()
