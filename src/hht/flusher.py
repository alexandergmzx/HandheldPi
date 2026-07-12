"""Background delivery of the offline confirmation queue + connectivity probe.

Wakes every wms.retry_interval_s, or immediately when kicked (a confirmation was
just enqueued). Posts NetStatusEvent / QueueDepthEvent back into the main loop.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

from .config import AppConfig
from .events import Event, NetStatusEvent, QueueDepthEvent
from .logsetup import evt
from .wms.base import WmsClient
from .wms.offline_queue import OfflineQueue

log = logging.getLogger("hht.flusher")


class Flusher:
    def __init__(self, cfg: AppConfig, wms: WmsClient, queue: OfflineQueue,
                 post: Callable[[Event], None]):
        self._interval = cfg.wms.retry_interval_s
        self._wms = wms
        self._queue = queue
        self._post = post
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="flusher", daemon=True)
        self._online: bool | None = None

    def start(self) -> None:
        self._thread.start()

    def kick(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stopping.set()
        self._wake.set()
        self._thread.join(timeout=2)

    def _loop(self) -> None:
        while not self._stopping.is_set():
            self._wake.wait(timeout=self._interval)
            self._wake.clear()
            if self._stopping.is_set():
                return
            pending_before = self._queue.pending_count()
            if pending_before:
                sent = self._queue.flush(self._wms)
                online = sent > 0 or self._queue.pending_count() == 0
            else:
                online = self._wms.ping()
            if online != self._online:
                self._online = online
                evt(log, "net_status", online=online)
                self._post(NetStatusEvent(online))
            self._post(QueueDepthEvent(self._queue.pending_count()))
