"""WMS integration: models, client implementations, persistent offline queue."""

from __future__ import annotations

from ..config import AppConfig
from .base import WmsClient, WmsError, WmsRejected, WmsUnavailable
from .models import Article, Confirmation, Session, Task

__all__ = [
    "Article", "Confirmation", "Session", "Task",
    "WmsClient", "WmsError", "WmsRejected", "WmsUnavailable",
    "make_wms_client",
]


def make_wms_client(cfg: AppConfig) -> WmsClient:
    if cfg.wms.backend == "mock":
        from .mock_client import MockWmsClient

        return MockWmsClient(cfg)
    from .http_client import HttpWmsClient

    return HttpWmsClient(cfg)
