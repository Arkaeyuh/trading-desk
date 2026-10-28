#!/usr/bin/env python3
"""
backtest.py
===========
Walk-forward backtest of the deterministic layer of the desk: macro_pillar.py
+ score.py's decision cascade, executed under the Autonomous Execution
Mandate's sizing rules. It imports engine/ unchanged — what is
tested is exactly what runs in a session.

No lookahead, by construction:
  - every decision on day t sees only bars up to and including t's close,
    in a trailing window of --window bars (default 290 = what the live desk
    fetches from get_equity_historicals, so EMA200 is seeded the same way);
  - every fill happens at the OPEN of t+1, with --slippage-bps against us;
  - the macro pillar for day t uses ETF closes up to t (and, if supplied, the
    10Y-2Y spread up to t-1, since FRED publishes with a lag).

Mandate rules applied: base size = --base-pct of equity capped at --max-order;
TACTICAL REBOUND at half size; RE-ENTRY at half size when macro <= -1; tactical
at a quarter (or skipped with --skip-tactical-adverse) when macro <= -1; max
--max-new new positions per day ranked by pillar total; --cash-reserve of
equity never spent; no averaging down (no buys while holding); no new buys the
day after equity fell more than 4%. Long-only, fractional shares.

Manual stops (on by default, --no-stops disables): the skill tells the agent to
keep a stop level "below the rebound pivot, re-checked each session" and to
take tactical trades off at a close target. Modelled as:
  - pivot stop  : exit if close < min(low of the 5 bars up to entry)
  - tactical    : exit at close >= EMA50, or after --tactical-max-days sessions

NOT modelled (cannot be, honestly): discretionary trades, news, the earnings
blackout, the Codex review. See backtest/README.md.

Outputs (in --out): report.txt, summary.json, trades.csv, equity.csv,
events.csv. stdlib only. Python 3.9+.
"""
from __future__ import annotations
import argparse
import csv
import json
import math
import os
import random
import sys
from concurrent.futures import ProcessPoolExecutor
from statistics import mean, median, pstdev

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "engine"))
import indicators as I  # noqa: E402
import macro_pillar as M  # noqa: E402
import score as S  # noqa: E402

MACRO_ETFS = ["SPY", "RSP", "IWM", "HYG", "LQD", "TLT", "XLY", "XLP"]
ENTRY_ACTIONS = ("RE-ENTRY (new cycle)", "TACTICAL REBOUND (counter-trend)")
EXIT_ACTIONS = ("EXIT / TRIM", "EXIT")
HORIZONS = (5, 10, 20)

# Alternative entry signals (--entry), evaluated on the same indicator stack.
# Thresholds are textbook values fixed before testing, not fitted.
ENTRY_SIGNALS = {
    "trend_pullback": lambda r: r["ema200"] is not None and r["close"] > r["ema200"] and r["rsi14"] < 35,
    "oversold": lambda r: r["rsi14"] is not None and r["rsi14"] < 30,
    "band_pullback": lambda r: (r["ema200"] is not None and r["close"] > r["ema200"]
                                and r["percent_b"] is not None and r["percent_b"] < 0),
    "breakout": lambda r: r["high252"],
}


# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------

def load_bars(data_dir: str, sym: str) -> list[dict]:
    with open(os.path.join(data_dir, f"{sym}.json")) as f:
        return json.load(f)["bars"]


def load_spread(path: str) -> dict[str, float]:
    """CSV with columns date,value (e.g. FRED T10Y2Y export). Blank/'.' rows skipped."""
    out = {}
    with open(path) as f:
        for row in csv.reader(f):
            if len(row) < 2:
                continue
            try:
                out[row[0][:10]] = float(row[1])
            except ValueError:
                continue
    return out


def macro_series(data_dir: str, window: int, spread: dict[str, float] | None,
                 spread_obs: int = 60) -> tuple[list[str], dict[str, int]]:
    """Macro pillar for every date on SPY's calendar. Returns (calendar, {date: pillar})."""
    raw = {s: {b["date"]: b["close"] for b in load_bars(data_dir, s)} for s in MACRO_ETFS}
    cal = sorted(raw["SPY"])
    spread_dates = sorted(spread) if spread else []
    pillar: dict[str, int] = {}
    si = 0
    for i, d in enumerate(cal):
        if i + 1 < window:
            continue
        win = cal[i + 1 - window: i + 1]
        series = {}
        for s in MACRO_ETFS:
            tail = [raw[s][x] for x in win if x in raw[s]]
            if len(tail) >= 2:
                series[s] = tail
        data = {"as_of": d, "series": series}
        if spread:
            while si < len(spread_dates) and spread_dates[si] < d:  # strictly before d
                si += 1
            hist = [spread[x] for x in spread_dates[max(0, si - spread_obs):si]]
            if hist:
                data["yield_spread"] = hist
        try:
            pillar[d] = M.score_macro(data).pillar_score
        except ValueError:
            continue
    return cal, pillar


