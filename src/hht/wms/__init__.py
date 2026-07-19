"""WMS integration: models, client implementations, persistent offline queue."""

from __future__ import annotations

from ..config import AppConfig
from .base import WmsAuthError, WmsClient, WmsError, WmsRejected, WmsUnavailable
from .models import (
    Article,
    ConfirmOutcome,
    OpKind,
    QueuedOp,
    ScanOutcome,
    Session,
    Task,
)

__all__ = [
    "Article", "ConfirmOutcome", "OpKind", "QueuedOp", "ScanOutcome",
    "Session", "Task",
    "WmsAuthError", "WmsClient", "WmsError", "WmsRejected", "WmsUnavailable",
    "make_wms_client",
]


def make_wms_client(cfg: AppConfig) -> WmsClient:
    if cfg.wms.backend == "mock":
        from .mock_client import MockWmsClient

        return MockWmsClient(cfg)
    from .http_client import HttpWmsClient

    return HttpWmsClient(cfg)
