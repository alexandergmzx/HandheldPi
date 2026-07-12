"""In-memory WMS for developing/testing without the Spring Boot server.

Behaves like the real thing including failure modes: set `client.offline = True`
to make every call raise WmsUnavailable (used by scripted functional tests).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ..config import AppConfig
from ..logsetup import evt
from .base import WmsClient, WmsRejected, WmsUnavailable
from .models import Article, Confirmation, ScanResult, Session, Task

log = logging.getLogger("hht.wms.mock")

_SAMPLE_TASKS = [
    ("A-01-03", Article("8412345678905", "SKU-4711", "Blue T-Shirt M"), 3),
    ("B-02-07", Article("8400000123457", "SKU-0815", "Sneaker 42"), 1),
    ("C-11-01", Article("8410987654321", "SKU-1234", "Cap Black"), 5),
]


class MockWmsClient(WmsClient):
    def __init__(self, cfg: AppConfig):
        self.offline = False
        self.confirmed: list[Confirmation] = []
        self._counter = 0
        self._current: Task | None = None

        self._operators: dict[str, tuple[str, str]] = {}  # id -> (name, pin)
        for entry in cfg.mock.operators:
            op_id, name, pin = entry.split(":", 2)
            self._operators[op_id] = (name, pin)

        self._templates = list(_SAMPLE_TASKS)
        if cfg.mock.tasks_file:
            self._templates = [
                (t["locationCode"],
                 Article(t["article"]["code"], t["article"].get("sku", ""),
                         t["article"].get("description", "")),
                 int(t["qtyRequested"]))
                for t in json.loads(Path(cfg.mock.tasks_file).read_text())
            ]

    def _check_online(self) -> None:
        if self.offline:
            raise WmsUnavailable("mock WMS is offline")

    def login_badge(self, operator_id: str) -> Session:
        self._check_online()
        if operator_id not in self._operators:
            raise WmsRejected(f"Unknown operator {operator_id}", 401)
        name, _ = self._operators[operator_id]
        return Session("mock-token", operator_id, name)

    def login_pin(self, pin: str) -> Session:
        self._check_online()
        for op_id, (name, op_pin) in self._operators.items():
            if pin == op_pin:
                return Session("mock-token", op_id, name)
        raise WmsRejected("Wrong PIN", 401)

    def next_task(self) -> Task | None:
        self._check_online()
        if not self._templates:
            return None
        loc, art, qty = self._templates[self._counter % len(self._templates)]
        self._counter += 1
        self._current = Task(f"T{self._counter:04d}", loc, art, qty)
        evt(log, "mock_task_issued", task_id=self._current.task_id, location=loc)
        return self._current

    def report_scan(self, task_id: str, scan_type: str, code: str) -> ScanResult:
        self._check_online()
        t = self._current
        if t is None or t.task_id != task_id:
            return ScanResult(False, "Unknown task")
        expected = t.location_code if scan_type == "location" else t.article.code
        return ScanResult(code == expected)

    def confirm(self, conf: Confirmation) -> None:
        self._check_online()
        if any(c.idempotency_key == conf.idempotency_key for c in self.confirmed):
            return  # idempotent
        self.confirmed.append(conf)
        evt(log, "mock_confirm", task_id=conf.task_id, qty=conf.qty_picked)

    def ping(self) -> bool:
        return not self.offline