def _signals_for(args: tuple) -> tuple[str, dict]:
    """Per-ticker precompute: decision both flat and holding, for every day."""
    data_dir, sym, window, min_bars, pillar, spells = args
    bars = load_bars(data_dir, sym)
    close = [b["close"] for b in bars]
    low = [b["low"] for b in bars]
    rows = []
    for i in range(len(bars)):
        d = bars[i]["date"]
        if i + 1 < min_bars or d not in pillar:
            rows.append(None)
            continue
        c = close[max(0, i + 1 - window): i + 1]
        ind = I.compute(c)
        t, _ = S.score_trend(ind)
        m, _ = S.score_momentum(ind)
        mac = pillar[d]
        flat = S.decide(ind, t, m, mac, False)
        hold = S.decide(ind, t, m, mac, True)
        rows.append({
            "flat": flat["action"], "hold": hold["action"], "macro": mac,
            "total": t + m + mac, "ema50": ind["ema50"],
            "pivot": min(low[max(0, i - 4): i + 1]),
            "close": close[i], "ema20": ind["ema20"], "ema200": ind["ema200"],
            "rsi14": ind["rsi14"], "percent_b": ind["percent_b"],
            "high252": i >= 251 and close[i] >= max(close[i - 251: i + 1]),
            # Point-in-time universe: may only be BOUGHT while an index member.
            "member": spells is None or any(lo <= d <= hi for lo, hi in spells),
        })
    return sym, {"bars": bars, "sig": rows}


# --------------------------------------------------------------------------
# Portfolio simulation
# --------------------------------------------------------------------------

class Position:
    __slots__ = ("sym", "kind", "shares", "orig_shares", "cost", "entry_date", "entry_idx",
                 "entry_px", "proceeds", "stop", "days", "streak")

    def __init__(self, sym, kind, shares, cost, date, idx, px, stop):
        self.sym, self.kind, self.shares, self.orig_shares = sym, kind, shares, shares
        self.cost, self.entry_date, self.entry_idx, self.entry_px = cost, date, idx, px
        self.proceeds, self.stop, self.days = 0.0, stop, 0
        self.streak = {a: 0 for a in EXIT_ACTIONS}  # consecutive sessions each exit has fired


def _entry(sg: dict, entry: str) -> bool:
    if entry == "framework":
        return sg["flat"] in ENTRY_ACTIONS
    if entry == "tactical":
        return sg["flat"] == "TACTICAL REBOUND (counter-trend)"
    return bool(ENTRY_SIGNALS[entry](sg))


