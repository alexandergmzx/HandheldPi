"""Unit tests for the picking workflow. Test IDs reference docs/TEST_SPECIFICATION.md."""

from hht.audio import SoundCue
from hht.events import AuthRequiredEvent, Button, ButtonEvent, NetStatusEvent, \
    ScanEvent, SyncFailedEvent, TickEvent
from hht.state_machine import State

TASK1_LOC = "A-01-03"
TASK1_ART = "ART-SHIRT"


def press(sm, button, action="press"):
    sm.handle(ButtonEvent(Button(button), action))


def login(env, username="picker01", pin=(1, 2, 3, 4)):
    env.sm.handle(TickEvent())
    env.sm.handle(ScanEvent(f"OP:{username}"))
    assert env.sm.state is State.LOGIN_PIN
    env.sm.pin_digits = list(pin)
    press(env.sm, "a")
    assert env.sm.state is State.IDLE


def start_task(env):
    login(env)
    press(env.sm, "a")
    assert env.sm.state is State.GOTO_LOCATION


def scan_to_quantity(env):
    start_task(env)
    env.sm.handle(ScanEvent(f"LOC:{TASK1_LOC}"))
    env.sm.handle(ScanEvent(f"ART:{TASK1_ART}"))
    assert env.sm.state is State.SET_QUANTITY


def drain(env):
    result = env.queue.flush(env.wms)
    return result


# -- login (HHT-TC-02x) -------------------------------------------------------

def test_startup_reaches_login(env):
    assert env.sm.state is State.STARTUP
    env.sm.handle(TickEvent())
    assert env.sm.state is State.LOGIN_BADGE


def test_badge_then_pin_login_ok(env):
    login(env)
    assert env.sm.session.username == "picker01"


def test_badge_scan_alone_does_not_authenticate(env):
    env.sm.handle(TickEvent())
    env.sm.handle(ScanEvent("OP:picker01"))
    assert env.sm.state is State.LOGIN_PIN
    assert env.sm.session is None


def test_wrong_pin_clears_entry_and_stays(env):
    env.sm.handle(TickEvent())
    env.sm.handle(ScanEvent("OP:picker01"))
    env.sm.pin_digits = [9, 9, 9, 9]
    press(env.sm, "a")
    assert env.sm.state is State.LOGIN_PIN
    assert "Wrong badge/PIN" in env.sm.error_text
    assert env.sm.pin_digits == [0, 0, 0, 0]


def test_unknown_badge_rejected_at_pin_submit(env):
    env.sm.handle(TickEvent())
    env.sm.handle(ScanEvent("OP:ghost"))
    env.sm.pin_digits = [1, 2, 3, 4]
    press(env.sm, "a")
    assert env.sm.state is State.LOGIN_PIN
    assert "Wrong badge/PIN" in env.sm.error_text


def test_non_badge_scan_rejected_at_login(env):
    env.sm.handle(TickEvent())
    env.sm.handle(ScanEvent("LOC:A-01-03"))
    assert env.sm.state is State.LOGIN_BADGE
    assert "badge" in env.sm.error_text.lower()


def test_pin_entry_buttons(env):
    env.sm.handle(TickEvent())
    env.sm.handle(ScanEvent("OP:picker01"))
    for digit in (1, 2, 3, 4):
        for _ in range(digit):
            press(env.sm, "up")
        press(env.sm, "right")
    press(env.sm, "a")
    assert env.sm.state is State.IDLE


def test_pin_back_returns_to_badge(env):
    env.sm.handle(TickEvent())
    env.sm.handle(ScanEvent("OP:picker01"))
    press(env.sm, "b")
    assert env.sm.state is State.LOGIN_BADGE
    assert env.sm.badge_username == ""


def test_logout_via_start_hold(env):
    login(env)
    press(env.sm, "start", action="hold")
    assert env.sm.state is State.LOGIN_BADGE
    assert env.sm.session is None


