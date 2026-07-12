"""Phase 0 bring-up: verify the GamePi20 button→GPIO map from the config.

    python -m hht.tools.buttontest -c /etc/hht/hht.toml

Press each physical button; its logical name and GPIO print to the terminal.
If a press prints the wrong name (board revision differences), fix [input.pins]
in the config — no code change needed. Ctrl-C to exit.
"""

from __future__ import annotations

import argparse
import signal

from ..config import load_config


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-c", "--config", default="/etc/hht/hht.toml")
    args = parser.parse_args()
    cfg = load_config(args.config)

    from gpiozero import Button as GpioButton

    held = []
    print(f"{len(cfg.input.pins)} buttons configured — press each one (Ctrl-C to exit)")
    for name, pin in sorted(cfg.input.pins.items(), key=lambda kv: kv[1]):
        btn = GpioButton(pin, pull_up=True, bounce_time=0.05)
        btn.when_pressed = (
            lambda n=name, p=pin: print(f"  {n.value.upper():<7} GPIO{p}")
        )
        held.append(btn)

    signal.pause()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
