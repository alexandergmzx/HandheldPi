# Device Dry Run — HHT-001 on the bench, no WMS

> The on-device round that needs **no server**: real panel, buttons, camera, and
> speaker against the **mock WMS**. It closes every device-only test case and PLAN
> leftover that doesn't depend on the network, so the follow-up hot run
> ([LAN_E2E_RUNBOOK.md](LAN_E2E_RUNBOOK.md), live WMS over WiFi) only has to prove
> the integration. Record results as a `TEST_REPORT_<date>_device-dry.md` round from
> [TEST_REPORT_TEMPLATE.md](TEST_REPORT_TEMPLATE.md); the off-device baseline is
> [TEST_REPORT_2026-07-18_offdevice.md](TEST_REPORT_2026-07-18_offdevice.md).

**What it closes:** TC-004 (audio baseline), TC-010/011 (camera latency + debounce),
TC-012 manual speaker check, TC-042 prep half (queue survives power-cut),
TC-050/051/052 (service), the scripted TC-02x/03x/04x rounds *on the LCD*, and the
PLAN Phase 0 leftovers (SPI clock push, battery runtime, as-built record, splash
timing).

**Materials:** the printed (or on-monitor) QR sheet
[docs/img/test_qr_sheet.png](img/test_qr_sheet.png) — badge `OP:picker01` (PIN 1234),
the three mock tasks' `LOC:`/`ART:` labels, and the three must-reject labels.
Regenerate after mock-data changes with `python scripts/make_test_sheet.py` (dev
machine, needs the `qrcode` dev extra).

## 0. Get the code onto the unit

```bash
ssh hht@raspi
cd ~/Development/HandheldPi
git pull                      # editable install — a pull is a deploy
sudo systemctl stop hht       # free the panel, GPIOs, and audio for the bench run
```

No new apt/pip deps are needed for the app itself. For §1 only:
`sudo apt install --no-install-recommends python3-pytest` (dev budget, apt-first).

## 1. Automated suite on the device (optional but cheap evidence)

```bash
.venv/bin/python -m pytest -q     # expect 108 passed (slower on the Zero 2 W — minutes)
```

This is the same suite as the dev machine; passing it **on the Pi** additionally
proves the apt-provided stack (Pillow, numpy, requests versions from bookworm).

## 2. Scripted functional round on the LCD

Run every functional script through the real panel — the frames paint on the
ST7789V instead of a PNG dir:

```bash
for s in tests/scripts/*.txt; do
  .venv/bin/python -m hht -c config/device-dry.toml --script "$s" || break
done
```

Expect `PASS <script>` for all 7. The frames advance instantly (fake clock); the
point is the exit codes plus eyes on the panel: correct colors/orientation at the
configured SPI clock while every screen in the app renders at least once
(re-verifies TC-001 alongside TC-02x/03x/04x).

## 3. Interactive dry run — camera, buttons, sounds

```bash
.venv/bin/python -m hht -c config/device-dry.toml
```

Walk the sheet (mock data, so this is the full TC-030 flow on hardware):

1. **Badge login** — scan `OP:picker01`, enter PIN 1234 with the D-pad, A.
   Watch for: badge-accepted cue + the **100 ms invert flash** + the bottom
   **last-decode line** (`OP:picker01 · QRCODE · <n>ms`).
2. **Task 101** — A to claim → scan `LOC:A-01-03` → `ART:ART-SHIRT` → confirm qty 3.
3. **Rejections** — during a claimed task scan `LOC:Z-99-99` (WRONG LOCATION,
   error cue, *no* flash), `ART:WRONG-SKU` (WRONG ARTICLE), and the bare EAN
   `8412345678905` (Not an article label) — TC-032/033/034 with the real camera.
4. **Discrepancy** — on task 102 (qty 1) press ▲ before confirming: DISCREPANCY
   screen, B recounts, exact count confirms (TC-037).
5. **Task 103** (qty 5) — complete it; Select toggles the status overlay
   (TC-038 — check the `Last scan` line there too), hold Start logs out.

