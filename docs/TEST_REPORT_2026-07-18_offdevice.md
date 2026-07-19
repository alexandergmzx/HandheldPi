# Test Report — HandheldPi picking terminal

> Off-device automated regression round. Fills every case that runs without the
> physical GamePi20 (unit + functional scripts + HTTP failure modes against a real
> socket). Device-only cases (00x hardware, 01x scanner-on-camera, 05x service) are
> marked PENDING with what each still needs; where a Phase 0 bring-up already produced
> device evidence (2026-07-12, HHT-001) it is cited. Follow with
> [LAN_E2E_RUNBOOK.md](LAN_E2E_RUNBOOK.md) for the on-device round.

## 1. Identification

| | |
|---|---|
| Report ID | HHT-TR-2026-07-18-01 |
| Date / tester | 2026-07-18 / Alexander Gomez |
| Scope | Off-device automated regression: unit + functional scripts + HTTP failure modes |
| App version / commit | `hht 0.1.0` / `2335162` (Scan feedback: on-screen last-decode line + accept flash) |
| Config used | `config/dev.toml` (mock WMS, image display for evidence); `tests/fake_wms.py` for HTTP cases |

## 2. Environment

| Item | Value |
|---|---|
| Device | Dev machine (x86_64 Linux), all boundaries mocked/dev-twinned — no GamePi20 |
| OS / kernel | Linux 6.8.0 (dev workstation) |
| WMS | Mock WMS (`hht.wms.mock_client`) for workflow; `tests/fake_wms.py` real-socket server for HTTP cases |
| Network | N/A (in-process); HTTP cases use a loopback TCP socket |
| Audio | `none` backend (cues exercised as semantic events, not played) |
| Power-on noise | N/A off-device |
| Test data | Mock built-ins: badge `OP:picker01` (PIN 1234), task 101 = `LOC:A-01-03` / `ART:ART-SHIRT` qty 3 |

## 3. Results

Automated suites:

| Suite | Command | Result |
|---|---|---|
| Unit + functional scripts + HTTP | `python -m pytest` | **106 passed / 0 failed** (11.9 s) |

Per test case (evidence PNGs in [evidence/2026-07-18-offdevice/](evidence/2026-07-18-offdevice/)):

