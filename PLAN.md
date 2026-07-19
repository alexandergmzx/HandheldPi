# HandheldPi — Implementation Plan

DIY warehouse hand-held terminal (HHT) proof-of-concept: a picking scanner built on a
Waveshare GamePi20 (Raspberry Pi Zero 2 W) with a Camera Module 3 as QR/barcode reader,
talking to a WMS server (Spring Boot REST API) over WiFi.

Goals, in priority order:

1. Demonstrate **configuration** discipline — nothing hardcoded, documented provisioning.
2. Demonstrate **integration** — REST client against a defined contract, offline resilience.
3. Demonstrate **testing** — automated unit tests, scripted functional tests, numbered
   test specification, filled-in test reports.
4. Demonstrate **documentation** — this plan, the docs/ set, and a README that lets a
   stranger reproduce the device.

---

## Ecosystem role (ECOSYSTEM v3, 2026-07-18)

This repo is the ecosystem's **`hht-picker`** — the first-generation picker
terminal of the warehouse-automation ecosystem (map: `../ECOSYSTEM.md`). Its
mandate there is: **finish to demonstrable, then freeze.** The frozen device
becomes a standing proof that the WMS v1 API serves two client generations
unchanged — this Python terminal and its successor, warehouse-android's
`:app-picker` (Kotlin/CameraX on a refurbished phone).

Concretely:

- *Finish to demonstrable* = the open boxes below: Phase 0 leftovers (SPI clock
  tuning, battery sanity, audio baseline, as-built column), Phase 1 scan-loop
  polish, Phase 2 scripted-UI completion, the Phase 3 LAN e2e on HHT-001, and
  Phase 4 (splash, idempotent install, log report, final docs + test report).
- *Freeze* = after that, maintenance only: keep the device green against the
  pinned WMS v1 contract. New picking features belong in `:app-picker`, not here.

---

## Hardware facts (researched 2026-07-11)

### Display

| Item | Value |
|---|---|
| Panel | 2.0" IPS, 320x240, **ST7789V** controller, SPI |
| Bus | SPI0, CE0 (GPIO8), MOSI GPIO10, SCLK GPIO11 (write-only, no MISO) |
| Control pins | DC = GPIO25, RESET = GPIO27, BACKLIGHT = GPIO24 |
| Rated SPI clock | up to 96 MHz per Waveshare; we start at 48 MHz and tune up |

**Driver decision:** `fbcp-ili9341` and Waveshare's `rpi-fbcp` fork are **not viable** on
modern Raspberry Pi OS. Both depend on the Broadcom DispmanX API, which was removed when
Raspberry Pi OS moved to the KMS graphics stack (Bookworm onward). Waveshare's older
`flexfb` instructions died even earlier (driver removed in kernel 5.4).

The supported modern path is the mainline **`panel-mipi-dbi` TinyDRM driver** loaded via
`dtoverlay=mipi-dbi-spi` with a small firmware blob containing the ST7789V init sequence
(compiled from text with notro's `mipi-dbi-cmd` tool). This registers a real DRM device
and `/dev/fb0`, so:

- the app renders with Pillow and blits RGB565 into the framebuffer (no SDL/pygame), and
- the Linux console can be mapped to the LCD during provisioning/debug (`fbcon=map:0`).

Sources: juj/fbcp-ili9341 README (DispmanX deprecation note), Raspberry Pi forums threads
on `mipi-dbi-spi`, notro/panel-mipi-dbi wiki. Init sequence in `firmware/` is derived from
Waveshare's ST7789V demo code and **must be validated on hardware in Phase 0** (VCOM,
gamma, and MADCTL orientation may need adjustment).

