"""Development displays: ANSI half-block rendering in the terminal, and PNG dumps
(the latter double as evidence captures for test reports)."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image

from . import Display


class ConsoleDisplay(Display):
    """Renders the 320x240 screen as truecolor half-blocks (▀), ~80x30 chars."""

    def __init__(self, cols: int = 80):
        self._cols = cols
        self._rows = round(cols * 240 / 320 / 2)
        self._last_hash: int | None = None
        self._enabled = sys.stdout.isatty()
        if self._enabled:
            sys.stdout.write("\x1b[2J\x1b[?25l")  # clear, hide cursor

    def show(self, img: Image.Image, tag: str = "") -> None:
        if not self._enabled:
            return
        arr = np.asarray(
            img.convert("RGB").resize((self._cols, self._rows * 2)), dtype=np.uint8
        )
        frame_hash = hash(arr.tobytes())
        if frame_hash == self._last_hash:
            return
        self._last_hash = frame_hash
        out = ["\x1b[H"]
        for y in range(self._rows):
            top, bot = arr[2 * y], arr[2 * y + 1]
            line = []
            for x in range(self._cols):
                tr, tg, tb = top[x]
                br, bg, bb = bot[x]
                line.append(f"\x1b[38;2;{tr};{tg};{tb}m\x1b[48;2;{br};{bg};{bb}m▀")
            out.append("".join(line) + "\x1b[0m\n")
        sys.stdout.write("".join(out))
        sys.stdout.flush()

    def close(self) -> None:
        if self._enabled:
            sys.stdout.write("\x1b[?25h\x1b[0m\n")  # show cursor again
            sys.stdout.flush()


class ImageDisplay(Display):
    """Writes every frame to current.png; tagged frames (from scripted tests) are
    also saved numbered, e.g. 003_expect_state_GOTO_LOCATION.png."""

    def __init__(self, out_dir: str):
        self._dir = Path(out_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._seq = 0

    def show(self, img: Image.Image, tag: str = "") -> None:
        img.save(self._dir / "current.png")
        if tag:
            self._seq += 1
            safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in tag)
            img.save(self._dir / f"{self._seq:03d}_{safe}.png")
