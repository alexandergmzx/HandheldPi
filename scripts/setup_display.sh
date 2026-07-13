#!/usr/bin/env bash
# Install the ST7789V panel firmware + mipi-dbi-spi overlay for the GamePi20 LCD.
# Idempotent; safe to re-run after editing firmware/st7789v_gamepi20.txt.
#
# Why this and not fbcp-ili9341: DispmanX is gone on modern Raspberry Pi OS (KMS stack); the
# mainline panel-mipi-dbi TinyDRM driver is the supported path. See PLAN.md.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_TXT="/boot/firmware/config.txt"
MIPI_CMD="$REPO_DIR/scripts/mipi-dbi-cmd"
BEGIN_MARK="# --- HHT GamePi20 display (managed by setup_display.sh) ---"
END_MARK="# --- HHT GamePi20 display end ---"

[[ $EUID -eq 0 ]] || { echo "run with sudo"; exit 1; }
[[ -f "$CONFIG_TXT" ]] || { echo "$CONFIG_TXT not found — is this a Raspberry Pi?"; exit 1; }

# 1. Fetch notro's firmware compiler once (small python script, MIT-ish kernel tooling)
if [[ ! -f "$MIPI_CMD" ]]; then
    echo "    fetching mipi-dbi-cmd"
    curl -fsSL -o "$MIPI_CMD" \
        https://raw.githubusercontent.com/notro/panel-mipi-dbi/main/mipi-dbi-cmd
    chmod +x "$MIPI_CMD"
fi

# 2. Compile the init sequence to the firmware blob the kernel will request.
# The blob name must match the FIRST compatible string below ("gamepi20").
echo "    compiling firmware/st7789v_gamepi20.txt -> /lib/firmware/gamepi20.bin"
python3 "$MIPI_CMD" /lib/firmware/gamepi20.bin "$REPO_DIR/firmware/st7789v_gamepi20.txt"

# 3. Managed config.txt block (replace previous block if present)
tmp="$(mktemp)"
sed "/^${BEGIN_MARK}$/,/^${END_MARK}$/d" "$CONFIG_TXT" > "$tmp"
cat >> "$tmp" <<EOF
${BEGIN_MARK}
dtparam=spi=on
dtoverlay=mipi-dbi-spi,spi0-0,speed=48000000,cpha,cpol
dtparam=compatible=gamepi20\\0panel-mipi-dbi-spi
dtparam=width=320,height=240,width-mm=40,height-mm=30
dtparam=reset-gpio=27,dc-gpio=25
dtparam=backlight-gpio=24
dtparam=write-only
${END_MARK}
EOF
cp "$tmp" "$CONFIG_TXT"
rm -f "$tmp"

# NOTE the first compatible ("gamepi20") is deliberately NOT "st7789v": a bare
# controller name generates the SPI modalias spi:st7789v, which the legacy staging
# fbtft module (fb_st7789v) claims — the wrong module loads and nothing binds
# (found during bring-up, 2026-07-12). The name is only used to pick the firmware
# file. And since SPI modalias autoload only considers that first name, the real
# driver is force-loaded at boot:
echo panel_mipi_dbi > /etc/modules-load.d/hht-display.conf
rm -f /lib/firmware/st7789v.bin   # stale blob from pre-rename installs

# Console/splash policy on cmdline.txt (splash vs verbose console-on-LCD) is
# owned by setup_splash.sh — this script only installs the panel driver.

echo "    display overlay written to $CONFIG_TXT (reboot required)"
echo "    NOTE: SPI mode 3 (cpha,cpol) is required by the Waveshare ST7789V module"
echo "    (mode 0 leaves it completely dark; RPi forums t=337019). Backlight only"
echo "    turns on when something paints the panel — a black screen right after"
echo "    boot with the hht service disabled is normal."
echo "    troubleshooting: upside-down/mirrored -> edit MADCTL (0x36) in"
echo "    firmware/st7789v_gamepi20.txt and re-run this script."