def test_logout_blocked_while_queue_pending(env):
    scan_to_quantity(env)
    env.wms.offline = True
    press(env.sm, "a")  # confirm queues
    assert env.queue.pending_count() == 1
    press(env.sm, "start", action="hold")
    assert env.sm.session is not None  # still logged in
    assert "cannot log out" in env.sm.error_text.lower()


# -- picking workflow (HHT-TC-03x) ---------------------------------------------

def test_happy_path_full_pick(env):
    scan_to_quantity(env)
    assert env.sm.qty == 3  # prefilled with the requested quantity
    press(env.sm, "a")
    assert env.sm.state is State.CONFIRMED
    assert env.queue.pending_count() == 1  # confirm is always queued

    drain(env)
    assert env.queue.pending_count() == 0
    task_id, _, qty = env.wms.confirmed[0]
    assert (task_id, qty) == (101, 3)

    env.clock.t += 5  # confirmation banner times out back to IDLE
    env.sm.handle(TickEvent())
    assert env.sm.state is State.IDLE
    assert env.sm.task is None


def test_online_scans_reach_the_server(env):
    scan_to_quantity(env)
    # both scans were delivered live: the server task is ARTICLE_CONFIRMED
    assert env.wms._find(101).state == "ARTICLE_CONFIRMED"
    assert env.queue.pending_count() == 0


def test_semantic_sound_cues_follow_workflow_outcomes(env):
    login(env)
    press(env.sm, "a")
    env.sm.handle(ScanEvent(f"LOC:{TASK1_LOC}"))
    env.sm.handle(ScanEvent("ART:WRONG-SKU"))
    env.sm.handle(ScanEvent(f"ART:{TASK1_ART}"))
    press(env.sm, "a")

    assert env.sounds == [
        SoundCue.READY,           # startup done
        SoundCue.BADGE_ACCEPTED,  # badge read
        SoundCue.READY,           # login ok
        SoundCue.LOCATION_ACCEPTED,
        SoundCue.ERROR,
        SoundCue.ARTICLE_ACCEPTED,
        SoundCue.CONFIRMED,
    ]


def test_offline_cue_only_fires_on_transition(env):
    env.sm.handle(NetStatusEvent(False))
    env.sm.handle(NetStatusEvent(False))
    assert env.sounds == [SoundCue.OFFLINE]


def test_sound_adapter_failure_does_not_break_workflow(env):
    def broken_sound(cue):
        raise RuntimeError("speaker missing")

    env.sm.play_sound = broken_sound
    env.sm.handle(TickEvent())
    assert env.sm.state is State.LOGIN_BADGE


def test_wrong_location_rejected_locally(env):
    start_task(env)
    env.sm.handle(ScanEvent("LOC:Z-99-99"))
    assert env.sm.state is State.GOTO_LOCATION
    assert "WRONG LOCATION" in env.sm.error_text
    assert env.wms._find(101).state == "ASSIGNED"  # nothing reached the server


def test_article_scan_in_location_state_hints(env):
    start_task(env)
    env.sm.handle(ScanEvent(f"ART:{TASK1_ART}"))
    assert env.sm.state is State.GOTO_LOCATION
    assert "LOCATION first" in env.sm.error_text


def test_wrong_article_rejected(env):
    start_task(env)
    env.sm.handle(ScanEvent(f"LOC:{TASK1_LOC}"))
    env.sm.handle(ScanEvent("ART:WRONG-SKU"))
    assert env.sm.state is State.SCAN_ARTICLE
    assert "WRONG ARTICLE" in env.sm.error_text


def test_bare_ean_no_longer_accepted(env):
    start_task(env)
    env.sm.handle(ScanEvent(f"LOC:{TASK1_LOC}"))
    env.sm.handle(ScanEvent("8412345678905"))  # v1 payloads are exact ART:<sku>
    assert env.sm.state is State.SCAN_ARTICLE
    assert "article label" in env.sm.error_text.lower()


def test_count_can_exceed_requested_but_never_confirms(env):
    scan_to_quantity(env)
    press(env.sm, "up")
    assert env.sm.qty == 4  # over-count must be enterable to be detectable
    press(env.sm, "a")
    assert env.sm.state is State.DISCREPANCY
    assert env.queue.pending_count() == 0


