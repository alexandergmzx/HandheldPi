"""QR/barcode scanning: picamera2 YUV stream → Y plane (grayscale) → pyzbar.

Pipeline choices (see PLAN.md research notes):
- YUV420 capture: the Y plane IS the grayscale image zbar wants — no color conversion.
- pyzbar over OpenCV: multi-symbology (QR + EAN/Code128), lighter footprint on 512 MB.
- Camera Module 3 autofocus: continuous AF by default; lock the lens with
  af_mode = "manual" + lens_position (dioptres) for faster first decode at fixed range.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from ..config import AppConfig
from ..events import Event, ScanEvent
from ..logsetup import evt

log = logging.getLogger("hht.scanner")

# libcamera AfMode enum values (avoid importing libcamera just for two constants)
_AF_MANUAL, _AF_CONTINUOUS = 0, 2


class CameraScanner:
    def __init__(self, cfg: AppConfig):
        self._cfg = cfg.scanner
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._picam2 = None
        self._last_seen: dict[str, float] = {}

    def start(self, emit: Callable[[Event], None]) -> None:
        from picamera2 import Picamera2  # lazy: hardware-only dep

        w, h = self._cfg.frame_size
        self._picam2 = Picamera2()
        config = self._picam2.create_video_configuration(
            main={"size": (w, h), "format": "YUV420"},
            buffer_count=2,  # keep RAM usage down on the 512 MB Zero 2W
        )
        self._picam2.configure(config)
        if self._cfg.af_mode == "manual":
            self._picam2.set_controls(
                {"AfMode": _AF_MANUAL, "LensPosition": self._cfg.lens_position}
            )
        else:
            self._picam2.set_controls({"AfMode": _AF_CONTINUOUS})
        self._picam2.start()
        evt(log, "camera_started", frame_size=[w, h], af_mode=self._cfg.af_mode)

        self._thread = threading.Thread(
            target=self._loop, args=(emit,), name="scanner", daemon=True
        )
        self._thread.start()

    def _loop(self, emit: Callable[[Event], None]) -> None:
        from pyzbar.pyzbar import decode  # lazy: hardware-only dep

        w, h = self._cfg.frame_size
        while not self._stop.is_set():
            t0 = time.monotonic()
            yuv = self._picam2.capture_array("main")
            gray = yuv[:h, :w]  # Y plane of YUV420
            for symbol in decode(gray):
                payload = symbol.data.decode("utf-8", errors="replace")
                if self._debounced(payload):
                    evt(log, "scan_decoded", payload=payload,
                        symbology=symbol.type,
                        latency_ms=round((time.monotonic() - t0) * 1000))
                    emit(ScanEvent(payload, symbology=symbol.type))

    def _debounced(self, payload: str) -> bool:
        """True if this payload should fire. Identical payloads are suppressed while
        continuously in view and for debounce_s after; new payloads fire immediately."""
        now = time.monotonic()
        last = self._last_seen.get(payload, 0.0)
        self._last_seen[payload] = now
        return now - last >= self._cfg.debounce_s

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        if self._picam2:
            self._picam2.stop()
            self._picam2.close()
