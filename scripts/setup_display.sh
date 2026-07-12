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

# 2. Compile the init sequence to the firmware blob the kernel will request
echo "    compiling firmware/st7789v_gamepi20.txt -> /lib/firmware/st7789v.bin"
python3 "$MIPI_CMD" /lib/firmware/st7789v.bin "$REPO_DIR/firmware/st7789v_gamepi20.txt"

# 3. Managed config.txt block (replace previous block if present)
tmp="$(mktemp)"
sed "/^${BEGIN_MARK}$/,/^${END_MARK}$/d" "$CONFIG_TXT" > "$tmp"
cat >> "$tmp" <<EOF
${BEGIN_MARK}
dtparam=spi=on
dtoverlay=mipi-dbi-spi,spi0-0,speed=48000000
dtparam=compatible=st7789v\\0panel-mipi-dbi-spi
dtparam=width=320,height=240
dtparam=reset-gpio=27,dc-gpio=25
dtparam=backlight-gpio=24
dtparam=write-only
${END_MARK}
EOF
cp "$tmp" "$CONFIG_TXT"
rm -f "$tmp"

echo "    display overlay written to $CONFIG_TXT (reboot required)"
echo "    troubleshooting: garbage pixels -> add 'dtparam=cpha,cpol' (SPI mode 3);"
echo "    mirrored/rotated -> edit MADCTL (0x36) in firmware/st7789v_gamepi20.txt"
echo "    and re-run this script. To show the boot console on the LCD, append"
echo "    'fbcon=map:1' (panel usually enumerates as fb1 next to the HDMI fb0)"
echo "    to /boot/firmware/cmdline.txt."
