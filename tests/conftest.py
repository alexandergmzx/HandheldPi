from pathlib import Path
from types import SimpleNamespace

import pytest

from hht.config import load_config
from hht.script_runner import FakeClock
from hht.state_machine import PickingStateMachine
from hht.wms.mock_client import MockWmsClient
from hht.wms.offline_queue import OfflineQueue

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def cfg(tmp_path):
    """Dev config with all writable paths redirected into the test tmp dir."""
    c = load_config(REPO / "config" / "dev.toml")
    c.queue.db_path = str(tmp_path / "queue.db")
    c.logging.file = str(tmp_path / "hht.jsonl")
    c.display.image_dir = str(tmp_path / "screens")
    return c


@pytest.fixture
def env(cfg, tmp_path):
    """Wired state machine with mock WMS, real sqlite queue, fake clock."""
    wms = MockWmsClient(cfg)
    queue = OfflineQueue(cfg.queue.db_path)
    clock = FakeClock()
    sm = PickingStateMachine(cfg, wms, queue, clock=clock)
    yield SimpleNamespace(cfg=cfg, wms=wms, queue=queue, clock=clock, sm=sm)
    queue.close()
