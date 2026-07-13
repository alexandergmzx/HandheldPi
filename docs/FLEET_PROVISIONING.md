# Fleet Provisioning — bringing up HHT units faster

| | |
|---|---|
| Document | HHT-DOC-FLEET |
| Context | HHT-001 took a full bring-up day, most of it *discovery* (display driver chain, camera autodetect). Every finding is now encoded in scripts and docs — this document is about never spending that day again. |
| Targets | unit #2 (script path): ≈ 35 min, mostly unattended · unit #N (golden image): ≈ 10 min bench time |

The one-unit procedure stays in [DEVICE_CONFIGURATION.md](DEVICE_CONFIGURATION.md);
this document layers the scale-up strategy on top.

## What made HHT-001 slow — and what now prevents it

| Time sink on HHT-001 | Encoded countermeasure |
|---|---|
| fbcp research, fbtft modalias hijack, SPI mode 3, fbdev activation | `scripts/setup_display.sh` + app-side activation, all applied by `install.sh` |
| camera autodetect failure, `,cam0` confusion | `scripts/setup_camera.sh`, applied by `install.sh` |
| manual eyeball verification of every subsystem | `scripts/verify_unit.sh` — the Phase 0 checklist as one command |
| editing per-unit identity by hand | `hht-firstboot.service` — device.id/hostname derived from the SoC serial |

## Tier 1 — scripted install (any unit count, works today)

1. Raspberry Pi Imager → Raspberry Pi OS Lite 64-bit (bookworm), with OS customization
   (hostname `hht-xxx`, SSH public key, WiFi). The Imager injects these via its
   first-boot mechanism — no manual console work.
2. SSH in, then:

   ```
   sudo apt update && sudo apt full-upgrade -y
   git clone https://github.com/alexandergmzx/HandheldPi.git ~/Development/HandheldPi
   cd ~/Development/HandheldPi && sudo scripts/install.sh --enable-service
   sudo reboot
   ```

3. `scripts/verify_unit.sh` → expect **all PASS** (paste the output into the unit's
   test report — it is the provisioning evidence).
4. Edit `/etc/hht/hht.toml`: `device.id`, `device.site`, `wms.base_url`.
5. Physical spot-checks only a human can do: buttons
   (`python -m hht.tools.buttontest`), one pick against the test QR sheet
   ([img/test_qr_sheet.png](img/test_qr_sheet.png)).

Cost per unit: ~35 min wall clock, ~10 min of it hands-on. The `full-upgrade` and apt
installs dominate — which is exactly what Tier 2 removes.

## Tier 2 — golden image (recommended from ~3 units)

Provision once, capture the SD card as an image, flash clones. New units boot
pre-installed and self-identify.

### 2a. Prepare the golden master

On a fully provisioned, verified unit:

```
sudo systemctl enable hht-firstboot.service      # identity-on-first-boot, see below
sudo sed -i 's/^id = ".*"/id = "HHT-AUTO"/' /etc/hht/hht.toml
sudo rm -f /var/log/hht/* /var/lib/hht/queue.db* # no unit-specific residue
history -c && sudo poweroff
```

`HHT-AUTO` is the placeholder that arms `hht-firstboot.sh` — on the next boot of any
clone it will:

- set `device.id = HHT-<last 4 of SoC serial>` and hostname `hht-<serial4>`,
- **regenerate SSH host keys and `/etc/machine-id`** — cloned images must never share
  these (duplicate host keys trigger MITM warnings and are a real security problem;
  duplicate machine-ids break DHCP leases on some networks),
- disable itself implicitly (the placeholder is gone).

### 2b. Capture and shrink

On the workstation, SD card in a USB reader (device name from `lsblk` — double-check,
`dd` does not forgive):

```
sudo dd if=/dev/sdX of=hht-golden-v1.img bs=4M status=progress
sudo pishrink.sh hht-golden-v1.img            # github.com/Drewsif/PiShrink
```

PiShrink shrinks the image to its used size and re-arms the first-boot filesystem
expansion, so clones auto-expand to whatever card they land on. Version the image
(`v1`, `v2`, …) and record the repo commit it was built from.

### 2c. Flash clones

Raspberry Pi Imager → *Use custom* → `hht-golden-v1.img`. Per-unit WiFi/SSH/hostname
customization in the Imager is **not needed** (baked in / derived at first boot) — but
note the Imager's customization dialog is designed for official Raspberry Pi OS images;
on custom images, skip it and let `hht-firstboot` do identity.

### 2d. First boot per unit (~10 min bench time)

1. Boot on USB power → unit renames itself (`HHT-xxxx` on the status screen, Select
   button) and comes up in the login screen — no SSH needed.
2. `scripts/verify_unit.sh` over SSH (find it as `hht-<serial4>.local`) → all PASS.
3. Button sweep + one test pick against the QR sheet.
4. Record the unit in the fleet table below; file the verify output as evidence.

### Fleet record

| Device ID | Serial | Golden image | Verified (date, by) | Notes |
|---|---|---|---|---|
| HHT-001 | | (hand-provisioned, reference unit) | 2026-07-12 | bring-up findings unit |
| | | | | |

## Tier 3 — image-as-code (outlook, beyond PoC scope)

For a real deployment the golden image itself should be reproducible from source
rather than captured from a hand-provisioned unit:

- [rpi-image-gen](https://github.com/raspberrypi/rpi-image-gen) (Raspberry Pi's
  official tool) or pi-gen: build the image in CI — apt packages, repo checkout,
  overlays and units declared in config, output versioned artifacts.
- [sdm](https://github.com/gitbls/sdm): customize a stock IMG offline (plugins for
  apt, users, WiFi) and apply per-unit tweaks at burn time — a middle ground that
  avoids maintaining captured images.
- Fleet configuration drift (config changes after deployment, app updates) is then a
  separate concern — `git pull` + `systemctl restart hht` per unit at this scale;
  Ansible or a device-management agent beyond it.

## Why the app itself needs no per-unit work

Everything device-specific lives in `/etc/hht/hht.toml` (identity, WMS URL, pins,
timeouts) — the code is identical on every unit, and `hht.service` runs it at boot as
a systemd **system service**: no auto-login, no desktop, no user session. A worker
powers the terminal on and sees the badge screen; SSH exists only for provisioning
and diagnostics.

## References

- PiShrink — https://github.com/Drewsif/PiShrink
- Cloning Pi SD cards, clone-hygiene checklist (host keys, machine-id, hostname) —
  https://www.dzombak.com/blog/2024/09/cloning-raspberry-pi-sd-cards/
- Raspberry Pi Imager OS customization —
  https://raspberry.tips/en/raspberrypi-tutorials/raspberry-pi-imager-guide-en
- rpi-image-gen — https://github.com/raspberrypi/rpi-image-gen
- sdm — https://github.com/gitbls/sdm
- Golden-image fleet pattern — https://qbee.io/docs/how-to-raspberrypi-golden-image.html
