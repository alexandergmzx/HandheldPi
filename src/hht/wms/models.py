"""Domain objects shared by the state machine and the WMS clients.

Shapes mirror the WMS v1 REST contract (warehouse-management/API.md).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class Session:
    token: str
    username: str
    role: str
    expires_at: str
    device_code: str

    @property
    def display_name(self) -> str:
        return self.username


@dataclass(frozen=True)
class Article:
    sku: str
    description: str


@dataclass(frozen=True)
class Task:
    id: int
    state: str
    order_number: str
    line_number: int
    task_sequence: int
    location_code: str
    article: Article
    quantity: int
    assigned_at: str = ""

    @property
    def expected_location_qr(self) -> str:
        """The exact payload the server derives and accepts for this location."""
        return f"LOC:{self.location_code}"

    @property
    def expected_article_qr(self) -> str:
        """The exact payload the server derives and accepts for this article."""
        return f"ART:{self.article.sku}"


@dataclass(frozen=True)
class ScanOutcome:
    state: str
    replayed: bool = False


@dataclass(frozen=True)
class ConfirmOutcome:
    state: str
    confirmed_quantity: int
    order_state: str


class OpKind(StrEnum):
    SCAN_LOCATION = "scan_location"
    SCAN_ARTICLE = "scan_article"
    CONFIRM = "confirm"


@dataclass(frozen=True)
class QueuedOp:
    """One store-and-forward operation against a claimed task.

    ``op_key`` is the uniqueness key: scans use ``"{task_id}:{kind}"`` (each
    transition happens at most once per task), confirms use the client-generated
    ``confirmationId`` UUID so a retried delivery hits the server's idempotency
    guarantee.
    """

    op_key: str
    kind: OpKind
    task_id: int
    payload: dict
    created_at: str = field(default_factory=utcnow_iso)

    @classmethod
    def scan(cls, kind: OpKind, task_id: int, qr_value: str) -> "QueuedOp":
        return cls(
            op_key=f"{task_id}:{kind}",
            kind=kind,
            task_id=task_id,
            payload={"qrValue": qr_value},
        )

    @classmethod
    def confirm(cls, task_id: int, confirmation_id: str, quantity: int) -> "QueuedOp":
        return cls(
            op_key=confirmation_id,
            kind=OpKind.CONFIRM,
            task_id=task_id,
            payload={"confirmationId": confirmation_id, "quantity": quantity},
        )

    def to_json(self) -> str:
        return json.dumps(
            {
                "op_key": self.op_key,
                "kind": str(self.kind),
                "task_id": self.task_id,
                "payload": self.payload,
                "created_at": self.created_at,
            },
            ensure_ascii=False,
        )

    @classmethod
    def from_json(cls, raw: str) -> "QueuedOp":
        doc = json.loads(raw)
        return cls(
            op_key=doc["op_key"],
            kind=OpKind(doc["kind"]),
            task_id=int(doc["task_id"]),
            payload=doc["payload"],
            created_at=doc["created_at"],
        )
