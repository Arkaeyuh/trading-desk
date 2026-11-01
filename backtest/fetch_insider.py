#!/usr/bin/env python3
"""
fetch_insider.py
================
Open-market insider purchases (Form 4, transaction code P) from the SEC's
quarterly "Insider Transactions Data Sets" (2006 Q1 onward), for the
insider_buy / insider_cluster entries in backtest.py.

  python3 backtest/fetch_insider.py --fetch [--from 2006q1] [--to 2026q3]

For each quarter: download {YYYY}q{N}_form345.zip, join SUBMISSION,
REPORTINGOWNER and NONDERIV_TRANS on ACCESSION_NUMBER, keep code-P
acquisitions, write backtest/data_insider/{YYYY}q{N}.csv and drop the zip.
Re-runs skip quarters already written. A quarter's file holds the filings
FILED in that quarter, so filing_date is when the market could know.

Point in time: Forms 3/4/5 filed by 10 pm ET get that day's filing date
(Reg S-T 13(a)(4)), i.e. possibly after the close. backtest.py therefore
decides on the close of the first trading day >= filing_date and fills at the
NEXT open, which a live desk reading EDGAR that night could do.

The SEC requires a User-Agent naming a contact ("Name email@example.com"):
$SEC_USER_AGENT, else ~/.config/sec/user_agent. Never printed.

Without arguments: self-test on a synthetic quarter. stdlib only. Python 3.9+.
"""
from __future__ import annotations
import argparse
import bisect
import csv
import io
import os
import re
import sys
import time
import urllib.error
import urllib.request
import zipfile
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data_insider")
URL = "https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/{q}_form345.zip"
COLS = ["filing_date", "accession", "form", "issuer_cik", "ticker", "owner_ciks", "owner_names",
        "relationship", "titles", "trans_date", "shares", "price", "value", "direct_indirect"]

# Pre-registered signal definition (2026-10-31, before any insider data was fetched).
MIN_FILING_VALUE = 10_000   # $ bought in one filing, so token buys don't count
CLUSTER_DAYS = 30           # calendar days of filings counted for a cluster
CLUSTER_MIN = 3             # distinct insiders in that window


# --------------------------------------------------------------------------
# Fetch and parse
# --------------------------------------------------------------------------

def _ua() -> str:
    ua = os.environ.get("SEC_USER_AGENT")
    if not ua:
        p = os.path.expanduser("~/.config/sec/user_agent")
        if os.path.exists(p):
            with open(p) as f:
                ua = f.read().strip()
    if not ua or "@" not in ua:
        sys.exit("SEC needs a contact User-Agent: set SEC_USER_AGENT or write "
                 "'Name email@example.com' to ~/.config/sec/user_agent")
    return ua


def _iso(s: str) -> str | None:
    s = (s or "").strip()
    for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _num(s: str) -> float | None:
    try:
        return float((s or "").replace(",", ""))
    except ValueError:
        return None


def _table(z: zipfile.ZipFile, name: str) -> list[dict]:
    member = next((n for n in z.namelist() if n.upper().endswith(name)), None)
    if member is None:
        raise ValueError(f"{name} not in archive: {z.namelist()}")
    with z.open(member) as f:
        text = io.TextIOWrapper(f, encoding="utf-8", errors="replace", newline="")
        rows = csv.DictReader(text, delimiter="\t", quoting=csv.QUOTE_NONE)
        return [{(k or "").strip().upper(): v for k, v in r.items()} for r in rows]


