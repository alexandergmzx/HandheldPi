#!/usr/bin/env bash
# Automated provisioning verification for an HHT unit — read-only, no sudo.
# Runs every software-checkable item of the Phase 0 checklist (see PLAN.md and
# docs/DEVICE_CONFIGURATION.md) and prints one PASS/FAIL line per check.
# Paste the output into the test report as provisioning evidence.
#
#   scripts/verify_unit.sh [-c /etc/hht/hht.toml]
#
# Exit code 0 = every mandatory check passed (warnings allowed).
# Deliberately NO pipefail: `grep -q` exits on first match and the resulting
# SIGPIPE in a still-writing producer (rpicam-hello, dmesg) would turn a
# passing pipeline into a failure (bit us on HHT-001).
set -u

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CFG="/etc/hht/hht.toml"
while getopts "c:" opt; do [[ $opt == c ]] && CFG="$OPTARG"; done

CONFIG_TXT="/boot/firmware/config.txt"
PY="$REPO_DIR/.venv/bin/python"
PASS=0; FAIL=0; WARN=0

ok()   { echo "[PASS] $1"; PASS=$((PASS+1)); }
ko()   { echo "[FAIL] $1"; FAIL=$((FAIL+1)); }
warn() { echo "[WARN] $1"; WARN=$((WARN+1)); }
check() { local d="$1"; shift; if "$@" >/dev/null 2>&1; then ok "$d"; else ko "$d"; fi; }

echo "HHT unit verification — $(hostname), $(date -Is)"
echo "repo: $REPO_DIR  config: $CFG"
echo

# --- OS ---------------------------------------------------------------------
check "64-bit OS (arm64)" [ "$(dpkg --print-architecture 2>/dev/null)" = "arm64" ]
if grep -q bookworm /etc/os-release; then ok "Raspberry Pi OS bookworm"; \
  else warn "not bookworm — procedures were validated on bookworm"; fi

# --- display ----------------------------------------------------------------
check "config.txt: HHT display block present" \
    grep -q "HHT GamePi20 display" "$CONFIG_TXT"
check "config.txt: SPI mode 3 (cpha,cpol) on overlay line" \
    grep -q "mipi-dbi-spi.*cpha,cpol" "$CONFIG_TXT"
check "panel firmware blob /lib/firmware/gamepi20.bin" \
    [ -f /lib/firmware/gamepi20.bin ]
check "panel_mipi_dbi in modules-load.d" \
    grep -qx panel_mipi_dbi /etc/modules-load.d/hht-display.conf
check "panel framebuffer registered (panel-mipi-dbid)" \
    grep -q "panel-mipi-dbi" /sys/class/graphics/fb*/name
if [ -e /sys/bus/spi/devices/spi0.0/driver ]; then
    check "spi0.0 bound to panel-mipi-dbi-spi" \
        sh -c 'readlink /sys/bus/spi/devices/spi0.0/driver | grep -q panel-mipi-dbi-spi'
else
    ko "spi0.0 has no driver bound"
fi

# --- boot splash / console ----------------------------------------------------
# Two valid modes, owned by setup_splash.sh: production (plymouth splash on the
# LCD, silent console on HDMI) and diag (verbose console on the LCD). The
# provisioning gate is "all PASS" — diag deliberately WARNs so a unit can't be
# handed over with a console on the screen.
CMDLINE_TXT="/boot/firmware/cmdline.txt"
if grep -qw splash "$CMDLINE_TXT" 2>/dev/null; then BOOTMODE=production
elif grep -q "fbcon=map:1" "$CMDLINE_TXT" 2>/dev/null; then BOOTMODE=diag
else BOOTMODE=unknown; fi

case $BOOTMODE in
production)
    ok "boot mode: production (splash on LCD, console on HDMI)"
    check "plymouth installed" dpkg -s plymouth
    check "plymouth-themes installed (script plugin)" dpkg -s plymouth-themes
    check "default plymouth theme is hht" \
        grep -qx "Theme=hht" /etc/plymouth/plymouthd.conf
    check "theme files in /usr/share/plymouth/themes/hht" \
        sh -c '[ -f /usr/share/plymouth/themes/hht/hht.script ] && [ -f /usr/share/plymouth/themes/hht/logo.png ]'
    check "plymouthd.conf: ShowDelay=0 (Debian default 5 s would hide the splash)" \
        grep -qx "ShowDelay=0" /etc/plymouth/plymouthd.conf
    check "cmdline: quiet" grep -qw quiet "$CMDLINE_TXT"
    check "cmdline: plymouth.ignore-serial-consoles (serial console present)" \
        grep -q "plymouth.ignore-serial-consoles" "$CMDLINE_TXT"
    if grep -q "fbcon=map:" "$CMDLINE_TXT"; then
        ko "cmdline: fbcon remap present in production (console would hit the LCD)"
    else
        ok "cmdline: no fbcon remap (console stays on HDMI/fb0)"
    fi
    check "config.txt: disable_splash=1 (firmware rainbow off)" \
        grep -q "^disable_splash=1" "$CONFIG_TXT"
    check "initramfs hook installed and executable" \
        [ -x /etc/initramfs-tools/hooks/hht-display ]
    for m in vc4 spi_bcm2835 gpio_backlight panel_mipi_dbi; do
        check "initramfs-tools/modules: $m" \
            grep -qx "$m" /etc/initramfs-tools/modules
    done
    # capture lsinitramfs ONCE (slow on a Zero 2 W; and grep -q on a live pipe
    # is the SIGPIPE trap the header warns about)
    INITRD="$(ls -t /boot/firmware/initramfs* 2>/dev/null | head -n1)"
    if [ -n "$INITRD" ]; then
        INITRD_LIST="$(lsinitramfs "$INITRD" 2>/dev/null)"
        for pat in "panel[-_]mipi[-_]dbi" "gamepi20.bin" "vc4.ko" "gpio[-_]backlight" "spi[-_]bcm2835"; do
            if echo "$INITRD_LIST" | grep -q -- "$pat"; then
                ok "initramfs contains $pat"
            else
                ko "initramfs missing $pat (re-run: sudo scripts/setup_splash.sh)"
            fi
        done
    else
        ko "no initramfs found in /boot/firmware (auto_initramfs off?)"
    fi
    check "hht.service ordered after plymouth-quit (DRM handoff)" \
        grep -q "plymouth-quit" /etc/systemd/system/hht.service
    # The app and plymouth both find the panel by name, so its fb index is not
    # pinned: headless it is fb0 (vc4 makes no fbdev without a display), with a
    # monitor attached the panel is fb1. Assert it is registered, not its index.
    check "panel framebuffer present for splash+app (found by name)" \
        grep -q "panel-mipi-dbi" /sys/class/graphics/fb*/name
    check "no console login on panel (getty@tty1 masked)" \
        sh -c '[ "$(systemctl is-enabled getty@tty1.service 2>/dev/null)" = masked ]'
    ;;
