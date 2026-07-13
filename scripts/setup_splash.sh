#!/usr/bin/env bash
# Boot/shutdown behavior of the unit — owns the console/splash policy on
# cmdline.txt (setup_display.sh only installs the panel driver). Idempotent,
# switchable both directions on an already-provisioned unit:
#
#   sudo scripts/setup_splash.sh          # production: plymouth splash on the
#                                         # LCD, silent boot, console on HDMI
#   sudo scripts/setup_splash.sh --diag   # bring-up: verbose boot console on
#                                         # the LCD (fbcon=map:1), no splash
#
# Production bakes the panel stack (vc4 first — see fb-order note below) into
# the initramfs so plymouth can light the LCD ~4-5 s into boot; the first
# seconds stay dark regardless (the GPU bootloader only drives HDMI).
# --diag leaves plymouth installed but dormant: without 'splash' on the
# cmdline plymouthd never grabs the display, so switching modes is a
# cmdline-only edit (no initramfs rebuild, seconds not minutes).
# Full rationale + troubleshooting: docs/DEVICE_CONFIGURATION.md §3.4.1
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_TXT="/boot/firmware/config.txt"
CMDLINE_TXT="/boot/firmware/cmdline.txt"
THEME_DIR="/usr/share/plymouth/themes/hht"
HOOK="/etc/initramfs-tools/hooks/hht-display"
MODULES_FILE="/etc/initramfs-tools/modules"
PLYMOUTHD_CONF="/etc/plymouth/plymouthd.conf"
BEGIN_MARK="# --- HHT boot splash (managed by setup_splash.sh) ---"
END_MARK="# --- HHT boot splash end ---"

# Every cmdline parameter this script may add in either mode; the normalizer
# strips all of them before appending the current mode's set, which migrates
# any historical state (including pre-splash units that had fbcon=map:1 from
# setup_display.sh) and keeps re-runs idempotent.
MANAGED_PARAMS="quiet splash plymouth.ignore-serial-consoles loglevel=3
logo.nologo systemd.show_status=false vt.global_cursor_default=0 fbcon=map:1
plymouth.debug"
PROD_PARAMS="quiet splash plymouth.ignore-serial-consoles loglevel=3 logo.nologo systemd.show_status=false vt.global_cursor_default=0"
DIAG_PARAMS="fbcon=map:1 vt.global_cursor_default=0"

MODE=production
PANEL_BAKE=1
for arg in "$@"; do
    case "$arg" in
        --diag) MODE=diag ;;
        # dev-only, for staged bring-up: install splash but skip baking the
        # panel stack into the initramfs (splash then starts ~7 s in, when
        # the panel driver binds from the rootfs and plymouth hotplugs it)
        --no-panel-bake) PANEL_BAKE=0 ;;
        *) echo "usage: sudo scripts/setup_splash.sh [--diag]"; exit 1 ;;
    esac
done

[[ $EUID -eq 0 ]] || { echo "run with sudo"; exit 1; }
[[ -f "$CONFIG_TXT" ]] || { echo "$CONFIG_TXT not found — is this a Raspberry Pi?"; exit 1; }

changed=0
inst() { # inst SRC DEST MODE — install only when content differs
    if ! cmp -s "$1" "$2" 2>/dev/null; then
        install -D -m "$3" "$1" "$2"
        changed=1
    fi
}

write_config_block() { # both modes: kill the firmware rainbow on HDMI
    local tmp; tmp="$(mktemp)"
    sed "/^${BEGIN_MARK}$/,/^${END_MARK}$/d" "$CONFIG_TXT" > "$tmp"
    cat >> "$tmp" <<EOF
${BEGIN_MARK}
disable_splash=1
${END_MARK}
EOF
    cmp -s "$tmp" "$CONFIG_TXT" || cp "$tmp" "$CONFIG_TXT"
    rm -f "$tmp"
}

