"""WMS client interface. Two implementations: http_client (real) and mock_client (dev)."""

from __future__ import annotations

from abc import ABC, abstractmethod

from .models import ConfirmOutcome, ScanOutcome, Session, Task


class WmsError(Exception):
    """Base for WMS failures."""


class WmsUnavailable(WmsError):
    """Network down / timeout / 5xx — retryable, device goes OFFLINE."""


class WmsRejected(WmsError):
    """4xx business rejection — shown to the operator, never blindly retried.

    ``code`` carries the machine-readable RFC 9457 problem code
    (e.g. WRONG_LOCATION, INVALID_TASK_STATE, QUANTITY_MISMATCH).
    """

    def __init__(self, message: str, status: int = 0, code: str = ""):
        super().__init__(message)
        self.status = status
        self.code = code


class WmsAuthError(WmsRejected):
    """401 INVALID_TOKEN / TOKEN_EXPIRED / TOKEN_REVOKED — the session is gone.

    Re-login required; queued work stays pending (never dead-lettered on auth).
    """


class WmsClient(ABC):
    @abstractmethod
    def login(self, username: str, password: str) -> Session:
        """POST /auth/login with this device's code. Raises WmsRejected on
        INVALID_CREDENTIALS / USER_INACTIVE / DEVICE_* errors."""

    @abstractmethod
    def logout(self) -> None:
        """POST /auth/logout. Best-effort: implementations must not raise."""

    @abstractmethod
    def next_task(self) -> Task | None:
        """Current active task, else atomically claims the next one (global
        FIFO). None when the WMS has no work (204)."""

    @abstractmethod
    def scan_location(self, task_id: int, qr_value: str) -> ScanOutcome:
        """Authoritative location-scan transition. Replay-safe on the server."""

    @abstractmethod
    def scan_article(self, task_id: int, qr_value: str) -> ScanOutcome:
        """Authoritative article-scan transition. Replay-safe on the server."""

    @abstractmethod
    def confirm(self, task_id: int, confirmation_id: str, quantity: int) -> ConfirmOutcome:
        """Transactional pick confirmation, idempotent by confirmation_id:
        retrying with the same UUID and quantity returns the original result."""

    @abstractmethod
    def ping(self) -> bool:
        """Cheap tokenless connectivity probe (GET /actuator/health)."""
