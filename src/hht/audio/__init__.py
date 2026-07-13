"""Short, non-blocking workflow sounds with a silent development backend."""

from __future__ import annotations

from enum import Enum
from typing import Protocol

from ..config import AppConfig


class SoundCue(str, Enum):
    READY = "ready"
    BADGE_ACCEPTED = "badge_accepted"
    LOCATION_ACCEPTED = "location_accepted"
    ARTICLE_ACCEPTED = "article_accepted"
    ERROR = "error"
    CONFIRMED = "confirmed"
    OFFLINE = "offline"


class AudioPlayer(Protocol):
    def start(self) -> None: ...

    def play(self, cue: SoundCue) -> None: ...

    def stop(self) -> None: ...


class NullAudioPlayer:
    def start(self) -> None:
        pass

    def play(self, cue: SoundCue) -> None:
        pass

    def stop(self) -> None:
        pass


def make_audio_player(cfg: AppConfig) -> AudioPlayer:
    if cfg.audio.backend == "alsa":
        from .alsa import AlsaAudioPlayer

        return AlsaAudioPlayer(cfg.audio)
    return NullAudioPlayer()
