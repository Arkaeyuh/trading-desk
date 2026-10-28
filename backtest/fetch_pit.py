#!/usr/bin/env python3
"""
fetch_pit.py
============
Builds a point-in-time S&P 500 universe for the backtest — the names that
were actually in the index on each date, delisted ones included — and caches
their daily bars in --out (default backtest/data_pit/), in fetch_data.py's
format, plus membership.json and coverage.json.

Membership: fja05680/sp500 (MIT), sp500_ticker_start_end.csv — one row per
membership spell (ticker, start_date, end_date). Download it into
backtest/universe_src/ first (see backtest/README.md).

Prices, per ticker, cheapest source that provably covers the spell:
  1. Yahoo (fetch_data.fetch). Accepted only if its first bar is no later
     than the spell start and, for a removed name, its last bar is no earlier
     than the spell end. A reused ticker (WB = Weibo today, Wachovia then)
     fails the first check, because the new company's history starts later.
  2. Tiingo, for what Yahoo cannot cover, only if Tiingo's ticker list shows
     an interval covering the spell. Free tier: 50 requests/hour, 500 unique
     symbols/month, so requests are spaced by --tiingo-interval seconds and
     every result is cached (re-runs resume). Close/open are rebuilt as
     split-adjusted, NOT dividend-adjusted, from Tiingo's raw prices and
     splitFactor, to match the rest of the backtest; adjclose = Tiingo adjClose.
  3. Renames: the hand-checked RENAMES table below — the same legal company
     continuing under a new ticker, with a same-day handoff in the membership
     file. The old spell is mapped onto the new ticker's data, and only if that
     data covers it. Deliberately NOT inferred: a same-day handoff is also what
     every ordinary index replacement looks like (ABMD out, STLD in), and a
     wrong guess would map a bankruptcy onto a healthy company.
Anything left is reported as MISSING in coverage.json: those names cannot be
traded in the backtest, and the report says how much of the universe that is.

API key: $TIINGO_API_KEY, else ~/.config/tiingo/api_key. Never printed.
stdlib only. Python 3.9+.
"""
from __future__ import annotations
import argparse
import csv
import io
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
import zipfile
from datetime import date, datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fetch_data import MACRO_ETFS, fetch as yahoo_fetch  # noqa: E402

SRC = os.path.join(HERE, "universe_src")
TIINGO_URL = "https://api.tiingo.com/tiingo/daily/{sym}/prices?startDate={start}&endDate={end}&token={key}"
SLACK = 5  # days of tolerance on spell boundaries (holidays, effective-date conventions)

# old ticker -> new ticker, same legal entity (verified same-day handoff in
# sp500_ticker_start_end.csv). Corporate actions around the rename (spin-offs,
# special dividends) show up in split-only-adjusted closes, as they would live.
RENAMES = {
    "ANTM": "ELV", "ABC": "COR", "BLL": "BALL", "FB": "META", "WLTW": "WTW", "PKI": "RVTY",
    "FISV": "FISV", "FI": "FISV", "HCP": "DOC", "PEAK": "DOC", "COG": "CTRA", "SYMC": "GEN",
    "NLOK": "GEN", "BBT": "TFC", "JEC": "J", "HRS": "LHX", "TMK": "GL", "LB": "BBWI",
    "UTX": "RTX", "DWDP": "DD", "CBS": "PARA", "VIAC": "PARA", "ARNC": "HWM", "BHGE": "BKR",
    "FBHS": "FBIN", "SQ": "XYZ",
}


def _key() -> str:
    k = os.environ.get("TIINGO_API_KEY")
    if not k:
        p = os.path.expanduser("~/.config/tiingo/api_key")
        if os.path.exists(p):
            with open(p) as f:
                k = f.read().strip()
    if not k:
        sys.exit("no Tiingo key: set TIINGO_API_KEY or write it to ~/.config/tiingo/api_key")
    return k


def _shift(d: str, days: int) -> str:
    return (date.fromisoformat(d) + timedelta(days=days)).isoformat()


def load_spells(path: str, since: str, today: str) -> dict[str, list[list[str]]]:
    spells: dict[str, list[list[str]]] = {}
    with open(path) as f:
        for r in csv.DictReader(f):
            end = r["end_date"] or today
            if end < since:
                continue
            t = r["ticker"].replace(".", "-").upper()
            spells.setdefault(t, []).append([r["start_date"], end, "removed" if r["end_date"] else "current"])
    return spells


def load_tiingo_list(path: str) -> dict[str, list[tuple[str, str]]]:
    out: dict[str, list[tuple[str, str]]] = {}
    with zipfile.ZipFile(path) as z, z.open(z.namelist()[0]) as f:
        for r in csv.DictReader(io.TextIOWrapper(f)):
            if r["assetType"] == "Stock" and r["startDate"]:
                out.setdefault(r["ticker"].upper(), []).append((r["startDate"], r["endDate"] or "9999-12-31"))
    return out


