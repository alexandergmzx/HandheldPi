"""Unit tests for the picking workflow. Test IDs reference docs/TEST_SPECIFICATION.md."""

from hht.events import Button, ButtonEvent, ScanEvent, TickEvent
from hht.state_machine import State

TASK1_LOC = "A-01-03"
TASK1_ART = "8412345678905"


def press(sm, button, action="press"):
    sm.handle(ButtonEvent(Button(button), action))


def login(env):
    env.sm.handle(TickEvent())
    env.sm.handle(ScanEvent("OP:1001"))
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


# -- login (HHT-TC-02x) -------------------------------------------------------

def test_startup_reaches_login(env):
    assert env.sm.state is State.STARTUP
    env.sm.handle(TickEvent())
    assert env.sm.state is State.LOGIN_BADGE


def test_badge_login_ok(env):
    login(env)
    assert env.sm.session.operator_name == "Alice"


def test_badge_login_unknown_operator(env):
    env.sm.handle(TickEvent())
    env.sm.handle(ScanEvent("OP:9999"))
    assert env.sm.state is State.LOGIN_BADGE
    assert "Unknown operator" in env.sm.error_text


def test_non_badge_scan_rejected_at_login(env):
    env.sm.handle(TickEvent())
    env.sm.handle(ScanEvent("LOC:A-01-03"))
    assert env.sm.state is State.LOGIN_BADGE
    assert "badge" in env.sm.error_text.lower()


def test_pin_login_ok(env):
    env.sm.handle(TickEvent())
    press(env.sm, "x")
    assert env.sm.state is State.LOGIN_PIN
    for digit in (1, 2, 3, 4):  # Alice's PIN in dev.toml
        for _ in range(digit):
            press(env.sm, "up")
        press(env.sm, "right")
    press(env.sm, "a")
    assert env.sm.state is State.IDLE
    assert env.sm.session.operator_id == "1001"


def test_pin_login_wrong_pin_clears_entry(env):
    env.sm.handle(TickEvent())
    press(env.sm, "x")
    press(env.sm, "up")  # PIN 1000 — wrong
    press(env.sm, "a")
    assert env.sm.state is State.LOGIN_PIN
    assert "Wrong PIN" in env.sm.error_text
    assert env.sm.pin_digits == [0, 0, 0, 0]


def test_logout_via_start_hold(env):
    login(env)
    press(env.sm, "start", action="hold")
    assert env.sm.state is State.LOGIN_BADGE
    assert env.sm.session is None


# -- picking workflow (HHT-TC-03x) ---------------------------------------------

def test_happy_path_full_pick(env):
    scan_to_quantity(env)
    assert env.sm.qty == 3  # prefilled with requested qty
    press(env.sm, "a")
    assert env.sm.state is State.CONFIRMED
    assert env.queue.pending_count() == 1

    env.queue.flush(env.wms)
    assert env.queue.pending_count() == 0
    conf = env.wms.confirmed[0]
    assert (conf.qty_picked, conf.short_pick) == (3, False)
    assert conf.location_code == TASK1_LOC

    env.clock.t += 5  # confirmation banner times out back to IDLE
    env.sm.handle(TickEvent())
    assert env.sm.state is State.IDLE
    assert env.sm.task is None


def test_wrong_location_rejected(env):
    start_task(env)
    env.sm.handle(ScanEvent("LOC:Z-99-99"))
    assert env.sm.state is State.GOTO_LOCATION
    assert "WRONG LOCATION" in env.sm.error_text


def test_article_scan_in_location_state_hints(env):
    start_task(env)
    env.sm.handle(ScanEvent(f"ART:{TASK1_ART}"))
    assert env.sm.state is State.GOTO_LOCATION
    assert "LOCATION first" in env.sm.error_text


def test_wrong_article_rejected(env):
    start_task(env)
    env.sm.handle(ScanEvent(f"LOC:{TASK1_LOC}"))
    env.sm.handle(ScanEvent("ART:0000000000000"))
    assert env.sm.state is State.SCAN_ARTICLE
    assert "WRONG ARTICLE" in env.sm.error_text


def test_bare_ean_accepted_as_article(env):
    start_task(env)
    env.sm.handle(ScanEvent(f"LOC:{TASK1_LOC}"))
    env.sm.handle(ScanEvent(TASK1_ART))  # no ART: prefix, e.g. printed EAN barcode
    assert env.sm.state is State.SET_QUANTITY


def test_quantity_clamping(env):
    scan_to_quantity(env)
    press(env.sm, "up")
    assert env.sm.qty == 3  # cannot exceed requested
    for _ in range(10):
        press(env.sm, "down")
    assert env.sm.qty == 0  # cannot go negative


def test_short_pick_flagged(env):
    scan_to_quantity(env)
    press(env.sm, "down")
    press(env.sm, "a")
    env.queue.flush(env.wms)
    conf = env.wms.confirmed[0]
    assert (conf.qty_picked, conf.short_pick) == (2, True)


def test_short_pick_blocked_when_disallowed(env):
    env.cfg.workflow.allow_short_pick = False
    scan_to_quantity(env)
    press(env.sm, "down")
    press(env.sm, "a")
    assert env.sm.state is State.SET_QUANTITY
    assert "Short pick" in env.sm.error_text


def test_no_task_available(env):
    env.wms._templates = []
    login(env)
    press(env.sm, "a")
    assert env.sm.state is State.NO_TASK


def test_status_overlay_swallows_buttons(env):
    login(env)
    press(env.sm, "select")
    assert env.sm.show_status
    press(env.sm, "a")  # must NOT fetch a task while the overlay is up
    assert env.sm.state is State.IDLE
    press(env.sm, "select")
    assert not env.sm.show_status


# -- offline behaviour (HHT-TC-04x) ---------------------------------------------

def test_offline_confirm_queues_and_recovers(env):
    scan_to_quantity(env)
    env.wms.offline = True
    press(env.sm, "a")
    assert env.sm.state is State.CONFIRMED  # workflow never blocks on the network
    assert env.queue.pending_count() == 1

    assert env.queue.flush(env.wms) == 0  # still offline: nothing delivered
    assert env.queue.pending_count() == 1

    env.wms.offline = False
    assert env.queue.flush(env.wms) == 1
    assert env.queue.pending_count() == 0
    assert len(env.wms.confirmed) == 1


def test_wms_down_at_login_shows_error(env):
    env.wms.offline = True
    env.sm.handle(TickEvent())
    env.sm.handle(ScanEvent("OP:1001"))
    assert env.sm.state is State.LOGIN_BADGE
    assert "unreachable" in env.sm.error_text.lower()
    assert env.sm.online is False


def test_error_banner_expires(env):
    start_task(env)
    env.sm.handle(ScanEvent("LOC:Z-99-99"))
    assert env.sm.error_text
    env.clock.t += env.cfg.workflow.error_banner_s + 0.1
    env.sm.handle(TickEvent())
    assert env.sm.error_text is None
