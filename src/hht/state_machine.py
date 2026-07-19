"""Picking workflow state machine.

Consumes Events, calls injected ports (WMS, queue, sound cues), and exposes its state
for rendering. No device I/O or threads of its own; the injectable clock and outputs
keep it fully unit- and script-testable (see tests/ and tests/scripts/).

STARTUP → LOGIN_BADGE → LOGIN_PIN → IDLE → GOTO_LOCATION → SCAN_ARTICLE
               ▲            (badge = username, PIN = password)      │
               │         IDLE ↘ NO_TASK   (wrong scan → banner, stay)
               │            ▲                                       ▼
               │            └── CONFIRMED ← SET_QUANTITY ⇄ DISCREPANCY
               └── SYNC_FAILED (replay rejected — dead-letter, see supervisor)

Scans are authoritative WMS transitions; while offline they validate locally
against the claimed task and queue for FIFO replay (Level 2 store-and-forward).
Global: Select = status overlay, hold Start = logout (blocked while ops are
pending), errors auto-clear.
"""

from __future__ import annotations

import logging
import time
import uuid
from enum import Enum
from typing import Callable

from . import __version__
from .audio import SoundCue
from .config import AppConfig
from .events import AuthRequiredEvent, Button, ButtonEvent, Event, NetStatusEvent, \
    QueueDepthEvent, ScanEvent, SyncFailedEvent, TickEvent
from .logsetup import evt
from .wms.base import WmsAuthError, WmsClient, WmsRejected, WmsUnavailable
from .wms.models import OpKind, QueuedOp, Session, Task
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
    DISCREPANCY = "DISCREPANCY"
    CONFIRMED = "CONFIRMED"
    SYNC_FAILED = "SYNC_FAILED"


_QTY_CAP = 999  # display sanity bound; the WMS enforces the exact quantity

# Task state (v1) -> the screen where the operator resumes it.
_RESUME_SCREEN = {
    "ASSIGNED": State.GOTO_LOCATION,
    "LOCATION_CONFIRMED": State.SCAN_ARTICLE,
    "ARTICLE_CONFIRMED": State.SET_QUANTITY,
}

_LOGIN_BANNERS = {
    "INVALID_CREDENTIALS": "Wrong badge/PIN",
    "USER_INACTIVE": "User inactive — see supervisor",
    "DEVICE_NOT_REGISTERED": "Device not registered",
    "DEVICE_INACTIVE": "Device inactive — see supervisor",
    "DEVICE_ASSIGNMENT_CONFLICT": "Device busy — see supervisor",
}


