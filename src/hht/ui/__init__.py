"""Display backends: ST7789V framebuffer (device), ANSI console + PNG dump (dev)."""

from __future__ import annotations

from abc import ABC, abstractmethod

from PIL import Image

from ..config import AppConfig

SCREEN_SIZE = (320, 240)


class Display(ABC):
    size = SCREEN_SIZE

    @abstractmethod
    def show(self, img: Image.Image, tag: str = "") -> None: ...

    def close(self) -> None:
        pass


class NullDisplay(Display):
    def show(self, img: Image.Image, tag: str = "") -> None:
        pass


def make_display(cfg: AppConfig) -> Display:
    if cfg.display.backend == "framebuffer":
        from .framebuffer import FramebufferDisplay

        return FramebufferDisplay(cfg.display.fb_device, cfg.display.rotation)
    if cfg.display.backend == "image":
        from .dev_displays import ImageDisplay

        return ImageDisplay(cfg.display.image_dir)
    from .dev_displays import ConsoleDisplay

    return ConsoleDisplay(cfg.display.console_cols)