def test_short_count_goes_to_discrepancy_and_recount(env):
    scan_to_quantity(env)
    press(env.sm, "down")
    press(env.sm, "a")
    assert env.sm.state is State.DISCREPANCY
    press(env.sm, "b")  # recount resets to the requested quantity
    assert env.sm.state is State.SET_QUANTITY
    assert env.sm.qty == 3
    press(env.sm, "a")
    assert env.sm.state is State.CONFIRMED


def test_no_task_available(env):
    env.wms._tasks = []
    login(env)
    press(env.sm, "a")
    assert env.sm.state is State.NO_TASK


def test_fetch_resumes_mid_state_task(env):
    login(env)
    # the server already holds this task at LOCATION_CONFIRMED for us
    env.wms.next_task()
    env.wms.scan_location(101, f"LOC:{TASK1_LOC}")
    press(env.sm, "a")
    assert env.sm.state is State.SCAN_ARTICLE  # resumed, not restarted


def test_fetch_refused_while_sync_pending(env):
    scan_to_quantity(env)
    env.wms.offline = True
    press(env.sm, "a")  # confirm queues
    env.clock.t += 5
    env.sm.handle(TickEvent())  # banner timeout -> IDLE
    assert env.sm.state is State.IDLE
    press(env.sm, "a")  # try to claim the next task
    assert env.sm.state is State.IDLE
    assert "Sync pending" in env.sm.error_text


def test_status_overlay_swallows_buttons(env):
    login(env)
    press(env.sm, "select")
    assert env.sm.show_status
    press(env.sm, "a")  # must NOT fetch a task while the overlay is up
    assert env.sm.state is State.IDLE
    press(env.sm, "select")
    assert not env.sm.show_status


# -- offline behaviour (HHT-TC-04x) ---------------------------------------------

def test_offline_pick_queues_ordered_chain_and_replays(env):
    start_task(env)
    env.wms.offline = True
    env.sm.handle(ScanEvent(f"LOC:{TASK1_LOC}"))  # local validation, queued
    assert env.sm.state is State.SCAN_ARTICLE
    env.sm.handle(ScanEvent(f"ART:{TASK1_ART}"))
    assert env.sm.state is State.SET_QUANTITY
    press(env.sm, "a")
    assert env.sm.state is State.CONFIRMED  # operator never blocked
    assert env.queue.pending_count() == 3

    assert drain(env).sent == 0  # still offline
    env.wms.offline = False
    result = drain(env)
    assert result.sent == 3  # FIFO: scan-loc, scan-art, confirm
    assert env.wms._find(101).state == "COMPLETED"


def test_scans_stay_queued_once_chain_started(env):
    """Order preservation: after an offline scan, later ops must queue too,
    even if connectivity returns before the queue has drained."""
    start_task(env)
    env.wms.offline = True
    env.sm.handle(ScanEvent(f"LOC:{TASK1_LOC}"))
    env.wms.offline = False  # WiFi back, but scan-location not yet replayed
    env.sm.handle(ScanEvent(f"ART:{TASK1_ART}"))
    assert env.queue.pending_count() == 2  # article queued behind location
    assert drain(env).sent == 2


def test_replay_rejection_surfaces_sync_failed(env):
    start_task(env)
    env.wms.offline = True
    env.sm.handle(ScanEvent(f"LOC:{TASK1_LOC}"))
    env.sm.handle(ScanEvent(f"ART:{TASK1_ART}"))
    press(env.sm, "a")
    env.wms.block_current_task()  # admin blocked it while we were offline
    env.wms.offline = False
    result = drain(env)
    assert result.failed_code == "INVALID_TASK_STATE"
    env.sm.handle(SyncFailedEvent(result.failed_task_id, result.failed_code))
    assert env.sm.state is State.SYNC_FAILED
    assert env.queue.dead_count() == 3
    press(env.sm, "a")  # acknowledge
    assert env.sm.state is State.IDLE
    assert env.queue.dead_count() == 0


