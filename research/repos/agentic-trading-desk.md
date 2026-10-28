# Oft3r/agentic-trading-desk

Reviewed 2026-10-27 at commit `2cdfe07`. Claude-as-analyst trading skill for Robinhood MCP: stdlib indicator engine (EMA/RSI/MACD/TRIX/Bollinger), three-pillar scorecard, exit-on-exhaustion / re-enter-on-rebound cascade, and an order-guard hook enforcing an autonomous-execution mandate.

## Kept
- `engine/`: the deterministic indicator, scoring and macro-pillar code, plus the Robinhood input cleaner (`prepare_inputs.py`). The indicator math passes known-answer tests and is a good, testable base.
- Design ideas worth keeping: code computes, the model never estimates numbers; a hook outside the model enforces order rules and fails closed; the guard is probed both ways at session start.

## What we found (details and tables in `backtest/README.md`)
- **The decision cascade has no edge.** Its entries were worse than random entries into the same names, held for the same time: 1st percentile on 30 names, 0th–1st on a point-in-time S&P 500 universe with delisted names. Its EXIT signals were followed by above-average returns, so it sells into mean reversion.
- **Stricter exits were rejected** out of sample. Tuning exits cannot help when the entries carry no timing information.
- **Momentum-continuation entries lose to random; mean-reversion entries beat it.** RSI14<30 with an EMA20 / 10-day exit kept a small edge on 2017–2026 point-in-time (99th percentile, about +0.25% a trade over random), but it was not significant on 2008–2016. It loses to SPY at the same exposure, and break-even is about 20 bp of cost per side.
- **Survivorship bias was large** for dip-buying: on 2008–2016 the mean trade fell from +1.17% to +0.31%, and max drawdown went from −9% to −29%.

## Not carried over
- The `SKILL.md` operations manual and the Robinhood mandate/hook: specific to their account setup. Our edits to their docs are saved in `agentic-trading-desk.upstream-doc-edits.diff`.
- The `gemini-spark/` copy.
