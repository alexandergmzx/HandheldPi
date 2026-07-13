#!/usr/bin/env bash
# Golden-image first boot: give a cloned unit its own identity. Idempotent —
# only acts while /etc/hht/hht.toml still carries the placeholder id HHT-AUTO
# (set on the golden master before capturing the image; see
# docs/FLEET_PROVISIONING.md). Runs as hht-firstboot.service.
set -euo pipefail

CFG="/etc/hht/hht.toml"
grep -q '^id = "HHT-AUTO"' "$CFG" 2>/dev/null || exit 0

serial="$(awk '/^Serial/ {s=$3} END {print toupper(substr(s, length(s)-3))}' /proc/cpuinfo)"
[[ -n "$serial" ]] || { echo "hht-firstboot: no CPU serial found"; exit 1; }

# unit identity: device.id + hostname derived from the SoC serial
sed -i "s/^id = \"HHT-AUTO\"/id = \"HHT-${serial}\"/" "$CFG"
hostnamectl set-hostname "hht-$(echo "$serial" | tr '[:upper:]' '[:lower:]')"

# clone hygiene: cloned images must not share SSH host keys or machine-id
rm -f /etc/ssh/ssh_host_*
dpkg-reconfigure -f noninteractive openssh-server
rm -f /etc/machine-id
systemd-machine-id-setup

echo "hht-firstboot: identity set to HHT-${serial}"
