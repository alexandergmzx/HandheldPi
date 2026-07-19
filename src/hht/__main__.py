"""Entry point: `python -m hht -c <config.toml>` (interactive) or `--script <file>`
(deterministic scripted run for functional tests — see tests/scripts/)."""

from __future__ import annotations

import argparse
import logging
import queue as queue_mod
import sys

from . import __version__
from .config import ConfigError, load_config
from .events import QuitEvent, TickEvent
from .flusher import Flusher
from .logsetup import evt, setup_logging
from .state_machine import PickingStateMachine
from .ui import make_display
from .ui.screens import render
from .wms import make_wms_client
from .wms.offline_queue import OfflineQueue

log = logging.getLogger("hht.app")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hht", description="HandheldPi picking terminal")
    parser.add_argument("-c", "--config", default="config/dev.toml",
                        help="path to TOML config (default: config/dev.toml)")
    parser.add_argument("--script", metavar="FILE",
                        help="run a functional test script instead of interactive mode")
    parser.add_argument("--version", action="version", version=f"hht {__version__}")
    args = parser.parse_args(argv)

    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2

    # In console-display mode the terminal IS the screen; keep human logs out of it.
    setup_logging(cfg.logging, console=cfg.display.backend != "console")

    if args.script:
        from .script_runner import run_script

        return run_script(cfg, args.script)
    try:
        return _run_interactive(cfg)
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


def _run_interactive(cfg) -> int:
    from .audio import make_audio_player
    from .inputs import make_input
    from .scanner import make_scanner

    events: queue_mod.Queue = queue_mod.Queue()
    wms = make_wms_client(cfg)
    offline_queue = OfflineQueue(cfg.queue.db_path)
    flusher = Flusher(cfg, wms, offline_queue, events.put)
    audio = make_audio_player(cfg)
    sm = PickingStateMachine(
        cfg, wms, offline_queue, kick_flusher=flusher.kick,
        play_sound=audio.play,
    )
    display = make_display(cfg)
    input_source = make_input(cfg)
    scanner = make_scanner(cfg)

    evt(log, "app_started", version=__version__, device_id=cfg.device.id,
        wms_backend=cfg.wms.backend, display=cfg.display.backend,
        audio=cfg.audio.backend)

    audio.start()
    input_source.start(events.put)
    scanner.start(events.put)
    flusher.start()
    display.show(render(sm))

    try:
        while True:
            # Wake sooner while an accept flash is showing so it clears on time.
            timeout = 0.05 if sm.invert_active else 0.5
            try:
                event = events.get(timeout=timeout)
            except queue_mod.Empty:
                event = TickEvent()
            if isinstance(event, QuitEvent):
                break
            sm.handle(event)
            while True:  # drain burst before re-rendering
                try:
                    event = events.get_nowait()
                except queue_mod.Empty:
                    break
                if isinstance(event, QuitEvent):
                    raise KeyboardInterrupt
                sm.handle(event)
            display.show(render(sm))
    except KeyboardInterrupt:
        pass
    finally:
        evt(log, "app_stopping")
        scanner.stop()
        input_source.stop()
        flusher.stop()
        audio.stop()
        display.close()
        offline_queue.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
