#!/usr/bin/env python3
"""
fetch_data.py
=============
Downloads daily OHLC bars for the backtest from Yahoo Finance's public chart
endpoint and caches them as one JSON file per symbol:

  <out>/<SYM>.json  {"symbol", "source", "fetched_at",
                     "bars": [{"date", "open", "high", "low", "close", "adjclose"}]}

`close`/`open` are split-adjusted but NOT dividend-adjusted — the same basis as
Robinhood's get_equity_historicals, so signals in the backtest are computed on
the prices the live desk actually sees. `adjclose` (dividends reinvested) is
kept for benchmark returns only.

Re-running skips symbols already cached unless --refresh is given.

stdlib only. Python 3.9+.
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

MACRO_ETFS = ["SPY", "RSP", "IWM", "HYG", "LQD", "TLT", "XLY", "XLP"]
DEFAULT_UNIVERSE = [
    # Broad / sector ETFs
    "QQQ", "DIA", "XLK", "XLF", "XLE", "XLV", "XLI", "XLU", "XLB", "SMH",
    # Large caps across sectors
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "JPM", "XOM", "UNH",
    "HD", "PG", "KO", "CAT", "COST", "AMD", "NFLX", "BA", "DIS", "INTC", "WMT",
]
URL = ("https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
       "?period1={p1}&period2={p2}&interval=1d&events=div%2Csplit&includeAdjustedClose=true")


def fetch(sym: str, start: str, end: str) -> list[dict]:
    p1 = int(datetime.fromisoformat(start).replace(tzinfo=timezone.utc).timestamp())
    p2 = int(datetime.fromisoformat(end).replace(tzinfo=timezone.utc).timestamp())
    req = urllib.request.Request(URL.format(sym=sym, p1=p1, p2=p2),
                                 headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        res = json.load(r)["chart"]["result"][0]
    ts = res.get("timestamp") or []
    q = res["indicators"]["quote"][0]
    adj = res["indicators"].get("adjclose", [{}])[0].get("adjclose") or q["close"]
    bars = []
    for i, t in enumerate(ts):
        o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
        if None in (o, h, l, c) or adj[i] is None:
            continue  # Yahoo emits null rows on halts / partial sessions
        d = datetime.fromtimestamp(t, tz=timezone.utc).date().isoformat()
        bars.append({"date": d, "open": o, "high": h, "low": l, "close": c, "adjclose": adj[i]})
    return bars


def main() -> int:
    ap = argparse.ArgumentParser(description="Cache daily bars for the backtest (Yahoo chart API).")
    ap.add_argument("symbols", nargs="*", help=f"Default: the 8 macro ETFs + {len(DEFAULT_UNIVERSE)} universe names.")
    ap.add_argument("--start", default="2005-01-01")
    ap.add_argument("--end", default=datetime.now(timezone.utc).date().isoformat())
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    syms = args.symbols or (MACRO_ETFS + DEFAULT_UNIVERSE)
    os.makedirs(args.out, exist_ok=True)
    failed = []
    for sym in syms:
        path = os.path.join(args.out, f"{sym}.json")
        if os.path.exists(path) and not args.refresh:
            print(f"  {sym:6} cached")
            continue
        try:
            bars = fetch(sym, args.start, args.end)
        except Exception as e:  # network, 404, schema change — report and move on
            print(f"  {sym:6} FAILED: {e}", file=sys.stderr)
            failed.append(sym)
            continue
        with open(path, "w") as f:
            json.dump({"symbol": sym, "source": "yahoo-chart",
                       "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                       "bars": bars}, f)
        print(f"  {sym:6} {len(bars)} bars  {bars[0]['date']} → {bars[-1]['date']}" if bars else f"  {sym:6} empty")
        time.sleep(0.4)  # be polite to the endpoint
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
