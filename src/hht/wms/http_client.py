"""HTTP implementation of WmsClient against the contract in API.md."""

from __future__ import annotations

import logging

import requests

from ..config import AppConfig
from ..logsetup import evt
from .base import WmsClient, WmsRejected, WmsUnavailable
from .models import Article, Confirmation, ScanResult, Session, Task, utcnow_iso

log = logging.getLogger("hht.wms.http")


class HttpWmsClient(WmsClient):
    def __init__(self, cfg: AppConfig):
        self._base = cfg.wms.base_url.rstrip("/")
        self._timeout = cfg.wms.timeout_s
        self._device_id = cfg.device.id
        self._http = requests.Session()

    # -- plumbing ---------------------------------------------------------

    def _request(self, method: str, path: str, *, json_body=None, params=None,
                 headers=None) -> requests.Response:
        url = f"{self._base}{path}"
        try:
            resp = self._http.request(
                method, url, json=json_body, params=params, headers=headers,
                timeout=self._timeout,
            )
        except (requests.ConnectionError, requests.Timeout) as e:
            raise WmsUnavailable(f"{method} {path}: {e.__class__.__name__}") from e
        if resp.status_code >= 500:
            raise WmsUnavailable(f"{method} {path}: HTTP {resp.status_code}")
        if 400 <= resp.status_code < 500:
            try:
                message = resp.json().get("message", resp.text[:200])
            except ValueError:
                message = resp.text[:200]
            raise WmsRejected(message or f"HTTP {resp.status_code}", resp.status_code)
        return resp

    def _login(self, body: dict) -> Session:
        resp = self._request("POST", "/api/v1/auth/login", json_body=body)
        doc = resp.json()
        session = Session(doc["token"], str(doc["operatorId"]), doc.get("operatorName", ""))
        self._http.headers["Authorization"] = f"Bearer {session.token}"
        evt(log, "wms_login_ok", operator_id=session.operator_id)
        return session

    # -- WmsClient --------------------------------------------------------

    def login_badge(self, operator_id: str) -> Session:
        return self._login({"deviceId": self._device_id, "method": "badge",
                            "operatorId": operator_id})

    def login_pin(self, pin: str) -> Session:
        return self._login({"deviceId": self._device_id, "method": "pin", "pin": pin})

    def next_task(self) -> Task | None:
        resp = self._request("GET", "/api/v1/tasks/next",
                             params={"deviceId": self._device_id})
        if resp.status_code == 204 or not resp.content:
            return None
        doc = resp.json()
        art = doc["article"]
        return Task(
            task_id=str(doc["taskId"]),
            location_code=str(doc["locationCode"]),
            article=Article(str(art["code"]), str(art.get("sku", "")),
                            str(art.get("description", ""))),
            qty_requested=int(doc["qtyRequested"]),
        )

    def report_scan(self, task_id: str, scan_type: str, code: str) -> ScanResult:
        resp = self._request(
            "POST", f"/api/v1/tasks/{task_id}/scan-{scan_type}",
            json_body={"code": code, "scannedAt": utcnow_iso()},
        )
        doc = resp.json()
        return ScanResult(bool(doc.get("valid", True)), doc.get("message", ""))

    def confirm(self, conf: Confirmation) -> None:
        try:
            self._request(
                "POST", f"/api/v1/tasks/{conf.task_id}/confirm",
                json_body={
                    "idempotencyKey": conf.idempotency_key,
                    "deviceId": conf.device_id,
                    "operatorId": conf.operator_id,
                    "taskId": conf.task_id,
                    "locationCode": conf.location_code,
                    "articleCode": conf.article_code,
                    "qtyRequested": conf.qty_requested,
                    "qtyPicked": conf.qty_picked,
                    "shortPick": conf.short_pick,
                    "confirmedAt": conf.confirmed_at,
                },
                headers={"Idempotency-Key": conf.idempotency_key},
            )
        except WmsRejected as e:
            if e.status == 409:  # duplicate idempotency key: already delivered
                evt(log, "wms_confirm_duplicate", task_id=conf.task_id)
                return
            raise

    def ping(self) -> bool:
        try:
            self._request("GET", "/api/v1/health")
            return True
        except WmsUnavailable:
            return False
