import pytest

from hht.wms.base import WmsAuthError, WmsRejected, WmsUnavailable
from hht.wms.mock_client import MockWmsClient


@pytest.fixture
def wms(cfg):
    client = MockWmsClient(cfg)
    client.login("picker01", "1234")
    return client


def test_login_and_rejections(cfg):
    client = MockWmsClient(cfg)
    s = client.login("picker01", "1234")
    assert (s.username, s.display_name) == ("picker01", "picker01")
    with pytest.raises(WmsRejected) as e:
        client.login("picker01", "9999")
    assert e.value.code == "INVALID_CREDENTIALS"
    with pytest.raises(WmsRejected):
        client.login("ghost", "1234")


def test_unauthenticated_calls_raise_auth_error(cfg):
    client = MockWmsClient(cfg)
    with pytest.raises(WmsAuthError):
        client.next_task()


def test_next_task_returns_current_active_not_a_new_one(wms):
    t1 = wms.next_task()
    t2 = wms.next_task()
    assert t1.id == t2.id  # v1: the active task comes back until completed
    assert t1.location_code == "A-01-03"


def test_scan_order_is_enforced(wms):
    t = wms.next_task()
    with pytest.raises(WmsRejected) as e:
        wms.scan_article(t.id, f"ART:{t.article.sku}")  # location not confirmed yet
    assert e.value.code == "INVALID_TASK_STATE"


def test_wrong_scans_rejected_with_codes(wms):
    t = wms.next_task()
    with pytest.raises(WmsRejected) as e:
        wms.scan_location(t.id, "LOC:Z-99-99")
    assert e.value.code == "WRONG_LOCATION"
    wms.scan_location(t.id, f"LOC:{t.location_code}")
    with pytest.raises(WmsRejected) as e:
        wms.scan_article(t.id, "ART:WRONG")
    assert e.value.code == "WRONG_ARTICLE"


def test_repeated_correct_scan_is_replay_safe(wms):
    t = wms.next_task()
    first = wms.scan_location(t.id, f"LOC:{t.location_code}")
    again = wms.scan_location(t.id, f"LOC:{t.location_code}")
    assert first.replayed is False
    assert again.replayed is True
    assert again.state == "LOCATION_CONFIRMED"  # never regresses


def _complete(wms, conf_id="c-1"):
    t = wms.next_task()
    wms.scan_location(t.id, f"LOC:{t.location_code}")
    wms.scan_article(t.id, f"ART:{t.article.sku}")
    return t, wms.confirm(t.id, conf_id, t.quantity)


def test_confirm_is_idempotent_by_confirmation_id(wms):
    t, outcome = _complete(wms)
    retry = wms.confirm(t.id, "c-1", t.quantity)
    assert retry == outcome  # same result, not booked twice
    assert len(wms.confirmed) == 1


def test_confirmation_id_reuse_with_other_payload_rejected(wms):
    t, _ = _complete(wms)
    with pytest.raises(WmsRejected) as e:
        wms.confirm(t.id, "c-1", t.quantity + 1)
    assert e.value.code == "CONFIRMATION_ID_REUSED"


def test_quantity_mismatch_rejected(wms):
    t = wms.next_task()
    wms.scan_location(t.id, f"LOC:{t.location_code}")
    wms.scan_article(t.id, f"ART:{t.article.sku}")
    with pytest.raises(WmsRejected) as e:
        wms.confirm(t.id, "c-9", t.quantity - 1)
    assert e.value.code == "QUANTITY_MISMATCH"
    assert e.value.status == 422


def test_completed_task_frees_the_next_claim(wms):
    t1, _ = _complete(wms)
    t2 = wms.next_task()
    assert t2.id != t1.id


def test_blocked_task_rejects_operations(wms):
    t = wms.next_task()
    wms.block_current_task()
    with pytest.raises(WmsRejected) as e:
        wms.scan_location(t.id, f"LOC:{t.location_code}")
    assert e.value.code == "INVALID_TASK_STATE"


def test_expired_token_raises_until_relogin(wms):
    wms.next_task()
    wms.expire_token()
    with pytest.raises(WmsAuthError):
        wms.next_task()
    wms.login("picker01", "1234")
    assert wms.next_task() is not None


def test_offline_flag_raises(wms):
    wms.offline = True
    with pytest.raises(WmsUnavailable):
        wms.next_task()
    assert wms.ping() is False


def test_unknown_task_is_404(wms):
    with pytest.raises(WmsRejected) as e:
        wms.scan_location(999, "LOC:A-01-01")
    assert e.value.code == "TASK_NOT_FOUND"
