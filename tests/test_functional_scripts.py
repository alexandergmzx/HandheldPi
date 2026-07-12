"""Runs every scripted functional test in tests/scripts/ through the real app
wiring (script runner + image display), same as `hht --script <file>` on device."""

from pathlib import Path

import pytest

from hht.script_runner import run_script

SCRIPTS = sorted((Path(__file__).parent / "scripts").glob("*.txt"))


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.stem)
def test_script(cfg, script, tmp_path):
    cfg.display.backend = "image"  # also exercises the PNG evidence capture
    assert run_script(cfg, script) == 0
    assert (tmp_path / "screens" / "current.png").exists()
