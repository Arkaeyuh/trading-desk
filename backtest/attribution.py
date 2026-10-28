#!/usr/bin/env python3
"""
attribution.py
==============
Residual-edge attribution of a backtest run: is the strategy's return
anything more than baseline exposure? Turns <run>/equity.csv into the input
of analysis/residual_edge.py, then runs it.

Baselines are fixed here, before looking at any result:
  primary      SPY                (total return, adjclose)
  sensitivity  RSP                (equal-weight S&P 500)
               SPY + IWM          (market + size)
Regime label: SPY close above / below its 200-day SMA as of the PRIOR close,
so the label never uses the day it describes.

The strategy's idle cash earns 0% in backtest.py, and the baselines are
charged nothing, so the regression intercept is a conservative alpha.

  python3 backtest/attribution.py backtest/results/pit_holdout/E3_oversold [--scope out_of_sample --universe point_in_time]

Writes attribution.csv / attribution_config.json / residual_edge.{json,md}
into the run directory and prints the verdict. stdlib only.
"""
from __future__ import annotations
import argparse
import csv
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ANALYZER = os.path.join(HERE, "..", "analysis", "residual_edge.py")


def _bars(data_dir: str, sym: str) -> dict[str, dict]:
    with open(os.path.join(data_dir, f"{sym}.json")) as f:
        return {b["date"]: b for b in json.load(f)["bars"]}


def build(run_dir: str, data_dir: str, scope: str, universe: str, rolling_window: int) -> tuple[str, str]:
    with open(os.path.join(run_dir, "equity.csv")) as f:
        eq = [(r["date"], float(r["equity"])) for r in csv.DictReader(f)]
    px = {s: _bars(data_dir, s) for s in ("SPY", "RSP", "IWM")}
    spy_dates = sorted(px["SPY"])
    spy_close = [px["SPY"][d]["close"] for d in spy_dates]
    regime = {}
    for i, d in enumerate(spy_dates):
        if i >= 200:
            sma = sum(spy_close[i - 200:i]) / 200  # SMA200 as of yesterday's close
            regime[d] = "spy_above_sma200" if spy_close[i - 1] > sma else "spy_below_sma200"

    rows = []
    for (d0, e0), (d1, e1) in zip(eq, eq[1:]):
        if d1 not in regime or any(d0 not in px[s] or d1 not in px[s] for s in px):
            continue
        r = {"date": d1, "strategy_return": e1 / e0 - 1}
        for s in px:
            r[f"{s.lower()}_return"] = px[s][d1]["adjclose"] / px[s][d0]["adjclose"] - 1
        r["trend_regime"] = regime[d1]
        rows.append(r)

    csv_path = os.path.join(run_dir, "attribution.csv")
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    cfg = {
        "schema_version": "1.0", "date_column": "date", "strategy_column": "strategy_return",
        "return_unit": "decimal", "frequency": "daily",
        "primary_model": {"name": "market", "baseline_columns": ["spy_return"]},
        "sensitivity_models": [
            {"name": "equal_weight", "baseline_columns": ["rsp_return"]},
            {"name": "market_plus_size", "baseline_columns": ["spy_return", "iwm_return"]},
        ],
        "regime_columns": ["trend_regime"],
        "rolling_window": rolling_window, "minimum_observations": 252,
        "minimum_regime_observations": 60, "minimum_rolling_windows": 12,
        "hac_lags": "auto", "include_series": False,
        "data_declarations": {
            "baseline_selection": "predeclared",
            # strategy is net of modelled slippage; ETF total returns are net of fees
            "strategy_return_basis": "net", "baseline_return_basis": "net",
            "analysis_scope": scope, "universe_data": universe,
        },
    }
    cfg_path = os.path.join(run_dir, "attribution_config.json")
    with open(cfg_path, "w") as f:
        json.dump(cfg, f, indent=1)
    return csv_path, cfg_path


def main() -> int:
    ap = argparse.ArgumentParser(description="Residual-edge attribution of a backtest run vs SPY/RSP/IWM.")
    ap.add_argument("run_dir")
    ap.add_argument("--data", default=os.path.join(HERE, "data"), help="Where SPY/RSP/IWM bars are cached.")
    ap.add_argument("--scope", default="out_of_sample", choices=("out_of_sample", "in_sample", "live"))
    ap.add_argument("--universe", default="point_in_time", choices=("point_in_time", "current_constituents"))
    ap.add_argument("--rolling-window", type=int, default=252)
    a = ap.parse_args()

    csv_path, cfg_path = build(a.run_dir, a.data, a.scope, a.universe, a.rolling_window)
    out_json = os.path.join(a.run_dir, "residual_edge.json")
    rc = subprocess.call([sys.executable, ANALYZER, "--input", csv_path, "--config", cfg_path,
                          "--output-json", out_json,
                          "--output-markdown", os.path.join(a.run_dir, "residual_edge.md")])
    if rc != 0:
        return rc
    with open(out_json) as f:
        rep = json.load(f)
    print(json.dumps(rep["verdict"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
