"""Terminal keyboard input for development over SSH.

Keys:  arrows = D-pad   a/Enter = A   b = B   x = X   y = Y   [ = L   ] = R
       s = Start (S = hold-Start → logout)   Tab = Select
       : = type a scan payload (e.g. :LOC:A-01-03 + Enter)   q / Ctrl-C = quit
"""

from __future__ import annotations

import logging
import sys
import termios
import threading
import tty
from typing import Callable

from ..config import AppConfig
from ..events import Button, ButtonEvent, Event, QuitEvent, ScanEvent
from ..logsetup import evt

log = logging.getLogger("hht.input.kbd")

_KEYMAP = {
    "a": ButtonEvent(Button.A), "\r": ButtonEvent(Button.A), "\n": ButtonEvent(Button.A),
    "b": ButtonEvent(Button.B),
    "x": ButtonEvent(Button.X),
    "y": ButtonEvent(Button.Y),
    "[": ButtonEvent(Button.L),
    "]": ButtonEvent(Button.R),
    "s": ButtonEvent(Button.START),
    "S": ButtonEvent(Button.START, "hold"),
    "\t": ButtonEvent(Button.SELECT),
}
_ARROWS = {"A": Button.UP, "B": Button.DOWN, "C": Button.RIGHT, "D": Button.LEFT}


class KeyboardInput:
    def __init__(self, cfg: AppConfig):
        if not sys.stdin.isatty():
            raise RuntimeError("keyboard input backend needs a TTY "
                               "(use --script for non-interactive runs)")
        self._fd = sys.stdin.fileno()
        self._saved = termios.tcgetattr(self._fd)
        self._thread: threading.Thread | None = None
        self._running = False

    def start(self, emit: Callable[[Event], None]) -> None:
        self._running = True
        self._thread = threading.Thread(
            target=self._loop, args=(emit,), name="kbd", daemon=True
        )
        tty.setcbreak(self._fd)
        self._thread.start()
        evt(log, "keyboard_input_ready")

    def _loop(self, emit: Callable[[Event], None]) -> None:
        while self._running:
            ch = sys.stdin.read(1)
            if not ch:
                continue
            if ch == "\x1b":  # escape sequence (arrows)
                seq = sys.stdin.read(2)
                if len(seq) == 2 and seq[0] == "[" and seq[1] in _ARROWS:
                    emit(ButtonEvent(_ARROWS[seq[1]]))
            elif ch == ":":
                emit(ScanEvent(self._read_scan_line()))
            elif ch in ("q", "\x03"):
                emit(QuitEvent())
                return
            elif ch in _KEYMAP:
                emit(_KEYMAP[ch])

    def _read_scan_line(self) -> str:
        # temporarily back to cooked mode so the payload can be typed with echo
        termios.tcsetattr(self._fd, termios.TCSADRAIN, self._saved)
        try:
            sys.stdout.write("\x1b[999;1H\x1b[2Kscan> ")
            sys.stdout.flush()
            return sys.stdin.readline().strip()
        finally:
            tty.setcbreak(self._fd)

    def stop(self) -> None:
        self._running = False
        termios.tcsetattr(self._fd, termios.TCSADRAIN, self._saved)
