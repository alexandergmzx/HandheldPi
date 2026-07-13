import threading
import time
import wave
from array import array
from pathlib import Path

from hht.audio import NullAudioPlayer, SoundCue, make_audio_player
from hht.audio import alsa
from hht.audio.alsa import AlsaAudioPlayer

REPO = Path(__file__).resolve().parent.parent
SOUNDS = REPO / "assets" / "sounds"


def test_committed_cues_are_short_low_level_stereo_wavs():
    for cue in SoundCue:
        path = SOUNDS / f"{cue.value}.wav"
        assert path.is_file(), f"missing cue asset: {path}"
        with wave.open(str(path), "rb") as wav:
            assert wav.getnchannels() == 2
            assert wav.getsampwidth() == 2
            assert wav.getframerate() == 44_100
            duration = wav.getnframes() / wav.getframerate()
            assert 0.05 <= duration <= 0.6
            samples = array("h", wav.readframes(wav.getnframes()))
            assert max(abs(sample) for sample in samples) <= 6500


def test_committed_songs_are_low_level_stereo_wavs():
    for name in ("ringtone", "funny"):
        path = SOUNDS / f"{name}.wav"
        assert path.is_file(), f"missing song asset: {path}"
        with wave.open(str(path), "rb") as wav:
            assert wav.getnchannels() == 2
            assert wav.getsampwidth() == 2
            assert wav.getframerate() == 44_100
            duration = wav.getnframes() / wav.getframerate()
            assert 2.0 <= duration <= 60.0
            samples = array("h", wav.readframes(wav.getnframes()))
            assert max(abs(sample) for sample in samples) <= 6500


def test_none_backend_uses_null_player(cfg):
    cfg.audio.backend = "none"
    assert isinstance(make_audio_player(cfg), NullAudioPlayer)


def test_alsa_play_is_non_blocking_and_uses_configured_device(cfg, monkeypatch):
    completed = threading.Event()
    commands = []

    class FakeProcess:
        def __init__(self, command, **kwargs):
            commands.append(command)
            self.returncode = None

        def communicate(self):
            self.returncode = 0
            completed.set()
            return "", ""

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = -15

    monkeypatch.setattr(alsa.shutil, "which", lambda command: "/usr/bin/aplay")
    cfg.audio.backend = "alsa"
    cfg.audio.device = "test-device"
    cfg.audio.sounds_dir = str(SOUNDS)
    player = AlsaAudioPlayer(cfg.audio, process_factory=FakeProcess)
    player.start()
    started = time.monotonic()
    player.play(SoundCue.LOCATION_ACCEPTED)
    elapsed = time.monotonic() - started

    assert elapsed < 0.05
    assert completed.wait(timeout=1.0)
    player.stop()
    assert commands == [[
        "aplay", "-q", "-D", "test-device",
        str(SOUNDS / "location_accepted.wav"),
    ]]


def test_missing_aplay_disables_audio_without_raising(cfg, monkeypatch):
    monkeypatch.setattr(alsa.shutil, "which", lambda command: None)
    cfg.audio.sounds_dir = str(SOUNDS)
    player = AlsaAudioPlayer(cfg.audio)
    player.start()
    player.play(SoundCue.ERROR)
    player.stop()
