# Functional Test Specification — HandheldPi picking terminal

| | |
|---|---|
| Document | HHT-DOC-TSPEC |
| System under test | HHT app (src/hht) on GamePi20 hardware + mock or real WMS |
| Result recording | one [TEST_REPORT](TEST_REPORT_TEMPLATE.md) per execution round |

**Automation levels** — `pytest`: unit-level, runs anywhere (`python -m pytest`);
`script`: end-to-end through the real app wiring (`hht --script tests/scripts/<f>.txt`,
also run by pytest); `manual`: needs the physical device.

Test data used throughout (built into the mock WMS): operator badge
`OP:picker01` (PIN/password 1234); task 101 = location `LOC:A-01-03`, article
`ART:ART-SHIRT` (Blue T-Shirt M), qty 3. Loopback runs against a real WMS dev
instance use its V1_2 seed instead: badge `OP:picker02` (PIN 2468), device
`HHT-DEV-01`, task data from `V1_1__seed_demo_data.sql`.

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

### HHT-TC-004 — GamePi20 audio routing and noise baseline
**Priority** high · **Type** manual
**Steps:** execute DEVICE_CONFIGURATION §3.5.1 on battery and clean USB power, then
play `assets/sounds/ready.wav` through the configured ALSA device and repeat the Up/Right
button checks.
**Expected:** PWM audio is on GPIO18; ready cue is clean at low volume; GPIO12/GPIO13
remain buttons; switch-time versus sustained noise is recorded rather than guessed.

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

### HHT-TC-012 — Semantic scan sounds
**Priority** medium · **Type** pytest `test_semantic_sound_cues_follow_workflow_outcomes`
+ script `happy_path.txt` / `wrong_scans.txt` · **Manual** speaker check
**Expected:** accepted badge, location, and article have distinct cues; a rejected or
out-of-order scan uses the error cue; confirmation is distinct. Raw decode does not
produce an optimistic cue before workflow validation.

### HHT-TC-013 — Audio is non-blocking and fail-open
**Priority** high · **Type** pytest `test_alsa_play_is_non_blocking_and_uses_configured_device`,
`test_sound_adapter_failure_does_not_break_workflow`
**Expected:** queuing a cue returns immediately; missing/broken playback never changes
workflow state or crashes the application.

## 02x — Login (badge = username, PIN = password; WMS v1 `POST /auth/login`)

### HHT-TC-020 — Badge scan leads to PIN entry, never authenticates alone
**Priority** high · **Type** pytest `test_badge_scan_alone_does_not_authenticate`
**Steps:** from LOGIN_BADGE, scan `OP:picker01`.
**Expected:** state LOGIN_PIN showing the badge username; no session yet.

### HHT-TC-021 — Unknown badge rejected at submit
**Priority** high · **Type** pytest `test_unknown_badge_rejected_at_pin_submit`
**Steps:** scan `OP:ghost`, enter any PIN, press A.
**Expected:** "Wrong badge/PIN" banner (the WMS answers `401
INVALID_CREDENTIALS`); state stays LOGIN_PIN.

### HHT-TC-022 — Non-badge QR at login rejected
**Priority** medium · **Type** pytest `test_non_badge_scan_rejected_at_login`
**Steps:** scan a location QR at the login screen.
**Expected:** "Not a badge QR" error; no login.

### HHT-TC-023 — Badge + PIN login accepted
**Priority** high · **Type** pytest `test_pin_entry_buttons`,
`test_badge_then_pin_login_ok` + script `happy_path.txt`
**Steps:** scan `OP:picker01` → enter 1-2-3-4 with the D-pad → A.
**Expected:** one `POST /auth/login {username, password, deviceCode}`; state
IDLE; username in the header.

### HHT-TC-024 — Wrong PIN rejected and cleared
**Priority** high · **Type** pytest `test_wrong_pin_clears_entry_and_stays`
**Expected:** "Wrong badge/PIN" error, entry reset to 0-0-0-0, state stays
LOGIN_PIN; B returns to LOGIN_BADGE (`test_pin_back_returns_to_badge`).

### HHT-TC-025 — Logout revokes the session
**Priority** medium · **Type** pytest `test_logout_via_start_hold`
**Steps:** while logged in with an empty queue, hold Start ≥ `hold_start_s`.
**Expected:** `POST /auth/logout` (best-effort), session cleared, back to
LOGIN_BADGE. See TC-049 for the pending-queue guard.

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

### HHT-TC-034 — Bare EAN rejected (v1 payloads are exact)
**Priority** medium · **Type** pytest `test_bare_ean_no_longer_accepted` +
script `wrong_scans.txt`
**Expected:** scanning a bare EAN without the `ART:` prefix shows "Not an
article label" and stays in SCAN_ARTICLE. *(Replaces the retired v0 behavior
that accepted bare EANs; the WMS accepts only `ART:<sku>`.)*

### HHT-TC-035 — Count entry limits
**Priority** high · **Type** pytest `test_count_can_exceed_requested_but_never_confirms`
**Expected:** count starts at the requested quantity; it can go above it (an
over-count must be enterable to be detectable) but never below 0; only the
exact quantity can confirm.

### HHT-TC-036 — Short pick submission (retired)
**Retired.** The WMS workflow baseline requires the exact task quantity
(`422 QUANTITY_MISMATCH`); the device never submits a short pick. Superseded
by TC-037.

