"""Persistent store-and-forward queue for task operations (sqlite, WAL).

Level 2 offline support: once a task is claimed, the ordered op sequence
scan-location -> scan-article -> confirm is enqueued (immediately when offline,
confirm always) and delivered FIFO by the flusher. Server-side replay-safety
and confirm idempotency make redelivery harmless.

Row status lifecycle:
    pending -> sent   delivered (kept as on-device audit trail)
    pending -> dead   rejected by the WMS, or cascaded after a rejected
                      predecessor of the same task; parked for audit until the
                      operator acknowledges the SYNC_FAILED screen.

Schema v2 (PRAGMA user_version = 2). A legacy v0 database (confirmations
table from the retired assumed-v0 contract) is migrated on open: unsent rows
are archived as dead — a v0 server never existed, so they are undeliverable.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

from ..logsetup import evt
from .base import WmsAuthError, WmsClient, WmsRejected, WmsUnavailable
from .models import OpKind, QueuedOp, utcnow_iso

log = logging.getLogger("hht.queue")

_SCHEMA_VERSION = 2

_SCHEMA = """
CREATE TABLE IF NOT EXISTS operations (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    op_key     TEXT UNIQUE NOT NULL,
    task_id    INTEGER NOT NULL,
    kind       TEXT NOT NULL,
    payload    TEXT NOT NULL,
    created_at TEXT NOT NULL,
    attempts   INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    status     TEXT NOT NULL DEFAULT 'pending',
    sent_at    TEXT
);
"""


@dataclass(frozen=True)
class FlushResult:
    sent: int = 0
    dead: int = 0
    failed_task_id: int | None = None
    failed_code: str = ""
    auth_required: bool = False


class OfflineQueue:
    def __init__(self, db_path: str | Path):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(db_path), check_same_thread=False)
        with self._lock, self._db:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._migrate()

    def _migrate(self) -> None:
        version = self._db.execute("PRAGMA user_version").fetchone()[0]
        if version >= _SCHEMA_VERSION:
            return
        legacy = self._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='confirmations'"
        ).fetchone()
        self._db.execute(_SCHEMA)
        if legacy is not None:
            rows = self._db.execute(
                "SELECT idempotency_key, payload, created_at FROM confirmations "
                "WHERE sent_at IS NULL ORDER BY id"
            ).fetchall()
            for key, payload, created_at in rows:
                self._db.execute(
                    "INSERT OR IGNORE INTO operations "
                    "(op_key, task_id, kind, payload, created_at, status, last_error) "
                    "VALUES (?, 0, ?, ?, ?, 'dead', ?)",
                    (key, str(OpKind.CONFIRM), payload, created_at,
                     "v0 payload — not deliverable to v1 API"),
                )
            if rows:
                evt(log, "legacy_rows_archived", _level=logging.WARNING,
                    count=len(rows))
        self._db.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
        evt(log, "queue_schema_migrated", version=_SCHEMA_VERSION)

    def enqueue(self, op: QueuedOp) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR IGNORE INTO operations "
                "(op_key, task_id, kind, payload, created_at) VALUES (?, ?, ?, ?, ?)",
                (op.op_key, op.task_id, str(op.kind), op.to_json(), utcnow_iso()),
            )
        evt(log, "op_enqueued", task_id=op.task_id, kind=str(op.kind),
            op_key=op.op_key, pending=self.pending_count())

    def pending_count(self) -> int:
        return self._count("pending")

    def dead_count(self) -> int:
        return self._count("dead")

    def _count(self, status: str) -> int:
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) FROM operations WHERE status = ?", (status,)
            ).fetchone()
        return int(row[0])

    def dead_ops(self) -> list[tuple[str, str, str]]:
        """(op_key, kind, last_error) of parked rows, for logreport/audit."""
        with self._lock:
            rows = self._db.execute(
                "SELECT op_key, kind, last_error FROM operations "
                "WHERE status = 'dead' ORDER BY id"
            ).fetchall()
        return [(str(k), str(kind), str(err or "")) for k, kind, err in rows]

    def acknowledge_dead(self) -> int:
        """Operator acknowledged SYNC_FAILED: mark dead rows as seen."""
        with self._lock, self._db:
            cur = self._db.execute(
                "UPDATE operations SET status = 'acknowledged' WHERE status = 'dead'"
            )
        evt(log, "dead_ops_acknowledged", count=cur.rowcount)
        return cur.rowcount

    def flush(self, client: WmsClient) -> FlushResult:
        """Deliver pending ops FIFO. Never raises.

        WmsUnavailable  -> stop, rows stay pending (retry later).
        WmsAuthError    -> stop, rows stay pending, auth_required=True.
        WmsRejected     -> poison: this op goes dead and every later pending op
                           of the same task cascades dead; delivery stops so the
                           state machine can surface SYNC_FAILED.
        """
        sent = dead = 0
        failed_task_id: int | None = None
        failed_code = ""
        auth_required = False
        while True:
            with self._lock:
                row = self._db.execute(
                    "SELECT id, payload FROM operations WHERE status = 'pending' "
                    "ORDER BY id LIMIT 1"
                ).fetchone()
            if row is None:
                break
            row_id, payload = row
            op = QueuedOp.from_json(payload)
            try:
                self._deliver(client, op)
            except WmsUnavailable as e:
                self._mark(row_id, "pending", error=str(e))
                evt(log, "queue_flush_paused", _level=logging.WARNING,
                    reason=str(e), pending=self.pending_count())
                break
            except WmsAuthError as e:
                self._mark(row_id, "pending", error=f"auth: {e}")
                auth_required = True
                evt(log, "queue_flush_auth_required", _level=logging.WARNING,
                    code=e.code, pending=self.pending_count())
                break
            except WmsRejected as e:
                self._mark(row_id, "dead", error=f"rejected {e.code}: {e}")
                dead = 1 + self._cascade_dead(op.task_id)
                failed_task_id = op.task_id
                failed_code = e.code
                evt(log, "op_rejected", _level=logging.ERROR, task_id=op.task_id,
                    kind=str(op.kind), code=e.code, dead=dead)
                break
            else:
                self._mark(row_id, "sent")
                evt(log, "op_sent", task_id=op.task_id, kind=str(op.kind),
                    op_key=op.op_key)
                sent += 1
        return FlushResult(sent, dead, failed_task_id, failed_code, auth_required)

    @staticmethod
    def _deliver(client: WmsClient, op: QueuedOp) -> None:
        if op.kind is OpKind.SCAN_LOCATION:
            client.scan_location(op.task_id, op.payload["qrValue"])
        elif op.kind is OpKind.SCAN_ARTICLE:
            client.scan_article(op.task_id, op.payload["qrValue"])
        else:
            client.confirm(op.task_id, op.payload["confirmationId"],
                           int(op.payload["quantity"]))

    def _cascade_dead(self, task_id: int) -> int:
        """A rejected op poisons the rest of its task's pending chain."""
        with self._lock, self._db:
            cur = self._db.execute(
                "UPDATE operations SET status = 'dead', "
                "last_error = 'cascaded: predecessor rejected' "
                "WHERE status = 'pending' AND task_id = ?",
                (task_id,),
            )
        return cur.rowcount

    def _mark(self, row_id: int, status: str, *, error: str | None = None) -> None:
        with self._lock, self._db:
            self._db.execute(
                "UPDATE operations SET attempts = attempts + 1, last_error = ?, "
                "status = ?, sent_at = CASE WHEN ? = 'sent' THEN ? ELSE sent_at END "
                "WHERE id = ?",
                (error, status, status, utcnow_iso(), row_id),
            )

    def clear_all(self) -> None:
        """Wipe the queue. Used only by scripted tests (`reset_queue`)."""
        with self._lock, self._db:
            self._db.execute("DELETE FROM operations")

    def close(self) -> None:
        with self._lock:
            self._db.close()
