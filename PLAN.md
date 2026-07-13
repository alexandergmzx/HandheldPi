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
- **Scan validation:** the task payload carries the expected location/article codes, so
  validation is local and works offline; when online the device also reports scans to the
  WMS (`report_scan`) as best-effort telemetry. Documented in API.md.
- **Structured logging:** JSON-lines to a rotating file (machine analysis) + human-readable
  stderr/journal. Every workflow transition, scan, and network event is one JSON record.
- **Scripted functional testing:** `--script file.txt` feeds button/scan events into the
  real app with `expect_state` / `expect_error` assertions — the functional test spec in
  docs/ maps 1:1 onto runnable scripts in `tests/scripts/`.

Picking workflow states:

```
STARTUP → LOGIN_BADGE ⇄ LOGIN_PIN → IDLE → FETCHING → GOTO_LOCATION → SCAN_ARTICLE
                                      ▲  ↘ NO_TASK        │ (wrong scan → error, stay)
                                      │                    ▼
                                      └── CONFIRMED ← SET_QUANTITY
Global: Select = status screen, Start(hold) = logout, error banner with timeout.
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
- [ ] Camera: seat FFC (contacts down on Zero), `rpicam-hello --list-cameras` shows imx708;
      `rpicam-still -o test.jpg` sharp at 15 cm with autofocus.
      *(in progress: firmware autodetect sees nothing on CSI I²C — electrical-level
      debugging with a forced `dtoverlay=imx708`)*
- [ ] Battery sanity: run display+camera+WiFi loop 30 min on battery, watch for brownout
      (`vcgencmd get_throttled`). Record runtime estimate.
- [ ] Fill in the "as-built" column in docs/DEVICE_CONFIGURATION.md.

Risks: unknown GamePi20 board revision (pin map), init-sequence tuning, camera FFC
clearance inside the shell. Mitigation: everything above is config, not code.

## Phase 1 — QR scan loop (on device)

Exit criteria: point at QR → decoded payload on LCD < 500 ms typical, no duplicate reads.

- [ ] `hht.scanner.camera_scanner`: picamera2 lores YUV → pyzbar, decode thread posting
      ScanEvents.
- [ ] Debounce: same payload suppressed for `scanner.debounce_s` (default 2 s); different
      payload accepted immediately.
- [ ] On-screen feedback: last code, symbology, decode latency; beep-equivalent flash
      (invert screen 100 ms) on accept.
- [ ] Measure and record decode latency + CPU on Zero 2 W (goes in test report).
- [ ] Test cases HHT-TC-01x (see docs/TEST_SPECIFICATION.md).

## Phase 2 — Picking workflow UI (developable off-device)

Exit criteria: full pick cycle against the **mock WMS** on the dev machine and on device;
scripted functional tests pass.

- [ ] State machine (already scaffolded) hardened: every event in every state defined,
      unknown events logged and ignored.
- [ ] Screens: badge/PIN login, idle, task card (location big + article + qty), quantity
      picker, error banners, status screen (net/queue/battery/operator).
- [ ] PIN entry with D-pad; badge login via `OP:<id>` QR.
- [ ] Scripted tests: happy path, wrong location, wrong article, short pick, logout.
- [ ] Test cases HHT-TC-02x/03x.

## Phase 3 — WMS integration + offline queue

Exit criteria: full cycle against the real Spring Boot API; pulling the WiFi mid-shift
loses nothing; queue drains automatically on reconnect.

- [ ] `http_client` against API.md (replace assumed contract with the real one when it
      lands); auth token handling; timeouts/retries from config.
- [ ] Offline queue flusher: FIFO, idempotency keys, exponential backoff capped at
      `wms.retry_interval_s`; queue depth on status bar.
- [ ] Connectivity probe → ONLINE/OFFLINE banner.
- [ ] Failure-mode tests with the WMS stopped, slow (timeout), and returning 4xx/5xx.
- [ ] Test cases HHT-TC-04x (offline/recovery) — the centrepiece of the test report.

## Phase 4 — Service, install, docs

Exit criteria: flash-to-working-terminal reproducible from docs alone; `systemctl status
hht` green after reboot; test report filled in.

- [ ] systemd unit: auto-start after network, restart on crash, journal + file logs.
- [ ] `install.sh` idempotent end-to-end on a fresh image (this *is* the provisioning test).
- [ ] Log rotation; `hht.tools.logreport` — summarize a shift's JSONL (picks/hour, errors,
      offline windows).
- [ ] Final pass on README, DEVICE_CONFIGURATION, TEST_SPECIFICATION; execute full spec,
      fill TEST_REPORT with evidence (log excerpts, PNG screen captures via ImageDisplay).

---

## Dependency budget (all apt, ~no pip beyond the app itself)

`python3-picamera2` (no-recommends), `python3-pyzbar`, `python3-pil`, `python3-numpy`,
`python3-requests`, `python3-gpiozero` + `python3-lgpio`, `fonts-dejavu-core`,
`python3-pytest` (dev). Stdlib: `tomllib`, `sqlite3`, `logging`, `queue`, `threading`.