def simulate(cal: list[str], tick: dict, a) -> dict:
    slip = a.slippage_bps / 1e4
    # date -> bar index, per ticker
    idx = {s: {b["date"]: i for i, b in enumerate(v["bars"])} for s, v in tick.items()}
    start = next(i for i, d in enumerate(cal) if d >= a.start)
    end = max(i for i, d in enumerate(cal) if d <= a.end)

    cash, pos, trades, curve = a.capital, {}, [], []
    last_px: dict[str, float] = {}
    prev_eq = a.capital
    for t in range(start, end):
        d, nd = cal[t], cal[t + 1]
        for s in tick:
            j = idx[s].get(d)
            if j is not None:
                last_px[s] = tick[s]["bars"][j]["close"]
        invested = sum(p.shares * last_px[p.sym] for p in pos.values())
        eq = cash + invested
        curve.append((d, eq, invested / eq if eq else 0.0))
        breaker = eq / prev_eq - 1 < -0.04
        prev_eq = eq

        # Data ends = delisted or acquired: close at the last print. With a
        # point-in-time universe this is where bankruptcies hit the book.
        for s, p in list(pos.items()):
            last = tick[s]["bars"][-1]
            if last["date"] <= d:
                px = last["close"] * (1 - slip)
                cash += p.shares * px
                p.proceeds += p.shares * px
                del pos[s]
                trades.append({"symbol": s, "kind": p.kind, "entry_date": p.entry_date,
                               "exit_date": last["date"], "entry_px": round(p.entry_px, 4),
                               "exit_px": round(px, 4), "cost": round(p.cost, 2),
                               "pnl": round(p.proceeds - p.cost, 2), "ret": p.proceeds / p.cost - 1,
                               "hold_days": len(tick[s]["bars"]) - 1 - p.entry_idx,
                               # still a member at the last bar = the listing ended (bought
                               # out, bankrupt); otherwise only our download stopped
                               "exit_reason": "delisted" if (tick[s]["sig"][-1] or {}).get("member")
                               else "left index (data end)"})

        # Decisions on d's close, fills at nd's open.
        sells, buys = [], []
        for s, v in tick.items():
            j, k = idx[s].get(d), idx[s].get(nd)
            if j is None or k is None or v["sig"][j] is None:
                continue
            sg, bar = v["sig"][j], v["bars"][j]
            p = pos.get(s)
            if p:
                p.days += 1
                reason = None
                for ea in EXIT_ACTIONS:
                    p.streak[ea] = p.streak[ea] + 1 if sg["hold"] == ea else 0
                act = sg["hold"]
                if a.exit_mode == "revert":
                    if sg["ema20"] is not None and bar["close"] >= sg["ema20"]:
                        reason = "revert target"
                    elif p.days >= a.revert_max_days:
                        reason = "revert time stop"
                elif act == "EXIT" and p.streak[act] >= a.exit_confirm and not (
                        a.exit_below_ema50 and sg["ema50"] is not None and bar["close"] >= sg["ema50"]):
                    reason = act
                elif act == "EXIT / TRIM" and not a.ignore_trim and p.streak[act] >= a.trim_confirm:
                    reason = act
                elif a.exit_mode == "framework" and not a.no_stops and bar["close"] < p.stop:
                    reason = "pivot stop"
                elif a.exit_mode == "framework" and not a.no_stops and p.kind == "tactical":
                    if sg["ema50"] is not None and bar["close"] >= sg["ema50"]:
                        reason = "tactical target"
                    elif p.days >= a.tactical_max_days:
                        reason = "tactical time stop"
                if reason:
                    frac = a.trim_frac if reason == "EXIT / TRIM" else 1.0
                    sells.append((s, k, reason, frac))
            elif not breaker and sg["member"] and _entry(sg, a.entry):
                if a.entry in ENTRY_SIGNALS:
                    kind, mult = a.entry, 1.0
                else:
                    kind = "cycle" if sg["flat"].startswith("RE-ENTRY") else "tactical"
                    mult = 1.0 if kind == "cycle" else 0.5
                if sg["macro"] <= -1:
                    if kind == "tactical" and a.skip_tactical_adverse:
                        continue
                    mult *= 0.5
                buys.append((sg["total"], s, k, kind, mult, sg["pivot"]))

        for s, k, reason, frac in sells:
            p = pos[s]
            px = tick[s]["bars"][k]["open"] * (1 - slip)
            q = p.shares * frac
            if p.shares - q < 0.1 * p.orig_shares:
                q = p.shares
            cash += q * px
            p.proceeds += q * px
            p.shares -= q
            if p.shares <= 1e-12:
                del pos[s]
                trades.append({"symbol": s, "kind": p.kind, "entry_date": p.entry_date,
                               "exit_date": nd, "entry_px": round(p.entry_px, 4),
                               "exit_px": round(px, 4), "cost": round(p.cost, 2),
                               "pnl": round(p.proceeds - p.cost, 2),
                               "ret": p.proceeds / p.cost - 1,
                               "hold_days": k - p.entry_idx, "exit_reason": reason})

        eq_open = cash
        for p in pos.values():
            k = idx[p.sym].get(nd)
            eq_open += p.shares * (tick[p.sym]["bars"][k]["open"] if k is not None else last_px[p.sym])
        buys.sort(key=lambda b: (-b[0], b[1]))
        for total, s, k, kind, mult, pivot in buys[: a.max_new]:
            size = min(a.base_pct * eq_open, a.max_order) * mult
            if cash - size < a.cash_reserve * eq_open or size < 1:
                continue
            px = tick[s]["bars"][k]["open"] * (1 + slip)
            cash -= size
            pos[s] = Position(s, kind, size / px, size, nd, k, px, pivot)

    # Liquidate at the end so every trade is counted.
    d = cal[end]
    for s, p in list(pos.items()):
        j = max(i for dd, i in idx[s].items() if dd <= d)
        px = tick[s]["bars"][j]["close"] * (1 - slip)
        cash += p.shares * px
        p.proceeds += p.shares * px
        trades.append({"symbol": s, "kind": p.kind, "entry_date": p.entry_date, "exit_date": d,
                       "entry_px": round(p.entry_px, 4), "exit_px": round(px, 4),
                       "cost": round(p.cost, 2), "pnl": round(p.proceeds - p.cost, 2),
                       "ret": p.proceeds / p.cost - 1, "hold_days": j - p.entry_idx,
                       "exit_reason": "end of test"})
    curve.append((d, cash, 0.0))
    return {"curve": curve, "trades": trades, "start": cal[start], "end": d}