**Bring-up finding (2026-07-12, HHT-001):** the panel's first compatible string must be a
*neutral* name (`gamepi20`), not `st7789v` — the bare controller name generates the SPI
modalias `spi:st7789v`, which the legacy staging `fb_st7789v`/`fbtft` modules claim, so
the wrong module loads and nothing binds to `spi0.0`. Since SPI modalias autoload only
considers that first name, `panel_mipi_dbi` is also force-loaded at boot via
`/etc/modules-load.d/hht-display.conf`. Both handled by `scripts/setup_display.sh`. With
this fix the panel binds as `/dev/fb1` (`panel-mipi-dbid`) next to the HDMI `fb0`.
Two more panel facts from the same session: the module needs **SPI mode 3** (`cpha,cpol`
on the overlay line — mode 0 leaves it completely dark) and **MADCTL 0xa8** (BGR wiring),
both matching the community-verified config for the Waveshare 2.0" ST7789V
(forums.raspberrypi.com t=337019). Finally, fbdev *writes* never enable the DRM
pipeline (that is fbcon's job, and fbcon lives on the HDMI fb0) — the panel stays
uninitialized with the backlight off until an unblank + `FB_ACTIVATE_FORCE` mode-set;
`FramebufferDisplay` now issues both on startup. A black screen while the hht service
is not running is therefore normal.

### Buttons (GamePi20)

Decoded from Waveshare's own RetroPie config
(`mk_arcade_joystick_rpi`, `map=5 gpio=12,20,21,13,26,16,23,4,6,17,22,5,-1`,
driver order: up,down,left,right,start,select,a,b,tr,y,x,tl). All buttons are
**active-low, internal pull-ups**.

| Button | GPIO | Button | GPIO |
|---|---|---|---|
| D-pad Up | 12 | A | 23 |
| D-pad Down | 20 | B | 4 |
| D-pad Left | 21 | X | 22 |
| D-pad Right | 13 | Y | 17 |
| Start | 26 | L (TL) | 5 |
| Select | 16 | R (TR) | 6 |

No overlap with SPI0 (8/9/10/11) or display control pins (24/25/27).
**Phase 0 verifies every pin on real hardware** — this table is research, not gospel.

### Audio (GamePi20)

- Waveshare's pinout and schematic route the onboard headphone/speaker circuit from
  **GPIO18 (physical pin 12)** through an NS8002 amplifier. GPIO19 is unconnected.
- Raspberry Pi PWM audio is enabled with `dtparam=audio=on` and
  `dtoverlay=audremap,pins_18_19`. Never use audremap's default `pins_12_13` on this
  unit: GPIO12 and GPIO13 are D-pad Up and Right.
- A pop or buzz that begins at the physical power switch can precede Linux pin control;
  software cues cannot prove or repair an amplifier-ground/power fault. Bring-up records
  switch timing, power source, potentiometer position, and speaker/headphone behavior
  before any component change.
- Application cues are short generated WAVs played through a bounded worker queue. The
  main loop never waits for ALSA, and the `none` backend keeps development/tests silent.

Sources: [Waveshare GamePi20 pinout](https://www.waveshare.com/wiki/GamePi20),
[GamePi20 schematic](https://www.waveshare.net/w/upload/d/de/GamePi20_Schematic.pdf),
[Raspberry Pi overlay README](https://github.com/raspberrypi/firmware/blob/master/boot/overlays/README).

### Camera

- Camera Module 3 (IMX708) on the Zero's 22-pin CSI connector (needs the Zero-width FFC).
- **No electrical conflict with the SPI display** — CSI-2 is a dedicated interface, no
  shared GPIOs. Real risks are mechanical (cable routing in the GamePi20 shell) and power
  draw on battery.
- CM3 has **autofocus** — essential here, since QR scan distance is 10–20 cm and the
  fixed-focus v1/v2 modules are blurry that close. Use continuous AF, or lock focus to
  ~15 cm (`AfMode=Manual`, `LensPosition≈6.6` dioptres) for faster first decode.
- Software: `python3-picamera2` from apt with `--no-install-recommends` (headless-safe on
  OS Lite; apt keeps libcamera and picamera2 in lockstep, unlike pip).

### QR decoding

**pyzbar** (libzbar) over OpenCV `QRCodeDetector`:

- `python3-opencv` pulls in hundreds of MB and a heavy import footprint — a non-starter
  with 512 MB RAM.
- OpenCV's detector reads a single QR per frame and no 1D barcodes; zbar reads multiple
  symbologies (QR + EAN/Code128 — real warehouses mix them).
- zbar has better detection accuracy in comparative tests; it is slower than commercial
  SDKs but a 640x480 grayscale decode on the Zero 2 W fits comfortably in a 5–10 fps scan
  loop, which is plenty for hand-aimed scanning.

Pipeline: picamera2 lores YUV420 stream → Y plane (grayscale, zero-copy) → pyzbar.
Fallback if zbar disappoints on hardware: `zxing-cpp` (pip wheel, faster and more robust,
slightly heavier install).

---

## Software architecture

```
                   ┌────────────────────────────── main loop (single thread) ─┐
 buttons (gpio) ──▶│  event queue ──▶ StateMachine ──▶ ViewModel ──▶ Display   │
 scanner thread ──▶│                     │                          (fb/PIL)  │
 tick timer     ──▶│                     ▼                                    │
                   │              WmsClient (http or mock)                    │
                   └──────────────────────│───────────────────────────────────┘
                                          ▼
                            OfflineQueue (sqlite, WAL) ◀── flusher thread ──▶ WMS
```

- **Everything behind an interface** with a mock/dev twin: `Display` (framebuffer /
  console / PNG), `InputSource` (GPIO / terminal keyboard), `Scanner` (camera / scripted),
  `WmsClient` (HTTP / mock). The whole app runs on a dev machine over SSH with
  `config/dev.toml` — no hardware needed to build the UI and workflow.
- **Store-and-forward confirmations:** every pick confirmation is written to a persistent
  sqlite queue first, then a background flusher delivers FIFO with an idempotency key.
  "Online" just means delivery is immediate; WiFi loss changes nothing in the workflow.
- **Scan validation:** scans are authoritative WMS v1 state transitions. The task payload
  carries the expected location/article codes, so the device pre-validates locally with
  the exact rule the server applies; while offline the ordered op chain (scan-location →
  scan-article → confirm) queues and replays FIFO on reconnect — server replay-safety and
  confirm idempotency make redelivery harmless. Documented in API.md.
- **Structured logging:** JSON-lines to a rotating file (machine analysis) + human-readable
  stderr/journal. Every workflow transition, scan, and network event is one JSON record.
- **Scripted functional testing:** `--script file.txt` feeds button/scan events into the
  real app with `expect_state` / `expect_error` assertions — the functional test spec in
  docs/ maps 1:1 onto runnable scripts in `tests/scripts/`.

Picking workflow states:

```
STARTUP → LOGIN_BADGE → LOGIN_PIN → IDLE → GOTO_LOCATION → SCAN_ARTICLE
               ▲     (badge = username,  ▲ ↘ NO_TASK  │ (wrong scan → error, stay)
               │      PIN = password)    │            ▼
               │        CONFIRMED ←──────┴─ SET_QUANTITY ⇄ DISCREPANCY
               └── SYNC_FAILED (replay rejected → dead-letter, see supervisor)
Global: Select = status screen, Start(hold) = logout (blocked while ops are
pending), error banner with timeout.
```

---

## Phase 0 — Hardware bring-up (on device, checklist)

Exit criteria: console visible on LCD, all 12 buttons verified, camera captures a sharp
QR at 15 cm, all documented in docs/DEVICE_CONFIGURATION.md with actual values.

- [x] Flash Raspberry Pi OS **bookworm Lite 64-bit** (chosen over trixie: more reliable
      first-boot/userconf provisioning in Imager); preconfigure SSH + WiFi in Imager.
      *(done 2026-07-12, HHT-001 as host `raspi`)*
- [x] First boot on USB power (not battery), SSH in, `apt update && full-upgrade`.
- [x] Run `scripts/install.sh` (installs apt deps, venv, app, display overlay, service
      disabled by default).
- [x] Display: reboot, verify the panel framebuffer (`cat /sys/class/graphics/fb*/name`
      → `panel-mipi-dbid`, enumerates as **fb1** next to the HDMI fb0); paint it with
      `--script tests/scripts/happy_path.txt` on a framebuffer config.
      *(done 2026-07-12 after three fixes — see "Bring-up finding" above; MADCTL 0xA8
      confirmed visually: READY screen, correct orientation and colors)*
- [ ] Push SPI clock: 48 → 64 → 80 MHz until artifacts, then back off one step. Record.
- [x] Buttons: run `python -m hht.tools.buttontest` — press each key, verify GPIO map
      table above; correct config if Waveshare revision differs.
      *(done 2026-07-12: 12/12 correct on first try, no bounce, map as researched)*
- [x] Camera: seat FFC (contacts down on Zero), `rpicam-hello --list-cameras` shows imx708;
      `rpicam-still -o test.jpg` sharp at 15 cm with autofocus.
      *(done 2026-07-12: firmware autodetect fails to identify this CM3 —
      explicit `camera_auto_detect=0` + `dtoverlay=imx708` works (no `,cam0`: the
      Zero's single CSI port is CAM1, the default). In-app QR decode verified at
      close range off a screen: **73–102 ms latency** (target was <500 ms), badge
      login + debounce confirmed on hardware — Phase 1's core numbers, measured early)*
- [ ] Battery sanity: run display+camera+WiFi loop 30 min on battery, watch for brownout
      (`vcgencmd get_throttled`). Record runtime estimate.
- [ ] Audio/noise baseline: run DEVICE_CONFIGURATION §3.5.1 on battery and USB power;
      classify switch-time pop vs sustained buzz, verify GPIO18 PWM sound and headphones,
      then repeat the 12-button sweep.
- [ ] Fill in the "as-built" column in docs/DEVICE_CONFIGURATION.md.

Risks: unknown GamePi20 board revision (pin map), init-sequence tuning, camera FFC
clearance inside the shell. Mitigation: everything above is config, not code.

## Phase 1 — QR scan loop (on device)

Exit criteria: point at QR → decoded payload on LCD < 500 ms typical, no duplicate reads.

- [x] `hht.scanner.camera_scanner`: picamera2 lores YUV → pyzbar, decode thread posting
      ScanEvents. *(implemented; verified on HHT-001 2026-07-12, 73–102 ms in-app decode)*
- [x] Debounce: same payload suppressed for `scanner.debounce_s` (default 2 s); different
      payload accepted immediately. *(`camera_scanner._debounced`; confirmed on hardware
      2026-07-12)*
- [x] Semantic sound feedback: distinct non-blocking cues for accepted badge/location/
      article, rejection, offline transition, ready, and confirmation; silent test backend.
- [x] On-screen feedback: transient last code / symbology / decode-latency line plus a
      100 ms screen-invert flash on accept (accessibility/fallback equivalent to sound);
      last decode also shown on the status overlay. *(2026-07-18, off-device)*
- [ ] Measure and record decode latency + CPU on Zero 2 W (goes in test report). *(latency
      measured 2026-07-12: 73–102 ms; CPU/formal test-report entry still open)*
- [ ] Test cases HHT-TC-01x (on device, see docs/TEST_SPECIFICATION.md).

## Phase 2 — Picking workflow UI (developable off-device)

Exit criteria: full pick cycle against the **mock WMS** on the dev machine and on device;
scripted functional tests pass.

- [x] State machine (already scaffolded) hardened: every event in every state defined,
      unknown events logged and ignored. *(2026-07-15, Phase 3 workflow rework)*
- [x] Screens: badge/PIN login, idle, task card (location big + article + qty), quantity
      picker, error banners, status screen (net/queue/battery/operator).
- [x] PIN entry with D-pad; badge login via `OP:<username>` QR.
- [x] Scripted tests: happy path, wrong location, wrong article, DISCREPANCY (exact-qty,
      short picks retired), logout — plus offline/SYNC_FAILED/token-expiry chains.
- [x] Test cases HHT-TC-02x/03x (automated in `tests/test_functional_scripts.py`).

## Phase 3 — WMS integration + offline queue

Exit criteria: full cycle against the real Spring Boot API (`warehouse-management`,
contract v1); pulling the WiFi mid-task loses nothing; the queue drains automatically
on reconnect; a replay the server refuses is surfaced, never hidden.

- [x] `http_client` rewritten against the real v1 contract (`warehouse-management/API.md`):
      login `{username, password, deviceCode}` → opaque bearer token, `/hht/tasks/*`
      paths, RFC 9457 problem+json → `WmsRejected(code)` / `WmsAuthError`, logout,
      `/actuator/health` probe, `X-Correlation-Id` per request *(2026-07-15)*
- [x] Login flow = badge (`OP:<username>`) + PIN-as-password; picker accounts get numeric
      passwords of `pin_length` digits (WMS dev seed V1_2: `picker02`/2468) *(2026-07-15)*
- [x] Level 2 store-and-forward: queue generalized to ordered per-task op chains
      (scan-location → scan-article → confirm) with dead-letter + poison-cascade;
      legacy v0 queue DBs migrated on open (`PRAGMA user_version`) *(2026-07-15)*
- [x] Flusher: FIFO replay, pause-on-auth-expiry (never dead-letters on 401), retry every
      `wms.retry_interval_s`; queue depth + SYNC_FAILED / re-login events into the UI
      *(2026-07-15)*
- [x] Workflow guards: claim refused while sync pending, logout refused with pending ops,
      mid-state task resume, exact-quantity DISCREPANCY screen (no short picks — WMS
      invariant) *(2026-07-15)*
- [x] HTTP failure-mode tests against a real socket (`tests/fake_wms.py`): stopped,
      slow (timeout), 4xx problem+json, 5xx, 401 sequences *(2026-07-15, 100 pytest)*
- [x] Test cases HHT-TC-04x (offline/recovery) rewritten and automated
      (`offline_pick`, `sync_failed`, `discrepancy`, `token_expiry` scripts) —
      the centrepiece of the test report *(2026-07-15)*
- [x] Loopback e2e against a running WMS dev instance (`config/dev-http.toml`,
      badge `OP:picker02`): happy path, server-side wrong scans, offline drain
      through a kill-able TCP proxy, replay rejection after admin block +
      resume recovery, token revocation → re-login → drain, quantity mismatch,
      claim/logout guards, device conflict, correlation-ID join — 44/44 checks
      *(2026-07-15; evidence in
      `warehouse-management/docs/evidence/2026-07-15-hht-loopback-integration.md`)*
- [ ] LAN e2e on HHT-001 over WiFi against the WMS host (runbook firewall §3–4,
      WMS-printed `LOC:`/`ART:` labels + `make_badge.py` badge); evidence in both repos.
      *Prepared as a mechanical checklist in docs/LAN_E2E_RUNBOOK.md (2026-07-18); awaits
      HHT-001 + the WMS host both on the LAN.*

## Phase 4 — Service, install, docs

Exit criteria: flash-to-working-terminal reproducible from docs alone; `systemctl status
hht` green after reboot; test report filled in.

- [x] systemd unit: auto-start after network, restart on crash, journal + file logs.
      *(enabled on HHT-001 2026-07-12 — system service, boots into the app with no login)*
- [x] Fleet provisioning: `verify_unit.sh` (Phase 0 checklist as one command),
      `setup_camera.sh`, golden-image flow with `hht-firstboot` identity-from-serial
      and clone hygiene — docs/FLEET_PROVISIONING.md *(2026-07-12)*
- [ ] Boot/shutdown splash: plymouth `script` theme on the LCD, silent cmdline,
      `setup_splash.sh --diag` console switch, panel stack (vc4-first) baked into
      the initramfs, boot chime (`hht-boot-sound.service` plays ready.wav over the
      splash) — *implemented (commit 512ef48); still to verify on HHT-001 and record
      splash timing in DEVICE_CONFIGURATION §5.*
- [ ] `install.sh` idempotent end-to-end on a fresh image (this *is* the provisioning test).
- [x] Log rotation (`logsetup` RotatingFileHandler); `hht.tools.logreport` summarizes a
      shift's JSONL (picks, scan/workflow errors, offline windows).
- [ ] Final pass on README, DEVICE_CONFIGURATION, TEST_SPECIFICATION; execute full spec,
      fill TEST_REPORT with evidence (log excerpts, PNG screen captures via ImageDisplay).

---

## Dependency budget (all apt, ~no pip beyond the app itself)

`python3-picamera2` (no-recommends), `python3-pyzbar`, `python3-pil`, `python3-numpy`,
`python3-requests`, `python3-gpiozero` + `python3-lgpio`, `fonts-dejavu-core`,
`alsa-utils`, `python3-pytest` (dev). Stdlib: `tomllib`, `sqlite3`, `logging`, `queue`,
`threading`.
