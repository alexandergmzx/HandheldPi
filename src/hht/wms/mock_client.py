"""In-memory WMS mirroring the v1 contract, for dev/testing without the server.

Mirrors the server-authoritative semantics: tasks live server-side with states
AVAILABLE -> ASSIGNED -> LOCATION_CONFIRMED -> ARTICLE_CONFIRMED -> COMPLETED;
scans enforce order and correctness, replays are safe, confirm is idempotent by
confirmationId. Failure injection for scripted tests:

    client.offline = True       every call raises WmsUnavailable
    client.block_current_task() admin blocked the task -> replay rejection
    client.expire_token()       authed calls raise WmsAuthError until re-login
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from ..config import AppConfig
from ..logsetup import evt
from .base import WmsAuthError, WmsClient, WmsRejected, WmsUnavailable
from .models import Article, ConfirmOutcome, ScanOutcome, Session, Task

log = logging.getLogger("hht.wms.mock")

_SAMPLE_TASKS = [
    ("M-1001", 1, 1, "A-01-03", "ART-SHIRT", "Blue T-Shirt M", 3),
    ("M-1001", 2, 1, "B-02-07", "ART-SNEAKER", "Sneaker 42", 1),
    ("M-1002", 1, 1, "C-11-01", "ART-CAP", "Cap Black", 5),
]


@dataclass
class _MockTask:
    id: int
    order_number: str
    line_number: int
    task_sequence: int
    location_code: str
    article: Article
    quantity: int
    state: str = "AVAILABLE"
    confirmation_id: str = ""
    outcome: ConfirmOutcome | None = None

    def to_task(self) -> Task:
        return Task(
            id=self.id, state=self.state, order_number=self.order_number,
            line_number=self.line_number, task_sequence=self.task_sequence,
            location_code=self.location_code, article=self.article,
            quantity=self.quantity,
        )


class MockWmsClient(WmsClient):
    def __init__(self, cfg: AppConfig):
        self.offline = False
        self.confirmed: list[tuple[int, str, int]] = []  # (task_id, conf_id, qty)
        self._token_valid = False
        self._username = ""

        self._operators: dict[str, tuple[str, str]] = {}  # username -> (name, pin)
        for entry in cfg.mock.operators:
            username, name, pin = entry.split(":", 2)
            self._operators[username] = (name, pin)

        rows = _SAMPLE_TASKS
        if cfg.mock.tasks_file:
            rows = [
                (t["orderNumber"], int(t.get("lineNumber", 1)),
                 int(t.get("taskSequence", 1)), t["locationCode"],
                 t["article"]["sku"], t["article"].get("description", ""),
                 int(t["quantity"]))
                for t in json.loads(Path(cfg.mock.tasks_file).read_text())
            ]
        self._tasks = [
            _MockTask(i + 101, order, line, seq, loc, Article(sku, desc), qty)
            for i, (order, line, seq, loc, sku, desc, qty) in enumerate(rows)
        ]

    # -- failure injection (scripted tests) --------------------------------

    def block_current_task(self) -> None:
        """Admin recovery per ADR 0004: block releases the assignment."""
        task = self._active_task()
        if task is None:
            raise RuntimeError("no active task to block")
        task.state = "BLOCKED"
        evt(log, "mock_task_blocked", task_id=task.id)

    def expire_token(self) -> None:
        self._token_valid = False
        evt(log, "mock_token_expired")

    # -- plumbing -----------------------------------------------------------

    def _check_online(self) -> None:
        if self.offline:
            raise WmsUnavailable("mock WMS is offline")

    def _check_auth(self) -> None:
        if not self._token_valid:
            raise WmsAuthError("token expired", 401, "TOKEN_EXPIRED")

    def _active_task(self) -> _MockTask | None:
        for t in self._tasks:
            if t.state in ("ASSIGNED", "LOCATION_CONFIRMED", "ARTICLE_CONFIRMED"):
                return t
        return None

    def _find(self, task_id: int) -> _MockTask:
        for t in self._tasks:
            if t.id == task_id:
                return t
        raise WmsRejected("task not found", 404, "TASK_NOT_FOUND")

    # -- WmsClient ----------------------------------------------------------

    def login(self, username: str, password: str) -> Session:
        self._check_online()
        known = self._operators.get(username)
        if known is None or known[1] != password:
            raise WmsRejected("invalid credentials", 401, "INVALID_CREDENTIALS")
        self._token_valid = True
        self._username = username
        evt(log, "mock_login_ok", username=username)
        return Session(token="mock-token", username=username, role="PICKER",
                       expires_at="2099-01-01T00:00:00Z", device_code="HHT-MOCK")

    def logout(self) -> None:
        self._token_valid = False

    def next_task(self) -> Task | None:
        self._check_online()
        self._check_auth()
        active = self._active_task()
        if active is not None:
            return active.to_task()  # v1: the current task comes back, not a new one
        for t in self._tasks:
            if t.state == "AVAILABLE":
                t.state = "ASSIGNED"
                evt(log, "mock_task_claimed", task_id=t.id, location=t.location_code)
                return t.to_task()
        return None

    def scan_location(self, task_id: int, qr_value: str) -> ScanOutcome:
        return self._scan(task_id, qr_value, from_state="ASSIGNED",
                          to_state="LOCATION_CONFIRMED", wrong_code="WRONG_LOCATION",
                          expected_fn=lambda t: f"LOC:{t.location_code}")

    def scan_article(self, task_id: int, qr_value: str) -> ScanOutcome:
        return self._scan(task_id, qr_value, from_state="LOCATION_CONFIRMED",
                          to_state="ARTICLE_CONFIRMED", wrong_code="WRONG_ARTICLE",
                          expected_fn=lambda t: f"ART:{t.article.sku}")

    def _scan(self, task_id: int, qr_value: str, *, from_state: str, to_state: str,
              wrong_code: str, expected_fn) -> ScanOutcome:
        self._check_online()
        self._check_auth()
        t = self._find(task_id)
        if t.state == to_state and qr_value == expected_fn(t):
            return ScanOutcome(t.state, replayed=True)  # replay-safe, no regression
        if t.state != from_state:
            raise WmsRejected(f"task is {t.state}", 409, "INVALID_TASK_STATE")
        if qr_value != expected_fn(t):
            raise WmsRejected(f"expected {expected_fn(t)}", 409, wrong_code)
        t.state = to_state
        evt(log, "mock_scan_ok", task_id=t.id, state=to_state)
        return ScanOutcome(t.state)

    def confirm(self, task_id: int, confirmation_id: str, quantity: int) -> ConfirmOutcome:
        self._check_online()
        self._check_auth()
        t = self._find(task_id)
        if t.state == "COMPLETED":
            if confirmation_id == t.confirmation_id and t.outcome is not None \
                    and quantity == t.outcome.confirmed_quantity:
                return t.outcome  # idempotent retry returns the original result
            if confirmation_id == t.confirmation_id:
                raise WmsRejected("payload differs", 409, "CONFIRMATION_ID_REUSED")
            raise WmsRejected("task is COMPLETED", 409, "INVALID_TASK_STATE")
        if t.state != "ARTICLE_CONFIRMED":
            raise WmsRejected(f"task is {t.state}", 409, "INVALID_TASK_STATE")
        if quantity != t.quantity:
            raise WmsRejected("quantity differs from the task quantity", 422,
                              "QUANTITY_MISMATCH")
        t.state = "COMPLETED"
        t.confirmation_id = confirmation_id
        t.outcome = ConfirmOutcome("COMPLETED", quantity, "IN_PROGRESS")
        self.confirmed.append((t.id, confirmation_id, quantity))
        evt(log, "mock_confirm", task_id=t.id, qty=quantity)
        return t.outcome

    def ping(self) -> bool:
        return not self.offline
