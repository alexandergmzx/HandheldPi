"""ALSA cue playback through a bounded worker queue.

The HHT main loop only calls ``play``; starting ``aplay`` and waiting for the
short WAV to finish happens on the worker. A missing device, command, or asset
is therefore an audio failure, never a workflow failure.
"""

from __future__ import annotations

import logging
import queue
import shutil
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path

from ..config import AudioCfg
from ..logsetup import evt
from . import SoundCue

log = logging.getLogger("hht.audio")


class AlsaAudioPlayer:
    def __init__(self, cfg: AudioCfg,
                 process_factory: Callable[..., subprocess.Popen] = subprocess.Popen):
        self._cfg = cfg
        self._process_factory = process_factory
        self._sounds_dir = Path(cfg.sounds_dir)
        self._queue: queue.Queue[SoundCue] = queue.Queue(maxsize=cfg.queue_size)
        self._queued: set[SoundCue] = set()
        self._active: SoundCue | None = None
        self._process: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        if shutil.which("aplay") is None:
            evt(log, "audio_unavailable", _level=logging.WARNING,
                reason="aplay command not found")
            return
        missing = [cue.value for cue in SoundCue if not self._path(cue).is_file()]
        if missing:
            evt(log, "audio_unavailable", _level=logging.WARNING,
                reason="sound assets missing", missing=missing,
                sounds_dir=str(self._sounds_dir))
            return
        self._stop.clear()
        self._started = True
        self._thread = threading.Thread(
            target=self._run, name="audio", daemon=True,
        )
        self._thread.start()
        evt(log, "audio_ready", device=self._cfg.device,
            sounds_dir=str(self._sounds_dir))

    def play(self, cue: SoundCue) -> None:
        if not self._started or self._stop.is_set():
            return
        with self._lock:
            if cue == self._active or cue in self._queued:
                evt(log, "sound_coalesced", _level=logging.DEBUG, cue=cue.value)
                return
            try:
                self._queue.put_nowait(cue)
            except queue.Full:
                evt(log, "sound_dropped", _level=logging.WARNING,
                    cue=cue.value, reason="queue_full")
                return
            self._queued.add(cue)
        evt(log, "sound_queued", _level=logging.DEBUG, cue=cue.value)

    def stop(self) -> None:
        if not self._started:
            return
        self._stop.set()
        with self._lock:
            process = self._process
            if process is not None and process.poll() is None:
                process.terminate()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            if self._thread.is_alive():
                evt(log, "audio_stop_timeout", _level=logging.WARNING)
        self._started = False
        self._thread = None
        evt(log, "audio_stopped")

    def _path(self, cue: SoundCue) -> Path:
        return self._sounds_dir / f"{cue.value}.wav"

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                cue = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            with self._lock:
                self._queued.discard(cue)
                self._active = cue
            try:
                self._play(cue)
            finally:
                with self._lock:
                    self._active = None
                self._queue.task_done()

    def _play(self, cue: SoundCue) -> None:
        command = [
            "aplay", "-q", "-D", self._cfg.device, str(self._path(cue)),
        ]
        try:
            with self._lock:
                if self._stop.is_set():
                    return
                self._process = self._process_factory(
                    command,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                process = self._process
            evt(log, "sound_started", cue=cue.value)
            _, stderr = process.communicate()
            if process.returncode == 0:
                evt(log, "sound_finished", _level=logging.DEBUG, cue=cue.value)
            elif not self._stop.is_set():
                evt(log, "sound_failed", _level=logging.WARNING, cue=cue.value,
                    returncode=process.returncode, error=(stderr or "").strip()[:300])
        except OSError as e:
            evt(log, "sound_failed", _level=logging.WARNING,
                cue=cue.value, error=str(e))
        finally:
            with self._lock:
                self._process = None
