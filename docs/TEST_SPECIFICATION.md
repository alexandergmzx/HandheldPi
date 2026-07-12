# Functional Test Specification — HandheldPi picking terminal

| | |
|---|---|
| Document | HHT-DOC-TSPEC |
| System under test | HHT app (src/hht) on GamePi20 hardware + mock or real WMS |
| Result recording | one [TEST_REPORT](TEST_REPORT_TEMPLATE.md) per execution round |

**Automation levels** — `pytest`: unit-level, runs anywhere (`python -m pytest`);
`script`: end-to-end through the real app wiring (`hht --script tests/scripts/<f>.txt`,
also run by pytest); `manual`: needs the physical device.

Test data used throughout (built into the mock WMS): operator `OP:1001` (Alice,
PIN 1234); task 1 = location `LOC:A-01-03`, article `ART:8412345678905`
(SKU-4711, Blue T-Shirt M), qty 3.

---

## 00x — Hardware bring-up (manual, provisioning)

### HHT-TC-001 — Display bring-up
**Priority** high · **Type** manual
**Preconditions:** fresh install per DEVICE_CONFIGURATION §3.1–3.3, rebooted.
**Steps:** 1. `cat /sys/class/graphics/fb*/name`. 2. Run the app on the framebuffer.
**Expected:** a panel-mipi-dbi framebuffer exists; UI is landscape, right colors, no
artifacts at the configured SPI clock.

### HHT-TC-002 — Button map
**Priority** high · **Type** manual
**Steps:** run `hht.tools.buttontest`; press all 12 buttons one by one.
**Expected:** every press prints the correct logical name, exactly once (debounce).

### HHT-TC-003 — Camera detection and close focus
**Priority** high · **Type** manual
**Steps:** 1. `rpicam-hello --list-cameras`. 2. `rpicam-still` with continuous AF at
15 cm from the test QR sheet.
**Expected:** `imx708` listed; QR sharp in the capture.

## 01x — Scanner (device, phase 1)

### HHT-TC-010 — Decode latency
**Priority** high · **Type** manual
**Preconditions:** app running with `[scanner] backend = "camera"`.
**Steps:** present a test QR 10–20 cm from the lens 10 times; read `latency_ms` from the
`scan_decoded` log events.
**Expected:** decoded feedback on screen; median latency < 500 ms, no decode > 2 s.

### HHT-TC-011 — Scan debounce
**Priority** medium · **Type** manual
**Steps:** hold the same QR steady in view for 10 s.
**Expected:** exactly one `scan_decoded` event (payload continuously in view never
re-fires); removing it for > `debounce_s` and re-presenting fires again.

## 02x — Login

### HHT-TC-020 — Badge login accepted
**Priority** high · **Type** pytest `test_badge_login_ok`
**Steps:** from LOGIN_BADGE, scan `OP:1001`.
**Expected:** state IDLE; operator name shown in the header.

### HHT-TC-021 — Unknown badge rejected
**Priority** high · **Type** pytest `test_badge_login_unknown_operator`
**Steps:** scan `OP:9999`.
**Expected:** error banner "Unknown operator", state stays LOGIN_BADGE.

### HHT-TC-022 — Non-badge QR at login rejected
**Priority** medium · **Type** pytest `test_non_badge_scan_rejected_at_login`
**Steps:** scan a location QR at the login screen.
**Expected:** "Not a badge QR" error; no login.

### HHT-TC-023 — PIN login accepted
**Priority** high · **Type** pytest `test_pin_login_ok`
**Steps:** X → enter 1-2-3-4 with the D-pad → A.
**Expected:** state IDLE, operator 1001.

### HHT-TC-024 — Wrong PIN rejected and cleared
**Priority** high · **Type** pytest `test_pin_login_wrong_pin_clears_entry`
**Expected:** "Wrong PIN" error, entry reset to 0-0-0-0, state stays LOGIN_PIN.

### HHT-TC-025 — Logout
**Priority** medium · **Type** pytest `test_logout_via_start_hold`
**Steps:** while logged in, hold Start ≥ `hold_start_s`.
**Expected:** session cleared, back to LOGIN_BADGE.

## 03x — Picking workflow

### HHT-TC-030 — Happy-path pick
**Priority** high · **Type** script `happy_path.txt` (+ pytest `test_happy_path_full_pick`)
**Steps:** login → A (next task) → scan correct location → scan correct article →
A (confirm at requested qty).
**Expected:** states advance IDLE → GOTO_LOCATION → SCAN_ARTICLE → SET_QUANTITY →
CONFIRMED; confirmation delivered to WMS with qty 3, `shortPick=false`; banner returns
to IDLE.

