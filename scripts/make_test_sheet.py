#!/usr/bin/env python3
"""Generate docs/img/test_qr_sheet.png — the provisioning / dry-run QR sheet.

One printable page with every code the on-device dry run needs, matching the
mock WMS built-in data (badge OP:picker01 PIN 1234; tasks 101–103) plus the
negative labels that must be REJECTED (wrong location, wrong article, bare
EAN — v1 payloads are exact, TC-032/033/034).

Usage:
    python scripts/make_test_sheet.py [-o docs/img/test_qr_sheet.png]

Requires the dev extra (pip install -e ".[dev]") for the `qrcode` library;
this never runs on the device — print the PNG or display it on a monitor.
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

# (payload, title, subtitle) — rows of three, None = empty cell.
# Mirrors _SAMPLE_TASKS in src/hht/wms/mock_client.py; keep in sync.
_CELLS = [
    ("OP:picker01", "OPERATOR BADGE", "Picker One / PIN 1234"),
    ("LOC:Z-99-99", "WRONG LOCATION", "must be rejected"),
    ("8412345678905", "BARE EAN", "must be rejected (no ART: prefix)"),

    ("LOC:A-01-03", "LOCATION — task 101", "order M-1001 line 1"),
    ("ART:ART-SHIRT", "ARTICLE — task 101", "Blue T-Shirt M · qty 3"),
    ("ART:WRONG-SKU", "WRONG ARTICLE", "must be rejected"),

    ("LOC:B-02-07", "LOCATION — task 102", "order M-1001 line 2"),
    ("ART:ART-SNEAKER", "ARTICLE — task 102", "Sneaker 42 · qty 1"),
    None,

    ("LOC:C-11-01", "LOCATION — task 103", "order M-1002 line 1"),
    ("ART:ART-CAP", "ARTICLE — task 103", "Cap Black · qty 5"),
    None,
]

_COLS = 3
_CELL_W, _CELL_H = 370, 400
_HEADER_H = 70


def _font(size: int) -> ImageFont.FreeTypeFont:
    for path in _FONT_PATHS:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _qr(payload: str) -> Image.Image:
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M,
                       box_size=6, border=3)
    qr.add_data(payload)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white").convert("RGB")


def make_sheet(out: Path) -> None:
    rows = (len(_CELLS) + _COLS - 1) // _COLS
    img = Image.new("RGB", (_COLS * _CELL_W, _HEADER_H + rows * _CELL_H), "white")
    d = ImageDraw.Draw(img)
    d.text((img.width // 2, _HEADER_H // 2),
           "HandheldPi — provisioning & dry-run QR sheet (mock WMS data)",
           font=_font(30), fill="black", anchor="mm")

    for i, cell in enumerate(_CELLS):
        if cell is None:
            continue
        payload, title, subtitle = cell
        x0 = (i % _COLS) * _CELL_W
        y0 = _HEADER_H + (i // _COLS) * _CELL_H
        code = _qr(payload)
        img.paste(code, (x0 + (_CELL_W - code.width) // 2, y0 + 10))
        cx = x0 + _CELL_W // 2
        ty = y0 + 10 + code.height + 8
        d.text((cx, ty), title, font=_font(24), fill="black", anchor="ma")
        d.text((cx, ty + 32), payload, font=_font(20), fill="black", anchor="ma")
        d.text((cx, ty + 60), subtitle, font=_font(16), fill="gray", anchor="ma")

    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    print(f"wrote {out} ({sum(1 for c in _CELLS if c)} codes)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("-o", "--out", type=Path,
                        default=Path("docs/img/test_qr_sheet.png"))
    args = parser.parse_args()
    make_sheet(args.out)


if __name__ == "__main__":
    main()
