"""Summarize a shift's JSONL log: pick counts, scan errors, offline windows.

    python -m hht.tools.logreport /var/log/hht/hht.jsonl
"""

from __future__ import annotations

import argparse
import json
from collections import Counter


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("logfile")
    args = parser.parse_args()

    counts: Counter[str] = Counter()
    picks = short_picks = 0
    offline_from: str | None = None
    offline_windows: list[tuple[str, str]] = []
    first_ts = last_ts = None

    with open(args.logfile, encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            first_ts = first_ts or rec.get("ts")
            last_ts = rec.get("ts", last_ts)
            event = rec.get("event", "?")
            counts[event] += 1
            if event == "pick_confirmed":
                picks += 1
                short_picks += bool(rec.get("short_pick"))
            elif event == "net_status":
                if not rec.get("online") and offline_from is None:
                    offline_from = rec.get("ts")
                elif rec.get("online") and offline_from is not None:
                    offline_windows.append((offline_from, rec.get("ts", "?")))
                    offline_from = None
    if offline_from:
        offline_windows.append((offline_from, "end-of-log"))

    print(f"log span         {first_ts} .. {last_ts}")
    print(f"picks confirmed  {picks} ({short_picks} short)")
    print(f"scans rejected   {counts['scan_rejected']}")
    print(f"workflow errors  {counts['workflow_error']}")
    print(f"offline windows  {len(offline_windows)}")
    for start, end in offline_windows:
        print(f"    {start} -> {end}")
    print("\nevents by type:")
    for event, n in counts.most_common():
        print(f"  {n:>6}  {event}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