Sound check (TC-012 manual half): distinct cues for ready / badge / location /
article / error / confirmed on the GamePi20 speaker; volume pot as recorded in
DEVICE_CONFIGURATION §3.5.1.

## 4. Scanner measurements (TC-010, TC-011)

Still in the interactive run (or after it, from the log):

```bash
# TC-010 — present a label 10x from 10–20 cm, then:
grep scan_decoded var/dry-run.jsonl | tail -10
.venv/bin/python - <<'EOF'
import json, statistics
lat = [json.loads(l)["latency_ms"] for l in open("var/dry-run.jsonl")
       if '"scan_decoded"' in l]
print(f"n={len(lat)} median={statistics.median(lat)}ms max={max(lat)}ms")
EOF
```

Pass: median < 500 ms, no decode > 2 s (Phase 0 spot-check was 73–102 ms).
**TC-011:** hold one label steady in view 10 s → exactly **one** `scan_decoded`
for it in the log; remove it > 2 s (`debounce_s`), re-present → fires again.

## 5. Phase 0 leftovers (bench, no WMS)

- **SPI clock push** — edit the `speed=48000000` value in the overlay line that
  `scripts/setup_display.sh` writes (48 → 64 → 80 MHz), reboot, re-run §2 each
  step; back off one step at the first artifact. Record the final value in
  DEVICE_CONFIGURATION §5 (as-built).
- **Battery runtime** — unplug USB, run the interactive app with the camera live
  for 30 min; then `vcgencmd get_throttled` (want `0x0` — no brownout) and note
  the battery behavior/estimated runtime.
- **Audio/noise baseline (TC-004)** — DEVICE_CONFIGURATION §3.5.1 on battery
  *and* USB: classify switch-time pop vs sustained buzz, verify GPIO18 PWM audio
  and headphones, repeat the 12-button sweep after.
- **Splash timing** — reboot with the splash enabled; record time-to-splash,
  time-to-chime, time-to-login-screen in DEVICE_CONFIGURATION §5
  (`verify_unit.sh` should stay all-PASS).

## 6. Power-cut durability — prep half of TC-042

```bash
.venv/bin/python -m hht -c config/device-dry.toml --script tests/scripts/power_cut_prep.txt
# PASS leaves 3 undelivered ops in var/dry-queue.db on purpose. Now HARD power-cut
# the unit (power switch / pull the battery — not a clean shutdown). Boot, then:
.venv/bin/python - <<'EOF'
from hht.wms.offline_queue import OfflineQueue
q = OfflineQueue("var/dry-queue.db")
print(f"pending after power-cut: {q.pending_count()} (expect 3), dead: {q.dead_count()} (expect 0)")
q.close()
EOF
```

The WAL queue surviving a hard cut is the dry half; the *delivery* of exactly one
stock movement after the cut is the hot run's job. Clean up:
`rm -f var/dry-queue.db*`.

## 7. Service checks (TC-050/051/052)

These run against the real `/etc/hht/hht.toml` (http backend). Without a reachable
WMS the app must still boot to the login screen showing `OFF` — that *is* the
degraded-gracefully behavior.

```bash
sudo systemctl start hht && sudo systemctl status hht     # TC-050 (+ reboot check)
sudo pkill -9 -f "python -m hht"                          # TC-051: back within ~5 s
journalctl -u hht -n 20                                    # fresh app_started event
.venv/bin/python -m hht.tools.logreport var/dry-run.jsonl  # TC-052 on the dry shift
```

The logreport picks/rejects/offline-window counts must match what you actually did
in §3.

## 8. Record and close out

- Fill `TEST_REPORT_<date>_device-dry.md`: §1–§7 map to TC-001 (re-check),
  TC-004, TC-010/011/012, TC-02x/03x scripted-on-LCD, TC-042 (prep), TC-050/051/052.
  Evidence = `var/dry-run.jsonl` excerpts, panel photos, the §4 numbers.
- Tick the matching PLAN.md Phase 0/1 boxes with dated notes; fill the as-built
  column in DEVICE_CONFIGURATION §5.
- Then proceed to the **hot run**: [LAN_E2E_RUNBOOK.md](LAN_E2E_RUNBOOK.md).