def parse_quarter(blob: bytes) -> list[dict]:
    """One quarter's zip -> code-P acquisitions on Forms 4 and 4/A, one row per transaction."""
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        subs = {r["ACCESSION_NUMBER"]: r for r in _table(z, "SUBMISSION.TSV")}
        owners: dict[str, list[dict]] = {}
        for r in _table(z, "REPORTINGOWNER.TSV"):
            owners.setdefault(r["ACCESSION_NUMBER"], []).append(r)
        trans = _table(z, "NONDERIV_TRANS.TSV")
    for need, rows in (("FILING_DATE", subs.values()), ("TRANS_CODE", trans)):
        if rows and need not in next(iter(rows)):
            raise ValueError(f"column {need} missing; got {sorted(next(iter(rows)))}")
    out = []
    for t in trans:
        if (t.get("TRANS_CODE") or "").strip().upper() != "P":
            continue
        if (t.get("TRANS_ACQUIRED_DISP_CD") or "").strip().upper() != "A":
            continue
        s = subs.get(t["ACCESSION_NUMBER"])
        if s is None or (s.get("DOCUMENT_TYPE") or "").strip() not in ("4", "4/A"):
            continue
        own = owners.get(t["ACCESSION_NUMBER"], [])
        sh, px = _num(t.get("TRANS_SHARES")), _num(t.get("TRANS_PRICEPERSHARE"))
        rel = sorted({x.strip() for o in own for x in (o.get("RPTOWNER_RELATIONSHIP") or "").split(",") if x.strip()})
        out.append({
            "filing_date": _iso(s.get("FILING_DATE")), "accession": t["ACCESSION_NUMBER"],
            "form": s["DOCUMENT_TYPE"].strip(), "issuer_cik": (s.get("ISSUERCIK") or "").strip().lstrip("0"),
            "ticker": (s.get("ISSUERTRADINGSYMBOL") or "").strip(),
            "owner_ciks": ";".join(sorted((o.get("RPTOWNERCIK") or "").strip().lstrip("0") for o in own)),
            "owner_names": ";".join((o.get("RPTOWNERNAME") or "").strip() for o in own),
            "relationship": ";".join(rel),
            "titles": ";".join(x for x in ((o.get("RPTOWNER_TITLE") or "").strip() for o in own) if x),
            "trans_date": _iso(t.get("TRANS_DATE")), "shares": sh, "price": px,
            "value": round(sh * px, 2) if sh is not None and px is not None else None,
            "direct_indirect": (t.get("DIRECT_INDIRECT_OWNERSHIP") or "").strip(),
        })
    return out


def quarters(lo: str, hi: str) -> list[str]:
    y, q = int(lo[:4]), int(lo[-1])
    out = []
    while f"{y}q{q}" <= hi:
        out.append(f"{y}q{q}")
        y, q = (y + 1, 1) if q == 4 else (y, q + 1)
    return out


def fetch(lo: str, hi: str, out: str, pause: float) -> int:
    os.makedirs(out, exist_ok=True)
    ua = _ua()
    for q in quarters(lo, hi):
        path = os.path.join(out, f"{q}.csv")
        if os.path.exists(path):
            continue
        req = urllib.request.Request(URL.format(q=q), headers={"User-Agent": ua, "Accept-Encoding": "identity"})
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                blob = r.read()
        except urllib.error.HTTPError as e:
            print(f"{q}: HTTP {e.code}{' (not published yet?)' if e.code == 404 else ''}", file=sys.stderr)
            if e.code == 403:
                return 1
            continue
        rows = parse_quarter(blob)
        with open(path + ".tmp", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=COLS)
            w.writeheader()
            w.writerows(rows)
        os.replace(path + ".tmp", path)
        print(f"{q}: {len(blob) / 1e6:.0f} MB -> {len(rows)} purchases", file=sys.stderr)
        time.sleep(pause)
    return 0


# --------------------------------------------------------------------------
# Signal inputs for backtest.py
# --------------------------------------------------------------------------

def norm_ticker(t: str) -> str:
    t = re.split(r"[\s,;/]+", (t or "").strip().upper())[0] if (t or "").strip() else ""
    return t.replace(".", "-")


