# Test Report — HandheldPi picking terminal

> Template. Copy to `TEST_REPORT_<date>_<scope>.md`, fill every field, keep with the
> release. One row in §3 per executed test case from
> [TEST_SPECIFICATION.md](TEST_SPECIFICATION.md).

## 1. Identification

| | |
|---|---|
| Report ID | HHT-TR-____ |
| Date / tester | |
| Scope | e.g. "phase 2 regression", "provisioning HHT-002", "full spec" |
| App version / commit | `hht --version` / `git rev-parse --short HEAD` |
| Config used | e.g. `/etc/hht/hht.toml` (attach) or `config/dev.toml` |

## 2. Environment

| Item | Value |
|---|---|
| Device | e.g. HHT-001 (GamePi20, Pi Zero 2 W) or "dev machine, mocks" |
| OS / kernel | |
| WMS | mock / Spring Boot `<version>` at `<url>` |
| Network | e.g. warehouse WiFi, AP model, RSSI at test spot |
| Audio | ALSA device; battery/USB; speaker/headphones; potentiometer position |
| Power-on noise | none / click / buzz / hiss; start time and duration |
| Test data | mock built-ins / server dataset reference |

## 3. Results

Automated suites (attach output):

| Suite | Command | Result |
|---|---|---|
| Unit + functional scripts | `python -m pytest` | __ passed / __ failed |

Per test case:

| TC | Title | Result | Evidence | Notes / defect |
|---|---|---|---|---|
| HHT-TC-030 | Happy-path pick | PASS/FAIL/BLOCKED | `evidence/…png`, log excerpt | |
| | | | | |

**Evidence conventions:** scripted runs with `[display] backend = "image"` save a
numbered PNG per step — copy the relevant ones to `evidence/`. Log excerpts: quote the
JSONL lines (they carry timestamps and task IDs).

## 4. Defects

| ID | TC | Severity (S1 blocker…S4 cosmetic) | Description | Status |
|---|---|---|---|---|
| | | | | |

## 5. Deviations from the specification

Anything not executed as written (skipped cases, changed preconditions, test-data
differences) and why.

## 6. Verdict & sign-off

| | |
|---|---|
| Summary | __ pass / __ fail / __ blocked of __ executed |
| Verdict | RELEASE / RELEASE WITH RESTRICTIONS / NO RELEASE |
| Restrictions | |
| Signed | |
