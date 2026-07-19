"""HTTP implementation of WmsClient against the WMS v1 contract (see API.md).

Error mapping:
    5xx / timeout / connection error  -> WmsUnavailable (retryable, go OFFLINE)
    401 INVALID_TOKEN/TOKEN_EXPIRED/TOKEN_REVOKED -> WmsAuthError (re-login)
    other 4xx problem+json            -> WmsRejected(message, status, code)

Every request carries a fresh X-Correlation-Id; the WMS echoes it, so device
and server logs join on it. The bearer token is never logged.
"""

from __future__ import annotations

import logging
import uuid

import requests

from ..config import AppConfig
from ..logsetup import evt
from .base import WmsAuthError, WmsClient, WmsRejected, WmsUnavailable
from .models import Article, ConfirmOutcome, ScanOutcome, Session, Task

log = logging.getLogger("hht.wms.http")

_AUTH_CODES = {"INVALID_TOKEN", "TOKEN_EXPIRED", "TOKEN_REVOKED"}


class HttpWmsClient(WmsClient):
    def __init__(self, cfg: AppConfig):
        self._base = cfg.wms.base_url.rstrip("/")
        self._timeout = cfg.wms.timeout_s
        self._device_code = cfg.device.id
        self._http = requests.Session()
        self._http.headers["Accept"] = "application/json"

    # -- plumbing ---------------------------------------------------------

    def _request(self, method: str, path: str, *, json_body=None) -> requests.Response:
        url = f"{self._base}{path}"
        correlation_id = str(uuid.uuid4())
        try:
            resp = self._http.request(
                method, url, json=json_body,
                headers={"X-Correlation-Id": correlation_id},
                timeout=self._timeout,
            )
        except (requests.ConnectionError, requests.Timeout) as e:
            raise WmsUnavailable(f"{method} {path}: {e.__class__.__name__}") from e
        if resp.status_code >= 500:
            raise WmsUnavailable(f"{method} {path}: HTTP {resp.status_code}")
        if 400 <= resp.status_code < 500:
            code, message = self._problem(resp)
            evt(log, "wms_rejected", _level=logging.WARNING, method=method,
                path=path, status=resp.status_code, code=code,
                correlation_id=correlation_id)
            if resp.status_code == 401 and code in _AUTH_CODES:
                raise WmsAuthError(message, resp.status_code, code)
            raise WmsRejected(message, resp.status_code, code)
        return resp

    @staticmethod
    def _problem(resp: requests.Response) -> tuple[str, str]:
        """Extract (code, human message) from an RFC 9457 problem+json body."""
        try:
            doc = resp.json()
        except ValueError:
            return "", resp.text[:200] or f"HTTP {resp.status_code}"
        code = str(doc.get("code", ""))
        message = str(doc.get("detail") or doc.get("title") or f"HTTP {resp.status_code}")
        return code, message

    # -- WmsClient --------------------------------------------------------

    def login(self, username: str, password: str) -> Session:
        resp = self._request("POST", "/api/v1/auth/login", json_body={
            "username": username,
            "password": password,
            "deviceCode": self._device_code,
        })
        doc = resp.json()
        session = Session(
            token=doc["token"],
            username=str(doc["user"]["username"]),
            role=str(doc["user"]["role"]),
            expires_at=str(doc.get("expiresAt", "")),
            device_code=str(doc["device"]["code"]),
        )
        self._http.headers["Authorization"] = f"Bearer {session.token}"
        evt(log, "wms_login_ok", username=session.username, role=session.role,
            device_code=session.device_code, expires_at=session.expires_at)
        return session

    def logout(self) -> None:
        try:
            self._request("POST", "/api/v1/auth/logout")
        except WmsRejected:
            pass  # token already invalid — the goal state is reached
        except WmsUnavailable:
            evt(log, "wms_logout_unreachable", _level=logging.WARNING)
        finally:
            self._http.headers.pop("Authorization", None)
            evt(log, "wms_logout_local")

    def next_task(self) -> Task | None:
        resp = self._request("GET", "/api/v1/hht/tasks/next")
        if resp.status_code == 204 or not resp.content:
            return None
        doc = resp.json()
        art = doc["article"]
        return Task(
            id=int(doc["id"]),
            state=str(doc["state"]),
            order_number=str(doc["orderNumber"]),
            line_number=int(doc["lineNumber"]),
            task_sequence=int(doc["taskSequence"]),
            location_code=str(doc["location"]["code"]),
            article=Article(str(art["sku"]), str(art.get("description", ""))),
            quantity=int(doc["quantity"]),
            assigned_at=str(doc.get("assignedAt", "")),
        )

    def scan_location(self, task_id: int, qr_value: str) -> ScanOutcome:
        resp = self._request("POST", f"/api/v1/hht/tasks/{task_id}/scan-location",
                             json_body={"qrValue": qr_value})
        doc = resp.json()
        return ScanOutcome(str(doc["state"]), bool(doc.get("replayed", False)))

    def scan_article(self, task_id: int, qr_value: str) -> ScanOutcome:
        resp = self._request("POST", f"/api/v1/hht/tasks/{task_id}/scan-article",
                             json_body={"qrValue": qr_value})
        doc = resp.json()
        return ScanOutcome(str(doc["state"]), bool(doc.get("replayed", False)))

    def confirm(self, task_id: int, confirmation_id: str, quantity: int) -> ConfirmOutcome:
        resp = self._request("POST", f"/api/v1/hht/tasks/{task_id}/confirm", json_body={
            "confirmationId": confirmation_id,
            "quantity": quantity,
        })
        doc = resp.json()
        order = doc.get("order") or {}
        return ConfirmOutcome(
            state=str(doc["state"]),
            confirmed_quantity=int(doc["confirmedQuantity"]),
            order_state=str(order.get("state", "")),
        )

    def ping(self) -> bool:
        url = f"{self._base}/actuator/health"
        try:
            resp = self._http.request("GET", url, timeout=self._timeout)
            return resp.status_code == 200
        except (requests.ConnectionError, requests.Timeout):
            return False
