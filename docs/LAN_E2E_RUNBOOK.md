# LAN End-to-End Runbook — HHT-001 against the live WMS (the "hot run")

> The second of the two on-device manuals: run the **dry run**
> ([DEVICE_DRY_RUN.md](DEVICE_DRY_RUN.md), no WMS needed) first — it closes every
> device-only case so this round only has to prove the live integration.
>
> The on-device round that closes Phase 3. Everything here is prepared so the session
> is mechanical once **HHT-001** and the **WMS host** are both on the warehouse LAN.
> It is the physical-radio successor to the 2026-07-15 loopback run (44/44 checks, all
> virtual/local) recorded in
> `../warehouse-management/docs/evidence/2026-07-15-hht-loopback-integration.md`; that
> run explicitly left "the physical device and the real radio" open — this closes it.
>
> **Status: not yet executed** — HHT-001 and the WMS host are not both set up on the LAN
> at time of writing (2026-07-18). Fill a TEST_REPORT round from the checklist below.

## What this proves

The WMS v1 contract serves a real picker terminal over real WiFi: badge+PIN auth, a full
guided pick with server-authoritative scans, and offline store-and-forward that survives an
actual radio loss (not a mocked `offline` flag) and drains on reconnect — plus the device-only
cases the off-device round (TEST_REPORT_2026-07-18_offdevice.md) left PENDING.

## 1. Preconditions

### 1.1 WMS host (owner-managed, WMS repo)
- [ ] WMS running on the LAN host per `../warehouse-management/docs/runbook-linux.md` §2,
      health green locally (`curl -fsS http://localhost:8080/actuator/health` → `"UP"`, §1 step 5 / §2).
- [ ] Firewall scoped to the API port for the warehouse subnet — runbook §3:
      `sudo ufw allow from <LAN-subnet>/24 to any port 8080 proto tcp`. PostgreSQL stays
      loopback-only (never opened to the LAN).
- [ ] **LAN reachability from a second machine** confirmed — runbook §4:
      `curl -fsS http://<WMS-LAN-IP>:8080/actuator/health` → `"UP"`. If this fails, it is
      firewall/routing, not the app — do not restart the WMS.

### 1.2 WMS data (registered before the run)
- [ ] **Device**: HHT-001's `device_code` is registered and **active** in the WMS `device`
      table (uppercase), and equals `[device] id` in the unit's `hht.toml`. The loopback used
      the dev-seed device `HHT-DEV-01`; a physical unit needs its own registered code (e.g.
      `HHT-PI-01`). An unregistered/inactive device fails login with
      `DEVICE_NOT_REGISTERED` / `DEVICE_INACTIVE`.
- [ ] **Picker account**: username with a **numeric** password whose length equals
      `[workflow] pin_length` (dev seed: `picker02` / `2468`, `pin_length = 4`).
- [ ] A claimable task exists for that picker (dev seed demo data, or create one).

### 1.3 Physical materials
- [ ] **Badge QR** for the picker: `python scripts/make_badge.py picker02 -o badge-picker02.png`
      (needs the dev extra; runs on a workstation, never on-device) → payload `OP:picker02`.
- [ ] **Location / article labels** printed from the WMS, exact case-sensitive payloads
      `LOC:<location-code>` and `ART:<sku>` (ADR 0007 in the WMS repo) matching the seeded task.
- [ ] Test QR sheet handy (`docs/img/test_qr_sheet.png`) for the pre-run camera focus check.

## 2. Device configuration (HHT-001)

Start from `config/dev-http.toml`; the on-device `hht.toml` differs from the loopback config
only in the boundaries that become real hardware plus the WMS address:

| Key | Loopback (`dev-http.toml`) | On HHT-001 |
|---|---|---|
| `[device] id` | `HHT-DEV-01` | the unit's **registered** device_code |
| `[wms] base_url` | `http://localhost:8080` | `http://<WMS-LAN-IP>:8080` |
| `[wms] timeout_s` / `retry_interval_s` | 2.0 / 5.0 | 5.0 / 30.0 (WiFi latency headroom) |
| `[scanner] backend` | `mock` | `camera` |
| `[display] backend` | `console` | `framebuffer` |
| `[input] backend` | `keyboard` | `gpio` (all 12 pins mapped) |
| `[audio] backend` | `none` | `alsa` |

Everything else (workflow, queue, logging) carries over. Confirm the app reaches the WMS:
`python -m hht -c /etc/hht/hht.toml` → header shows `ON`, badge login succeeds.

