"""Blit PIL images to the panel-mipi-dbi framebuffer as RGB565.

The ST7789V panel registered by `dtoverlay=mipi-dbi-spi` appears as /dev/fbN with
16 bpp. `fb_device = "auto"` locates it by name so we never write to the HDMI
emulated framebuffer by accident.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
from PIL import Image

from ..logsetup import evt
from . import Display

log = logging.getLogger("hht.display.fb")


def _autodetect_fb() -> str:
    candidates = sorted(Path("/sys/class/graphics").glob("fb*"))
    for sys_dir in candidates:
        name = (sys_dir / "name").read_text().strip().lower()
        if any(key in name for key in ("mipi", "st7789", "panel")):
            return f"/dev/{sys_dir.name}"
    if candidates:
        return f"/dev/{candidates[0].name}"
    raise RuntimeError("no framebuffer found — is the display overlay installed?")


class FramebufferDisplay(Display):
    def __init__(self, fb_device: str, rotation: int = 0):
        self._rotation = rotation % 360
        device = _autodetect_fb() if fb_device == "auto" else fb_device
        sys_dir = Path("/sys/class/graphics") / Path(device).name
        w, h = (int(v) for v in (sys_dir / "virtual_size").read_text().split(","))
        self._fb_size = (w, h)
        self._bpp = int((sys_dir / "bits_per_pixel").read_text())
        self._stride = int((sys_dir / "stride").read_text())
        if self._bpp != 16:
            raise RuntimeError(f"{device}: expected 16 bpp RGB565, got {self._bpp}")
        self._fb = open(device, "r+b", buffering=0)
        evt(log, "framebuffer_opened", device=device, width=w, height=h,
            stride=self._stride)

    def show(self, img: Image.Image, tag: str = "") -> None:
        if self._rotation:
            img = img.rotate(-self._rotation, expand=True)
        if img.size != self._fb_size:
            img = img.resize(self._fb_size)
        rgb = np.asarray(img.convert("RGB"), dtype=np.uint16)
        rgb565 = ((rgb[..., 0] >> 3) << 11) | ((rgb[..., 1] >> 2) << 5) | (rgb[..., 2] >> 3)
        rows = rgb565.astype("<u2").tobytes(order="C")
        w, h = self._fb_size
        if self._stride > w * 2:  # pad each row to the fb line length
            padded = bytearray(self._stride * h)
            for y in range(h):
                padded[y * self._stride:y * self._stride + w * 2] = \
                    rows[y * w * 2:(y + 1) * w * 2]
            rows = bytes(padded)
        self._fb.seek(0)
        self._fb.write(rows)

    def close(self) -> None:
        self._fb.close()
