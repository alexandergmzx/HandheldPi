# Device Configuration Procedure — HHT unit provisioning

| | |
|---|---|
| Document | HHT-DOC-CONF |
| Applies to | Waveshare GamePi20 + Raspberry Pi Zero 2 W + Camera Module 3 |
| OS image | Raspberry Pi OS Lite (bookworm), 64-bit |
| Time required | ~45 min (mostly unattended) |

Follow this procedure start-to-finish to turn a blank SD card into a working picking
terminal. Record the **as-built** values in §5 — one copy of this document is filled in
and archived per unit.

## 1. Unit identity (fill in first)

| Field | Value |
|---|---|
| Device ID (`device.id`) | HHT-___ |
| Hostname | hht-___ |
| WiFi MAC | |
| SD card serial / label | |
| Camera module (printed on PCB) | |
| GamePi20 board revision | |
| Provisioned by / date | |

## 2. Prerequisites

- GamePi20 assembled per Waveshare manual; Camera Module 3 connected to the Zero's
  22-pin CSI port with a **Zero-width FFC cable** (contacts facing the board on the Pi
  side), routed clear of the battery.
- microSD ≥ 8 GB; workstation with Raspberry Pi Imager; SSH key pair.
- WiFi SSID/password of the warehouse network; IP/port of the WMS server.
- Test QR sheet: print [img/test_qr_sheet.png](img/test_qr_sheet.png) or display it on
  a monitor/phone — badge `OP:1001`, location `LOC:A-01-03`, article
  `ART:8412345678905`, matching the built-in mock task data.

## 3. Provisioning steps

Each step ends with a check. Do not continue past a failed check — see §6.

### 3.1 Flash the OS

1. Raspberry Pi Imager → *Raspberry Pi Zero 2 W* → *Raspberry Pi OS Lite (64-bit)*
   (bookworm — chosen over trixie for a more reliable first-boot/userconf provisioning).
2. In the customization dialog set: hostname (`hht-001`), enable SSH with your public
   key, WiFi SSID/password/country, locale.
3. Write, insert the card, power the unit from a **5 V/2 A USB supply** (not battery)
   for the whole procedure.

**Check:** `ssh <user>@hht-001.local` succeeds within ~2 min of first boot.

### 3.2 Update and fetch the repo

```
sudo apt update && sudo apt full-upgrade -y
git clone <repo-url> ~/HandheldPi        # or scp the repo over — it is self-contained
sudo reboot
```

**Check:** `uname -m` prints `aarch64`; `cat /etc/os-release` shows bookworm.

### 3.3 Install

```
cd ~/HandheldPi
sudo scripts/install.sh
sudo reboot
```

The script is idempotent: apt packages, `.venv` (system-site-packages), `/etc/hht/hht.toml`
(from the example, only if absent), `/var/log/hht` + `/var/lib/hht`, the display overlay
(§3.4), the boot splash (§3.4.1 — pass `--diag` to keep the verbose boot console
instead), GamePi20 audio routing (§3.5.1), and the systemd unit (installed, **not**
enabled). On an existing unit it preserves configured values and adds the new `[audio]`
section only when that section is absent.

**Check:** script ends with `Done.` and no red apt errors.

### 3.4 Display verification

After the reboot:

```
cat /sys/class/graphics/fb*/name        # one entry contains "panel-mipi-dbi" / spi name
```

Then blank/paint the panel:

```
cd ~/HandheldPi && .venv/bin/python -m hht -c config/dev.toml --script tests/scripts/happy_path.txt
```

(with `[display] backend = "framebuffer"` in a copy of dev.toml this paints the panel;
see §3.7 for the full smoke test).

Tuning table — edit `firmware/st7789v_gamepi20.txt`, re-run
`sudo scripts/setup_display.sh`, reboot:

| Symptom | Fix |
|---|---|
| Overlay missing | confirm `/boot/firmware/overlays/mipi-dbi-spi.dtbo` exists; if not, `sudo apt full-upgrade` (older bookworm images predate it) |
| No fb1; `fbtft`/`fb_st7789v` in dmesg; `/sys/bus/spi/devices/spi0.0/driver` missing | modalias collision: the panel's first compatible must be a neutral name (`gamepi20`), never a controller name, and `panel_mipi_dbi` must be listed in `/etc/modules-load.d/hht-display.conf` — both written by `setup_display.sh`; re-run it (found during HHT-001 bring-up) |
| Black right after boot | normal while the hht service is disabled — the backlight only powers on when something paints the panel (`/sys/class/backlight/*/bl_power` is 4 until then); run the smoke test in §3.7 |
| Nothing even when painted | SPI mode: the Waveshare module needs mode 3 — `cpha,cpol` must be on the `dtoverlay=mipi-dbi-spi` line (mode 0 = panel ignores all commands; found during HHT-001 bring-up, forum-verified) |
| Nothing at all | check `dmesg \| grep -i mipi` for firmware load errors |
| Garbage pixels | lower `speed=` to 32 MHz |
| Colors swapped (red↔blue) | keep the BGR bit (0x08) in MADCTL `0x36` |
| Colors inverted | remove/keep `command 0x21` (INVON) |
| Upside down / mirrored | MADCTL `0x36`: `0xa8` baseline; `0x68` flips, `0xe8`/`0x28` mirror |
| Washed out | tune VCOMS `0xbb` (0x1a–0x35) |