normalize_cmdline() { # $1 = parameters to append after stripping managed ones
    local line1 tok m keep new=""
    line1="$(head -n1 "$CMDLINE_TXT")"
    for tok in $line1; do
        keep=1
        for m in $MANAGED_PARAMS; do
            [[ "$tok" == "$m" ]] && { keep=0; break; }
        done
        [[ $keep -eq 1 ]] && new+="${tok} "
    done
    printf '%s\n' "${new}$1" > "${CMDLINE_TXT}.tmp"
    # keep any extra lines (cmdline.txt is single-line on Raspberry Pi OS,
    # but don't destroy hand edits if it isn't)
    tail -n +2 "$CMDLINE_TXT" >> "${CMDLINE_TXT}.tmp"
    mv "${CMDLINE_TXT}.tmp" "$CMDLINE_TXT"
}

if [[ $MODE == diag ]]; then
    write_config_block
    normalize_cmdline "$DIAG_PARAMS"
    # Restore the local console login on the panel — the whole point of diag.
    systemctl unmask getty@tty1.service >/dev/null 2>&1 || true
    echo "    diag mode: verbose boot console on the LCD (fbcon=map:1), no splash"
    echo "    plymouth (if installed) stays dormant without 'splash' on the cmdline"
    echo "    reboot to apply; back to production: sudo scripts/setup_splash.sh"
    exit 0
fi

# --- production ---------------------------------------------------------------

# 1. The splash reuses the panel firmware installed by setup_display.sh.
[[ -f /lib/firmware/gamepi20.bin ]] || {
    echo "/lib/firmware/gamepi20.bin missing — run scripts/setup_display.sh first"; exit 1; }

# 2. plymouth (not shipped on Raspberry Pi OS Lite); plymouth-themes provides
# the 'script' plugin the hht theme uses.
if ! dpkg -s plymouth >/dev/null 2>&1 || ! dpkg -s plymouth-themes >/dev/null 2>&1; then
    echo "    installing plymouth + plymouth-themes"
    apt-get install -y --no-install-recommends plymouth plymouth-themes
    changed=1
fi

# 3. Theme (image-only: no Image.Text(), which would need the label plugin +
# pango + fonts inside the initramfs).
inst "$REPO_DIR/assets/plymouth/hht/hht.plymouth" "$THEME_DIR/hht.plymouth" 644
inst "$REPO_DIR/assets/plymouth/hht/hht.script"   "$THEME_DIR/hht.script"   644
inst "$REPO_DIR/assets/plymouth/hht/logo.png"     "$THEME_DIR/logo.png"     644

if [[ $PANEL_BAKE -eq 1 ]]; then
    # 4. initramfs hook: the panel stack + its firmware blob. The app and the
    # splash both find the panel by NAME, so its fb index is irrelevant
    # (headless it is fb0 since vc4 makes no fbdev without a display; with a
    # monitor attached vc4 takes fb0 and the panel is fb1). vc4 is still listed
    # first for a deterministic DRM card order. gpio_backlight is required:
    # without it the panel probe EPROBE_DEFERs on the backlight-gpio node and
    # never binds in the initramfs.
    tmp_hook="$(mktemp)"
    cat > "$tmp_hook" <<'EOF'
#!/bin/sh
# Bake the GamePi20 panel stack into the initramfs so plymouth can splash on
# the LCD seconds into boot. vc4 goes first for a deterministic DRM card order;
# the app and splash find the panel by name, so its fb index is not pinned.
# Installed by scripts/setup_splash.sh.
PREREQ=""
prereqs() { echo "$PREREQ"; }
case "$1" in prereqs) prereqs; exit 0 ;; esac
. /usr/share/initramfs-tools/hook-functions
manual_add_modules vc4
manual_add_modules spi_bcm2835
manual_add_modules gpio_backlight
manual_add_modules panel_mipi_dbi
# panel-mipi-dbi requests its firmware by a DT-derived name ("gamepi20.bin"),
# which initramfs-tools cannot discover — copy it explicitly. Warn instead of
# fail: a failing hook would break every future kernel postinst.
if [ -f /lib/firmware/gamepi20.bin ]; then
    mkdir -p "${DESTDIR}/lib/firmware"
    cp -p /lib/firmware/gamepi20.bin "${DESTDIR}/lib/firmware/"