def _parse_payload(payload: str) -> tuple[str, str]:
    """'LOC:A-01-03' → ('LOC', 'A-01-03'); anything unprefixed → ('RAW', payload)."""
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
        self.badge_username = ""
        self.qty = 0
        self.pin_digits = [0] * cfg.workflow.pin_length
        self.pin_cursor = 0
        self.online = True
        self.queue_depth = queue.pending_count()
        self.sync_failed: tuple[int, str] | None = None
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

    def _reset_pin(self) -> None:
        self.pin_digits = [0] * self.cfg.workflow.pin_length
        self.pin_cursor = 0

    def _logout(self) -> None:
        if self.queue.pending_count() > 0:
            # Logout revokes the token the queued replay still needs.
            self._set_error("Sync pending — cannot log out")
            return
        self.wms.logout()  # best-effort by contract, never raises
        operator = self.session.username if self.session else None
        self.session = None
        self.task = None
        self.badge_username = ""
        self.show_status = False
        evt(log, "logout", username=operator)
        self._goto(State.LOGIN_BADGE, "logout")

    def _session_expired(self) -> None:
        """Token gone (expired/revoked): re-login required, the queue is kept."""
        self.session = None
        self.task = None
        self._set_error("Session expired — log in again")
        self._goto(State.LOGIN_BADGE, "session_expired")

    def _fetch_task(self) -> None:
        if self.queue.pending_count() > 0:
            # /hht/tasks/next returns the *current active* task — while the
            # finished pick is still queued that would hand it back to us.
            self._set_error("Sync pending — wait")
            return
        for attempt in (1, 2):
            try:
                task = self.wms.next_task()
            except WmsUnavailable:
                self.online = False
                self._set_error("WMS unreachable — retry")
                return
            except WmsAuthError:
                self._session_expired()
                return
            except WmsRejected as e:
                if e.code == "TASK_ASSIGNMENT_CONFLICT" and attempt == 1:
                    continue  # per contract: just request the next task again
                self._set_error(str(e))
                return
            break
        self.online = True
        if task is None:
            self._goto(State.NO_TASK, "no_task_available")
            return
        self.task = task
        evt(log, "task_received", task_id=task.id, state=task.state,
            order=task.order_number, location=task.location_code,
            article=task.article.sku, qty=task.quantity)
        screen = _RESUME_SCREEN.get(task.state, State.GOTO_LOCATION)
        if screen is State.SET_QUANTITY:
            self.qty = task.quantity
        self._goto(screen, "task_received")

    def _login(self, username: str, password: str) -> None:
        try:
            self.session = self.wms.login(username, password)
        except WmsUnavailable:
            self.online = False
            self._set_error("WMS unreachable")
            return
        except WmsRejected as e:
            self._set_error(_LOGIN_BANNERS.get(e.code, str(e)))
            return
        self.online = True
        evt(log, "login_ok", username=self.session.username,
            device_code=self.session.device_code)
        self._goto(State.IDLE, "login_ok")
        self._sound(SoundCue.READY)
        self.kick_flusher()  # resume any replay that paused on token expiry

    def _submit_scan(self, kind: OpKind, qr_value: str) -> bool:
        """Deliver an authoritative scan transition. True = advance the screen.

        Offline (or with ops already queued, to preserve FIFO order) the scan
        is locally validated by the caller and queued for replay instead.
        """
        assert self.task is not None
        if self.queue.pending_count() > 0:
            self._enqueue_scan(kind, qr_value)
            return True
        try:
            if kind is OpKind.SCAN_LOCATION:
                self.wms.scan_location(self.task.id, qr_value)
            else:
                self.wms.scan_article(self.task.id, qr_value)
        except WmsUnavailable:
            self.online = False
            self._enqueue_scan(kind, qr_value)
            return True
        except WmsAuthError:
            self._session_expired()
            return False
        except WmsRejected as e:
            # Local state disagrees with the server (e.g. task was blocked or
            # reassigned meanwhile): drop the task and start over from IDLE.
            evt(log, "scan_rejected_by_server", _level=logging.ERROR,
                task_id=self.task.id, kind=str(kind), code=e.code)
            self._set_error(f"Task lost ({e.code}) — see supervisor")
            self.task = None
            self._goto(State.IDLE, "task_rejected_by_server")
            return False
        self.online = True
        return True

    def _enqueue_scan(self, kind: OpKind, qr_value: str) -> None:
        assert self.task is not None
        self.queue.enqueue(QueuedOp.scan(kind, self.task.id, qr_value))
        self.queue_depth = self.queue.pending_count()
        self.kick_flusher()
        evt(log, "scan_queued_offline", task_id=self.task.id, kind=str(kind))

    def _confirm_pick(self) -> None:
        assert self.task is not None and self.session is not None
        op = QueuedOp.confirm(self.task.id, str(uuid.uuid4()), self.qty)
        self.queue.enqueue(op)
        self.queue_depth = self.queue.pending_count()
        self.kick_flusher()
        evt(log, "pick_confirmed", task_id=self.task.id, qty=self.qty,
            confirmation_id=op.op_key)
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
        if isinstance(event, SyncFailedEvent):
            self.sync_failed = (event.task_id, event.code)
            self.task = None
            self._goto(State.SYNC_FAILED, "replay_rejected")
            self._sound(SoundCue.ERROR)
            return
        if isinstance(event, AuthRequiredEvent):
            if self.session is not None and self.state is not State.SYNC_FAILED:
                self._session_expired()
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
        if not isinstance(event, ScanEvent):
            return
        kind, username = _parse_payload(event.payload)
        if kind != "OP" or not username:
            self._set_error("Not a badge QR")
            return
        self.badge_username = username
        self._reset_pin()
        evt(log, "badge_scanned", username=username)
        self._goto(State.LOGIN_PIN, "badge_scanned")
        self._sound(SoundCue.BADGE_ACCEPTED)

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
            self._login(self.badge_username, pin)
            if self.state is State.LOGIN_PIN:  # login failed: clear entry
                self._reset_pin()
        elif b is Button.B:
            self.badge_username = ""
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
        if kind == "ART":
            self._set_error("Scan the LOCATION first")
            return
        if kind != "LOC":
            self._set_error("Not a location label")
            return
        if event.payload != self.task.expected_location_qr:
            evt(log, "scan_rejected", _level=logging.WARNING,
                scan_type="location", code=code,
                expected=self.task.location_code)
            self._set_error(f"WRONG LOCATION ({code})")
            return
        if self._submit_scan(OpKind.SCAN_LOCATION, event.payload):
            evt(log, "scan_accepted", scan_type="location", code=code)
            self._goto(State.SCAN_ARTICLE, "location_ok")
            self._sound(SoundCue.LOCATION_ACCEPTED)

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
        if kind != "ART":
            self._set_error("Not an article label")
            return
        if event.payload != self.task.expected_article_qr:
            evt(log, "scan_rejected", _level=logging.WARNING,
                scan_type="article", code=code, expected=self.task.article.sku)
            self._set_error(f"WRONG ARTICLE ({code})")
            return
        if self._submit_scan(OpKind.SCAN_ARTICLE, event.payload):
            evt(log, "scan_accepted", scan_type="article", code=code)
            self.qty = self.task.quantity
            self._goto(State.SET_QUANTITY, "article_ok")
            self._sound(SoundCue.ARTICLE_ACCEPTED)

    def _in_set_quantity(self, event: Event) -> None:
        assert self.task is not None
        if not isinstance(event, ButtonEvent):
            return
        b = event.button
        if b is Button.UP:
            self.qty = min(_QTY_CAP, self.qty + 1)  # uncapped past requested:
            # an over-count must be enterable so the mismatch check can catch it
        elif b is Button.DOWN:
            self.qty = max(0, self.qty - 1)
        elif b is Button.A:
            if self.qty == self.task.quantity:
                self._confirm_pick()
            else:
                evt(log, "count_mismatch", _level=logging.WARNING,
                    task_id=self.task.id, counted=self.qty,
                    requested=self.task.quantity)
                self._goto(State.DISCREPANCY, "count_mismatch")
                self._sound(SoundCue.ERROR)
        elif b is Button.B:
            self._goto(State.SCAN_ARTICLE, "back")

    def _in_discrepancy(self, event: Event) -> None:
        # The WMS accepts only the exact task quantity; a mismatch is a
        # physical problem the supervisor resolves (block task, adjust stock).
        if isinstance(event, ButtonEvent) and event.button is Button.B:
            assert self.task is not None
            self.qty = self.task.quantity
            self._goto(State.SET_QUANTITY, "recount")

    def _in_confirmed(self, event: Event) -> None:
        if isinstance(event, ButtonEvent) and event.button is Button.A:
            self.task = None
            self._goto(State.IDLE, "confirm_ack")

    def _in_sync_failed(self, event: Event) -> None:
        if isinstance(event, ButtonEvent) and event.button is Button.A:
            self.queue.acknowledge_dead()
            self.queue_depth = self.queue.pending_count()
            self.sync_failed = None
            target = State.IDLE if self.session else State.LOGIN_BADGE
            self._goto(target, "sync_failed_acknowledged")
