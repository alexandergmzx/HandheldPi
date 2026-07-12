from pathlib import Path

import pytest

from hht.config import ConfigError, load_config
from hht.events import Button

REPO = Path(__file__).resolve().parent.parent


def test_example_config_loads():
    cfg = load_config(REPO / "config" / "hht.toml.example")
    assert cfg.device.id == "HHT-001"
    assert cfg.wms.backend == "http"
    assert cfg.input.backend == "gpio"
    assert len(cfg.input.pins) == 12
    assert cfg.input.pins[Button.A] == 23  # GamePi20 map
    assert cfg.scanner.frame_size == (640, 480)


def test_dev_config_loads():
    cfg = load_config(REPO / "config" / "dev.toml")
    assert cfg.wms.backend == "mock"
    assert cfg.scanner.backend == "mock"
    assert cfg.display.backend == "console"


def test_missing_device_id(tmp_path):
    p = tmp_path / "bad.toml"
    p.write_text('[device]\nsite = "X"\n')
    with pytest.raises(ConfigError, match="id is required"):
        load_config(p)


def test_unknown_button_name(tmp_path):
    p = tmp_path / "bad.toml"
    p.write_text('[device]\nid = "H"\n[input.pins]\nturbo = 9\n')
    with pytest.raises(ConfigError, match="unknown button"):
        load_config(p)


def test_gpio_backend_requires_full_pin_map(tmp_path):
    p = tmp_path / "bad.toml"
    p.write_text('[device]\nid = "H"\n[input]\nbackend = "gpio"\n')
    with pytest.raises(ConfigError, match="12 buttons"):
        load_config(p)


def test_invalid_backend_choice(tmp_path):
    p = tmp_path / "bad.toml"
    p.write_text('[device]\nid = "H"\n[display]\nbackend = "hologram"\n')
    with pytest.raises(ConfigError, match="hologram"):
        load_config(p)


def test_missing_file():
    with pytest.raises(ConfigError, match="not found"):
        load_config("/nonexistent/nope.toml")
