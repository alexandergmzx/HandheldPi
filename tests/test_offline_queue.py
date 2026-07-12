import pytest

from hht.wms.base import WmsRejected, WmsUnavailable
from hht.wms.models import Article, Confirmation, Task
from hht.wms.offline_queue import OfflineQueue

TASK = Task("T0001", "A-01-03", Article("8412345678905", "SKU-4711", "Shirt"), 3)


def make_conf():
    return Confirmation.build(device_id="HHT-T", operator_id="1001",
                              task=TASK, qty_picked=3)


class FakeSender:
    """confirm() follows a script of 'ok' | 'down' | 'reject' outcomes."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.received = []

    def confirm(self, conf):
        outcome = self.outcomes.pop(0) if self.outcomes else "ok"
        if outcome == "down":
            raise WmsUnavailable("no network")
        if outcome == "reject":
            raise WmsRejected("bad payload", 400)
        self.received.append(conf)


def test_enqueue_and_pending(tmp_path):
    q = OfflineQueue(tmp_path / "q.db")
    assert q.pending_count() == 0
    q.enqueue(make_conf())
    q.enqueue(make_conf())
    assert q.pending_count() == 2


def test_persistence_across_reopen(tmp_path):
    q = OfflineQueue(tmp_path / "q.db")
    q.enqueue(make_conf())
    q.close()
    q2 = OfflineQueue(tmp_path / "q.db")  # power loss / restart survives
    assert q2.pending_count() == 1


def test_duplicate_idempotency_key_ignored(tmp_path):
    q = OfflineQueue(tmp_path / "q.db")
    conf = make_conf()
    q.enqueue(conf)
    q.enqueue(conf)
    assert q.pending_count() == 1


def test_flush_stops_at_first_unavailable_preserving_order(tmp_path):
    q = OfflineQueue(tmp_path / "q.db")
    first, second = make_conf(), make_conf()
    q.enqueue(first)
    q.enqueue(second)
    sender = FakeSender("ok", "down")
    assert q.flush(sender) == 1
    assert q.pending_count() == 1
    # retry delivers the remaining one, FIFO order overall
    assert q.flush(sender) == 1
    keys = [c.idempotency_key for c in sender.received]
    assert keys == [first.idempotency_key, second.idempotency_key]


def test_rejected_confirmation_is_not_retried(tmp_path):
    q = OfflineQueue(tmp_path / "q.db")
    q.enqueue(make_conf())
    sender = FakeSender("reject")
    q.flush(sender)
    assert q.pending_count() == 0  # marked sent-with-error, won't loop forever


def test_clear_all(tmp_path):
    q = OfflineQueue(tmp_path / "q.db")
    q.enqueue(make_conf())
    q.clear_all()
    assert q.pending_count() == 0
