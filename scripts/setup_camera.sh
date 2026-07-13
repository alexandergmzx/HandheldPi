#!/usr/bin/env bash
# Install the explicit Camera Module 3 overlay for the HHT unit. Idempotent.
#
# Why not camera_auto_detect: the firmware fails to identify the CM3 on the
# Zero 2 W (detected=0) even with a good cable, while the kernel driver probes
# it fine (HHT-001 bring-up, 2026-07-12). And never a ,cam0 suffix — the
# Zero 2 W's single CSI port is CAM1, the overlay default.
set -euo pipefail

CONFIG_TXT="/boot/firmware/config.txt"
BEGIN_MARK="# --- HHT camera (managed by setup_camera.sh) ---"
END_MARK="# --- HHT camera end ---"

[[ $EUID -eq 0 ]] || { echo "run with sudo"; exit 1; }
[[ -f "$CONFIG_TXT" ]] || { echo "$CONFIG_TXT not found — is this a Raspberry Pi?"; exit 1; }

# neutralize the stock auto-detect line so ordering in the file never matters
sed -i 's/^camera_auto_detect=1/camera_auto_detect=0/' "$CONFIG_TXT"

# drop any previous HHT camera block (including hand-written variants)
sed -i "/^# --- HHT camera/,/^${END_MARK}$/d" "$CONFIG_TXT"

cat >> "$CONFIG_TXT" <<EOF
${BEGIN_MARK}
# Explicit overlay: firmware autodetect fails to identify the CM3 while the
# kernel probe works (HHT-001). No ,cam0 — the Zero 2 W CSI port is CAM1.
camera_auto_detect=0
dtoverlay=imx708
${END_MARK}
EOF

echo "    camera overlay written to $CONFIG_TXT (reboot required)"
echo "    verify after reboot: dmesg | grep imx708  ->  'camera module ID 0x0301'"
