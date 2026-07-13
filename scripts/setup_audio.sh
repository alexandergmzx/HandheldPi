#!/usr/bin/env bash
# Route Raspberry Pi PWM audio to the GamePi20 headphone/speaker circuit.
#
# GamePi20 uses GPIO18 (physical pin 12) as its mono audio input. GPIO12 and
# GPIO13 are D-pad Up/Right, so the audremap default pins_12_13 must never be
# used on this device. Idempotent; reboot is required after changes.
set -euo pipefail

CONFIG_TXT="/boot/firmware/config.txt"
BEGIN_MARK="# --- HHT GamePi20 audio (managed by setup_audio.sh) ---"
END_MARK="# --- HHT GamePi20 audio end ---"

[[ $EUID -eq 0 ]] || { echo "run with sudo"; exit 1; }
[[ -f "$CONFIG_TXT" ]] || { echo "$CONFIG_TXT not found — is this a Raspberry Pi?"; exit 1; }

tmp="$(mktemp)"
# Replace our block and any older hand-written audremap line. Keeping two
# audremap overlays can leave the final GPIO function dependent on file order.
sed -e "/^${BEGIN_MARK}$/,/^${END_MARK}$/d" \
    -e '/^[[:space:]]*dtoverlay=audremap\([,[:space:]].*\)\{0,1\}[[:space:]]*$/d' \
    "$CONFIG_TXT" > "$tmp"
cat >> "$tmp" <<EOF
${BEGIN_MARK}
# Load bcm2835 ALSA audio and put PWM channels on GPIO18/19. The GamePi20
# circuit consumes GPIO18; GPIO19 is unconnected on this board.
dtparam=audio=on
dtoverlay=audremap,pins_18_19
${END_MARK}
EOF
cp "$tmp" "$CONFIG_TXT"
rm -f "$tmp"

echo "    PWM audio routed to GPIO18/19 in $CONFIG_TXT (reboot required)"
echo "    GamePi20 speaker input is GPIO18; GPIO12/13 remain D-pad buttons"
echo "    verify after reboot: aplay -l; pinctrl get 12 13 18"
