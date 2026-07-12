"""Persistent store-and-forward queue for pick confirmations (sqlite, WAL).

Every confirmation is enqueued first, then delivered FIFO by the flusher.
"Online" only means delivery is immediate; WiFi loss changes nothing upstream.
Sent rows are kept (sent_at set) as an on-device audit trail.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from pathlib import Path

from ..logsetup import evt
from .base import WmsClient, WmsRejected, WmsUnavailable
from .models import Confirmation, utcnow_iso

log = logging.getLogger("hht.queue")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS confirmations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    idempotency_key TEXT UNIQUE NOT NULL,
    payload         TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    attempts        INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT,
    sent_at         TEXT
);
"""


class OfflineQueue:
    def __init__(self, db_path: str | Path):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(db_path), check_same_thread=False)
        with self._lock, self._db:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute(_SCHEMA)

    def enqueue(self, conf: Confirmation) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR IGNORE INTO confirmations "
                "(idempotency_key, payload, created_at) VALUES (?, ?, ?)",
                (conf.idempotency_key, conf.to_json(), utcnow_iso()),
            )
        evt(log, "confirmation_enqueued", task_id=conf.task_id,
            key=conf.idempotency_key, pending=self.pending_count())

    def pending_count(self) -> int:
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) FROM confirmations WHERE sent_at IS NULL"
            ).fetchone()
        return int(row[0])

    def flush(self, client: WmsClient) -> int:
        """Deliver pending confirmations FIFO. Returns how many were sent.

        Stops (without raising) at the first WmsUnavailable so order is preserved.
        WmsRejected marks the row as sent-with-error: the WMS refused it on business
        grounds and retrying an identical payload can never succeed.
        """
        sent = 0
        while True:
            with self._lock:
                row = self._db.execute(
                    "SELECT id, payload FROM confirmations WHERE sent_at IS NULL "
                    "ORDER BY id LIMIT 1"
                ).fetchone()
            if row is None:
                break
            row_id, payload = row
            conf = Confirmation.from_json(payload)
            try:
                client.confirm(conf)
            except WmsUnavailable as e:
                self._mark(row_id, error=str(e))
                evt(log, "queue_flush_paused", _level=logging.WARNING,
                    reason=str(e), pending=self.pending_count())
                break
            except WmsRejected as e:
                self._mark(row_id, error=f"rejected: {e}", sent=True)
                evt(log, "confirmation_rejected", _level=logging.ERROR,
                    task_id=conf.task_id, reason=str(e))
                sent += 1
            else:
                self._mark(row_id, sent=True)
                evt(log, "confirmation_sent", task_id=conf.task_id,
                    key=conf.idempotency_key)
                sent += 1
        return sent

    def _mark(self, row_id: int, *, error: str | None = None, sent: bool = False) -> None:
        with self._lock, self._db:
            self._db.execute(
                "UPDATE confirmations SET attempts = attempts + 1, last_error = ?, "
                "sent_at = COALESCE(sent_at, ?) WHERE id = ?",
                (error, utcnow_iso() if sent else None, row_id),
            )

    def clear_all(self) -> None:
        """Wipe the queue. Used only by scripted tests (`reset_queue`)."""
        with self._lock, self._db:
            self._db.execute("DELETE FROM confirmations")

    def close(self) -> None:
        with self._lock:
            self._db.close()
