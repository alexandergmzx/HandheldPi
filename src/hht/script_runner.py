"""Deterministic functional-test driver: feeds scripted events straight into the
state machine (no threads, fake clock) and asserts on the outcome.

Grammar (one command per line, '#' comments):

    tick                    advance one tick (also leaves STARTUP)
    press <button>          up|down|left|right|a|b|x|y|l|r|start|select
    hold <button>           e.g. `hold start` = logout
    scan <payload>          e.g. `scan LOC:A-01-03`
    pin <digits>            set the PIN entry directly (LOGIN_PIN only; the
                            button-by-button path is covered in happy_path)
    wait <seconds>          advance the FAKE clock, then tick (no real sleeping)
    wms <online|offline>    toggle mock-WMS availability (mock backend only)
    wms block_task          admin blocks the active mock task (replay rejection)
    wms expire_token        invalidate the mock session token (re-login needed)
    flush                   one delivery pass; feeds the resulting queue-depth /
                            sync-failed / auth-required events into the machine
    reset_queue             empty the offline queue (start from a known state)
    expect_state <STATE>    assert current state, e.g. GOTO_LOCATION
    expect_error <substr>   assert the visible error banner contains <substr>
    expect_no_error         assert no error banner is shown
    expect_queue <n>        assert n operations are pending
    expect_dead <n>         assert n operations are dead-lettered
    expect_sound <cue>      assert the latest semantic sound cue

Each command renders a frame; with `[display] backend = "image"` every step is
saved as a numbered PNG — ready-made evidence for the test report.

Exit code 0 = PASS, 1 = FAIL (first failing line reported).
"""

from __future__ import annotations

import logging
from pathlib import Path

from .audio import SoundCue
from .config import AppConfig
from .events import AuthRequiredEvent, Button, ButtonEvent, NetStatusEvent, \
    QueueDepthEvent, ScanEvent, SyncFailedEvent, TickEvent
from .logsetup import evt
from .state_machine import PickingStateMachine, State
from .ui import make_display
from .ui.screens import render
from .wms import make_wms_client
from .wms.mock_client import MockWmsClient
from .wms.offline_queue import OfflineQueue

log = logging.getLogger("hht.script")


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


class ScriptFailure(Exception):
    pass


def run_script(cfg: AppConfig, script_path: str | Path) -> int:
    script_path = Path(script_path)
    clock = FakeClock()
    wms = make_wms_client(cfg)
    queue = OfflineQueue(cfg.queue.db_path)
    sounds: list[SoundCue] = []
    sm = PickingStateMachine(cfg, wms, queue, play_sound=sounds.append, clock=clock)
    display = make_display(cfg)
    evt(log, "script_started", script=str(script_path))

    try:
        for lineno, raw in enumerate(script_path.read_text().splitlines(), start=1):
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            try:
                _execute(line, sm, wms, queue, clock, sounds)
            except ScriptFailure as e:
                print(f"FAIL {script_path}:{lineno}: {line!r} — {e}")
                evt(log, "script_failed", _level=logging.ERROR,
                    line=lineno, command=line, reason=str(e))
                return 1
            display.show(render(sm), tag=f"L{lineno:02d}_{line[:40]}")
    finally:
        display.close()
        queue.close()

    print(f"PASS {script_path}")
    evt(log, "script_passed", script=str(script_path))
    return 0


def _execute(line: str, sm: PickingStateMachine, wms, queue: OfflineQueue,
             clock: FakeClock, sounds: list[SoundCue] | None = None) -> None:
    sounds = sounds if sounds is not None else []
    cmd, _, arg = line.partition(" ")
    arg = arg.strip()

    if cmd == "tick":
        sm.handle(TickEvent())
    elif cmd in ("press", "hold"):
        try:
            button = Button(arg.lower())
        except ValueError:
            raise ScriptFailure(f"unknown button '{arg}'") from None
        sm.handle(ButtonEvent(button, "hold" if cmd == "hold" else "press"))
    elif cmd == "scan":
        if not arg:
            raise ScriptFailure("scan needs a payload")
        sm.handle(ScanEvent(arg))
    elif cmd == "pin":
        if sm.state is not State.LOGIN_PIN:
            raise ScriptFailure(f"'pin' needs LOGIN_PIN state, machine is {sm.state.value}")
        if not arg.isdigit() or len(arg) != len(sm.pin_digits):
            raise ScriptFailure(f"'pin' needs exactly {len(sm.pin_digits)} digits")
        sm.pin_digits = [int(ch) for ch in arg]
    elif cmd == "wait":
        clock.t += float(arg)
        sm.handle(TickEvent())
    elif cmd == "wms":
        if not isinstance(wms, MockWmsClient):
            raise ScriptFailure("'wms' command needs [wms] backend = \"mock\"")
        if arg in ("online", "offline"):
            wms.offline = arg == "offline"
            sm.handle(NetStatusEvent(not wms.offline))
        elif arg == "block_task":
            wms.block_current_task()
        elif arg == "expire_token":
            wms.expire_token()
        else:
            raise ScriptFailure(f"unknown wms action '{arg}'")
    elif cmd == "flush":
        # Mirror what Flusher posts, but synchronously and deterministically.
        result = queue.flush(wms)
        if result.auth_required:
            sm.handle(AuthRequiredEvent())
        if result.failed_code:
            sm.handle(SyncFailedEvent(result.failed_task_id or 0, result.failed_code))
        sm.handle(QueueDepthEvent(queue.pending_count()))
    elif cmd == "reset_queue":
        queue.clear_all()
        sm.handle(QueueDepthEvent(0))
    elif cmd == "expect_state":
        if sm.state.value != arg:
            raise ScriptFailure(f"state is {sm.state.value}, expected {arg}")
    elif cmd == "expect_error":
        shown = sm.error_text or ""
        if arg.lower() not in shown.lower():
            raise ScriptFailure(f"error banner is {shown!r}, expected to contain {arg!r}")
    elif cmd == "expect_no_error":
        if sm.error_text:
            raise ScriptFailure(f"unexpected error banner: {sm.error_text!r}")
    elif cmd == "expect_queue":
        pending = queue.pending_count()
        if pending != int(arg):
            raise ScriptFailure(f"queue has {pending} pending, expected {arg}")
    elif cmd == "expect_dead":
        dead = queue.dead_count()
        if dead != int(arg):
            raise ScriptFailure(f"queue has {dead} dead, expected {arg}")
    elif cmd == "expect_sound":
        try:
            expected = SoundCue(arg)
        except ValueError:
            raise ScriptFailure(f"unknown sound cue '{arg}'") from None
        if not sounds:
            raise ScriptFailure(f"no sound emitted, expected {expected.value}")
        if sounds[-1] is not expected:
            raise ScriptFailure(
                f"latest sound is {sounds[-1].value}, expected {expected.value}"
            )
    elif cmd == "quit":
        pass
    else:
        raise ScriptFailure(f"unknown command '{cmd}'")
