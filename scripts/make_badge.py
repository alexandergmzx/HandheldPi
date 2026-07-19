#!/usr/bin/env python3
"""Generate an operator badge QR (payload OP:<username>) as a PNG.

Badges are a device-side login convention: the badge supplies the WMS
username, the PIN pad supplies the password. The WMS itself never sees
badges, which is why this is a dev-side script and not a WMS label endpoint
(those cover LOC:/ART: labels only).

Usage:
    python scripts/make_badge.py picker02 -o badge-picker02.png

Requires the dev extra (pip install -e ".[dev]") for the `qrcode` library;
this never runs on the device.
"""

from __future__ import annotations

import argparse
from pathlib import Path

try:
    import qrcode
except ImportError:  # pragma: no cover
    raise SystemExit("qrcode not installed — run: pip install -e '.[dev]'")

from PIL import Image, ImageDraw, ImageFont

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
    return ImageFont.load_default()


def make_badge(username: str, out: Path) -> None:
    payload = f"OP:{username}"
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M,
                       box_size=10, border=4)
    qr.add_data(payload)
    qr.make(fit=True)
    code = qr.make_image(fill_color="black", back_color="white").convert("RGB")

    caption_h = 60
    img = Image.new("RGB", (code.width, code.height + caption_h), "white")
    img.paste(code, (0, 0))
    d = ImageDraw.Draw(img)
    d.text((img.width // 2, code.height + 8), f"Operator {username}",
           font=_font(28), fill="black", anchor="ma")
    img.save(out)
    print(f"wrote {out} ({payload})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("username", help="WMS username the badge encodes")
    parser.add_argument("-o", "--out", type=Path, default=None,
                        help="output PNG (default badge-<username>.png)")
    args = parser.parse_args()
    make_badge(args.username, args.out or Path(f"badge-{args.username}.png"))


if __name__ == "__main__":
    main()