else
    echo "hht-display hook: /lib/firmware/gamepi20.bin missing — splash will stay on HDMI" >&2
fi
EOF
    inst "$tmp_hook" "$HOOK" 755
    rm -f "$tmp_hook"

    # 5. Force-load the same modules in the initramfs: udev coldplug cannot —
    # the neutral "gamepi20" compatible matches no modalias (same finding as
    # setup_display.sh, which force-loads it from the rootfs).
    for m in vc4 spi_bcm2835 gpio_backlight panel_mipi_dbi; do
        grep -qx "$m" "$MODULES_FILE" 2>/dev/null || { echo "$m" >> "$MODULES_FILE"; changed=1; }
    done
fi

# 6. Select the theme; kill Debian's default ShowDelay=5 (splash suppressed
# for the first 5 s — on this hardware that is most of the boot).
# Guard on plymouthd.conf, not `plymouth-set-default-theme` (the query): its
# parser is thrown off by commented Theme= lines in the stock conf and can
# report the wrong theme, which would rebuild the initramfs on every run.
# plymouthd itself reads [Daemon] Theme= from this file (verified on HHT-001).
if ! grep -qx "Theme=hht" "$PLYMOUTHD_CONF" 2>/dev/null; then
    plymouth-set-default-theme hht
    changed=1
fi
if ! grep -qx 'ShowDelay=0' "$PLYMOUTHD_CONF" 2>/dev/null; then
    sed -i '/^ShowDelay=/d' "$PLYMOUTHD_CONF"
    if grep -q '^\[Daemon\]' "$PLYMOUTHD_CONF"; then
        sed -i '/^\[Daemon\]/a ShowDelay=0' "$PLYMOUTHD_CONF"
    else
        printf '[Daemon]\nShowDelay=0\n' >> "$PLYMOUTHD_CONF"
    fi
    changed=1
fi

# 7+8. Firmware splash off; silent cmdline. plymouth.ignore-serial-consoles is
# mandatory — cmdline has console=serial0,115200 and plymouth would otherwise
# stay in text mode on the serial console.
write_config_block
normalize_cmdline "$PROD_PARAMS"

# No local console login on the panel: the app owns the screen. On a headless
# unit the panel is the default console (fb0), so getty@tty1 would paint
# "<host> login:" and a blinking cursor over the app. Masking stops systemd's
# autovt from respawning it; SSH is unaffected, and --diag restores it.
systemctl mask --now getty@tty1.service >/dev/null 2>&1 || true

# 9. Rebuild the initramfs only when something changed (1-2 min on a Zero 2 W).
INITRD="$(ls -t /boot/firmware/initramfs* 2>/dev/null | head -n1 || true)"
need_rebuild=$changed
if [[ -z "$INITRD" ]]; then
    need_rebuild=1
elif [[ $PANEL_BAKE -eq 1 ]]; then
    initrd_list="$(lsinitramfs "$INITRD" 2>/dev/null || true)"
    grep -q "panel[-_]mipi[-_]dbi" <<< "$initrd_list" || need_rebuild=1
fi
if [[ $need_rebuild -eq 1 ]]; then
    echo "    rebuilding initramfs (theme + panel stack; takes a minute or two)"
    update-initramfs -u
fi

echo "    production mode: splash on the LCD, silent boot, console on HDMI"
echo "    reboot to apply. The panel stays dark for the first ~5 s (GPU"
echo "    bootloader drives HDMI only) — that is normal."
echo "    verbose console for bench debugging: sudo scripts/setup_splash.sh --diag"
