#!/usr/bin/env bash
# Automated provisioning verification for an HHT unit — read-only, no sudo.
# Runs every software-checkable item of the Phase 0 checklist (see PLAN.md and
# docs/DEVICE_CONFIGURATION.md) and prints one PASS/FAIL line per check.
# Paste the output into the test report as provisioning evidence.
#
#   scripts/verify_unit.sh [-c /etc/hht/hht.toml]
#
# Exit code 0 = every mandatory check passed (warnings allowed).
set -uo pipefail

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

# --- camera -----------------------------------------------------------------
check "config.txt: HHT camera block present" \
    grep -q "HHT camera" "$CONFIG_TXT"
check "config.txt: camera_auto_detect effectively off" \
    sh -c '[ "$(grep "^camera_auto_detect=" '"$CONFIG_TXT"' | tail -1)" = "camera_auto_detect=0" ]'
if rpicam-hello --list-cameras 2>/dev/null | grep -q imx708; then
    ok "camera imx708 detected by libcamera"
elif dmesg 2>/dev/null | grep -q "camera module ID"; then
    ok "camera imx708 probed (dmesg)"
else
    ko "camera imx708 not detected (check FFC cable + overlay)"
fi

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

# --- service ----------------------------------------------------------------
check "hht.service enabled (starts at boot, no login needed)" \
    systemctl is-enabled --quiet hht
if systemctl is-active --quiet hht; then ok "hht.service active"; \
  elif pgrep -f "python -m hht" >/dev/null; then \
    warn "service inactive but a manual instance is running"; \
  else warn "hht not running (fine mid-provisioning; enable with: sudo systemctl enable --now hht)"; fi

RUN_USER="$(grep -s '^User=' /etc/systemd/system/hht.service | cut -d= -f2)"
RUN_USER="${RUN_USER:-$USER}"
for grp in video gpio spi; do
    check "user $RUN_USER in group $grp" \
        sh -c "id -nG '$RUN_USER' | tr ' ' '\n' | grep -qx $grp"
done

# --- summary ----------------------------------------------------------------
echo
echo "result: $PASS passed, $FAIL failed, $WARN warnings"
[ "$FAIL" -eq 0 ]
