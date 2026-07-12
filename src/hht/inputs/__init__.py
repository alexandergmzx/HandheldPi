"""Input backends: GamePi20 GPIO buttons (device) or terminal keyboard (dev)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable

from ..config import AppConfig
from ..events import Event


class InputSource(ABC):
    @abstractmethod
    def start(self, emit: Callable[[Event], None]) -> None: ...

    def stop(self) -> None:
        pass


def make_input(cfg: AppConfig) -> InputSource:
    if cfg.input.backend == "gpio":
        from .gpio_buttons import GpioInput

        return GpioInput(cfg)
    from .keyboard import KeyboardInput

    return KeyboardInput(cfg)