# --------------------------------------------------------------------------
# Statistics
# --------------------------------------------------------------------------

def curve_stats(curve: list[tuple]) -> dict:
    eqs = [e for _, e, _ in curve]
    rets = [eqs[i] / eqs[i - 1] - 1 for i in range(1, len(eqs))]
    years = max(len(rets) / 252, 1e-9)
    peak, mdd = eqs[0], 0.0
    for e in eqs:
        peak = max(peak, e)
        mdd = min(mdd, e / peak - 1)
    vol = pstdev(rets) * math.sqrt(252) if len(rets) > 1 else 0.0
    cagr = (eqs[-1] / eqs[0]) ** (1 / years) - 1
    return {"total_return": eqs[-1] / eqs[0] - 1, "cagr": cagr, "ann_vol": vol,
            "sharpe": (mean(rets) * 252) / vol if vol else None,
            "max_drawdown": mdd, "avg_exposure": mean(x for _, _, x in curve)}


def trade_stats(trades: list[dict]) -> dict:
    if not trades:
        return {"n": 0}
    r = [x["ret"] for x in trades]
    wins = [x for x in r if x > 0]
    losses = [x for x in r if x <= 0]
    gp = sum(x["pnl"] for x in trades if x["pnl"] > 0)
    gl = -sum(x["pnl"] for x in trades if x["pnl"] <= 0)
    return {"n": len(r), "win_rate": len(wins) / len(r), "mean_ret": mean(r), "median_ret": median(r),
            "avg_win": mean(wins) if wins else 0.0, "avg_loss": mean(losses) if losses else 0.0,
            "profit_factor": gp / gl if gl else None,
            "avg_hold_days": mean(x["hold_days"] for x in trades)}


def benchmark(data_dir: str, sym: str, start: str, end: str, exposure: float) -> dict:
    """Buy-and-hold on adjclose, plus a daily-rebalanced exposure-matched version
    (exposure x benchmark, rest in 0% cash): the fair comparison for a book that
    is mostly cash."""
    bars = [b for b in load_bars(data_dir, sym) if start <= b["date"] <= end]
    px = [b["adjclose"] for b in bars]
    full = [(b["date"], p, 1.0) for b, p in zip(bars, px)]
    eq, matched = 1.0, [(bars[0]["date"], 1.0, exposure)]
    for i in range(1, len(px)):
        eq *= 1 + exposure * (px[i] / px[i - 1] - 1)
        matched.append((bars[i]["date"], eq, exposure))
    return {"buy_hold": curve_stats(full), "exposure_matched": curve_stats(matched)}


def random_baseline(tick: dict, trades: list[dict], a, sims: int) -> dict:
    """Null model: same tickers, same holding periods, random entry days.
    Answers 'do the entry signals beat entering at random?'"""
    rng = random.Random(a.seed)
    slip = a.slippage_bps / 1e4
    windows = {}
    for s, v in tick.items():
        windows[s] = [i for i, b in enumerate(v["bars"]) if a.start <= b["date"] <= a.end
                      and v["sig"][i] is not None and v["sig"][i]["member"]]
    real = mean(x["ret"] for x in trades)
    means = []
    for _ in range(sims):
        rs = []
        for x in trades:
            w, bars = windows[x["symbol"]], tick[x["symbol"]]["bars"]
            h = max(1, x["hold_days"])
            if not w:
                continue
            i = w[rng.randrange(len(w))]
            # A random holding that runs past the end of the data is closed at the
            # last print, exactly as the strategy's would be (delisting).
            out = bars[i + h]["open"] if i + h < len(bars) else bars[-1]["close"]
            rs.append(out * (1 - slip) / (bars[i]["open"] * (1 + slip)) - 1)
        means.append(mean(rs))
    means.sort()
    pct = sum(m < real for m in means) / len(means)
    return {"strategy_mean_ret": real, "random_mean_ret": mean(means),
            "random_p05": means[int(0.05 * sims)], "random_p95": means[int(0.95 * sims) - 1],
            "percentile": pct, "p_value_one_sided": 1 - pct, "sims": sims}


