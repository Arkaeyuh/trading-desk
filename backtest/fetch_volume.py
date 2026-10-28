#!/usr/bin/env python3
"""
fetch_volume.py
===============
Adds daily share volume to bar caches written before the fetchers kept it
(fetch_data.py, fetch_pit.py). Each file is re-fetched from the source that
wrote it and volume is merged in BY DATE. Prices are never touched, so every
earlier backtest result stays reproducible. A bar the re-fetch does not return
gets volume = None.

Re-fetched closes are compared with the cached ones as a check that the
source still serves the same history; files where more than 1% of shared days
differ by over 1% are reported and left unchanged.

Tiingo free tier: 50 requests/hour, so Tiingo files are spaced by
--tiingo-interval seconds; re-runs skip files that already have volume.

stdlib only. Python 3.9+.
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
import urllib.error
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fetch_data import fetch as yahoo_fetch  # noqa: E402
from fetch_pit import _key, tiingo_fetch  # noqa: E402


def _plus(d: str, days: int) -> str:
    return (date.fromisoformat(d) + timedelta(days=days)).isoformat()


def merge(bars: list[dict], fresh: list[dict]) -> tuple[int, int, int]:
    """Set bar['volume'] from fresh, by date. Returns (matched, missing, price_mismatches)."""
    by_date = {b["date"]: b for b in fresh}
    matched = missing = bad = 0
    for b in bars:
        f = by_date.get(b["date"])
        if f is None:
            b["volume"] = None
            missing += 1
            continue
        matched += 1
        if abs(f["close"] / b["close"] - 1) > 0.01:
            bad += 1
        b["volume"] = f.get("volume")
    return matched, missing, bad


def main() -> int:
    ap = argparse.ArgumentParser(description="Merge daily volume into cached bar files.")
    ap.add_argument("symbols", nargs="*", help="Default: every bar file in --data.")
    ap.add_argument("--data", default=os.path.join(HERE, "data_pit"))
    ap.add_argument("--only", choices=("yahoo", "tiingo"), help="Restrict to files from one source.")
    ap.add_argument("--tiingo-interval", type=float, default=75)
    a = ap.parse_args()

    files = a.symbols or sorted(f[:-5] for f in os.listdir(a.data)
                                if f.endswith(".json") and f not in ("membership.json", "coverage.json"))
    key = None
    done = skipped = failed = 0
    for sym in files:
        path = os.path.join(a.data, f"{sym}.json")
        with open(path) as f:
            doc = json.load(f)
        bars = doc["bars"]
        if bars and all("volume" in b for b in bars):
            skipped += 1
            continue
        src = "tiingo" if doc.get("source") == "tiingo" else "yahoo"
        if a.only and src != a.only:
            continue
        start, end = bars[0]["date"], bars[-1]["date"]
        try:
            if src == "yahoo":
                # period2 is exclusive: pad the end so the last cached bar is included
                fresh = yahoo_fetch(sym, start, _plus(end, 5))
                time.sleep(0.4)
            else:
                key = key or _key()
                fresh = tiingo_fetch(sym, start, end, key)
        except urllib.error.HTTPError as ex:
            print(f"  {sym:6} {src} HTTP {ex.code}", file=sys.stderr)
            failed += 1
            if src == "tiingo" and ex.code == 429:
                print("  tiingo rate limit hit — stopping; re-run later to resume", file=sys.stderr)
                break
            continue
        except Exception as ex:  # network, schema change: report and move on
            print(f"  {sym:6} {src} FAILED: {type(ex).__name__}: {ex}", file=sys.stderr)
            failed += 1
            continue
        matched, missing, bad = merge(bars, fresh)
        if bad > 0.01 * max(matched, 1):
            print(f"  {sym:6} {src} prices differ on {bad}/{matched} days — left unchanged", file=sys.stderr)
            failed += 1
            continue
        with open(path, "w") as f:
            json.dump(doc, f)
        done += 1
        print(f"  {sym:6} {src} volume on {matched} bars, {missing} without", file=sys.stderr)
        if src == "tiingo":
            time.sleep(a.tiingo_interval)
    print(f"merged {done} · already had volume {skipped} · failed {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
