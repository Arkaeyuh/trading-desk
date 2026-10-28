#!/usr/bin/env python3
"""
distribution_gate.py
====================
Tests IBD distribution days as a market-exposure gate: hold SPY, and go to
cash while the distribution-day count says HIGH risk or worse.

Rules (claude-trading-skills ibd-distribution-day-monitor defaults, fixed
before testing):
  - distribution day: close <= 0.2% below the prior close, on higher volume
  - it stays active for 25 sessions, unless a later session's high reaches
    5% above its close (invalidated)
  - dN = active distribution days at most N sessions old
  - HIGH or worse: d25 >= 5 or d15 >= 3 or d5 >= 2 -> cash, else SPY

Decided on day t's close, applied from t+1's open: the gate earns SPY's
open-to-open return (dividend-adjusted via adjclose/close) while in, and
nothing while out (cash yields 0, a conservative choice). --cost-bps per
side on every switch.

Null model: the same in/out mask circularly shifted to random offsets. That
keeps how often and how long the gate is out, and moves only when. If the
timing carries information, the gate's Sharpe sits in the right tail.

stdlib only. Python 3.9+.
"""
from __future__ import annotations
import argparse
import json
import math
import os
import random
import sys
from statistics import mean, pstdev

HERE = os.path.dirname(os.path.abspath(__file__))


def distribution_days(bars: list[dict]) -> list[bool]:
    return [i > 0 and bars[i]["close"] <= bars[i - 1]["close"] * (1 - 0.002) - 1e-12
            and bars[i]["volume"] is not None and bars[i - 1]["volume"] is not None
            and bars[i]["volume"] > bars[i - 1]["volume"] for i in range(len(bars))]


def risk_off(bars: list[dict], expire: int = 25, gain: float = 0.05) -> list[bool]:
    dd = distribution_days(bars)
    out = []
    for t in range(len(bars)):
        ages = []
        for j in range(max(0, t - expire), t + 1):
            if not dd[j]:
                continue
            # invalidated by a session AFTER the DD (up to t) whose high is 5% above the DD close
            if any(bars[k]["high"] >= bars[j]["close"] * (1 + gain) for k in range(j + 1, t + 1)):
                continue
            ages.append(t - j)
        d5, d15, d25 = (sum(a <= n for a in ages) for n in (5, 15, 25))
        out.append(d25 >= 5 or d15 >= 3 or d5 >= 2)
    return out


def returns(bars: list[dict]) -> list[float]:
    """r[t] = dividend-adjusted open-to-open return from t+1 to t+2: what a
    decision at t's close earns."""
    adj = [b["open"] * b["adjclose"] / b["close"] for b in bars]
    return [adj[t + 2] / adj[t + 1] - 1 if t + 2 < len(bars) else 0.0 for t in range(len(bars))]


def run(mask_in: list[bool], r: list[float], cost: float) -> list[float]:
    out, prev = [], mask_in[0]
    for t, inn in enumerate(mask_in):
        x = r[t] if inn else 0.0
        if inn != prev:
            x -= cost
        out.append(x)
        prev = inn
    return out


def stats(daily: list[float]) -> dict:
    eq, peak, dd = 1.0, 1.0, 0.0
    for x in daily:
        eq *= 1 + x
        peak = max(peak, eq)
        dd = min(dd, eq / peak - 1)
    yrs = len(daily) / 252
    sd = pstdev(daily)
    return {"cagr": eq ** (1 / yrs) - 1, "sharpe": mean(daily) / sd * math.sqrt(252) if sd else 0.0, "maxdd": dd}


def main() -> int:
    ap = argparse.ArgumentParser(description="IBD distribution days as an SPY exposure gate.")
    ap.add_argument("--data", default=os.path.join(HERE, "data_pit"))
    ap.add_argument("--symbol", default="SPY")
    ap.add_argument("--start", default="2008-01-01")
    ap.add_argument("--end", default="2016-12-31")
    ap.add_argument("--cost-bps", type=float, default=2)
    ap.add_argument("--sims", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()

    with open(os.path.join(a.data, f"{a.symbol}.json")) as f:
        bars = json.load(f)["bars"]
    if any(b.get("volume") is None for b in bars):
        sys.exit(f"{a.symbol}: bars without volume; run fetch_volume.py first")
    off = risk_off(bars)
    r = returns(bars)
    idx = [t for t, b in enumerate(bars) if a.start <= b["date"] <= a.end and t + 2 < len(bars)]
    mask = [not off[t] for t in idx]
    rr = [r[t] for t in idx]
    cost = a.cost_bps / 1e4

    gate = stats(run(mask, rr, cost))
    hold = stats(rr)
    expo = sum(mask) / len(mask)
    same = stats([expo * x for x in rr])
    switches = sum(mask[i] != mask[i - 1] for i in range(1, len(mask)))
    rng = random.Random(a.seed)
    null = []
    for _ in range(a.sims):
        k = rng.randrange(1, len(mask))
        null.append(stats(run(mask[k:] + mask[:k], rr, cost))["sharpe"])
    null.sort()
    pct = sum(x < gate["sharpe"] for x in null) / len(null)
    # Forward 20-session SPY return after risk-off vs risk-on days (overlapping windows)
    fwd = {True: [], False: []}
    for t in idx:
        if t + 21 < len(bars):
            fwd[off[t]].append(math.prod(1 + r[k] for k in range(t, t + 20)) - 1)

    print(f"{a.symbol} {bars[idx[0]]['date']} → {bars[idx[-1]]['date']} · cost {a.cost_bps:g}bp/side · {a.sims} shifted masks")
    print(f"  gate         CAGR {gate['cagr']:+.2%}  Sharpe {gate['sharpe']:.2f}  maxDD {gate['maxdd']:+.1%}  "
          f"in market {expo:.0%}  switches {switches}")
    print(f"  buy & hold   CAGR {hold['cagr']:+.2%}  Sharpe {hold['sharpe']:.2f}  maxDD {hold['maxdd']:+.1%}")
    print(f"  same exposure CAGR {same['cagr']:+.2%}  Sharpe {same['sharpe']:.2f}  maxDD {same['maxdd']:+.1%}")
    print(f"  vs shifted masks: Sharpe percentile {pct:.0%}  (null median {null[len(null) // 2]:.2f}, "
          f"5–95% {null[int(0.05 * len(null))]:.2f}..{null[int(0.95 * len(null)) - 1]:.2f})")
    print(f"  fwd 20d SPY after risk-off {mean(fwd[True]):+.2%} (n={len(fwd[True])}) · "
          f"after risk-on {mean(fwd[False]):+.2%} (n={len(fwd[False])})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