def test_live_scan_rejected_by_server_drops_task(env):
    start_task(env)
    env.wms.block_current_task()  # blocked while the operator walks the aisle
    env.sm.handle(ScanEvent(f"LOC:{TASK1_LOC}"))
    assert env.sm.state is State.IDLE
    assert env.sm.task is None
    assert "INVALID_TASK_STATE" in env.sm.error_text


def test_auth_required_drops_to_login_and_keeps_queue(env):
    scan_to_quantity(env)
    env.wms.offline = True
    press(env.sm, "a")
    assert env.queue.pending_count() == 1
    env.sm.handle(AuthRequiredEvent())
    assert env.sm.state is State.LOGIN_BADGE
    assert env.sm.session is None
    assert env.queue.pending_count() == 1  # queue kept for replay after re-login


def test_expired_token_on_live_call_drops_to_login(env):
    start_task(env)
    env.wms.expire_token()
    env.sm.handle(ScanEvent(f"LOC:{TASK1_LOC}"))
    assert env.sm.state is State.LOGIN_BADGE
    assert "expired" in env.sm.error_text.lower()


def test_wms_down_at_login_shows_error(env):
    env.wms.offline = True
    env.sm.handle(TickEvent())
    env.sm.handle(ScanEvent("OP:picker01"))
    env.sm.pin_digits = [1, 2, 3, 4]
    press(env.sm, "a")
    assert env.sm.state is State.LOGIN_PIN
    assert "unreachable" in env.sm.error_text.lower()
    assert env.sm.online is False


def test_error_banner_expires(env):
    start_task(env)
    env.sm.handle(ScanEvent("LOC:Z-99-99"))
    assert env.sm.error_text
    env.clock.t += env.cfg.workflow.error_banner_s + 0.1
    env.sm.handle(TickEvent())
    assert env.sm.error_text is None


# -- scan feedback + accept flash (HHT-TC-01x) --------------------------------

def test_scan_feedback_records_last_decode(env):
    start_task(env)
    env.sm.handle(ScanEvent(f"LOC:{TASK1_LOC}", symbology="QRCODE", latency_ms=88))
    fb = env.sm.scan_feedback
    assert fb is not None
    assert fb.payload == f"LOC:{TASK1_LOC}"
    assert fb.symbology == "QRCODE"
    assert fb.latency_ms == 88


def test_scan_feedback_dwell_expires(env):
    start_task(env)
    env.sm.handle(ScanEvent(f"LOC:{TASK1_LOC}"))
    assert env.sm.scan_feedback is not None
    env.clock.t += env.cfg.scanner.feedback_s + 0.1
    assert env.sm.scan_feedback is None
    assert env.sm.last_scan is not None  # retained for the status overlay


def test_rejected_scan_still_shows_in_feedback(env):
    start_task(env)
    env.sm.handle(ScanEvent("LOC:Z-99-99"))  # wrong location, rejected
    assert env.sm.error_text
    assert env.sm.scan_feedback is not None
    assert env.sm.scan_feedback.payload == "LOC:Z-99-99"


def test_accept_flash_fires_on_accepted_scan_and_expires(env):
    start_task(env)
    env.clock.t += env.cfg.scanner.accept_flash_s + 0.01  # clear the login flash
    assert env.sm.invert_active is False
    env.sm.handle(ScanEvent(f"LOC:{TASK1_LOC}"))
    assert env.sm.state is State.SCAN_ARTICLE
    assert env.sm.invert_active is True
    env.clock.t += env.cfg.scanner.accept_flash_s + 0.01
    assert env.sm.invert_active is False


def test_rejected_scan_does_not_flash(env):
    start_task(env)
    env.clock.t += env.cfg.scanner.accept_flash_s + 0.01  # clear the login flash
    env.sm.handle(ScanEvent("LOC:Z-99-99"))
    assert env.sm.invert_active is False


def test_badge_scan_flashes(env):
    env.sm.handle(TickEvent())
    env.sm.handle(ScanEvent("OP:picker01"))
    assert env.sm.state is State.LOGIN_PIN
    assert env.sm.invert_active is True
