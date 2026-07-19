# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A DIY warehouse hand-held picking terminal (HHT): Waveshare GamePi20 (Pi Zero
2 W, 512 MB RAM, 2.0" ST7789V SPI display, 12 GPIO buttons) + Camera Module 3
as QR scanner, talking to the `../warehouse-management` WMS over REST.
PLAN.md holds the phase plan, the researched-then-verified hardware facts, and
the bring-up findings — read it before touching anything hardware-adjacent.

**Ecosystem role (`../ECOSYSTEM.md`, v3):** this is `hht-picker`, the
first-generation picker terminal — *finish to demonstrable, then freeze*.
After freeze: maintenance against the pinned WMS v1 contract only; new picking
features belong in warehouse-android's `:app-picker`. Don't propose feature
growth here.

## Commands

```bash
python3 -m venv .venv && .venv/bin/pip install -e .[dev]   # dev machine
.venv/bin/python -m pytest                                  # full suite (unit + HTTP + functional scripts)
.venv/bin/python -m hht -c config/dev.toml                  # run off-device (mock WMS, console/PNG display)
.venv/bin/python -m hht -c config/dev-http.toml             # run against a live WMS dev instance
.venv/bin/python -m hht -c config/dev.toml --script tests/scripts/offline_pick.txt   # scripted functional test
python -m hht.tools.buttontest                              # on device: GPIO map verification
```

On-device: `scripts/install.sh` provisions everything (apt deps,
`--system-site-packages` venv, display overlay, systemd unit `hht`);
`scripts/verify_unit.sh` runs the Phase 0 checklist as one command. Unit
HHT-001 is the reference device (`raspi` on the LAN).

## Architecture (the parts that span files)

- Single-threaded main loop: event queue → `state_machine` → ViewModel →
  Display. Producers (GPIO input, scanner thread, tick timer) only post
  events; nothing else mutates workflow state.
- **Every boundary has a dev twin** — Display (framebuffer / console / PNG),
  InputSource (GPIO / terminal keyboard), Scanner (camera / scripted),
  WmsClient (HTTP / mock) — chosen by config, so the entire app runs on a dev
  machine with no hardware. Keep new code behind this pattern.
- Store-and-forward: confirmations and scans append to a sqlite (WAL) queue as
  ordered per-task op chains; a flusher thread replays FIFO with idempotency
  keys, pauses on auth expiry (never dead-letters on 401), dead-letters server
  refusals (SYNC_FAILED surfaced, never hidden). Scans are authoritative WMS
  state transitions, pre-validated locally with the server's exact rule.
- Scripted functional tests (`--script` + `expect_state`/`expect_error`) map
  1:1 to the numbered cases in docs/TEST_SPECIFICATION.md (HHT-TC-xxx).

## Rules

- The WMS v1 contract is owned by `../warehouse-management/API.md`; this
  repo's API.md documents the client-side semantics. Contract changes start
  on the WMS side — never adapt this client to an undocumented behavior.
- Dependency budget is a hard constraint (512 MB RAM): apt-first, pip only for
  what's already listed in pyproject.toml. Camera/GPIO stacks come from apt
  (`python3-picamera2 --no-install-recommends`, `python3-pyzbar`,
  `python3-gpiozero`/`python3-lgpio`) — never pip, they must match system
  libcamera. No OpenCV.
- Hardware claims in PLAN.md tables are "research, not gospel" until verified
  on the device; verified facts get dated bring-up notes in PLAN.md and the
  as-built column in docs/DEVICE_CONFIGURATION.md.
- Evidence discipline matches the WMS repo: completion is a filled test
  report with citable evidence, not passing compilation. Cross-repo
  integration evidence lives in both repos (see the 2026-07-15 loopback run
  in `warehouse-management/docs/evidence/`).
