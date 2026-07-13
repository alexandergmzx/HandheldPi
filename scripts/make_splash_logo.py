#!/usr/bin/env python3
"""Generate the Plymouth splash logo (assets/plymouth/hht/logo.png).

Dev-machine tool, not part of provisioning: the PNG is committed so a unit
never needs Pillow or fonts to install the theme. Re-run after changing the
wordmark or colors, then commit the result:

    python3 scripts/make_splash_logo.py

Colors mirror src/hht/ui/screens.py (not imported: that module drags in the
state machine, and this script must run on a bare dev checkout).
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BG = (10, 12, 16)  # painted by hht.script as the window background
ACCENT = (255, 200, 40)
DIM = (135, 140, 150)

SIZE = (240, 100)  # comfortably inside the 320x240 panel

_FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def _font(size: int) -> ImageFont.FreeTypeFont:
    for path in _FONT_PATHS:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    raise SystemExit("DejaVu fonts not found — apt install fonts-dejavu-core")


def main() -> None:
    img = Image.new("RGBA", SIZE, (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    w = SIZE[0] // 2
    d.text((w, 38), "HHT", font=_font(58), fill=ACCENT, anchor="mm")
    d.text((w, 82), "warehouse terminal", font=_font(16), fill=DIM, anchor="mm")

    out = Path(__file__).resolve().parent.parent / "assets/plymouth/hht/logo.png"
    img.save(out)
    print(f"wrote {out} ({SIZE[0]}x{SIZE[1]})")


if __name__ == "__main__":
    main()
