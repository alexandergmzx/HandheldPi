"""Logging: JSON-lines file (for later log analysis) + human-readable stderr.

Convention: every noteworthy occurrence is one event record —
    evt(log, "scan_accepted", code="LOC:A-01-03", state="GOTO_LOCATION")
producing
    {"ts":"2026-07-11T09:30:00.123Z","level":"INFO","logger":"hht.sm",
     "event":"scan_accepted","code":"LOC:A-01-03","state":"GOTO_LOCATION"}
"""

from __future__ import annotations

import json
import logging
import logging.handlers
from datetime import datetime, timezone
from pathlib import Path

from .config import LoggingCfg


def evt(logger: logging.Logger, event: str, _level: int = logging.INFO, **fields) -> None:
    logger.log(_level, event, extra={"evt_fields": fields})


class JsonLinesFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        doc = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        doc.update(getattr(record, "evt_fields", {}))
        if record.exc_info:
            doc["exc"] = self.formatException(record.exc_info)
        return json.dumps(doc, ensure_ascii=False, default=str)


class HumanFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        fields = getattr(record, "evt_fields", {})
        tail = " ".join(f"{k}={v}" for k, v in fields.items())
        base = f"{self.formatTime(record, '%H:%M:%S')} {record.levelname:<7} " \
               f"{record.getMessage()}"
        if record.exc_info:
            tail = (tail + " " if tail else "") + self.formatException(record.exc_info)
        return f"{base} {tail}".rstrip()


def setup_logging(cfg: LoggingCfg, *, console: bool = True) -> None:
    root = logging.getLogger()
    root.setLevel(getattr(logging, cfg.level.upper(), logging.INFO))
    root.handlers.clear()

    log_path = Path(cfg.file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fh = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=cfg.max_bytes, backupCount=cfg.backup_count, encoding="utf-8"
    )
    fh.setFormatter(JsonLinesFormatter())
    root.addHandler(fh)

    if console:
        sh = logging.StreamHandler()
        sh.setFormatter(HumanFormatter())
        root.addHandler(sh)
