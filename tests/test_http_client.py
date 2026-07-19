"""HTTP-level tests of HttpWmsClient against a real socket (tests/fake_wms.py).

Covers the v1 field mapping, RFC 9457 problem+json error taxonomy, and the
transport failure modes (5xx / timeout / connection refused -> WmsUnavailable).
"""

import pytest

from fake_wms import FakeWms
from hht.wms.base import WmsAuthError, WmsRejected, WmsUnavailable
from hht.wms.http_client import HttpWmsClient

LOGIN_OK = {
    "token": "wms_secret_token", "tokenType": "Bearer",
    "expiresAt": "2026-07-15T22:00:00Z",
    "user": {"id": 3, "username": "picker02", "role": "PICKER"},
    "device": {"id": 1, "code": "HHT-PI-01"},
}

TASK_OK = {
    "id": 101, "state": "ASSIGNED", "orderNumber": "DEMO-1001",
    "lineNumber": 1, "taskSequence": 1,
    "location": {"code": "A-01-01"},
    "article": {"sku": "ART-001", "description": "Black basic T-shirt"},
    "quantity": 20, "assignedAt": "2026-07-15T14:24:00Z",
}


@pytest.fixture
def fake():
    server = FakeWms()
    yield server
    server.close()


@pytest.fixture
def client(cfg, fake):
    cfg.wms.backend = "http"
    cfg.wms.base_url = fake.base_url
    cfg.device.id = "HHT-PI-01"
    return HttpWmsClient(cfg)


def logged_in(fake, client):
    fake.enqueue(200, LOGIN_OK)
    client.login("picker02", "2468")
    fake.requests.clear()
    return client


# -- login / logout -----------------------------------------------------------

def test_login_maps_v1_fields_and_sets_bearer(fake, client):
    fake.enqueue(200, LOGIN_OK)
    session = client.login("picker02", "2468")
    assert (session.username, session.role) == ("picker02", "PICKER")
    assert session.device_code == "HHT-PI-01"

    req = fake.requests[0]
    assert (req.method, req.path) == ("POST", "/api/v1/auth/login")
    assert req.body == {"username": "picker02", "password": "2468",
                        "deviceCode": "HHT-PI-01"}

    fake.enqueue(200, TASK_OK)
    client.next_task()
    assert fake.requests[1].headers["Authorization"] == "Bearer wms_secret_token"


def test_login_rejection_is_not_an_auth_error(fake, client):
    fake.enqueue_problem(401, "INVALID_CREDENTIALS")
    with pytest.raises(WmsRejected) as e:
        client.login("picker02", "0000")
    assert not isinstance(e.value, WmsAuthError)  # bad credentials != stale token
    assert e.value.code == "INVALID_CREDENTIALS"


def test_device_conflict_code_propagates(fake, client):
    fake.enqueue_problem(409, "DEVICE_ASSIGNMENT_CONFLICT")
    with pytest.raises(WmsRejected) as e:
        client.login("picker02", "2468")
    assert e.value.code == "DEVICE_ASSIGNMENT_CONFLICT"


def test_logout_posts_and_clears_bearer(fake, client):
    logged_in(fake, client)
    fake.enqueue(204)
    client.logout()
    assert fake.requests[0].path == "/api/v1/auth/logout"

    fake.enqueue(200, TASK_OK)
    client.next_task()
    assert "Authorization" not in fake.requests[1].headers


def test_logout_never_raises(fake, client):
    logged_in(fake, client)
    fake.enqueue_problem(401, "TOKEN_REVOKED")
    client.logout()  # goal state reached; must not raise
    fake.close()
    client.logout()  # even with the server gone


# -- next task ------------------------------------------------------------------

def test_next_task_parses_v1_shape(fake, client):
    logged_in(fake, client)
    fake.enqueue(200, TASK_OK)
    task = client.next_task()
    assert (task.id, task.state) == (101, "ASSIGNED")
    assert task.location_code == "A-01-01"
    assert (task.article.sku, task.quantity) == ("ART-001", 20)
    assert task.expected_location_qr == "LOC:A-01-01"
    assert task.expected_article_qr == "ART:ART-001"
    assert fake.requests[0].path == "/api/v1/hht/tasks/next"  # no deviceId param


def test_next_task_204_means_no_work(fake, client):
    logged_in(fake, client)
    fake.enqueue(204)
    assert client.next_task() is None


def test_assignment_conflict_code_propagates(fake, client):
    logged_in(fake, client)
    fake.enqueue_problem(409, "TASK_ASSIGNMENT_CONFLICT")
    with pytest.raises(WmsRejected) as e:
        client.next_task()
    assert e.value.code == "TASK_ASSIGNMENT_CONFLICT"


# -- scans ------------------------------------------------------------------------

