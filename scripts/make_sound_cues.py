#!/usr/bin/env python3
"""Generate the low-level stereo WAV cues and songs used by the HHT."""

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

# User-provided melodies. A score contains its tempo followed by
# (frequency Hz, note divider) pairs. Frequency 0 is a rest; negative dividers
# are dotted notes. This matches the convention used by the source sketches.
SONGS = {
    "ringtone": (180, [
        (659, 8), (587, 8), (370, 4), (415, 4),
        (554, 8), (494, 8), (294, 4), (330, 4),
        (494, 8), (440, 8), (277, 4), (330, 4),
        (440, 2),
    ]),
    "funny": (160, [
        (0, 1), (0, 1),
        (262, 4), (330, 4), (392, 4), (330, 4),
        (262, 4), (330, 8), (392, -4), (330, 4),
        (220, 4), (262, 4), (330, 4), (262, 4),
        (220, 4), (262, 8), (330, -4), (262, 4),
        (196, 4), (247, 4), (294, 4), (247, 4),
        (196, 4), (247, 8), (294, -4), (247, 4),
        (196, 4), (196, 8), (196, -4), (196, 8), (196, 4),
        (196, 4), (196, 4), (196, 8), (196, 4),
        (262, 4), (330, 4), (392, 4), (330, 4),
        (262, 4), (330, 8), (392, -4), (330, 4),
        (220, 4), (262, 4), (330, 4), (262, 4),
        (220, 4), (262, 8), (330, -4), (262, 4),
        (196, 4), (247, 4), (294, 4), (247, 4),
        (196, 4), (247, 8), (294, -4), (247, 4),
        (196, -1),
    ]),
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


def _render_song(tempo: int, score: list[tuple[int, int]]) -> bytes:
    whole_note = 60.0 * 4.0 / tempo
    payload = bytearray()
    for frequency, divider in score:
        duration = whole_note / abs(divider)
        if divider < 0:
            duration *= 1.5
        if frequency == 0:
            payload.extend(_silence(duration))
        else:
            payload.extend(_tone(frequency, duration * 0.9))
            payload.extend(_silence(duration * 0.1))
    return bytes(payload)


def _write_wav(path: Path, payload: bytes) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(2)       # GPIO18 gets the sound whichever side is mapped
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes(payload)
    print(path)


def generate(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, notes in CUES.items():
        payload = bytearray()
        for frequency, duration, gap in notes:
            payload.extend(_tone(frequency, duration))
            payload.extend(_silence(gap))
        _write_wav(output_dir / f"{name}.wav", payload)
    for name, (tempo, score) in SONGS.items():
        _write_wav(output_dir / f"{name}.wav", _render_song(tempo, score))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", nargs="?", default="assets/sounds",
                        type=Path)
    generate(parser.parse_args().output_dir)


if __name__ == "__main__":
    main()
