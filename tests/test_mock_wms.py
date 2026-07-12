import pytest

from hht.wms.base import WmsRejected, WmsUnavailable
from hht.wms.mock_client import MockWmsClient
from hht.wms.models import Confirmation


@pytest.fixture
def wms(cfg):
    return MockWmsClient(cfg)


def test_badge_login(wms):
    s = wms.login_badge("1001")
    assert (s.operator_id, s.operator_name) == ("1001", "Alice")
    with pytest.raises(WmsRejected):
        wms.login_badge("nope")


def test_pin_login(wms):
    assert wms.login_pin("5678").operator_name == "Bob"
    with pytest.raises(WmsRejected):
        wms.login_pin("0000")


def test_tasks_cycle_with_fresh_ids(wms):
    t1, t2 = wms.next_task(), wms.next_task()
    assert t1.task_id != t2.task_id
    assert t1.location_code == "A-01-03"


def test_report_scan_validates_against_current_task(wms):
    t = wms.next_task()
    assert wms.report_scan(t.task_id, "location", t.location_code).valid
    assert not wms.report_scan(t.task_id, "location", "Z-99-99").valid
    assert wms.report_scan(t.task_id, "article", t.article.code).valid


def test_offline_flag_raises(wms):
    wms.offline = True
    with pytest.raises(WmsUnavailable):
        wms.next_task()
    assert wms.ping() is False


def test_confirm_is_idempotent(wms):
    t = wms.next_task()
    conf = Confirmation.build(device_id="D", operator_id="1001", task=t, qty_picked=1)
    wms.confirm(conf)
    wms.confirm(conf)
    assert len(wms.confirmed) == 1
