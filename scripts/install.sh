#!/usr/bin/env bash
# HandheldPi provisioning — idempotent, run as root on the device:
#   sudo scripts/install.sh [--enable-service]
# Full procedure with verification steps: docs/DEVICE_CONFIGURATION.md
# Provisioning many units: docs/FLEET_PROVISIONING.md
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_USER="${SUDO_USER:-pi}"
ENABLE_SERVICE=0
[[ "${1:-}" == "--enable-service" ]] && ENABLE_SERVICE=1

[[ $EUID -eq 0 ]] || { echo "run with sudo"; exit 1; }

echo "==> [1/8] apt dependencies (kept lean for 512 MB RAM)"
apt-get update
apt-get install -y --no-install-recommends \
    python3-picamera2 python3-pyzbar python3-pil python3-numpy \
    python3-requests python3-gpiozero python3-lgpio \
    python3-venv fonts-dejavu-core alsa-utils

echo "==> [2/8] venv (--system-site-packages: picamera2/libcamera must come from apt)"
if [[ ! -d "$REPO_DIR/.venv" ]]; then
    sudo -u "$RUN_USER" python3 -m venv --system-site-packages "$REPO_DIR/.venv"
fi
sudo -u "$RUN_USER" "$REPO_DIR/.venv/bin/pip" install -q -e "$REPO_DIR"

echo "==> [3/8] config + data directories"
mkdir -p /etc/hht /var/log/hht /var/lib/hht
chown "$RUN_USER": /var/log/hht /var/lib/hht
if [[ ! -f /etc/hht/hht.toml ]]; then
    install -o "$RUN_USER" -m 644 "$REPO_DIR/config/hht.toml.example" /etc/hht/hht.toml
    echo "    created /etc/hht/hht.toml — EDIT device.id and wms.base_url"
else
    echo "    /etc/hht/hht.toml exists, leaving existing values alone"
    if ! grep -q '^\[audio\][[:space:]]*$' /etc/hht/hht.toml; then
        cat >> /etc/hht/hht.toml <<'EOF'

[audio]
backend = "alsa"
device = "plughw:CARD=Headphones,DEV=0"
sounds_dir = "assets/sounds"
queue_size = 8
EOF
        echo "    added missing [audio] section (set backend=\"none\" for silence)"
    fi
fi

echo "==> [4/8] display overlay (panel-mipi-dbi / ST7789V, SPI mode 3)"
"$REPO_DIR/scripts/setup_display.sh"

echo "==> [5/8] camera overlay (explicit imx708 — autodetect is unreliable)"
"$REPO_DIR/scripts/setup_camera.sh"

echo "==> [6/8] audio overlay (GamePi20 PWM input on GPIO18)"
"$REPO_DIR/scripts/setup_audio.sh"

echo "==> [7/8] device access for $RUN_USER"
usermod -aG video,render,gpio,spi,audio "$RUN_USER" 2>/dev/null || true

echo "==> [8/8] systemd units"
for unit in hht.service hht-firstboot.service; do
    sed -e "s|@REPO_DIR@|$REPO_DIR|g" -e "s|@RUN_USER@|$RUN_USER|g" \
        "$REPO_DIR/systemd/$unit" > "/etc/systemd/system/$unit"
done
systemctl daemon-reload
if [[ $ENABLE_SERVICE -eq 1 ]]; then
    systemctl enable hht.service
    echo "    hht.service enabled (starts on boot, no login needed)"
else
    echo "    hht.service installed but NOT enabled (finish Phase 0 first;"
    echo "    then: sudo systemctl enable --now hht)"
fi
echo "    hht-firstboot.service installed, NOT enabled (golden-image flow only)"

echo
echo "Done. Reboot to activate the overlays, then run scripts/verify_unit.sh —"
echo "all PASS means the unit matches the validated HHT-001 bring-up state."
