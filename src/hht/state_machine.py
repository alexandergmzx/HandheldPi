"""Picking workflow state machine.

Consumes Events, calls injected ports (WMS, queue, sound cues), and exposes its state
for rendering. No device I/O or threads of its own; the injectable clock and outputs
keep it fully unit- and script-testable (see tests/ and tests/scripts/).

STARTUP → LOGIN_BADGE ⇄ LOGIN_PIN → IDLE → GOTO_LOCATION → SCAN_ARTICLE
                                     ▲  ↘ NO_TASK   (wrong scan → error banner, stay)
                                     │                    │
                                     └──── CONFIRMED ← SET_QUANTITY
Global: Select = status overlay, hold Start = logout, errors auto-clear.
"""

from __future__ import annotations

import logging
import time
from enum import Enum
from typing import Callable

from . import __version__
from .audio import SoundCue
from .config import AppConfig
from .events import Button, ButtonEvent, Event, NetStatusEvent, QueueDepthEvent, \
    ScanEvent, TickEvent
from .logsetup import evt
from .wms.base import WmsClient, WmsError, WmsRejected, WmsUnavailable
from .wms.models import Confirmation, Session, Task
from .wms.offline_queue import OfflineQueue

log = logging.getLogger("hht.sm")


class State(str, Enum):
    STARTUP = "STARTUP"
    LOGIN_BADGE = "LOGIN_BADGE"
    LOGIN_PIN = "LOGIN_PIN"
    IDLE = "IDLE"
    NO_TASK = "NO_TASK"
    GOTO_LOCATION = "GOTO_LOCATION"
    SCAN_ARTICLE = "SCAN_ARTICLE"
    SET_QUANTITY = "SET_QUANTITY"
    CONFIRMED = "CONFIRMED"


def _parse_payload(payload: str) -> tuple[str, str]:
    """'LOC:A-01-03' → ('LOC', 'A-01-03'); bare EANs → ('RAW', payload)."""
    for prefix in ("OP", "LOC", "ART"):
        if payload.startswith(prefix + ":"):
            return prefix, payload[len(prefix) + 1:]
    return "RAW", payload


