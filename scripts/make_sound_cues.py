#!/usr/bin/env python3
"""Generate the original, low-level stereo WAV cues used by the HHT."""

from __future__ import annotations

import argparse
import math
import struct
import wave
from pathlib import Path

RATE = 44_100
LEVEL = 0.18

# (frequency Hz, duration seconds, following gap seconds)
CUES = {
    "ready": [(523, 0.08, 0.025), (784, 0.12, 0.0)],
    "badge_accepted": [(659, 0.07, 0.02), (880, 0.10, 0.0)],
    "location_accepted": [(988, 0.08, 0.0)],
    "article_accepted": [(784, 0.065, 0.025), (1047, 0.09, 0.0)],
    "error": [(220, 0.11, 0.025), (165, 0.14, 0.0)],
    "confirmed": [(523, 0.07, 0.02), (659, 0.07, 0.02), (1047, 0.15, 0.0)],
    "offline": [(440, 0.08, 0.02), (294, 0.13, 0.0)],
}


def _tone(frequency: float, duration: float) -> bytes:
    frames = max(1, round(RATE * duration))
    attack = max(1, round(RATE * 0.006))
    release = max(1, round(RATE * 0.018))
    out = bytearray()
    for i in range(frames):
        envelope = min(1.0, i / attack, (frames - i - 1) / release)
        phase = 2.0 * math.pi * frequency * i / RATE
        # A quiet fundamental plus a little second harmonic gives tiny speakers
        # definition without driving them near clipping.
        value = math.sin(phase) + 0.18 * math.sin(phase * 2.0)
        sample = round(32767 * LEVEL * envelope * value / 1.18)
        out.extend(struct.pack("<hh", sample, sample))
    return bytes(out)


def _silence(duration: float) -> bytes:
    return b"\0\0\0\0" * round(RATE * duration)


def generate(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, notes in CUES.items():
        payload = bytearray()
        for frequency, duration, gap in notes:
            payload.extend(_tone(frequency, duration))
            payload.extend(_silence(gap))
        path = output_dir / f"{name}.wav"
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(2)       # GPIO18 gets the cue whichever side is mapped
            wav.setsampwidth(2)
            wav.setframerate(RATE)
            wav.writeframes(payload)
        print(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", nargs="?", default="assets/sounds",
                        type=Path)
    generate(parser.parse_args().output_dir)


if __name__ == "__main__":
    main()