def qualifying(rows: list[dict]) -> list[dict]:
    """Per filing: original Form 4, an officer or director among the reporting
    owners, positive prices, >= MIN_FILING_VALUE bought. 4/A amendments are
    dropped (they restate filings already counted). 10%-owner-only filings,
    mostly funds, are dropped."""
    by_acc: dict[str, dict] = {}
    for r in rows:
        if r["form"] != "4" or not r["filing_date"]:
            continue
        rel = r["relationship"].lower()
        if "director" not in rel and "officer" not in rel:
            continue
        v = _num(r["value"]) if isinstance(r["value"], str) else r["value"]
        p = _num(r["price"]) if isinstance(r["price"], str) else r["price"]
        if not v or not p or p <= 0:
            continue
        f = by_acc.setdefault(r["accession"], {"filing_date": r["filing_date"], "issuer_cik": r["issuer_cik"],
                                               "ticker": norm_ticker(r["ticker"]), "owner": r["owner_ciks"],
                                               "value": 0.0})
        f["value"] += v
    return [f for f in by_acc.values() if f["value"] >= MIN_FILING_VALUE]


def map_to_universe(filings: list[dict], membership: dict, renames: dict,
                    names: dict) -> tuple[dict, dict]:
    """Attach each filing to a data file of the backtest universe.

    names: index spells under the ticker the stock had AT THE TIME (the source
    CSV, e.g. SYMC 2003-2019, NLOK 2019-2022, GEN 2022-). membership: spells
    per data file after fetch_pit's renames (GEN holds all three). A ticker on
    a filing dated d counts only if that ticker itself was in the index on d,
    so GenOn's "GEN" filings in 2011 can't land on Symantec. Candidates are the
    filing's ticker plus every ticker the same issuer CIK put on other filings
    (a stale ticker after a rename). Returns ({sym: [(date, owner, value)]}, stats)."""
    def resolve(t: str) -> str:
        seen = set()
        while t in renames and t not in seen:
            seen.add(t)
            t = renames[t]
        return t
    by_cik: dict[str, set[str]] = {}
    for f in filings:
        by_cik.setdefault(f["issuer_cik"], set()).add(f["ticker"])
    out: dict[str, list[tuple[str, str, float]]] = {}
    st = {"filings": len(filings), "mapped": 0, "unmapped_tickers": {}}
    for f in filings:
        d = f["filing_date"]
        cands = sorted(t for t in ({f["ticker"]} | by_cik.get(f["issuer_cik"], set())) - {""}
                       if any(sp[0] <= d <= sp[1] for sp in names.get(t, []))
                       and any(lo <= d <= hi for lo, hi in membership.get(resolve(t), [])))
        if not cands:
            st["unmapped_tickers"][f["ticker"]] = st["unmapped_tickers"].get(f["ticker"], 0) + 1
            continue
        sym = resolve(f["ticker"] if f["ticker"] in cands else cands[0])
        out.setdefault(sym, []).append((d, f["owner"], f["value"]))
        st["mapped"] += 1
    for v in out.values():
        v.sort()
    return out, st


def load_events(src: str, membership: dict, renames: dict, names: dict) -> tuple[dict, dict]:
    rows = []
    for name in sorted(os.listdir(src)):
        if re.fullmatch(r"\d{4}q[1-4]\.csv", name):
            with open(os.path.join(src, name), newline="") as f:
                rows.extend(csv.DictReader(f))
    if not rows:
        raise SystemExit(f"no quarter files in {src}: run fetch_insider.py --fetch first")
    return map_to_universe(qualifying(rows), membership, renames, names)