class PickingStateMachine:
    def __init__(self, cfg: AppConfig, wms: WmsClient, queue: OfflineQueue,
                 kick_flusher: Callable[[], None] = lambda: None,
                 play_sound: Callable[[SoundCue], None] = lambda cue: None,
                 clock: Callable[[], float] = time.monotonic):
        self.cfg = cfg
        self.wms = wms
        self.queue = queue
        self.kick_flusher = kick_flusher
        self.play_sound = play_sound
        self.now = clock

        self.state = State.STARTUP
        self.session: Session | None = None
        self.task: Task | None = None
        self.qty = 0
        self.pin_digits = [0] * cfg.workflow.pin_length
        self.pin_cursor = 0
        self.online = True
        self.queue_depth = queue.pending_count()
        self.show_status = False
        self.version = __version__
        self._error: tuple[str, float] | None = None
        self._confirm_until = 0.0

    # -- rendering helpers --------------------------------------------------

    @property
    def error_text(self) -> str | None:
        if self._error and self.now() < self._error[1]:
            return self._error[0]
        return None

    # -- internals ----------------------------------------------------------

    def _set_error(self, msg: str) -> None:
        self._error = (msg, self.now() + self.cfg.workflow.error_banner_s)
        evt(log, "workflow_error", _level=logging.WARNING,
            state=self.state.value, message=msg)
        self._sound(SoundCue.ERROR)

    def _sound(self, cue: SoundCue) -> None:
        """Emit a semantic cue without letting an output failure affect workflow."""
        try:
            self.play_sound(cue)
        except Exception as e:  # output adapters must be fail-open
            evt(log, "sound_emit_failed", _level=logging.WARNING,
                cue=cue.value, error=str(e))

    def _goto(self, new: State, reason: str) -> None:
        evt(log, "state_transition", from_state=self.state.value,
            to_state=new.value, reason=reason)
        self.state = new

    def _logout(self) -> None:
        operator = self.session.operator_id if self.session else None
        self.session = None
        self.task = None
        self.show_status = False
        evt(log, "logout", operator_id=operator)
        self._goto(State.LOGIN_BADGE, "logout")

    def _fetch_task(self) -> None:
        try:
            task = self.wms.next_task()
        except WmsUnavailable:
            self.online = False
            self._set_error("WMS unreachable — retry")
            return
        except WmsRejected as e:
            self._set_error(str(e))
            return
        self.online = True
        if task is None:
            self._goto(State.NO_TASK, "no_task_available")
            return
        self.task = task
        evt(log, "task_received", task_id=task.task_id, location=task.location_code,
            article=task.article.code, qty=task.qty_requested)
        self._goto(State.GOTO_LOCATION, "task_received")

    def _login(self, do_login: Callable[[], Session], how: str) -> None:
        try:
            self.session = do_login()
        except WmsUnavailable:
            self.online = False
            self._set_error("WMS unreachable")
            return
        except WmsRejected as e:
            self._set_error(str(e))
            return
        self.online = True
        evt(log, "login_ok", method=how, operator_id=self.session.operator_id)
        self._goto(State.IDLE, f"login_{how}")
        self._sound(SoundCue.BADGE_ACCEPTED)

    def _report_scan(self, scan_type: str, code: str) -> None:
        """Best-effort telemetry; never blocks the workflow (see API.md)."""
        assert self.task is not None
        try:
            self.wms.report_scan(self.task.task_id, scan_type, code)
        except WmsError as e:
            evt(log, "report_scan_skipped", _level=logging.DEBUG,
                scan_type=scan_type, reason=str(e))

    def _confirm_pick(self) -> None:
        assert self.task is not None and self.session is not None
        if self.qty < self.task.qty_requested and not self.cfg.workflow.allow_short_pick:
            self._set_error("Short pick not allowed")
            return
        conf = Confirmation.build(
            device_id=self.cfg.device.id, operator_id=self.session.operator_id,
            task=self.task, qty_picked=self.qty,
        )
        self.queue.enqueue(conf)
        self.queue_depth = self.queue.pending_count()
        self.kick_flusher()
        evt(log, "pick_confirmed", task_id=conf.task_id, qty_picked=conf.qty_picked,
            short_pick=conf.short_pick, key=conf.idempotency_key)
        self._confirm_until = self.now() + self.cfg.workflow.error_banner_s
        self._goto(State.CONFIRMED, "pick_confirmed")
        self._sound(SoundCue.CONFIRMED)

    # -- event handling -------------------------------------------------------

    def handle(self, event: Event) -> None:
        if isinstance(event, NetStatusEvent):
            if event.online != self.online:
                evt(log, "net_status", online=event.online)
                if not event.online:
                    self._sound(SoundCue.OFFLINE)
            self.online = event.online
            return
        if isinstance(event, QueueDepthEvent):
            self.queue_depth = event.pending
            return
        if isinstance(event, TickEvent):
            if self._error and self.now() >= self._error[1]:
                self._error = None
            if self.state is State.STARTUP:
                self._goto(State.LOGIN_BADGE, "startup_done")
                self._sound(SoundCue.READY)
            elif self.state is State.CONFIRMED and self.now() >= self._confirm_until:
                self.task = None
                self._goto(State.IDLE, "confirm_banner_timeout")
            return

        if isinstance(event, ButtonEvent):
            if event.button is Button.SELECT and event.action == "press":
                self.show_status = not self.show_status
                return
            if event.button is Button.START and event.action == "hold" and self.session:
                self._logout()
                return
            if self.show_status:  # status overlay swallows other keys
                return

        handler = getattr(self, f"_in_{self.state.value.lower()}", None)
        if handler:
            handler(event)

    # -- per-state handlers ---------------------------------------------------

    def _in_startup(self, event: Event) -> None:
        pass  # leaves via TickEvent

    def _in_login_badge(self, event: Event) -> None:
        if isinstance(event, ScanEvent):
            kind, code = _parse_payload(event.payload)
            if kind != "OP":
                self._set_error("Not a badge QR")
                return
            self._login(lambda: self.wms.login_badge(code), "badge")
        elif isinstance(event, ButtonEvent) and event.button is Button.X:
            self.pin_digits = [0] * self.cfg.workflow.pin_length
            self.pin_cursor = 0
            self._goto(State.LOGIN_PIN, "pin_entry_selected")

    def _in_login_pin(self, event: Event) -> None:
        if not isinstance(event, ButtonEvent):
            return
        b = event.button
        if b is Button.UP:
            self.pin_digits[self.pin_cursor] = (self.pin_digits[self.pin_cursor] + 1) % 10
        elif b is Button.DOWN:
            self.pin_digits[self.pin_cursor] = (self.pin_digits[self.pin_cursor] - 1) % 10
        elif b is Button.LEFT:
            self.pin_cursor = max(0, self.pin_cursor - 1)
        elif b is Button.RIGHT:
            self.pin_cursor = min(len(self.pin_digits) - 1, self.pin_cursor + 1)
        elif b is Button.A:
            pin = "".join(str(d) for d in self.pin_digits)
            self._login(lambda: self.wms.login_pin(pin), "pin")
            if self.state is State.LOGIN_PIN:  # login failed: clear entry
                self.pin_digits = [0] * self.cfg.workflow.pin_length
                self.pin_cursor = 0
        elif b is Button.B:
            self._goto(State.LOGIN_BADGE, "pin_entry_cancelled")

    def _in_idle(self, event: Event) -> None:
        if isinstance(event, ButtonEvent) and event.button is Button.A:
            self._fetch_task()

    def _in_no_task(self, event: Event) -> None:
        if isinstance(event, ButtonEvent):
            if event.button is Button.A:
                self._fetch_task()
            elif event.button is Button.B:
                self._goto(State.IDLE, "back")

    def _in_goto_location(self, event: Event) -> None:
        if not isinstance(event, ScanEvent):
            return
        assert self.task is not None
        kind, code = _parse_payload(event.payload)
        if kind == "LOC":
            if code == self.task.location_code:
                evt(log, "scan_accepted", scan_type="location", code=code)
                self._report_scan("location", code)
                self._goto(State.SCAN_ARTICLE, "location_ok")
                self._sound(SoundCue.LOCATION_ACCEPTED)
            else:
                evt(log, "scan_rejected", _level=logging.WARNING,
                    scan_type="location", code=code,
                    expected=self.task.location_code)
                self._set_error(f"WRONG LOCATION ({code})")
        elif kind == "ART":
            self._set_error("Scan the LOCATION first")
        else:
            self._set_error("Not a location label")

    def _in_scan_article(self, event: Event) -> None:
        assert self.task is not None
        if isinstance(event, ButtonEvent) and event.button is Button.B:
            self._goto(State.GOTO_LOCATION, "back")
            return
        if not isinstance(event, ScanEvent):
            return
        kind, code = _parse_payload(event.payload)
        if kind == "LOC":
            self._set_error("Scan the ARTICLE now")
            return
        if kind == "OP":
            self._set_error("Not an article label")
            return
        if code == self.task.article.code:
            evt(log, "scan_accepted", scan_type="article", code=code)
            self._report_scan("article", code)
            self.qty = self.task.qty_requested
            self._goto(State.SET_QUANTITY, "article_ok")
            self._sound(SoundCue.ARTICLE_ACCEPTED)
        else:
            evt(log, "scan_rejected", _level=logging.WARNING,
                scan_type="article", code=code, expected=self.task.article.code)
            self._set_error(f"WRONG ARTICLE ({code})")

    def _in_set_quantity(self, event: Event) -> None:
        assert self.task is not None
        if not isinstance(event, ButtonEvent):
            return
        b = event.button
        if b is Button.UP:
            self.qty = min(self.task.qty_requested, self.qty + 1)
        elif b is Button.DOWN:
            self.qty = max(0, self.qty - 1)
        elif b is Button.A:
            self._confirm_pick()
        elif b is Button.B:
            self._goto(State.SCAN_ARTICLE, "back")

    def _in_confirmed(self, event: Event) -> None:
        if isinstance(event, ButtonEvent) and event.button is Button.A:
            self.task = None
            self._goto(State.IDLE, "confirm_ack")