### HHT-TC-037 — Count mismatch goes to DISCREPANCY, never confirms
**Priority** high · **Type** pytest `test_short_count_goes_to_discrepancy_and_recount`
+ script `discrepancy.txt`
**Steps:** in SET_QUANTITY set a count ≠ requested, press A.
**Expected:** DISCREPANCY screen ("call supervisor"); nothing sent or queued;
B recounts from the requested quantity; the exact count confirms normally.

### HHT-TC-038 — Status overlay
**Priority** low · **Type** pytest `test_status_overlay_swallows_buttons`
**Expected:** Select toggles the status screen; workflow buttons are inert while shown.

## 04x — Offline resilience / WMS integration (Level 2 store-and-forward)

Scans are authoritative WMS state transitions. Claiming a task always needs
connectivity; once claimed, the device validates scans locally (the same rule
the server applies) and queues the ordered chain scan-location → scan-article
→ confirm for FIFO replay. The server stays authoritative: a rejected replay
is surfaced, never hidden.

### HHT-TC-040 — Offline pick queues the ordered chain and replays FIFO
**Priority** high · **Type** script `offline_pick.txt` + pytest
`test_offline_pick_queues_ordered_chain_and_replays`
**Steps:** claim a task online; go offline mid-task; scan location, scan
article, confirm the exact count; attempt delivery; restore the WMS; deliver.
**Expected:** every step gives immediate local feedback (operator never
blocked); queue grows 1 → 2 → 3; delivery while offline loses nothing; after
reconnect the chain replays in order, the queue drains to 0, and the WMS task
is COMPLETED with exactly one stock movement.

### HHT-TC-041 — WMS down at login
**Priority** high · **Type** pytest `test_wms_down_at_login_shows_error`
**Expected:** "WMS unreachable" error, OFFLINE indicator, no crash.

### HHT-TC-042 — Queue survives power loss
**Priority** high · **Type** pytest `test_persistence_across_reopen` + manual on device
**Steps (manual):** complete a pick with WiFi off, hard power-cut the unit, boot,
restore WiFi.
**Expected:** the chain reaches the WMS after boot; stock moves exactly once
(client-generated `confirmationId`).

### HHT-TC-043 — Business rejection dead-letters the task chain
**Priority** high · **Type** pytest
`test_rejection_dead_letters_and_cascades_task_chain`
**Expected:** a rejected op goes to the dead-letter with its problem `code`;
later pending ops of the same task cascade dead ("poison chain"); delivery
stops; nothing is silently dropped or retried forever.

### HHT-TC-044 — Duplicate delivery suppressed
**Priority** medium · **Type** pytest `test_duplicate_op_key_ignored`,
`test_confirm_is_idempotent_by_confirmation_id`,
`test_repeated_correct_scan_is_replay_safe`
**Expected:** the same op is never enqueued twice (`op_key`); a re-delivered
scan returns `replayed: true` without regressing state; a re-delivered confirm
returns the original result without a second stock movement.

### HHT-TC-045 — Replay rejection surfaces SYNC_FAILED
**Priority** high · **Type** script `sync_failed.txt` + pytest
`test_replay_rejection_surfaces_sync_failed`
**Steps:** complete a pick offline; an admin blocks the task in the WMS; go
online; deliver.
**Expected:** replay is refused (`INVALID_TASK_STATE`); the whole chain is
parked as dead-letter; the device shows SYNC_FAILED with task and code
("see supervisor"); A acknowledges back to IDLE. Recovery is administrative
(WMS dashboard resume / stock adjustment).

### HHT-TC-046 — Token expiry pauses replay, re-login drains
**Priority** high · **Type** script `token_expiry.txt` + pytest
`test_auth_required_drops_to_login_and_keeps_queue`
**Expected:** a 401 auth code never dead-letters queued work; the device drops
to LOGIN_BADGE with "Session expired"; after re-login the queue drains under
the fresh token.

### HHT-TC-047 — Next-task claim refused while sync is pending
**Priority** high · **Type** pytest `test_fetch_refused_while_sync_pending`
**Expected:** with pending ops, A in IDLE shows "Sync pending — wait" instead
of fetching (the server would hand back the still-unsynced task).

### HHT-TC-048 — Mid-state task resumes on fetch
**Priority** medium · **Type** pytest `test_fetch_resumes_mid_state_task`
**Expected:** if the server holds the caller's task at LOCATION_CONFIRMED or
ARTICLE_CONFIRMED (e.g. after reboot or re-login), fetching maps it to the
matching screen instead of restarting at GOTO_LOCATION.

### HHT-TC-049 — Logout blocked while ops are pending
**Priority** high · **Type** pytest `test_logout_blocked_while_queue_pending`
**Expected:** hold-Start with a non-empty queue shows "Sync pending — cannot
log out" and keeps the session (logout would revoke the token the replay
needs).

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
| --- | --- |
| Operator login (badge + PIN = WMS credentials) | TC-020…025, TC-049 |
| Guided pick with server-authoritative scans | TC-030…035, TC-037, TC-038 |
| Wrong scans produce errors | TC-032, TC-033, TC-034 |
| Exact-quantity invariant (no short picks) | TC-035, TC-036 (retired), TC-037 |
| Offline store-and-forward + FIFO replay | TC-040, TC-042, TC-044 |
| Replay rejection surfaced, dead-letter audit | TC-043, TC-045 |
| Session lifecycle under queued work | TC-046…049 |
| HTTP contract v1 (field mapping, problem+json, transport) | `tests/test_http_client.py` |
| Fully configurable device | TC-002 (pin remap), config tests in `tests/test_config.py` |
| Audible scan feedback | TC-004, TC-012, TC-013 |
| Auto-start terminal appliance | TC-050, TC-051 |
| Analyzable logs | TC-052 |