def event_study(tick: dict, a) -> list[dict]:
    """Forward open-to-open returns after each action label, minus that ticker's
    unconditional forward return over the test window (controls for the
    universe's drift). Windows overlap, so treat counts as optimistic."""
    acc: dict[tuple, dict[int, list[float]]] = {}
    for s, v in tick.items():
        bars, sig = v["bars"], v["sig"]
        valid = [i for i in range(len(bars) - max(HORIZONS) - 1)
                 if sig[i] is not None and sig[i]["member"] and a.start <= bars[i]["date"] <= a.end]
        if not valid:
            continue
        fwd = {h: {i: bars[i + 1 + h]["open"] / bars[i + 1]["open"] - 1 for i in valid} for h in HORIZONS}
        base = {h: mean(fwd[h].values()) for h in HORIZONS}
        for i in valid:
            labels = [("flat", sig[i]["flat"]), ("hold", sig[i]["hold"])]
            labels += [("sig", n) for n, f in ENTRY_SIGNALS.items() if f(sig[i])]
            for key in labels:
                bucket = acc.setdefault(key, {h: [] for h in HORIZONS} | {-1: []})
                for h in HORIZONS:
                    bucket[h].append(fwd[h][i] - base[h])
                bucket[-1].append(fwd[10][i])
    out = []
    for (side, label), b in sorted(acc.items()):
        row = {"state": side, "action": label, "n": len(b[10]),
               "hit_rate_10d": sum(x > 0 for x in b[-1]) / len(b[-1])}
        for h in HORIZONS:
            row[f"excess_{h}d"] = mean(b[h])
        out.append(row)
    return out


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------

def _p(x, pct=True, nd=2):
    if x is None:
        return "  n/a"
    return f"{x*100:+.{nd}f}%" if pct else f"{x:.{nd}f}"


def by_year(curve: list[tuple]) -> list[tuple]:
    out, prev_eq = [], curve[0][1]
    for i, (d, e, _) in enumerate(curve):
        y = d[:4]
        nxt = curve[i + 1][0][:4] if i + 1 < len(curve) else None
        if nxt != y:
            out.append((y, e / prev_eq - 1))
            prev_eq = e
    return out


