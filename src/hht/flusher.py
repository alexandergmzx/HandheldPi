"""Background delivery of the offline operation queue + connectivity probe.

Wakes every wms.retry_interval_s, or immediately when kicked (an op was just
enqueued, or a re-login happened). Posts NetStatusEvent / QueueDepthEvent /
SyncFailedEvent / AuthRequiredEvent back into the main loop.

While the session is invalid (WmsAuthError seen) the timer no longer attempts
deliveries — flushing resumes on the next kick(), which the state machine
sends right after a successful re-login.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

from .config import AppConfig
from .events import (
    AuthRequiredEvent,
    Event,
    NetStatusEvent,
    QueueDepthEvent,
    SyncFailedEvent,
)
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
        self._kicked = threading.Event()
        self._stopping = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="flusher", daemon=True)
        self._online: bool | None = None
        self._auth_blocked = False

    def start(self) -> None:
        self._thread.start()

    def kick(self) -> None:
        self._kicked.set()
        self._wake.set()

    def stop(self) -> None:
        self._stopping.set()
        self._wake.set()
        self._thread.join(timeout=2)

    def _loop(self) -> None:
        while not self._stopping.is_set():
            self._wake.wait(timeout=self._interval)
            self._wake.clear()
            kicked = self._kicked.is_set()
            self._kicked.clear()
            if self._stopping.is_set():
                return
            if self._auth_blocked and not kicked:
                continue  # timer tick while logged out: wait for the re-login kick
            pending_before = self._queue.pending_count()
            if pending_before:
                result = self._queue.flush(self._wms)
                if kicked:
                    self._auth_blocked = False
                if result.auth_required:
                    self._auth_blocked = True
                    self._post(AuthRequiredEvent())
                if result.failed_code:
                    self._post(SyncFailedEvent(result.failed_task_id or 0,
                                               result.failed_code))
                online = result.sent > 0 or self._queue.pending_count() == 0
            else:
                self._auth_blocked = False
                online = self._wms.ping()
            if online != self._online:
                self._online = online
                evt(log, "net_status", online=online)
                self._post(NetStatusEvent(online))
            self._post(QueueDepthEvent(self._queue.pending_count()))
