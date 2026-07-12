"""Scanner backends: PiCamera + pyzbar (device) or nothing (dev — scans are
injected via the keyboard ':' prompt or by scripted tests)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable

from ..config import AppConfig
from ..events import Event


class Scanner(ABC):
    @abstractmethod
    def start(self, emit: Callable[[Event], None]) -> None: ...

    def stop(self) -> None:
        pass


class NullScanner(Scanner):
    def start(self, emit: Callable[[Event], None]) -> None:
        pass


def make_scanner(cfg: AppConfig) -> Scanner:
    if cfg.scanner.backend == "camera":
        from .camera_scanner import CameraScanner

        return CameraScanner(cfg)
    return NullScanner()
