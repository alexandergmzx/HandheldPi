"""Domain objects shared by the state machine and the WMS clients."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class Session:
    token: str
    operator_id: str
    operator_name: str


@dataclass(frozen=True)
class Article:
    code: str  # scannable code (EAN etc.)
    sku: str
    description: str


@dataclass(frozen=True)
class Task:
    task_id: str
    location_code: str
    article: Article
    qty_requested: int


@dataclass(frozen=True)
class ScanResult:
    valid: bool
    message: str = ""


@dataclass(frozen=True)
class Confirmation:
    idempotency_key: str
    device_id: str
    operator_id: str
    task_id: str
    location_code: str
    article_code: str
    qty_requested: int
    qty_picked: int
    short_pick: bool
    confirmed_at: str

    @classmethod
    def build(cls, *, device_id: str, operator_id: str, task: Task,
              qty_picked: int) -> "Confirmation":
        return cls(
            idempotency_key=uuid.uuid4().hex,
            device_id=device_id,
            operator_id=operator_id,
            task_id=task.task_id,
            location_code=task.location_code,
            article_code=task.article.code,
            qty_requested=task.qty_requested,
            qty_picked=qty_picked,
            short_pick=qty_picked < task.qty_requested,
            confirmed_at=utcnow_iso(),
        )

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> "Confirmation":
        return cls(**json.loads(raw))