def insider_flags(dates: list[str], events: list[tuple[str, str, float]]) -> list[tuple[bool, bool, float]]:
    """Per trading day d: (buy, cluster, value).
    buy     : a qualifying filing dated in (previous trading day, d], i.e. d is the
              first session on or after its filing date;
    cluster : buy, and >= CLUSTER_MIN distinct insiders filed in [d - CLUSTER_DAYS, d];
    value   : $ bought in that window, used to rank same-day candidates."""
    fd = [e[0] for e in events]
    out = []
    for i, d in enumerate(dates):
        prev = dates[i - 1] if i else "0000-00-00"
        new = bisect.bisect_right(fd, d) > bisect.bisect_right(fd, prev)
        if not new:
            out.append((False, False, 0.0))
            continue
        lo = (date.fromisoformat(d) - timedelta(days=CLUSTER_DAYS)).isoformat()
        win = events[bisect.bisect_left(fd, lo): bisect.bisect_right(fd, d)]
        out.append((True, len({e[1] for e in win}) >= CLUSTER_MIN, sum(e[2] for e in win)))
    return out


# --------------------------------------------------------------------------
# Self-test
# --------------------------------------------------------------------------

def _synthetic_zip() -> bytes:
    def tsv(cols, rows):
        return "\t".join(cols) + "\n" + "".join("\t".join(r) + "\n" for r in rows)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("SUBMISSION.tsv", tsv(
            ["ACCESSION_NUMBER", "FILING_DATE", "DOCUMENT_TYPE", "ISSUERCIK", "ISSUERNAME", "ISSUERTRADINGSYMBOL"],
            [["a1", "03-JAN-2015", "4", "0000320193", "Apple", "aapl"],
             ["a2", "05-JAN-2015", "4/A", "0000320193", "Apple", "AAPL"],
             ["a3", "06-JAN-2015", "4", "0001326801", "Facebook", "FB"],
             ["a4", "06-JAN-2015", "4", "0000000099", "Fund target", "XYZ"]]))
        z.writestr("REPORTINGOWNER.tsv", tsv(
            ["ACCESSION_NUMBER", "RPTOWNERCIK", "RPTOWNERNAME", "RPTOWNER_RELATIONSHIP", "RPTOWNER_TITLE"],
            [["a1", "0000000001", "Cook Tim", "Director,Officer", "CEO"],
             ["a2", "0000000001", "Cook Tim", "Officer", "CEO"],
             ["a3", "0000000002", "Zuck", "Director", ""],
             ["a4", "0000000003", "Big Fund LP", "TenPercentOwner", ""]]))
        z.writestr("NONDERIV_TRANS.tsv", tsv(
            ["ACCESSION_NUMBER", "TRANS_DATE", "TRANS_CODE", "TRANS_SHARES", "TRANS_PRICEPERSHARE",
             "TRANS_ACQUIRED_DISP_CD", "DIRECT_INDIRECT_OWNERSHIP"],
            [["a1", "02-JAN-2015", "P", "1,000", "110.5", "A", "D"],
             ["a1", "02-JAN-2015", "S", "500", "111", "D", "D"],      # sale: dropped
             ["a1", "02-JAN-2015", "M", "100", "10", "A", "D"],       # option exercise: dropped
             ["a2", "02-JAN-2015", "P", "1000", "110.5", "A", "D"],   # amendment: kept raw, not qualifying
             ["a3", "05-JAN-2015", "P", "50", "78", "A", "D"],        # $3,900: below threshold
             ["a4", "05-JAN-2015", "P", "100000", "5", "A", "I"]]))   # 10% owner only
    return buf.getvalue()


