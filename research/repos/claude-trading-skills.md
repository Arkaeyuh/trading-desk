# tradermonty/claude-trading-skills

Reviewed 2026-10-27 at commit `c9b8419` (MIT). This is not an agent that trades: it is 74 Claude skills for a "core ETF/dividend + satellite swing trading" process. They cover market-regime checks, screeners, trade planning, journaling and strategy research, with about 310k lines of Python. Many skills need the paid FMP API ($30–80/month) or FINVIZ Elite; a few use free public CSVs or chart screenshots.

**Its own index marks 0 of 74 skills as empirically validated** (54 `not_verified`, the rest not applicable or blank). Unit tests exist for some skills; none claims its signal makes money. So every screener in it is a hypothesis for `backtest/`, not something to adopt as-is.

## Adopted

| What | Where | Why |
|---|---|---|
| `residual-edge-analyzer` (stdlib, read in full before adopting: intercept OLS on standardized factors, Gauss-Jordan solve, Newey-West HAC errors with Bartlett weights, rolling-window stability, VIF, regime breakdown) | `analysis/residual_edge.py` + methodology and input-contract docs | It formalizes the test we were doing by hand ("does it beat SPY at the same exposure?") into alpha with an honest t-stat, robustness checks across alternative baselines, and a predeclared-baseline discipline. `backtest/attribution.py` feeds it any backtest run. |
| "Seek plateaus, not peaks" (from `backtest-expert`) | parameter-plateau grid in `backtest/README.md` | The one robustness test we were missing: a signal that only works at exactly one parameter value is curve-fit. |

## First results with the adopted analyzer (2017–2026, point-in-time)

| Strategy | SPY beta | alpha / yr (HAC t) | status | active return when SPY > / < its 200-day SMA |
|---|---|---|---|---|
| RSI14<30, EMA20 / 10-day exit | 0.42 | −2.41% (−1.01) | RESIDUAL_FRAGILE | −3.5% / **+2.6%** |
| original framework (agentic-trading-desk) | 0.39 | −2.05% (−0.87) | RESIDUAL_FRAGILE | +0.2% / **−12.3%** |

Neither strategy has alpha beyond its market exposure; the alternative baselines (RSP, SPY+IWM) agree. The regime split is new: dip-buying only adds return in downtrends, while the momentum framework is badly hurt by them. It is a **hypothesis** (found on a period we have already looked at many times), to be tested on a fresh split before anything is built on it.

## Plateau test result

RSI threshold (25/30/35) × time stop (5/10/15 days) on 2008–2016: **no plateau**. RSI<30 sits in a valley, from the 13th to the 80th percentile vs random, while 25 and 35 score 88–99. The timing effect is not stable in its parameter, which strengthens the "no tradable edge" verdict. Table in `backtest/README.md`.

## Worth testing next (price/volume rules only, re-implemented on our data, not their FMP code)

1. **Regime-gated RSI<30**: take entries only when SPY is below its 200-day SMA. This comes from the regime split above, so it needs a fresh hold-out.
2. **Stockbee momentum burst**: close ≥ 1.04 × prior close on rising volume, 3–5 day hold. A continuation signal, which has lost to random everywhere so far, so it's a good test of whether that pattern is general.
3. **Stockbee exhaustion hammer**: a selling-climax reversal candle. A mean-reversion signal like RSI<30.
4. **IBD distribution days / follow-through day on SPY/QQQ**: index-level exposure timing, tested as a market gate rather than a stock picker.

Items 2–4 need daily **volume**, which our fetchers drop. Yahoo returns it; adding it is a ~10-minute re-download for 615 names, plus ~2.5 h on Tiingo's free tier for the 120 delisted names.

## Ideas to keep for when there is something to trade

- `pre-trade-discipline-gate`, `drawdown-circuit-breaker`, `trader-memory-core` + `signal-postmortem`: an offline checklist gate, an account-level drawdown stop, and a thesis journal that scores every trade afterwards. That fits with our Codex ledger. Not useful until a strategy passes the backtest.
- `position-sizer`: correct but standard fixed-fraction / ATR / half-Kelly formulas. Kelly sizes every strategy we have tested at 0%.

## Rejected

- **FMP-dependent fundamental screeners** (CANSLIM, VCP, value/dividend, PEAD, earnings, institutional flow, pair trades): paid data that is not point-in-time, so they can't be backtested without lookahead.
- **Chart-screenshot skills** (`technical-analyst`, `sector-analyst`, `breadth-chart-analyst`): an LLM reading a picture of a chart. That is exactly the "estimate numbers by eye" the engine exists to avoid.
- **Narrative synthesizers** (`stanley-druckenmiller-investment`, `scenario-analyzer`, `market-news-analyst`, the `edge-*` pipeline): LLM-generated theses with no falsifiable output to test.
- Options, futures, crypto, MT5, Japanese dividend/tax workflows, and the skill-maintenance meta tooling: out of scope.
