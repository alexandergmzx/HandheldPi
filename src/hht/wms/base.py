"""WMS client interface. Two implementations: http_client (real) and mock_client (dev)."""

from __future__ import annotations

from abc import ABC, abstractmethod

from .models import Confirmation, ScanResult, Session, Task


class WmsError(Exception):
    """Base for WMS failures."""


class WmsUnavailable(WmsError):
    """Network down / timeout / 5xx — retryable, device goes OFFLINE."""


class WmsRejected(WmsError):
    """4xx business rejection — shown to the operator, never retried."""

    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


class WmsClient(ABC):
    @abstractmethod
    def login_badge(self, operator_id: str) -> Session: ...

    @abstractmethod
    def login_pin(self, pin: str) -> Session: ...

    @abstractmethod
    def next_task(self) -> Task | None:
        """Next picking task, or None when the WMS has nothing for this device."""

    @abstractmethod
    def report_scan(self, task_id: str, scan_type: str, code: str) -> ScanResult:
        """Best-effort progress telemetry ('location' | 'article'). May raise WmsError;
        callers must not block the workflow on it — validation is local (see API.md)."""

    @abstractmethod
    def confirm(self, conf: Confirmation) -> None:
        """Transactional pick confirmation. Raises WmsUnavailable to trigger queueing."""

    @abstractmethod
    def ping(self) -> bool:
        """Cheap connectivity probe."""