diag)
    warn "boot mode: diag (verbose console on LCD — not for handover; restore: sudo scripts/setup_splash.sh)"
    if grep -qw splash "$CMDLINE_TXT"; then
        ko "cmdline: splash present alongside diag console"
    else
        ok "cmdline: no splash (diag)"
    fi
    check "console login on panel restored (getty@tty1 not masked)" \
        sh -c '[ "$(systemctl is-enabled getty@tty1.service 2>/dev/null)" != masked ]'
    ;;
*)
    ko "boot mode neither production nor diag — run: sudo scripts/setup_splash.sh"
    ;;
esac

# --- camera -----------------------------------------------------------------
check "config.txt: HHT camera block present" \
    grep -q "HHT camera" "$CONFIG_TXT"
check "config.txt: camera_auto_detect effectively off" \
    sh -c '[ "$(grep "^camera_auto_detect=" '"$CONFIG_TXT"' | tail -1)" = "camera_auto_detect=0" ]'
if ls /sys/bus/i2c/drivers/imx708 2>/dev/null | grep -q -- "-001a"; then
    ok "camera imx708 bound (sysfs i2c driver)"
elif rpicam-hello --list-cameras 2>/dev/null | grep -q imx708; then
    ok "camera imx708 detected by libcamera"
elif dmesg 2>/dev/null | grep -q "camera module ID"; then
    ok "camera imx708 probed (dmesg)"
else
    ko "camera imx708 not detected (check FFC cable + overlay)"
fi

# --- audio ------------------------------------------------------------------
check "config.txt: HHT GamePi20 audio block present" \
    grep -q "HHT GamePi20 audio" "$CONFIG_TXT"
check "config.txt: PWM audio routed to GPIO18/19" \
    grep -q '^dtoverlay=audremap,pins_18_19$' "$CONFIG_TXT"
check "alsa-utils installed (aplay)" command -v aplay
check "bcm2835 Headphones ALSA device available" \
    sh -c "aplay -L | grep -q 'CARD=Headphones'"
check "all generated workflow sound cues present" \
    "$PY" -c "
from pathlib import Path
from hht.audio import SoundCue
p = Path(r'$REPO_DIR/assets/sounds')
assert all((p / f'{cue.value}.wav').is_file() for cue in SoundCue)
"
check "boot chime unit enabled (plays over the splash)" \
    systemctl is-enabled --quiet hht-boot-sound

# --- application ------------------------------------------------------------
check "venv app runs ($PY -m hht --version)" "$PY" -m hht --version
check "device config parses and validates" \
    "$PY" -c "from hht.config import load_config; load_config(r'$CFG')"
check "config: gpio input with all 12 buttons mapped" \
    "$PY" -c "
from hht.config import load_config
c = load_config(r'$CFG')
assert c.input.backend == 'gpio' and len(c.input.pins) == 12
"
check "config: framebuffer display backend" \
    "$PY" -c "
from hht.config import load_config
assert load_config(r'$CFG').display.backend == 'framebuffer'
"
check "config: ALSA audio backend" \
    "$PY" -c "
from hht.config import load_config
c = load_config(r'$CFG')
assert c.audio.backend == 'alsa' and 'Headphones' in c.audio.device
"

# --- service ----------------------------------------------------------------
check "hht.service enabled (starts at boot, no login needed)" \
    systemctl is-enabled --quiet hht
if systemctl is-active --quiet hht; then ok "hht.service active"; \
  elif pgrep -f "python -m hht" >/dev/null; then \
    warn "service inactive but a manual instance is running"; \
  else warn "hht not running (fine mid-provisioning; enable with: sudo systemctl enable --now hht)"; fi

RUN_USER="$(grep -s '^User=' /etc/systemd/system/hht.service | cut -d= -f2)"
RUN_USER="${RUN_USER:-$USER}"
for grp in video gpio spi audio; do
    check "user $RUN_USER in group $grp" \
        sh -c "id -nG '$RUN_USER' | tr ' ' '\n' | grep -qx $grp"
done

# --- summary ----------------------------------------------------------------
echo
echo "result: $PASS passed, $FAIL failed, $WARN warnings"
[ "$FAIL" -eq 0 ]
