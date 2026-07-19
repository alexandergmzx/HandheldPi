"""Events flowing from input/scanner/network producers into the state machine."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum


class Button(str, Enum):
    UP = "up"
    DOWN = "down"
    LEFT = "left"
    RIGHT = "right"
    A = "a"
    B = "b"
    X = "x"
    Y = "y"
    L = "l"
    R = "r"
    START = "start"
    SELECT = "select"


@dataclass(frozen=True)
class ButtonEvent:
    button: Button
    action: str = "press"  # press | hold


@dataclass(frozen=True)
class ScanEvent:
    payload: str
    symbology: str = "QRCODE"
    at: float = field(default_factory=time.monotonic)


@dataclass(frozen=True)
class TickEvent:
    pass


@dataclass(frozen=True)
class NetStatusEvent:
    online: bool


@dataclass(frozen=True)
class QueueDepthEvent:
    pending: int


@dataclass(frozen=True)
class SyncFailedEvent:
    """A replayed op was rejected by the WMS; its task chain is dead-lettered."""

    task_id: int
    code: str


@dataclass(frozen=True)
class AuthRequiredEvent:
    """The bearer token is no longer valid; re-login needed, queue kept."""


@dataclass(frozen=True)
class QuitEvent:
    pass


Event = (
    ButtonEvent | ScanEvent | TickEvent | NetStatusEvent | QueueDepthEvent
    | SyncFailedEvent | AuthRequiredEvent | QuitEvent
)