### HHT-TC-031 — No task available
**Priority** medium · **Type** pytest `test_no_task_available`
**Expected:** NO_TASK screen, A retries.

### HHT-TC-032 — Wrong location rejected
**Priority** high · **Type** script `wrong_scans.txt`
**Steps:** in GOTO_LOCATION scan `LOC:Z-99-99`.
**Expected:** "WRONG LOCATION" banner with the scanned code; state unchanged; a
`scan_rejected` event with expected vs. actual codes is logged.

### HHT-TC-033 — Wrong article rejected
**Priority** high · **Type** script `wrong_scans.txt`
**Steps:** in SCAN_ARTICLE scan a wrong EAN.
**Expected:** "WRONG ARTICLE" banner; state unchanged.

### HHT-TC-034 — Bare EAN accepted as article
**Priority** medium · **Type** pytest `test_bare_ean_accepted_as_article`
**Expected:** scanning the article's EAN without the `ART:` prefix (a real printed
barcode) advances to SET_QUANTITY.

### HHT-TC-035 — Quantity limits
**Priority** high · **Type** pytest `test_quantity_clamping`
**Expected:** quantity starts at requested; cannot exceed requested; cannot go below 0.

### HHT-TC-036 — Short pick recorded
**Priority** high · **Type** pytest `test_short_pick_flagged`
**Expected:** confirming below requested qty sets `shortPick=true`, `qtyPicked` correct.

### HHT-TC-037 — Short pick blocked by configuration
**Priority** medium · **Type** pytest `test_short_pick_blocked_when_disallowed`
**Preconditions:** `allow_short_pick = false`.
**Expected:** confirm refused with error; state stays SET_QUANTITY.

### HHT-TC-038 — Status overlay
**Priority** low · **Type** pytest `test_status_overlay_swallows_buttons`
**Expected:** Select toggles the status screen; workflow buttons are inert while shown.

## 04x — Offline resilience / WMS integration

### HHT-TC-040 — Offline confirmation queues and re-sends
**Priority** high · **Type** script `offline_pick.txt`
**Steps:** complete a pick while the WMS is unreachable; attempt delivery; restore the
WMS; deliver again.
**Expected:** CONFIRMED shown immediately (operator never blocked); queue = 1; delivery
while offline loses nothing; after reconnect the queue drains to 0 and the WMS holds
exactly one confirmation.

### HHT-TC-041 — WMS down at login
**Priority** high · **Type** pytest `test_wms_down_at_login_shows_error`
**Expected:** "WMS unreachable" error, OFFLINE indicator, no crash.

### HHT-TC-042 — Queue survives power loss
**Priority** high · **Type** pytest `test_persistence_across_reopen` + manual on device
**Steps (manual):** confirm a pick with WiFi off, hard power-cut the unit, boot, restore
WiFi.
**Expected:** the confirmation reaches the WMS after boot; exactly once
(idempotency key).

### HHT-TC-043 — Business rejection is not retried
**Priority** medium · **Type** pytest `test_rejected_confirmation_is_not_retried`
**Expected:** a 4xx-rejected confirmation is marked failed-final with the error stored,
and does not block the queue.

### HHT-TC-044 — Duplicate confirmation suppressed
**Priority** medium · **Type** pytest `test_duplicate_idempotency_key_ignored`,
`test_confirm_is_idempotent`
**Expected:** same idempotency key never enqueued or accepted twice.

## 05x — Service & operations (manual, device)

### HHT-TC-050 — Autostart
**Priority** high · **Type** manual
**Steps:** `systemctl enable --now hht`, reboot.
**Expected:** login screen on the LCD without operator intervention;
`systemctl status hht` active.

### HHT-TC-051 — Crash recovery
**Priority** medium · **Type** manual
**Steps:** `sudo pkill -9 -f "python -m hht"`.
**Expected:** systemd restarts the app within ~5 s; a fresh `app_started` event logged.

### HHT-TC-052 — Log analyzability
**Priority** medium · **Type** manual
**Steps:** after a test shift, run `python -m hht.tools.logreport /var/log/hht/hht.jsonl`.
**Expected:** every line parses as JSON; report shows pick count, rejects and offline
windows consistent with what was done.

## Traceability

| Requirement | Covered by |
|---|---|
| Operator login (badge + PIN) | TC-020…025 |
| Guided pick with scan validation | TC-030…038 |
| Wrong scans produce errors | TC-032, TC-033 |
| Offline queue + re-send | TC-040…044 |
| Fully configurable device | TC-002 (pin remap), config tests in `tests/test_config.py` |
| Auto-start terminal appliance | TC-050, TC-051 |
| Analyzable logs | TC-052 |
