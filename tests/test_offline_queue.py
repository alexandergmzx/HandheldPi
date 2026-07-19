import sqlite3
import uuid

from hht.wms.base import WmsAuthError, WmsRejected, WmsUnavailable
from hht.wms.models import OpKind, QueuedOp
from hht.wms.offline_queue import OfflineQueue

TASK_ID = 101


def scan_loc(task_id=TASK_ID):
    return QueuedOp.scan(OpKind.SCAN_LOCATION, task_id, f"LOC:A-01-0{task_id % 9}")


def scan_art(task_id=TASK_ID):
    return QueuedOp.scan(OpKind.SCAN_ARTICLE, task_id, "ART:ART-001")


def confirm(task_id=TASK_ID, quantity=3):
    return QueuedOp.confirm(task_id, str(uuid.uuid4()), quantity)


class FakeClient:
    """Each delivery follows a script of 'ok' | 'down' | 'reject' | 'auth'."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.received = []  # (method, task_id, key-ish)

    def _next(self, method, task_id, key):
        outcome = self.outcomes.pop(0) if self.outcomes else "ok"
        if outcome == "down":
            raise WmsUnavailable("no network")
        if outcome == "reject":
            raise WmsRejected("task blocked", 409, "INVALID_TASK_STATE")
        if outcome == "auth":
            raise WmsAuthError("expired", 401, "TOKEN_EXPIRED")
        self.received.append((method, task_id, key))

    def scan_location(self, task_id, qr_value):
        self._next("scan_location", task_id, qr_value)

    def scan_article(self, task_id, qr_value):
        self._next("scan_article", task_id, qr_value)

    def confirm(self, task_id, confirmation_id, quantity):
        self._next("confirm", task_id, confirmation_id)


def test_enqueue_and_pending(tmp_path):
    q = OfflineQueue(tmp_path / "q.db")
    assert q.pending_count() == 0
    q.enqueue(scan_loc())
    q.enqueue(confirm())
    assert q.pending_count() == 2


def test_persistence_across_reopen(tmp_path):
    q = OfflineQueue(tmp_path / "q.db")
    q.enqueue(confirm())
    q.close()
    q2 = OfflineQueue(tmp_path / "q.db")  # power loss / restart survives
    assert q2.pending_count() == 1


def test_duplicate_op_key_ignored(tmp_path):
    q = OfflineQueue(tmp_path / "q.db")
    q.enqueue(scan_loc())
    q.enqueue(scan_loc())  # same task, same kind -> same op_key
    op = confirm()
    q.enqueue(op)
    q.enqueue(op)
    assert q.pending_count() == 2


def test_flush_delivers_fifo_and_dispatches_by_kind(tmp_path):
    q = OfflineQueue(tmp_path / "q.db")
    q.enqueue(scan_loc())
    q.enqueue(scan_art())
    q.enqueue(confirm())
    client = FakeClient()
    result = q.flush(client)
    assert result.sent == 3 and result.dead == 0
    assert [m for m, _, _ in client.received] == [
        "scan_location", "scan_article", "confirm"]


def test_flush_stops_at_first_unavailable_preserving_order(tmp_path):
    q = OfflineQueue(tmp_path / "q.db")
    q.enqueue(scan_loc())
    q.enqueue(scan_art())
    client = FakeClient("ok", "down")
    assert q.flush(client).sent == 1
    assert q.pending_count() == 1
    assert q.flush(client).sent == 1  # retry delivers the rest, FIFO overall
    assert [m for m, _, _ in client.received] == ["scan_location", "scan_article"]


def test_rejection_dead_letters_and_cascades_task_chain(tmp_path):
    q = OfflineQueue(tmp_path / "q.db")
    q.enqueue(scan_loc())
    q.enqueue(scan_art())
    q.enqueue(confirm())
    client = FakeClient("reject")
    result = q.flush(client)
    assert result.sent == 0
    assert result.dead == 3  # the rejected op + 2 cascaded successors
    assert result.failed_task_id == TASK_ID
    assert result.failed_code == "INVALID_TASK_STATE"
    assert q.pending_count() == 0
    assert q.dead_count() == 3
    kinds = [kind for _, kind, _ in q.dead_ops()]
    assert kinds == ["scan_location", "scan_article", "confirm"]


def test_acknowledge_dead_clears_the_parked_rows(tmp_path):
    q = OfflineQueue(tmp_path / "q.db")
    q.enqueue(scan_loc())
    q.flush(FakeClient("reject"))
    assert q.dead_count() == 1
    assert q.acknowledge_dead() == 1
    assert q.dead_count() == 0  # kept in the table as 'acknowledged' audit rows


def test_auth_error_keeps_rows_pending(tmp_path):
    q = OfflineQueue(tmp_path / "q.db")
    q.enqueue(scan_loc())
    q.enqueue(scan_art())
    result = q.flush(FakeClient("auth"))
    assert result.auth_required is True
    assert result.sent == 0 and result.dead == 0
    assert q.pending_count() == 2  # never dead-letter on auth: re-login, replay


def test_legacy_v0_database_is_archived_not_replayed(tmp_path):
    db_path = tmp_path / "q.db"
    legacy = sqlite3.connect(db_path)
    legacy.execute("""
        CREATE TABLE confirmations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            idempotency_key TEXT UNIQUE NOT NULL,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            sent_at TEXT
        )""")
    legacy.execute(
        "INSERT INTO confirmations (idempotency_key, payload, created_at) "
        "VALUES ('abc123', '{\"taskId\": \"T-1\"}', '2026-07-12T00:00:00Z')")
    legacy.execute(
        "INSERT INTO confirmations (idempotency_key, payload, created_at, sent_at) "
        "VALUES ('def456', '{}', '2026-07-12T00:00:00Z', '2026-07-12T00:01:00Z')")
    legacy.commit()
    legacy.close()

    q = OfflineQueue(db_path)
    assert q.pending_count() == 0  # nothing deliverable
    assert q.dead_count() == 1  # only the unsent legacy row is archived
    (_, kind, err) = q.dead_ops()[0]
    assert "v0" in err
    q.close()

    q2 = OfflineQueue(db_path)  # reopening does not re-migrate
    assert q2.dead_count() == 1


def test_clear_all(tmp_path):
    q = OfflineQueue(tmp_path / "q.db")
    q.enqueue(confirm())
    q.clear_all()
    assert q.pending_count() == 0