## 3. Test checklist (LAN-relevant + device-only)

Mirror the loopback stages, but drive them on the physical unit with real scans and a real
radio. Tick each and capture evidence (§4).

**Radio-loss method** (this is the whole point vs. loopback): cut WiFi for the "offline"
steps by one of — `nmcli radio wifi off` on the Pi, powering the AP/radio down, or walking
the unit out of range. Restore the same way. Do **not** use a mock offline flag.

- [ ] **Happy path** — badge+PIN login → claim task → scan real `LOC:` then `ART:` labels →
      confirm exact quantity → queue drains; WMS task `COMPLETED`, line pickedQuantity matches
      (loopback #1).
- [ ] **Wrong scans (server-side)** — wrong location and wrong article labels rejected with the
      on-screen banner; state unchanged (loopback #2). Confirm the new on-screen last-decode
      line and the accept-flash are visible on the panel.
- [ ] **Quantity discipline** — count ≠ requested → `DISCREPANCY`, nothing sent; recount confirms
      (loopback #3; TC-037).
- [ ] **Offline drain over real WiFi** — go offline mid-task (radio off), complete the pick
      (queue 1→2→3, operator never blocked), attempt delivery (stays queued), restore WiFi →
      chain replays FIFO, queue → 0, one stock movement (loopback #4; **TC-040 on real radio**).
- [ ] **Power-loss durability (TC-042)** — complete a pick with WiFi off, hard power-cut the
      unit, boot, restore WiFi → chain reaches the WMS after boot, stock moves exactly once
      (client `confirmationId` idempotency).
- [ ] **Replay rejection + admin recovery** — pick offline, admin blocks the task in the WMS,
      go online → replay refused, chain dead-lettered, device shows `SYNC_FAILED` → acknowledge;
      admin resume → task claimable again and completes (loopback #7; TC-045).
- [ ] **Token expiry → re-login → drain** — revoke the session server-side mid-queue → next call
      `401` → device drops to login keeping the queue → re-login → queue drains under the fresh
      token (loopback #9; TC-046).
- [ ] **Claim / logout guards** — claim refused and logout refused while ops pending
      (TC-047, TC-049).
- [ ] **Device conflict** — a second picker logging in on HHT-001 while a task is held →
      `409 DEVICE_ASSIGNMENT_CONFLICT` banner (loopback #8).
- [ ] **Correlation-ID join** — a device-generated `X-Correlation-Id` appears in **both** the
      device JSONL and the WMS structured log for the same request (loopback #10).
- [ ] **Camera / scanner (TC-010 full, TC-011)** — 10 decodes at 10–20 cm, median `latency_ms`
      < 500 ms from `scan_decoded` events; hold-steady debounce fires exactly once.
- [ ] **Audio + noise baseline (TC-004)** — DEVICE_CONFIGURATION §3.5.1 on battery and USB;
      GPIO18 PWM audio, accept/error/confirm cues clean; classify switch-pop vs. buzz.
- [ ] **Service (TC-050/051/052)** — `systemctl status hht` green after reboot; `pkill -9`
      restarts within ~5 s with a fresh `app_started`; `hht.tools.logreport` on the shift's
      JSONL reads back consistent picks/rejects/offline windows.

## 4. Evidence capture

Per CLAUDE.md's evidence discipline, cross-repo integration evidence lands in **both** repos:

- **This repo** — `docs/evidence/<date>-lan-e2e/`: the device `hht.jsonl` shift log (or excerpts
      quoting the JSONL lines with timestamps/task IDs), LCD photos or PNG captures (switch
      `[display] backend = "image"` for a capture pass, or photograph the panel), and a filled
      `TEST_REPORT_<date>_lan.md` from the template.
- **WMS repo** — `../warehouse-management/docs/evidence/<date>-hht-lan-e2e.md`: the WMS-side view
      (task state transitions, the matching correlation-ID log line, stock-movement count),
      mirroring the 2026-07-15 loopback record.
- Note the network conditions actually used (AP model, RSSI at the test spot, how the radio loss
      was induced) in the report's Environment section.

## 5. After the run

- Fill the Phase 3 LAN e2e checkbox in PLAN.md with a dated evidence pointer.
- With TC-004/010/011/042/050/051/052 executed, promote the release verdict from
  "release with restrictions" to a final sign-off, then **freeze** per the ecosystem role
  (new picking features belong in warehouse-android `:app-picker`, not here).