def render(res: dict) -> str:
    L = []
    cs, ts, rb = res["portfolio"], res["trades"], res["random_baseline"]
    L.append("=" * 72)
    L.append(f" BACKTEST  ·  {res['period'][0]} → {res['period'][1]}  ·  {res['n_symbols']} symbols")
    L.append(f" {res['config_line']}")
    L.append("=" * 72)
    L.append(f"  Portfolio   CAGR {_p(cs['cagr'])}  vol {_p(cs['ann_vol'])}  Sharpe {_p(cs['sharpe'], False)}"
             f"  maxDD {_p(cs['max_drawdown'])}  exposure {_p(cs['avg_exposure'], nd=1)}")
    for name, b in res["benchmarks"].items():
        bh, em = b["buy_hold"], b["exposure_matched"]
        L.append(f"  {name} B&H     CAGR {_p(bh['cagr'])}  Sharpe {_p(bh['sharpe'], False)}  maxDD {_p(bh['max_drawdown'])}")
        L.append(f"  {name} @ same exposure  CAGR {_p(em['cagr'])}  Sharpe {_p(em['sharpe'], False)}"
                 f"  maxDD {_p(em['max_drawdown'])}")
    L.append("-" * 72)
    if ts["n"]:
        L.append(f"  Trades {ts['n']}  win {_p(ts['win_rate'], nd=1)}  mean {_p(ts['mean_ret'])}"
                 f"  median {_p(ts['median_ret'])}  PF {_p(ts['profit_factor'], False)}"
                 f"  hold {ts['avg_hold_days']:.1f}d")
        L.append(f"         avg win {_p(ts['avg_win'])}  avg loss {_p(ts['avg_loss'])}")
        for k, v in res["trades_by_kind"].items():
            L.append(f"    {k:<9} n={v['n']:<5} win {_p(v['win_rate'], nd=1)}  mean {_p(v['mean_ret'])}"
                     f"  PF {_p(v.get('profit_factor'), False)}")
        L.append("  Exit reasons: " + ", ".join(f"{k} {v}" for k, v in res["exit_reasons"].items()))
        L.append(f"  vs random entries (same names, same hold lengths, {rb['sims']} sims):")
        L.append(f"    strategy mean {_p(rb['strategy_mean_ret'])}  random mean {_p(rb['random_mean_ret'])}"
                 f"  [5%..95%: {_p(rb['random_p05'])} .. {_p(rb['random_p95'])}]")
        L.append(f"    percentile {rb['percentile']*100:.0f}  ·  one-sided p ≈ {rb['p_value_one_sided']:.3f}")
    L.append("-" * 72)
    L.append("  Sub-periods:")
    for name, st in res["sub_periods"].items():
        L.append(f"    {name:<24} CAGR {_p(st['cagr'])}  Sharpe {_p(st['sharpe'], False)}  maxDD {_p(st['max_drawdown'])}")
    L.append("  Calendar years: " + "  ".join(f"{y} {_p(r, nd=1)}" for y, r in res["years"]))
    L.append("-" * 72)
    L.append("  Signal event study (forward return from next open, minus ticker's unconditional):")
    L.append(f"    {'state':<5} {'action':<34} {'n':>6} {'ex5d':>8} {'ex10d':>8} {'ex20d':>8} {'hit10':>6}")
    for r in res["events"]:
        L.append(f"    {r['state']:<5} {r['action'][:34]:<34} {r['n']:>6} {_p(r['excess_5d']):>8}"
                 f" {_p(r['excess_10d']):>8} {_p(r['excess_20d']):>8} {r['hit_rate_10d']*100:>5.0f}%")
    L.append("    (entry labels: want excess > 0 · EXIT labels on 'hold': want excess < 0)")
    L.append("=" * 72)
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="Walk-forward backtest of score.py + macro_pillar.py under the Mandate.")
    ap.add_argument("symbols", nargs="*", help="Tradable universe. Default: every cached file except the macro-only ETFs.")
    ap.add_argument("--data", default=os.path.join(HERE, "data"))
    ap.add_argument("--membership", help="membership.json from fetch_pit.py: buy a name only while it was an "
                                         "index member, and trade only names listed in it.")
    ap.add_argument("--out", default=os.path.join(HERE, "results"))
    ap.add_argument("--start", default="2008-01-01")
    ap.add_argument("--end", default="9999-12-31")
    ap.add_argument("--split", default="2017-01-01", help="Sub-period boundary for the stability check.")
    ap.add_argument("--window", type=int, default=290, help="Trailing bars per decision (live desk: ~290).")
    ap.add_argument("--min-bars", type=int, default=220)
    ap.add_argument("--capital", type=float, default=25_000)
    ap.add_argument("--base-pct", type=float, default=0.03)
    ap.add_argument("--max-order", type=float, default=1_200)
    ap.add_argument("--max-new", type=int, default=3)
    ap.add_argument("--cash-reserve", type=float, default=0.15)
    ap.add_argument("--slippage-bps", type=float, default=5)
    ap.add_argument("--trim-frac", type=float, default=1.0, help="Fraction sold on EXIT / TRIM (1.0 = full exit).")
    ap.add_argument("--no-stops", action="store_true", help="Pure cascade: no pivot/tactical stops.")
    ap.add_argument("--exit-confirm", type=int, default=1,
                    help="Sessions in a row EXIT (relentless bearish) must fire before selling.")
    ap.add_argument("--exit-below-ema50", action="store_true", help="Only act on EXIT when close < EMA50.")
    ap.add_argument("--trim-confirm", type=int, default=1, help="Same, for EXIT / TRIM (exhaustion).")
    ap.add_argument("--ignore-trim", action="store_true", help="Never sell on EXIT / TRIM.")
    ap.add_argument("--entry", default="framework", choices=("framework", "tactical", *ENTRY_SIGNALS),
                    help="Entry trigger when flat (framework = RE-ENTRY + TACTICAL REBOUND).")
    ap.add_argument("--exit-mode", default="framework", choices=("framework", "revert"),
                    help="revert: exit at close >= EMA20 or after --revert-max-days (mean-reversion trades).")
    ap.add_argument("--revert-max-days", type=int, default=10)
    ap.add_argument("--tactical-max-days", type=int, default=10)
    ap.add_argument("--skip-tactical-adverse", action="store_true")
    ap.add_argument("--spread-csv", help="Optional 10Y-2Y CSV (date,value), e.g. FRED T10Y2Y.")
    ap.add_argument("--sims", type=int, default=500)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    a = ap.parse_args()

    member = None
    if a.membership:
        with open(a.membership) as f:
            member = json.load(f)["membership"]
    cached = {f[:-5] for f in os.listdir(a.data) if f.endswith(".json")} - {"membership", "coverage"}
    syms = a.symbols or sorted((set(member) & cached) if member is not None else cached - set(MACRO_ETFS))
    spread = load_spread(a.spread_csv) if a.spread_csv else None
    print(f"macro pillar over SPY calendar (window {a.window}{', with 10Y-2Y' if spread else ', no 10Y-2Y'})…",
          file=sys.stderr)
    cal, pillar = macro_series(a.data, a.window, spread)
    print(f"scoring {len(syms)} symbols × 2 states on {a.workers} workers…", file=sys.stderr)
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        tick = dict(ex.map(_signals_for, [(a.data, s, a.window, a.min_bars, pillar,
                                           member.get(s) if member is not None else None) for s in syms]))

    sim = simulate(cal, tick, a)
    trades = sim["trades"]
    cs = curve_stats(sim["curve"])
    kinds = {}
    for k in ("cycle", "tactical", *ENTRY_SIGNALS):
        sub = [x for x in trades if x["kind"] == k]
        if sub:
            kinds[k] = trade_stats(sub)
    reasons = {}
    for x in trades:
        reasons[x["exit_reason"]] = reasons.get(x["exit_reason"], 0) + 1
    subs = {}
    for name, lo, hi in ((f"{sim['start']} → {a.split}", sim["start"], a.split),
                         (f"{a.split} → {sim['end']}", a.split, sim["end"])):
        part = [c for c in sim["curve"] if lo <= c[0] <= hi]
        if len(part) > 20:
            subs[name] = curve_stats(part)
    res = {
        "period": (sim["start"], sim["end"]), "n_symbols": len(syms), "symbols": syms,
        "config": vars(a),
        "config_line": (f"capital ${a.capital:,.0f} · base {a.base_pct:.0%} ≤ ${a.max_order:,.0f} · reserve "
                        f"{a.cash_reserve:.0%} · slip {a.slippage_bps:g}bp · trim {a.trim_frac:g} · "
                        f"stops {'off' if a.no_stops else 'on'} · window {a.window}"
                        f" · exit confirm {a.exit_confirm}{' <EMA50' if a.exit_below_ema50 else ''}"
                        f" · trim {'ignored' if a.ignore_trim else f'confirm {a.trim_confirm}'}"
                        f" · entry {a.entry} · exits {a.exit_mode}"),
        "portfolio": cs, "trades": trade_stats(trades), "trades_by_kind": kinds,
        "exit_reasons": dict(sorted(reasons.items(), key=lambda kv: -kv[1])),
        "benchmarks": {s: benchmark(a.data, s, sim["start"], sim["end"], cs["avg_exposure"])
                       for s in ("SPY",)},
        "random_baseline": random_baseline(tick, trades, a, a.sims) if trades else {},
        "sub_periods": subs, "years": by_year(sim["curve"]),
        "events": event_study(tick, a),
    }

    os.makedirs(a.out, exist_ok=True)
    report = render(res)
    with open(os.path.join(a.out, "report.txt"), "w") as f:
        f.write(report + "\n")
    with open(os.path.join(a.out, "summary.json"), "w") as f:
        json.dump(res, f, indent=1, default=str)
    with open(os.path.join(a.out, "trades.csv"), "w", newline="") as f:
        if trades:
            w = csv.DictWriter(f, fieldnames=list(trades[0]))
            w.writeheader()
            w.writerows(trades)
    with open(os.path.join(a.out, "equity.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "equity", "exposure"])
        w.writerows((d, round(e, 2), round(x, 4)) for d, e, x in sim["curve"])
    with open(os.path.join(a.out, "events.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(res["events"][0]))
        w.writeheader()
        w.writerows(res["events"])
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