def test_scan_location_posts_qr_value(fake, client):
    logged_in(fake, client)
    fake.enqueue(200, {"taskId": 101, "state": "LOCATION_CONFIRMED",
                       "locationCode": "A-01-01",
                       "confirmedAt": "2026-07-15T14:25:12Z"})
    outcome = client.scan_location(101, "LOC:A-01-01")
    assert (outcome.state, outcome.replayed) == ("LOCATION_CONFIRMED", False)
    req = fake.requests[0]
    assert req.path == "/api/v1/hht/tasks/101/scan-location"
    assert req.body == {"qrValue": "LOC:A-01-01"}


def test_replayed_scan_is_flagged(fake, client):
    logged_in(fake, client)
    fake.enqueue(200, {"taskId": 101, "state": "ARTICLE_CONFIRMED",
                       "articleSku": "ART-001", "replayed": True,
                       "confirmedAt": "2026-07-15T14:25:28Z"})
    assert client.scan_article(101, "ART:ART-001").replayed is True


def test_wrong_location_code_propagates(fake, client):
    logged_in(fake, client)
    fake.enqueue_problem(409, "WRONG_LOCATION")
    with pytest.raises(WmsRejected) as e:
        client.scan_location(101, "LOC:A-01-02")
    assert (e.value.code, e.value.status) == ("WRONG_LOCATION", 409)


# -- confirm ---------------------------------------------------------------------

def test_confirm_sends_minimal_v1_body_and_parses_outcome(fake, client):
    logged_in(fake, client)
    fake.enqueue(200, {"taskId": 101, "state": "COMPLETED",
                       "confirmedQuantity": 20, "movementId": 18,
                       "remainingStock": 0,
                       "order": {"number": "DEMO-1001", "state": "IN_PROGRESS"},
                       "completedAt": "2026-07-15T14:26:03Z"})
    outcome = client.confirm(101, "7a3d389f-9150-43ef-90e6-0955ea37d2a7", 20)
    assert (outcome.state, outcome.confirmed_quantity) == ("COMPLETED", 20)
    assert outcome.order_state == "IN_PROGRESS"
    req = fake.requests[0]
    assert req.body == {"confirmationId": "7a3d389f-9150-43ef-90e6-0955ea37d2a7",
                        "quantity": 20}
    assert "Idempotency-Key" not in req.headers  # v0 header is gone


def test_confirm_409_is_a_real_failure_not_a_duplicate_success(fake, client):
    logged_in(fake, client)
    for code in ("CONFIRMATION_ID_REUSED", "INVALID_TASK_STATE",
                 "TASK_NOT_ASSIGNED_TO_USER", "INSUFFICIENT_STOCK"):
        fake.enqueue_problem(409, code)
        with pytest.raises(WmsRejected) as e:
            client.confirm(101, "some-uuid", 20)
        assert e.value.code == code


def test_quantity_mismatch_propagates(fake, client):
    logged_in(fake, client)
    fake.enqueue_problem(422, "QUANTITY_MISMATCH")
    with pytest.raises(WmsRejected) as e:
        client.confirm(101, "some-uuid", 19)
    assert (e.value.code, e.value.status) == ("QUANTITY_MISMATCH", 422)


# -- auth / transport failures ----------------------------------------------------

def test_expired_token_raises_auth_error(fake, client):
    logged_in(fake, client)
    for code in ("INVALID_TOKEN", "TOKEN_EXPIRED", "TOKEN_REVOKED"):
        fake.enqueue_problem(401, code)
        with pytest.raises(WmsAuthError) as e:
            client.next_task()
        assert e.value.code == code


def test_5xx_is_unavailable(fake, client):
    logged_in(fake, client)
    fake.enqueue(503)
    with pytest.raises(WmsUnavailable):
        client.next_task()


def test_connection_refused_is_unavailable(fake, client):
    logged_in(fake, client)
    fake.close()
    with pytest.raises(WmsUnavailable):
        client.next_task()


def test_timeout_is_unavailable(cfg, fake):
    cfg.wms.backend = "http"
    cfg.wms.base_url = fake.base_url
    cfg.wms.timeout_s = 0.2
    client = HttpWmsClient(cfg)
    fake.enqueue(200, TASK_OK, delay_s=1.0)
    with pytest.raises(WmsUnavailable):
        client.next_task()


def test_every_request_carries_a_fresh_correlation_id(fake, client):
    logged_in(fake, client)
    fake.enqueue(204)
    fake.enqueue(204)
    client.next_task()
    client.next_task()
    ids = [r.headers.get("X-Correlation-Id") for r in fake.requests]
    assert all(ids) and ids[0] != ids[1]


def test_ping_uses_actuator_health(fake, client):
    fake.enqueue(200, {"status": "UP"})
    assert client.ping() is True
    assert fake.requests[0].path == "/actuator/health"
    fake.close()
    assert client.ping() is False