def covered_spells(bars: list[dict], spells: list[list[str]], fetch_start: str) -> list[list[str]]:
    """Spells this price history provably belongs to. Per spell, because a
    ticker can be reused: HLT was one Hilton until 2007 and another since 2013."""
    return [sp for sp in spells if covers(bars, sp[0], sp[1], sp[2] == "removed", fetch_start)]


def covers(bars: list[dict], start: str, end: str, removed: bool, fetch_start: str = "2005-01-01") -> bool:
    if not bars:
        return False
    if bars[0]["date"] > _shift(max(start, fetch_start), SLACK):
        return False
    return not removed or bars[-1]["date"] >= _shift(end, -SLACK)


def tiingo_fetch(sym: str, start: str, end: str, key: str) -> list[dict]:
    req = urllib.request.Request(TIINGO_URL.format(sym=sym, start=start, end=end, key=key),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        rows = json.load(r)
    # Split-adjust raw prices: divide by the product of all LATER split factors.
    fac, out = 1.0, []
    for row in reversed(rows):
        o, c, h, l = row["open"], row["close"], row["high"], row["low"]
        if None in (o, c, h, l, row.get("adjClose")):
            fac *= row.get("splitFactor") or 1.0
            continue
        out.append({"date": row["date"][:10], "open": o / fac, "high": h / fac, "low": l / fac,
                    "close": c / fac, "adjclose": row["adjClose"]})
        fac *= row.get("splitFactor") or 1.0
    out.reverse()
    return out


def _save(out_dir: str, sym: str, source: str, bars: list[dict]) -> None:
    with open(os.path.join(out_dir, f"{sym}.json"), "w") as f:
        json.dump({"symbol": sym, "source": source,
                   "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "bars": bars}, f)


def _load(out_dir: str, sym: str) -> list[dict] | None:
    p = os.path.join(out_dir, f"{sym}.json")
    if not os.path.exists(p):
        return None
    with open(p) as f:
        return json.load(f)["bars"]


def main() -> int:
    ap = argparse.ArgumentParser(description="Point-in-time S&P 500 universe (delisted included) for the backtest.")
    ap.add_argument("--since", default="2007-01-01", help="Keep spells that end on/after this date.")
    ap.add_argument("--start", default="2005-01-01", help="First bar to fetch (warm-up for EMA200).")
    ap.add_argument("--out", default=os.path.join(HERE, "data_pit"))
    ap.add_argument("--membership-csv", default=os.path.join(SRC, "sp500_ticker_start_end.csv"))
    ap.add_argument("--tiingo-list", default=os.path.join(SRC, "tiingo_tickers.zip"))
    ap.add_argument("--tiingo-interval", type=float, default=75.0, help="Seconds between Tiingo calls (free: 50/h).")
    ap.add_argument("--tiingo-max", type=int, default=450, help="Max Tiingo symbols this run (free: 500/month).")
    ap.add_argument("--skip-tiingo", action="store_true")
    ap.add_argument("--refetch-yahoo", action="store_true", help="Re-download every Yahoo-sourced ticker.")
    a = ap.parse_args()

    today = datetime.now(timezone.utc).date().isoformat()
    os.makedirs(a.out, exist_ok=True)
    spells = load_spells(a.membership_csv, a.since, today)
    tlist = load_tiingo_list(a.tiingo_list)
    status_path = os.path.join(a.out, "coverage.json")
    status: dict[str, dict] = {}
    if os.path.exists(status_path):
        with open(status_path) as f:
            status = json.load(f)["tickers"]

    def write_status():
        n = len(spells)
        by = {}
        for v in status.values():
            by[v["status"]] = by.get(v["status"], 0) + 1
        with open(status_path, "w") as f:
            json.dump({"updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                       "n_tickers": n, "by_status": by, "tickers": status}, f, indent=1)

    def need(sym):  # widest window over this ticker's spells
        sp = spells[sym]
        return min(s for s, _, _ in sp), max(e for _, e, _ in sp), any(k == "removed" for _, _, k in sp)

    # Macro ETFs come from the main cache (Yahoo, already validated).
    for s in MACRO_ETFS:
        src = os.path.join(HERE, "data", f"{s}.json")
        if os.path.exists(src) and not os.path.exists(os.path.join(a.out, f"{s}.json")):
            shutil.copy(src, a.out)

    # 1. Yahoo
    for i, sym in enumerate(sorted(spells)):
        st0 = status.get(sym, {})
        if a.refetch_yahoo and st0.get("status") == "yahoo":
            st0 = {}
        if st0.get("status") in ("yahoo", "tiingo"):
            if "covered" not in st0:  # cache from before per-spell coverage
                st0["covered"] = covered_spells(_load(a.out, sym) or [], spells[sym], a.start)
            continue
        if st0.get("yahoo_checked") == a.start:
            st0["status"] = "pending"  # Yahoo already tried with these settings
            continue
        s, e, removed = need(sym)
        try:
            bars = yahoo_fetch(sym, a.start, today)
        except Exception:
            bars = []
        ok = covered_spells(bars, spells[sym], a.start)
        if ok:
            _save(a.out, sym, "yahoo-chart", bars)
            status[sym] = {"status": "yahoo", "spells": spells[sym], "covered": ok}
        else:
            status[sym] = {"status": "pending", "spells": spells[sym], "yahoo_checked": a.start,
                           "yahoo": f"{bars[0]['date']}..{bars[-1]['date']}" if bars else "none"}
        if i % 50 == 0:
            print(f"  yahoo {i}/{len(spells)}", file=sys.stderr)
            write_status()
        time.sleep(0.3)
    write_status()

    # 2. Tiingo for what Yahoo could not cover
    if not a.skip_tiingo:
        key = _key()
        used = 0
        for sym in sorted(spells):
            st = status[sym]
            if st["status"] not in ("pending", "tiingo_error"):
                continue
            s, e, removed = need(sym)
            ivs = tlist.get(sym, [])
            if not any(lo <= _shift(max(sp[0], a.start), SLACK) and hi >= _shift(sp[1], -SLACK)
                       for lo, hi in ivs for sp in spells[sym]):
                st["status"] = "pending"
                st["tiingo"] = "not listed for this period" if not ivs else f"listed {ivs}"
                continue
            if used >= a.tiingo_max:
                break
            try:
                bars = tiingo_fetch(sym, a.start, today, key)
            except urllib.error.HTTPError as ex:
                st["status"], st["tiingo"] = "tiingo_error", f"HTTP {ex.code}"
                if ex.code == 429:
                    print("  tiingo rate limit hit — stopping; re-run later to resume", file=sys.stderr)
                    break
                bars = None
            except Exception as ex:
                st["status"], st["tiingo"] = "tiingo_error", type(ex).__name__
                bars = None
            used += 1
            if bars is not None:
                ok = covered_spells(bars, spells[sym], a.start)
                if ok:
                    _save(a.out, sym, "tiingo", bars)
                    st["status"], st["covered"] = "tiingo", ok
                else:
                    st["tiingo"] = f"returned {bars[0]['date']}..{bars[-1]['date']}" if bars else "empty"
                    st["status"] = "pending"
            print(f"  tiingo {used}: {sym} -> {st['status']}", file=sys.stderr)
            write_status()
            time.sleep(a.tiingo_interval)

    # 3. Renames (hand-checked table only)
    for sym, st in status.items():
        new = RENAMES.get(sym)
        if st["status"] in ("pending", "tiingo_error", "missing", "renamed") and new and new != sym:
            bars = _load(a.out, new)
            s, e, _ = need(sym)
            ok = covered_spells(bars, [[x, y, "current"] for x, y, _ in spells[sym]], a.start) if bars else []
            if ok:
                st["status"], st["renamed_to"], st["covered"] = "renamed", new, ok
            else:
                st["status"] = "pending"
                st.pop("renamed_to", None)
    for st in status.values():
        if st["status"] in ("pending", "tiingo_error"):
            st["status"] = "missing" if st["status"] == "pending" else st["status"]

    # membership.json: ticker -> spells, with renamed spells attached to the data file that holds them
    member: dict[str, list[list[str]]] = {}
    for sym, st in status.items():
        tgt = st.get("renamed_to", sym) if st["status"] == "renamed" else sym
        if st["status"] in ("yahoo", "tiingo", "renamed"):
            member.setdefault(tgt, []).extend([x[0], x[1]] for x in st["covered"])
    with open(os.path.join(a.out, "membership.json"), "w") as f:
        json.dump({"source": "fja05680/sp500 sp500_ticker_start_end.csv", "since": a.since,
                   "membership": {k: sorted(v) for k, v in sorted(member.items())}}, f, indent=1)
    write_status()

    by = {}
    for v in status.values():
        by[v["status"]] = by.get(v["status"], 0) + 1
    n_sp = {"current": [0, 0], "removed": [0, 0]}
    for sym, v in status.items():
        cov = {(x[0], x[1]) for x in v.get("covered", [])} if v["status"] in ("yahoo", "tiingo", "renamed") else set()
        for x in v["spells"]:
            n_sp[x[2]][0] += 1
            n_sp[x[2]][1] += (x[0], x[1]) in cov
    print(f"tickers {len(spells)}: " + ", ".join(f"{k} {v}" for k, v in sorted(by.items())))
    for k, (n, ok) in n_sp.items():
        print(f"{k:8} membership spells covered: {ok}/{n} ({ok / n:.0%})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