**Check:** panel shows content, correct orientation (landscape, D-pad left), record the
final SPI `speed=` value in §5.

### 3.4.1 Boot splash & diag console

What the LCD shows during boot/shutdown is owned by `scripts/setup_splash.sh`
(run by install.sh; re-runnable standalone to switch modes — it only edits
`cmdline.txt`/`config.txt`, so switching takes seconds):

| Mode | Command (then reboot) | LCD during boot/shutdown | cmdline parameters |
|---|---|---|---|
| **production** (default) | `sudo scripts/setup_splash.sh` | HHT splash (plymouth), no text; kernel console stays on HDMI | `quiet splash plymouth.ignore-serial-consoles loglevel=3 logo.nologo systemd.show_status=false vt.global_cursor_default=0` |
| **diag** | `sudo scripts/setup_splash.sh --diag` | verbose scrolling boot console (the pre-splash bring-up behavior) | `fbcon=map:1 vt.global_cursor_default=0` |

How production works: plymouth (not shipped on OS Lite; installed by the script
with the `script`-plugin theme from `assets/plymouth/hht/`, logo regenerable via
`scripts/make_splash_logo.py`) renders to the panel's DRM device from the
initramfs. An initramfs hook (`/etc/initramfs-tools/hooks/hht-display`) bakes
`vc4 → spi_bcm2835 → gpio_backlight → panel_mipi_dbi` plus `gamepi20.bin` into
the initramfs so plymouth can drive the panel early (verified on HHT-001: the
splash appears ~4 s in). The app and the splash both find the panel **by
name**, so its framebuffer index does not matter: on a headless unit the panel
is `fb0` (vc4 makes no fbdev without a connected display), and with a monitor
attached vc4 takes `fb0` and the panel is `fb1`. Production sets no
`fbcon=map:`, so the boot console follows the kernel default and stays silent
(`quiet`, `loglevel=3`, `systemd.show_status=false`); diag's `fbcon=map:1`
forces the console onto the panel. Because the panel is `fb0` headless, the
console *tty1* lands on it, so production also **masks `getty@tty1`** — without
it agetty paints `"<host> login:"` and a blinking cursor over the app (SSH is
unaffected; `--diag` unmasks it). `ShowDelay=0` is set in
`/etc/plymouth/plymouthd.conf`
(the Debian default of 5 s would hide the splash for most of the boot). The
first ~5 s stay dark in every mode — the GPU bootloader only drives HDMI.
On shutdown/reboot the splash returns via plymouth's poweroff/reboot units;
`hht.service` is ordered `After=plymouth-quit*` so plymouth releases the DRM
device before the app paints.

**Boot chime.** `hht-boot-sound.service` (a oneshot enabled by install.sh) plays
`assets/sounds/ready.wav` on the GamePi20 speaker while the splash is still up —
the audible counterpart to the logo, ordered `After=sound.target` and
`Before=plymouth-quit.service`. It reuses the same ALSA device as the app
(`plughw:CARD=Headphones,DEV=0`) and cannot stall the boot (`ExecStart=-…`,
`TimeoutStartSec`). Swap the wav in the unit for a longer clip only if you
accept the splash lingering until it finishes; disable the chime with
`sudo systemctl disable hht-boot-sound`.

Splash troubleshooting (production mode):

| Symptom | Fix |
|---|---|
| No splash on the panel, only on HDMI | `lsinitramfs $(ls -t /boot/firmware/initramfs* \| head -1) \| grep -E 'panel|gamepi20|vc4|backlight'` — if missing, re-run `sudo scripts/setup_splash.sh` (rebuilds the initramfs) |
| No splash anywhere | check theme is set: `plymouth-set-default-theme` prints `hht`; add `plymouth.debug` to cmdline, reboot, read `/var/log/plymouth-debug.log` and `journalctl -b \| grep plymouth` |
| Test the theme without rebooting | `sudo systemctl stop hht; sudo plymouthd; sudo plymouth show-splash; sleep 5; sudo plymouth quit; sudo systemctl start hht` |
| Boot text bleeds through | confirm `quiet` and `systemd.show_status=false` survived in `/boot/firmware/cmdline.txt` (a failed edit leaves the old line — the file is normalized on every setup_splash.sh run) |
| Everything broken, need the console | `sudo scripts/setup_splash.sh --diag && sudo reboot`, or plug an HDMI monitor (production keeps the console there) |

