"""Sanity checks on the committed Plymouth theme (assets/plymouth/hht/).
These run on the dev machine — the theme is installed verbatim by
scripts/setup_splash.sh, so a broken asset here means a black boot screen
on every cloned unit."""

import configparser
import re
from pathlib import Path

from PIL import Image

THEME = Path(__file__).parent.parent / "assets" / "plymouth" / "hht"
INSTALL_DIR = "/usr/share/plymouth/themes/hht"


def test_theme_descriptor_uses_script_plugin():
    cfg = configparser.ConfigParser()
    cfg.read(THEME / "hht.plymouth")
    assert cfg["Plymouth Theme"]["ModuleName"] == "script"
    assert cfg["script"]["ImageDir"] == INSTALL_DIR
    assert cfg["script"]["ScriptFile"] == f"{INSTALL_DIR}/hht.script"


def test_script_references_only_committed_images():
    script = (THEME / "hht.script").read_text()
    for name in re.findall(r'Image\("([^"]+)"\)', script):
        assert (THEME / name).exists(), f"hht.script references missing {name}"


def test_script_has_no_text_calls():
    # Image.Text() needs the plymouth-label plugin + pango + fonts inside the
    # initramfs; the theme must stay image-only or the splash silently fails.
    code = "\n".join(
        line.split("#")[0] for line in (THEME / "hht.script").read_text().splitlines()
    )
    assert "Image.Text" not in code


def test_logo_fits_the_panel():
    with Image.open(THEME / "logo.png") as logo:
        assert logo.width <= 320 and logo.height <= 240
        # transparent background: the script paints the BG color itself
        assert logo.mode == "RGBA"
