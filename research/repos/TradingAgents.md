# TauricResearch/TradingAgents

Reviewed 2026-10-27 at v0.7.1 (`c454491`, Apache-2.0). Paper: arXiv 2412.20138. A LangGraph multi-agent framework that turns one (ticker, date) into a 5-tier rating (Buy / Overweight / Hold / Underweight / Sell) by imitating a trading firm. It supports about 15 LLM providers; the default is OpenAI GPT-6 (`gpt-6-sol` for deep thinking, `gpt-6-luna` for quick).

## How one decision is made

```
Market · Sentiment · News · Fundamentals analysts   (parallel, each an LLM + tool loop)
      → Bull researcher ⇄ Bear researcher            (debate, max_debate_rounds = 1)
      → Research Manager                             (deep model: investment plan + rating)
      → Trader                                        (proposal)
      → Aggressive ⇄ Neutral ⇄ Conservative risk analysts (max_risk_discuss_rounds = 1)
      → Portfolio Manager                             (deep model: final rating)
      → Memory log                                    (decision stored; settled later vs benchmark, with an LLM "lesson")
```

That is roughly 15–30 model calls per ticker per date. The paper counts "11 LLM calls & 20+ tool calls/prediction". Data comes from Yahoo, Alpha Vantage, SEC EDGAR (fundamentals as filed), FRED, Reddit RSS, StockTwits and Yahoo news.

## What the paper shows, and doesn't

| Ticker (Jan 1 – Mar 29, 2024) | Buy & hold | Best rule baseline | TradingAgents | TradingAgents Sharpe | TradingAgents max DD |
|---|---|---|---|---|---|
| AAPL | −5.23% | +2.05% (KDJ&RSI) | **+26.62%** | 8.21 | 0.91% |
| GOOGL | +7.78% | +6.23% (SMA) | **+24.36%** | 6.39 | 1.69% |
| AMZN | +17.1% | +11.01% (SMA) | **+23.21%** | 5.60 | 2.11% |

- **Sample:** 3 tickers × ~60 trading days, all mega-cap tech, one quarter, one run each. The setup names 5 stocks, but the table reports only 3.
- **Sharpe 5.6–8.2:** the authors themselves say this "exceeds our expected empirical range". Sharpe above 3 sustained over years is rare even for professional funds, so over one quarter it is luck plus a tiny sample until shown otherwise.
- **No transaction costs, slippage or fees** are mentioned.
- **No null model, no significance test, no repeated runs**, although LLM output varies run to run (the README says so).
- **Model leakage, to be fair to the paper:** it used gpt-4o / gpt-4o-mini / o1-preview, whose training data ends in late 2023, before the Q1 2024 test window. The paper itself doesn't discuss this.

## The real problem for anyone backtesting it today

**A current model has read the future of any past date.** The default GPT-6 models, or Claude, were trained on news and prices from well past 2024. Ask one to rate NVDA on 2024-06-01 and it can "reason" toward what it knows happened. The repo is careful about *data* lookahead: every tool filters to the analysis date, there are tests for news, social and fundamentals leakage, and SEC filings are served as filed. But **nothing in the code or docs addresses the model's own memory**, and no point-in-time filter can remove it. Consequences:
- Its `tradingagents backtest` over past dates measures a model that may already know the answer. The only honest window is **after the model's training cutoff**, i.e. a few recent months, or forward/live.
- **Historical runs are also a different agent from live runs.** Reddit and Yahoo news are searched over the last ~week and then filtered to the window, so for older dates the sentiment and news analysts get almost nothing. The code reports the gap honestly. Historical runs are effectively technical + fundamentals only.
- Its built-in scorer reports mean alpha and hit rate per rating, with independent cells, a 5-day hold, no costs and no null model. That's a decent diagnostic, but not a test that could tell skill from noise.

## Adopted (as ideas; no code copied)

1. **Settle every decision against the benchmark, automatically.** Their memory log stores each rating and, once the holding window has passed, fills in the realized return and alpha vs a benchmark. Our Codex review ledger records verdicts but nothing scores them. → Build `review/settle.py`: score each ledger entry (proceed / reduce / skip) by forward return vs SPY, so the reviewer's value becomes measurable over time.
2. **Unreadable output is never "Hold".** Their parser maps an unreadable decision to `REVIEW`, not a position. Our gate already fails closed the same way (no parseable verdict → SKIP); keep that invariant explicit.
3. **Ground numbers in a deterministic snapshot.** Same principle as our `engine/`. It confirms the design, and gives us nothing new.
4. **Adversarial roles.** Bull vs bear, then three risk temperaments. Worth trying for the Codex gate: give Codex an explicit *bear* brief, since today it is a neutral "risk manager". Only measurable through (1).

## Rejected

- **As a decision engine:** there is no evidence it has an edge. The only published result is 3 stocks over one quarter with no costs, and it can't be backtested over past dates with current models because of training-data leakage.
- **Its LangGraph / LangChain / pandas / stockstats stack:** the opposite of our stdlib-only engine, and about 15–30 paid model calls per decision.
- **Debate as a way to get better numbers:** debate rounds add opinions, not data. Nothing in the repo shows that more rounds improve the outcome.

## If we ever want to test it

Run it only on dates **after the chosen model's training cutoff** (for Claude Opus 5.5, July 2026 onward), on a fixed point-in-time universe. Fix the protocol and the tickers before running, use several runs per cell to measure the LLM's own variance, and score with our backtester's random-entry null model plus the residual-edge analyzer. Rough cost at frontier-model prices: ~$0.5–2 per decision, so a 50-ticker weekly test over 4 months is ~850 decisions, ≈ $400–1,700. The sample would still be small; this is a months-long forward experiment, not a quick backtest.
