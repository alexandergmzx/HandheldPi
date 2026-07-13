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
(§3.4), and the systemd unit (installed, **not** enabled).

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

Optionally add `fbcon=map:1` to `/boot/firmware/cmdline.txt` to get the Linux console
on the LCD (record the panel's fb number first).

**Check:** panel shows content, correct orientation (landscape, D-pad left), record the
final SPI `speed=` value in §5.

### 3.5 Button verification

```
cd ~/HandheldPi && .venv/bin/python -m hht.tools.buttontest -c /etc/hht/hht.toml
```

Press all 12 buttons; each must print its own name exactly once per press. If a button
prints the wrong name, correct `[input.pins]` in `/etc/hht/hht.toml` (board revisions
may differ) and re-run.

**Check:** 12/12 buttons correct; final pin map recorded in §5.

### 3.6 Camera verification

**Bring-up finding (HHT-001):** the firmware's `camera_auto_detect` failed to identify
the Camera Module 3 even with a good cable, while the kernel driver probes it fine. Use
an explicit overlay — append to `/boot/firmware/config.txt` and reboot:

```
# --- HHT camera: explicit overlay ---
camera_auto_detect=0
dtoverlay=imx708
# --- HHT camera end ---
```

Do **not** add a `,cam0` suffix: the Zero 2 W's single CSI port is CAM1, which is the
overlay default. Then verify:

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

**Check:** after reboot the login screen appears on the LCD without any SSH interaction;
`systemctl status hht` is `active (running)`.

## 4. Handover

- Fill §1 and §5, execute the test cases marked *provisioning* in
  [TEST_SPECIFICATION.md](TEST_SPECIFICATION.md), file a
  [test report](TEST_REPORT_TEMPLATE.md).

## 5. As-built record

| Item | Expected | As-built |
|---|---|---|
| OS image + date | bookworm Lite 64-bit | |
| Kernel (`uname -r`) | 6.6 / 6.12 | |
| Panel fb device | /dev/fb1 (typ.) | |
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
- Config wrecked: `sudo cp ~/HandheldPi/config/hht.toml.example /etc/hht/hht.toml`.
- Offline queue stuck (poison row shows repeatedly in `queue_flush_paused` logs):
  inspect with `sqlite3 /var/lib/hht/queue.db 'SELECT id,attempts,last_error FROM
  confirmations WHERE sent_at IS NULL'`.

## Revision history

| Rev | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-07-11 | | initial draft (pre-hardware) |