**Check:** reboot shows the HHT splash on the LCD with no scrolling text before
the app starts; `verify_unit.sh` (§3.9) reports `boot mode: production`.

### 3.5 Button verification

```
cd ~/HandheldPi && .venv/bin/python -m hht.tools.buttontest -c /etc/hht/hht.toml
```

Press all 12 buttons; each must print its own name exactly once per press. If a button
prints the wrong name, correct `[input.pins]` in `/etc/hht/hht.toml` (board revisions
may differ) and re-run.

**Check:** 12/12 buttons correct; final pin map recorded in §5.

### 3.5.1 Audio routing and power-on noise baseline

The GamePi20 does not use GPIO13 for sound. It takes PWM audio from **GPIO18
(physical pin 12)**; GPIO12/GPIO13 remain D-pad Up/Right. `scripts/setup_audio.sh`
(run by install.sh) writes `dtoverlay=audremap,pins_18_19`. The board consumes GPIO18;
GPIO19 is unconnected. The onboard speaker path includes a 5 V NS8002 amplifier, so
noise that starts at the physical power switch may happen before Linux or the HHT app
can mute/control the PWM pin.

First set the physical volume potentiometer to minimum. After reboot:

```bash
aplay -l
aplay -L | grep -A2 Headphones
aplay -q -D plughw:CARD=Headphones,DEV=0 assets/sounds/ready.wav
pinctrl get 12 13 18
```

Raise the potentiometer only enough to hear the quiet ready cue. Then repeat the button
test in §3.5; Up and Right must still work. Record this matrix before changing hardware:

| Test | Record |
|---|---|
| Battery, service stopped, power switch ON | exact start time, click/buzz/hiss, duration |
| Clean 5 V USB power, same conditions | same / different from battery |
| Potentiometer minimum then normal | whether noise follows volume |
| Speaker then headphones, starting at minimum | which output carries the noise |
| HHT starts and plays `ready` | cue clean, distorted, late, or absent |

Interpretation gate:

- Noise beginning immediately at the switch is in the power/amplifier/input path; the
  application cannot be its root cause.
- Noise beginning only when `hht.service` starts points to ALSA device/routing or cue
  playback configuration.
- Sustained noise that changes with the potentiometer is consistent with an upstream
  input/PWM/ground/power problem, but is not enough by itself to identify a bad ground.

