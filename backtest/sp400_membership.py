#!/usr/bin/env python3
"""
sp400_membership.py
===================
Approximate point-in-time S&P MidCap 400 membership from the revision history
of Wikipedia's "List of S&P 400 companies" (one snapshot per quarter, from
2011, when the page first held a full list). Writes it in fja05680's
ticker,start_date,end_date format so fetch_pit.py can build the universe:

  python3 backtest/sp400_membership.py
  python3 backtest/fetch_pit.py --membership-csv backtest/universe_src/sp400_ticker_start_end.csv \
      --out backtest/data_mid --since 2011-01-01

A spell starts at the revision date of the first snapshot that lists the
ticker and ends at the revision date of the first later snapshot that does
not. Both dates are when the change was visible, so the universe never uses
information from after the trading day. It is an approximation: editors lag
the real index changes (some stretches went months without an edit), and a
name that joined and left between two snapshots is missed.

Fresh hold-out: spells are cut wherever the same ticker was in the S&P 500
(per sp500_ticker_start_end.csv), so no (ticker, day) pair overlaps the S&P
500 universe the earlier experiments were tuned on.

stdlib only. Python 3.9+.
"""
from __future__ import annotations
import argparse
import csv
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "universe_src")
API = "https://en.wikipedia.org/w/api.php"
PAGE = "List of S&P 400 companies"
SYMBOL = re.compile(r"\{\{\s*(?:NyseSymbol|NasdaqSymbol|nyse|nasdaq|NYSE|NASDAQ)\s*\|\s*([A-Za-z.\-]+)")


def snapshot(before: str) -> tuple[str, list[str]] | None:
    """Latest revision before `before` (ISO date): (revision date, tickers)."""
    q = urllib.parse.urlencode({"action": "query", "prop": "revisions", "titles": PAGE, "rvlimit": 1,
                                "rvstart": before + "T00:00:00Z", "rvdir": "older",
                                "rvprop": "timestamp|content", "rvslots": "main", "format": "json"})
    req = urllib.request.Request(f"{API}?{q}", headers={
        "User-Agent": "trading-desk-backtest/0.1 (personal research script; python-urllib)"})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                page = next(iter(json.load(r)["query"]["pages"].values()))
            break
        except urllib.error.HTTPError as ex:
            if ex.code != 429 or attempt == 5:
                raise
            time.sleep(float(ex.headers.get("Retry-After") or 10 * 2 ** attempt))
    revs = page.get("revisions")
    if not revs:
        return None
    text = revs[0]["slots"]["main"]["*"]
    return revs[0]["timestamp"][:10], sorted({t.upper().replace(".", "-") for t in SYMBOL.findall(text)})


def subtract(spell: tuple[str, str], blocks: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """spell minus the union of blocks (closed date intervals, ISO strings)."""
    out = [spell]
    for lo, hi in blocks:
        nxt = []
        for s, e in out:
            if hi < s or lo > e:
                nxt.append((s, e))
                continue
            if s < lo:
                nxt.append((s, lo))
            if hi < e:
                nxt.append((hi, e))
        out = nxt
    return [x for x in out if x[0] < x[1]]


def main() -> int:
    ap = argparse.ArgumentParser(description="S&P 400 membership spells from Wikipedia revision history.")
    ap.add_argument("--out", default=os.path.join(SRC, "sp400_ticker_start_end.csv"))
    ap.add_argument("--sp500-csv", default=os.path.join(SRC, "sp500_ticker_start_end.csv"))
    ap.add_argument("--min-names", type=int, default=300, help="Skip snapshots with fewer names (partial lists).")
    a = ap.parse_args()

    today = datetime.now(timezone.utc).date()
    snaps: list[tuple[str, set[str]]] = []
    for y in range(2011, today.year + 1):
        for m in (1, 4, 7, 10):
            d = date(y, m, 1)
            if d > today:
                break
            s = snapshot(d.isoformat())
            if s and len(s[1]) >= a.min_names and (not snaps or s[0] != snaps[-1][0]):
                snaps.append((s[0], set(s[1])))
            time.sleep(1.0)
    print(f"{len(snaps)} distinct snapshots, {snaps[0][0]} → {snaps[-1][0]}")

    spells: dict[str, list[tuple[str, str]]] = {}
    open_: dict[str, str] = {}
    for d, names in snaps:
        for t in names - set(open_):
            open_[t] = d
        for t in set(open_) - names:
            spells.setdefault(t, []).append((open_.pop(t), d))
    for t, s in open_.items():
        spells.setdefault(t, []).append((s, ""))  # still a member

    sp500: dict[str, list[tuple[str, str]]] = {}
    with open(a.sp500_csv) as f:
        for r in csv.DictReader(f):
            sp500.setdefault(r["ticker"].upper().replace(".", "-"), []).append(
                (r["start_date"], r["end_date"] or "9999-12-31"))

    rows, cut = [], 0
    for t in sorted(spells):
        for s, e in spells[t]:
            parts = subtract((s, e or "9999-12-31"), sp500.get(t, []))
            cut += len(parts) != 1 or parts[0] != (s, e or "9999-12-31")
            rows += [(t, lo, "" if hi == "9999-12-31" else hi) for lo, hi in parts]
    with open(a.out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ticker", "start_date", "end_date"])
        w.writerows(rows)
    print(f"{len(rows)} spells for {len({r[0] for r in rows})} tickers -> {a.out} "
          f"({cut} spells trimmed for S&P 500 overlap)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
