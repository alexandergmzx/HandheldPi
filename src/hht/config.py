"""Typed configuration loaded from a TOML file. No value is hardcoded elsewhere."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .events import Button


class ConfigError(Exception):
    pass


@dataclass
class DeviceCfg:
    id: str
    site: str = ""


@dataclass
class WmsCfg:
    backend: str = "mock"  # http | mock
    base_url: str = "http://localhost:8080"
    timeout_s: float = 5.0
    retry_interval_s: float = 30.0


@dataclass
class ScannerCfg:
    backend: str = "mock"  # camera | mock
    debounce_s: float = 2.0
    frame_size: tuple[int, int] = (640, 480)
    af_mode: str = "continuous"  # continuous | manual
    lens_position: float = 6.6


@dataclass
class DisplayCfg:
    backend: str = "console"  # framebuffer | console | image
    fb_device: str = "auto"
    rotation: int = 0
    image_dir: str = "var/screens"
    console_cols: int = 80


@dataclass
class InputCfg:
    backend: str = "keyboard"  # gpio | keyboard
    hold_start_s: float = 1.5
    pins: dict[Button, int] = field(default_factory=dict)


@dataclass
class WorkflowCfg:
    error_banner_s: float = 2.5
    allow_short_pick: bool = True
    pin_length: int = 4


@dataclass
class LoggingCfg:
    file: str = "var/hht.jsonl"
    level: str = "INFO"
    max_bytes: int = 1_048_576
    backup_count: int = 5


@dataclass
class QueueCfg:
    db_path: str = "var/queue.db"


@dataclass
class MockCfg:
    tasks_file: str = ""
    operators: list[str] = field(default_factory=lambda: ["1001:Alice:1234"])


@dataclass
class AppConfig:
    device: DeviceCfg
    wms: WmsCfg
    scanner: ScannerCfg
    display: DisplayCfg
    input: InputCfg
    workflow: WorkflowCfg
    logging: LoggingCfg
    queue: QueueCfg
    mock: MockCfg


_CHOICES = {
    ("wms", "backend"): {"http", "mock"},
    ("scanner", "backend"): {"camera", "mock"},
    ("display", "backend"): {"framebuffer", "console", "image"},
    ("input", "backend"): {"gpio", "keyboard"},
    ("scanner", "af_mode"): {"continuous", "manual"},
}


def _section(data: dict, name: str) -> dict:
    sec = data.get(name, {})
    if not isinstance(sec, dict):
        raise ConfigError(f"[{name}] must be a table")
    return dict(sec)


def load_config(path: str | Path) -> AppConfig:
    path = Path(path)
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError:
        raise ConfigError(f"config file not found: {path}") from None
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: invalid TOML: {e}") from None

    dev = _section(data, "device")
    if not dev.get("id"):
        raise ConfigError(f"{path}: [device] id is required")

    inp = _section(data, "input")
    raw_pins = inp.pop("pins", {})
    pins: dict[Button, int] = {}
    for name, gpio in raw_pins.items():
        try:
            pins[Button(name)] = int(gpio)
        except ValueError:
            raise ConfigError(f"{path}: [input.pins] unknown button '{name}'") from None
    inp["pins"] = pins

    scn = _section(data, "scanner")
    if "frame_size" in scn:
        fs = scn["frame_size"]
        if not (isinstance(fs, list) and len(fs) == 2):
            raise ConfigError(f"{path}: [scanner] frame_size must be [width, height]")
        scn["frame_size"] = (int(fs[0]), int(fs[1]))

    try:
        cfg = AppConfig(
            device=DeviceCfg(**dev),
            wms=WmsCfg(**_section(data, "wms")),
            scanner=ScannerCfg(**scn),
            display=DisplayCfg(**_section(data, "display")),
            input=InputCfg(**inp),
            workflow=WorkflowCfg(**_section(data, "workflow")),
            logging=LoggingCfg(**_section(data, "logging")),
            queue=QueueCfg(**_section(data, "queue")),
            mock=MockCfg(**_section(data, "mock")),
        )
    except TypeError as e:
        raise ConfigError(f"{path}: unknown or missing key: {e}") from None

    for (sec, key), allowed in _CHOICES.items():
        val = getattr(getattr(cfg, sec), key)
        if val not in allowed:
            raise ConfigError(f"{path}: [{sec}] {key}='{val}' not in {sorted(allowed)}")
    if cfg.input.backend == "gpio" and len(cfg.input.pins) < 12:
        raise ConfigError(f"{path}: [input.pins] gpio backend needs all 12 buttons mapped")
    return cfg