def _self_test() -> list[str]:
    fails = []
    rows = parse_quarter(_synthetic_zip())
    if [r["accession"] for r in rows] != ["a1", "a2", "a3", "a4"]:
        fails.append(f"parse kept {[r['accession'] for r in rows]}")
    a1 = rows[0]
    if (a1["filing_date"], a1["value"], a1["issuer_cik"], a1["relationship"]) != ("2015-01-03", 110500.0, "320193",
                                                                                   "Director;Officer"):
        fails.append(f"a1 parsed as {a1}")
    q = qualifying(rows)
    if [f["ticker"] for f in q] != ["AAPL"]:
        fails.append(f"qualifying -> {q}")
    # FB filing mapped onto META's data through the rename table; a stale "FB" after
    # the rename through the CIK; GenOn's "GEN" (2011) NOT onto Symantec's data.
    names = {"FB": [["2013-12-23", "2022-06-09"]], "META": [["2022-06-09", "2030-01-01"]],
             "SYMC": [["2003-03-31", "2019-11-05"]], "NLOK": [["2019-11-05", "2022-11-08"]],
             "GEN": [["2022-11-08", "2030-01-01"]]}
    mem = {"META": [["2013-12-23", "2022-06-09"], ["2022-06-09", "2030-01-01"]],
           "GEN": [["2003-03-31", "2019-11-05"], ["2019-11-05", "2022-11-08"], ["2022-11-08", "2030-01-01"]]}
    ren = {"FB": "META", "SYMC": "GEN", "NLOK": "GEN"}
    fil = [{"filing_date": "2015-01-06", "issuer_cik": "1", "ticker": "FB", "owner": "o", "value": 2e4},
           {"filing_date": "2022-07-01", "issuer_cik": "1", "ticker": "META", "owner": "o", "value": 2e4},
           {"filing_date": "2022-08-01", "issuer_cik": "1", "ticker": "FB", "owner": "o", "value": 2e4},
           {"filing_date": "2011-03-01", "issuer_cik": "3", "ticker": "SYMC", "owner": "o", "value": 2e4},
           {"filing_date": "2011-03-01", "issuer_cik": "4", "ticker": "GEN", "owner": "o", "value": 2e4},
           {"filing_date": "2011-03-01", "issuer_cik": "5", "ticker": "XYZ", "owner": "o", "value": 2e4}]
    ev, st = map_to_universe(fil, mem, ren, names)
    if ({k: len(v) for k, v in ev.items()} != {"META": 3, "GEN": 1}
            or st["unmapped_tickers"] != {"GEN": 1, "XYZ": 1}):
        fails.append(f"mapping -> {ev} {st}")
    if norm_ticker(" brk.b ") != "BRK-B" or norm_ticker("GOOG, GOOGL") != "GOOG" or norm_ticker("") != "":
        fails.append("norm_ticker")
    # Flags: filing on a Saturday acts on Monday; cluster needs 3 owners in 30 days.
    dates = ["2015-01-02", "2015-01-05", "2015-01-06", "2015-01-07", "2015-02-20"]
    evs = [("2015-01-03", "x", 2e4), ("2015-01-06", "y", 3e4), ("2015-01-07", "z", 1e4),
           ("2015-02-20", "z", 1e4)]
    fl = insider_flags(dates, evs)
    want = [(False, False, 0.0), (True, False, 2e4), (True, False, 5e4), (True, True, 6e4), (True, False, 1e4)]
    if fl != want:
        fails.append(f"flags {fl} != {want}")
    if quarters("2025q3", "2026q2") != ["2025q3", "2025q4", "2026q1", "2026q2"]:
        fails.append("quarters")
    return fails


def main() -> int:
    ap = argparse.ArgumentParser(description="SEC Form 4 open-market purchases. No arguments: self-test.")
    ap.add_argument("--fetch", action="store_true", help="Download quarters into --out.")
    ap.add_argument("--from", dest="lo", default="2006q1")
    ap.add_argument("--to", dest="hi", default=f"{date.today().year}q4")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--pause", type=float, default=1.0, help="Seconds between downloads (SEC allows 10 req/s).")
    a = ap.parse_args()
    if not a.fetch:
        fails = _self_test()
        for x in fails:
            print("FAIL", x, file=sys.stderr)
        print("self-test OK" if not fails else f"{len(fails)} failure(s)", file=sys.stderr)
        return 1 if fails else 0
    return fetch(a.lo, a.hi, a.out, a.pause)


if __name__ == "__main__":
    raise SystemExit(main())