Do not join arbitrary grounds or change amplifier components from this symptom alone.
If switch-time or sustained noise remains, power down and inspect ground continuity,
then have a qualified person measure the 5 V rail and the NS8002 shutdown/input network
against the [Waveshare schematic](https://www.waveshare.net/w/upload/d/de/GamePi20_Schematic.pdf).

**Check:** ready cue is audible and clean at low volume; no sustained noise at the agreed
working volume; GPIO12/GPIO13 buttons pass. Record any unavoidable switch click in §5.

### 3.6 Camera verification

**Bring-up finding (HHT-001):** the firmware's `camera_auto_detect` failed to identify
the Camera Module 3 even with a good cable, while the kernel driver probes it fine.
`scripts/setup_camera.sh` (run by install.sh since then) therefore writes an explicit
overlay block — `camera_auto_detect=0` + `dtoverlay=imx708`, and never a `,cam0`
suffix: the Zero 2 W's single CSI port is CAM1, the overlay default. Verify after the
reboot:

```
dmesg | grep imx708                  # expect: "imx708 10-001a: camera module ID 0x0301"
rpicam-hello --list-cameras          # expect: imx708 [4608x2592 ...]
rpicam-still --autofocus-mode continuous -t 3000 -o /tmp/test.jpg
```

Hold the test QR sheet ~15 cm from the lens for the still; copy it off and confirm the
QR is sharp.

**Check:** `camera module ID` in dmesg, `imx708` listed, QR legible in the still. If
dmesg instead shows `failed to read chip id` or stays silent, the FFC cable is bad or
reversed (blue stiffener faces the latch on both ends; 22-pin end at the Pi).

### 3.7 Device configuration + smoke test

Edit `/etc/hht/hht.toml`:

| Key | Set to |
|---|---|
| `device.id` | the ID from §1 |
| `device.site` | site code |
| `wms.base_url` | real WMS URL, e.g. `http://192.168.1.50:8080` |
| `wms.backend` | `mock` for now — switch to `http` when the server is up |
| `audio.backend` | `alsa` on device; `none` for silent operation |
| `audio.device` | `plughw:CARD=Headphones,DEV=0` for GamePi20 PWM audio |

Smoke test on the hardware (mock WMS, real display/buttons/camera):

```
cd ~/HandheldPi && .venv/bin/python -m hht -c /etc/hht/hht.toml
```

Log in with the test badge QR, complete one pick with the test location/article QRs,
confirm with A. `Ctrl-C` to exit.

**Check:** full pick cycle on the physical device; `PICK OK` shown;
`grep pick_confirmed /var/log/hht/hht.jsonl` shows the confirmation.

### 3.8 Enable the service

```
sudo systemctl enable --now hht
systemctl status hht                  # active (running)
sudo reboot
```

**Check:** after reboot the login screen appears on the LCD without any SSH interaction
(`hht.service` is a systemd *system* unit — it runs at boot with no login or user
session); `systemctl status hht` is `active (running)`.

### 3.9 Automated verification

```
scripts/verify_unit.sh
```

Runs every software-checkable item of this procedure (OS, display chain, camera,
audio routing/assets, config validity, service, permissions) and prints PASS/FAIL per
check. **All PASS**
is the provisioning gate — paste the output into the unit's test report as evidence.
Only the physical checks (§3.5 buttons, §3.5.1 sound/noise, and one pick against the QR
sheet) remain manual.

## 4. Handover

- Fill §1 and §5, execute the test cases marked *provisioning* in
  [TEST_SPECIFICATION.md](TEST_SPECIFICATION.md), file a
  [test report](TEST_REPORT_TEMPLATE.md).

## 5. As-built record

| Item | Expected | As-built |
|---|---|---|
| OS image + date | bookworm Lite 64-bit | |
| Kernel (`uname -r`) | 6.6 / 6.12 | |
| Panel fb device | fb0 headless / fb1 with HDMI | |
| Boot mode (§3.4.1) | production (splash) | |
| Splash first visible at | ~4–5 s after power | |
| ALSA PWM device | `plughw:CARD=Headphones,DEV=0` | |
| Power-on audio noise | none / click / sustained; battery vs USB | |
| SPI clock | 48–80 MHz | |
| MADCTL value | 0x70 | |
| Button map deltas | none | |
| Camera | imx708 | |
| App version (`hht --version`) | | |
| Repo commit | | |

## 6. Rollback / recovery

- Service misbehaving: `sudo systemctl disable --now hht`; run interactively with
  `-c config/dev.toml` (all mocks) to isolate hardware vs. integration issues.
- Display broken after tuning: remove the block between the
  `# --- HHT GamePi20 display` markers in `/boot/firmware/config.txt`, reboot (HDMI/SSH
  still work — the overlay never touches them).
- Splash misbehaving or boot text needed: `sudo scripts/setup_splash.sh --diag`,
  reboot — verbose console on the LCD, plymouth left installed but dormant.
- Audio routing broken: set `[audio] backend = "none"` to keep the workflow silent;
  re-run `sudo scripts/setup_audio.sh` and reboot when ready to diagnose it.
- Config wrecked: `sudo cp ~/HandheldPi/config/hht.toml.example /etc/hht/hht.toml`.
- Offline queue stuck (poison row shows repeatedly in `queue_flush_paused` logs):
  inspect with `sqlite3 /var/lib/hht/queue.db 'SELECT id,attempts,last_error FROM
  confirmations WHERE sent_at IS NULL'`.

## 7. Provisioning more units, faster

This document covers one unit end-to-end. For multiple units — golden SD images,
clone hygiene (SSH host keys, machine-id), zero-touch per-unit identity via
`hht-firstboot.service`, and the ~10-minute bench flow — see
[FLEET_PROVISIONING.md](FLEET_PROVISIONING.md).

## Revision history

| Rev | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-07-11 | | initial draft (pre-hardware) |
| 0.2 | 2026-07-12 | | HHT-001 bring-up findings folded in (display driver chain, SPI mode 3, explicit camera overlay); added §3.9 automated verification and §7 fleet pointer |
| 0.3 | 2026-07-13 | | boot splash: plymouth theme on the LCD, silent boot/shutdown, `--diag` console mode (§3.4.1); console-on-LCD moved from setup_display.sh to setup_splash.sh |
| 0.4 | 2026-07-13 | | GamePi20 GPIO18 PWM audio provisioning, semantic workflow cues, and power-on noise baseline (§3.5.1) |
