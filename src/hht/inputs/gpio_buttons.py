"""GamePi20 buttons via gpiozero (lgpio backend on bookworm/trixie).

All buttons are active-low with internal pull-ups. Pin map comes from
[input.pins] in the config — never hardcoded, so a different board revision
is a config edit, not a code change. Verify with `python -m hht.tools.buttontest`.
"""

from __future__ import annotations

import logging
from typing import Callable

from ..config import AppConfig
from ..events import Button, ButtonEvent, Event
from ..logsetup import evt

log = logging.getLogger("hht.input.gpio")


class GpioInput:
    def __init__(self, cfg: AppConfig):
        self._cfg = cfg
        self._buttons: list = []

    def start(self, emit: Callable[[Event], None]) -> None:
        from gpiozero import Button as GpioButton  # lazy: hardware-only dep

        for name, pin in self._cfg.input.pins.items():
            hold = name is Button.START
            btn = GpioButton(
                pin, pull_up=True, bounce_time=0.05,
                hold_time=self._cfg.input.hold_start_s if hold else 1.0,
            )
            btn.when_pressed = (
                lambda b=name: emit(ButtonEvent(b, "press"))
            )
            if hold:
                btn.when_held = lambda b=name: emit(ButtonEvent(b, "hold"))
            self._buttons.append(btn)
        evt(log, "gpio_input_ready", buttons=len(self._buttons))

    def stop(self) -> None:
        for btn in self._buttons:
            btn.close()
        self._buttons.clear()