| TC | Title | Result | Evidence | Notes |
|---|---|---|---|---|
| HHT-TC-001 | Display bring-up | PASS | PLAN.md bring-up note | On HHT-001 2026-07-12: `panel-mipi-dbi` fb1, MADCTL 0xA8, correct orientation/colors |
| HHT-TC-002 | Button map | PASS | PLAN.md bring-up note | On HHT-001 2026-07-12: 12/12 correct, no bounce |
| HHT-TC-003 | Camera detection + close focus | PASS | PLAN.md bring-up note | On HHT-001 2026-07-12: imx708, sharp at 15 cm; in-app decode 73–102 ms |
| HHT-TC-004 | Audio routing + noise baseline | PENDING | — | Needs device: DEVICE_CONFIGURATION §3.5.1 on battery + USB |
| HHT-TC-010 | Decode latency | PARTIAL | `TC-010_scan_feedback_and_flash.png` | On-screen feedback line + accept flash verified off-device; device latency measured 2026-07-12 (73–102 ms). Full 10-sample manual run PENDING |
| HHT-TC-011 | Scan debounce | PENDING | PLAN.md bring-up note | `_debounced` confirmed on hardware 2026-07-12; formal 10 s hold run PENDING |
| HHT-TC-012 | Semantic scan sounds | PASS (auto) | `test_semantic_sound_cues_follow_workflow_outcomes`; `happy_path`, `wrong_scans` | Manual speaker check PENDING (TC-004 round) |
| HHT-TC-013 | Audio non-blocking + fail-open | PASS | `test_alsa_play_is_non_blocking_and_uses_configured_device`, `test_sound_adapter_failure_does_not_break_workflow` | |
| HHT-TC-020 | Badge → PIN, never auth alone | PASS | `test_badge_scan_alone_does_not_authenticate` | |
| HHT-TC-021 | Unknown badge rejected | PASS | `test_unknown_badge_rejected_at_pin_submit` | |
| HHT-TC-022 | Non-badge QR at login rejected | PASS | `test_non_badge_scan_rejected_at_login` | |
| HHT-TC-023 | Badge + PIN login accepted | PASS | `test_pin_entry_buttons`, `test_badge_then_pin_login_ok`, `happy_path` | |
| HHT-TC-024 | Wrong PIN rejected + cleared | PASS | `test_wrong_pin_clears_entry_and_stays`, `test_pin_back_returns_to_badge` | |
| HHT-TC-025 | Logout revokes session | PASS | `test_logout_via_start_hold` | |
| HHT-TC-030 | Happy-path pick | PASS | `TC-030_happy_confirmed.png`; `happy_path`, `test_happy_path_full_pick` | |
| HHT-TC-031 | No task available | PASS | `test_no_task_available` | |
| HHT-TC-032 | Wrong location rejected | PASS | `TC-032_wrong_location.png`; `wrong_scans` | |
| HHT-TC-033 | Wrong article rejected | PASS | `TC-033_wrong_article.png`; `wrong_scans` | |
| HHT-TC-034 | Bare EAN rejected | PASS | `TC-034_bare_ean_rejected.png`; `test_bare_ean_no_longer_accepted`, `wrong_scans` | |
| HHT-TC-035 | Count entry limits | PASS | `test_count_can_exceed_requested_but_never_confirms` | |
| HHT-TC-036 | Short pick submission | RETIRED | — | Superseded by TC-037 (exact-quantity invariant) |
| HHT-TC-037 | Count mismatch → DISCREPANCY | PASS | `TC-037_discrepancy.png`; `test_short_count_goes_to_discrepancy_and_recount`, `discrepancy` | |
| HHT-TC-038 | Status overlay | PASS | `test_status_overlay_swallows_buttons` | Overlay now also shows last-decode line |
| HHT-TC-040 | Offline pick queues + replays FIFO | PASS | `TC-040_offline_confirmed_queue3.png`, `TC-040_offline_drained.png`; `offline_pick`, `test_offline_pick_queues_ordered_chain_and_replays` | |
| HHT-TC-041 | WMS down at login | PASS | `test_wms_down_at_login_shows_error` | |
| HHT-TC-042 | Queue survives power loss | PARTIAL | `test_persistence_across_reopen` | sqlite reopen persistence covered; hard power-cut on device PENDING |
| HHT-TC-043 | Business rejection dead-letters chain | PASS | `test_rejection_dead_letters_and_cascades_task_chain` | |
| HHT-TC-044 | Duplicate delivery suppressed | PASS | `test_duplicate_op_key_ignored`, `test_confirm_is_idempotent_by_confirmation_id`, `test_repeated_correct_scan_is_replay_safe` | |
| HHT-TC-045 | Replay rejection → SYNC_FAILED | PASS | `TC-045_sync_failed.png`; `sync_failed`, `test_replay_rejection_surfaces_sync_failed` | |
| HHT-TC-046 | Token expiry pauses replay | PASS | `TC-046_session_expired.png`; `token_expiry`, `test_auth_required_drops_to_login_and_keeps_queue` | |
| HHT-TC-047 | Claim refused while sync pending | PASS | `test_fetch_refused_while_sync_pending` | |
| HHT-TC-048 | Mid-state task resumes | PASS | `test_fetch_resumes_mid_state_task` | |
| HHT-TC-049 | Logout blocked with pending ops | PASS | `test_logout_blocked_while_queue_pending` | |
| HHT-TC-050 | Autostart | PASS | PLAN.md Phase 4 note | systemd unit enabled on HHT-001 2026-07-12, boots into app; reboot re-verify in device round |
| HHT-TC-051 | Crash recovery | PENDING | — | Needs device: `pkill -9`, confirm systemd restart |
| HHT-TC-052 | Log analyzability | PARTIAL | `hht.tools.logreport` | Tool implemented; run against a real device shift log PENDING |

HTTP contract v1 (field mapping, problem+json, transport) — `tests/test_http_client.py`
against `tests/fake_wms.py` (stopped / slow / 4xx / 5xx / 401 sequences): all PASS
within the 106-test suite.

Cross-repo integration reference: the 2026-07-15 live-WMS loopback (44/44 checks) is
recorded in
`warehouse-management/docs/evidence/2026-07-15-hht-loopback-integration.md`.

## 4. Defects

None. All 106 automated checks pass.

## 5. Deviations from the specification

Device-dependent cases were **not executed** in this round (no GamePi20 attached) and are
marked PENDING or PARTIAL above:

- **TC-004** (audio/noise baseline), **TC-011** (debounce hold), **TC-051** (crash
  recovery) — need HHT-001 on the bench.
- **TC-010** — on-screen feedback + flash verified off-device; the 10-sample camera
  latency run is device-only (an early single-shot measurement of 73–102 ms exists from
  2026-07-12).
- **TC-042** — reopen persistence is covered; the physical power-cut is device-only.
- **TC-050 / TC-052** — autostart was verified on 2026-07-12; a clean reboot re-check and
  a real shift-log `logreport` run belong to the device round.

TC-001/002/003 are reported PASS on the strength of the dated 2026-07-12 HHT-001 bring-up
evidence in PLAN.md rather than a fresh run this round.

## 6. Verdict & sign-off

| | |
|---|---|
| Summary | 27 pass / 0 fail / 3 partial / 4 pending / 1 retired of 35 cases |
| Verdict | **RELEASE WITH RESTRICTIONS** — off-device workflow, offline, and contract behavior fully green; device-only bring-up/service checks pending the HHT-001 LAN round |
| Restrictions | Not a final release sign-off: complete the device round per LAN_E2E_RUNBOOK.md (TC-004, TC-010 full, TC-011, TC-042 power-cut, TC-050/051/052) before freeze |
| Signed | Alexander Gomez, 2026-07-18 |
