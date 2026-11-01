# Video Summary: Building an "AI Trading Agent" with Claude

**Source:** TikTok, @nateherkai (49 s, vertical)
**Method:** Reconstructed from burned-in captions and on-screen footage. Audio was not transcribed, so wording is approximate and a few captions were only partly legible.

## Core claim

You can build an AI trading agent with Claude Code that watches the market and tells you exactly what to do when something changes.

## How it works (as presented)

| Step | What the video says | What is shown on screen |
|---|---|---|
| 1. Connect a trading platform | Connect Claude to a trading service so it can interact with markets (including prediction markets) from inside Claude | A dark trading dashboard branded "Liquid" with a "#1" label; stock, crypto, FX, and commodity charts; an order book |
| 2. Add market data | Connect Quiver Quant, which "gives Claude market data live," including insider transactions, congressional trades, and institutional activity | Quiver Quantitative site; an insider-transaction table (SEC Form 4 style); a politician-trade table (e.g., Nancy Pelosi); an institutional holdings table |
| 3. Give Claude a strategy | Example prompt: "watch a specific investor's disclosed trades and check for new activity throughout the day" | Claude chat window (Opus 5, High effort) with the prompt typed in |
| 4. Compare against your portfolio | Claude checks the strategy's signals against your current holdings and sends you a recommendation when something changes | Portfolio view (GOOGL, NVDA, PLTR, AVGO, MSTR, TSLA, FCX) with P/L columns |
| 5. Automate it | Create a routine that runs automatically, without you having to ask | A scheduled "Daily AI News Summary" automation task in another agent tool |
| 6. Test before risking money | Run the whole thing through paper trading first | "Paper Money $80,000" card; a Claude "Co-Invest" tool placing a demo basket of 5 trades (BTC, ETH, TSLA, EUR/USD, GOLD at about $15 each) and a confirmation widget to close a BTC position |

## Closing framing

Combining live data, a brokerage connection, a strategy, and a set of rules to follow is "basically giving Claude access" to act as a "trader."

## Components named

- **Claude Code / Claude chat** as the agent
- **Quiver Quant** for alternative data (insider, congressional, institutional filings)
- **A brokerage or exchange connection** (a "Liquid" platform and a "Co-Invest" connector appear; Robinhood-style app screens also appear)
- **Scheduled routines** for unattended runs
- **Paper trading** for testing

## Notes and caveats

- The video is a short promotional clip. It shows no backtest, performance numbers, costs, or risk controls.
- The example strategy (copying disclosed insider or congressional trades) relies on filings that are reported with a lag. On-screen tables show delays from 2 to 34 days between trade and disclosure, which limits how much signal is left by the time the agent acts.
- The demo trades are paper trades of about $15 each, so nothing shown demonstrates live profitability.


## Verdict (2026-10-31)

**Rejected as built: a promo with no evidence.** The automation on screen is a news summary, not a trading routine. The paper trades are ~$15 positions in leveraged products, and no backtest or P&L is shown. "Quiver gives market data live" is wrong: it serves disclosure data, which is live only when a filing appears.

**The two strategies in it are different:**
- **Copying congressional trades: rejected.** Disclosure takes up to 45 days under the STOCK Act. Studies covering the years after 2012 find no reliable outperformance (Eggers & Hainmueller for 2004–08; Belmont et al. 2022 for after the STOCK Act). The ETFs built on it mostly hold a large-cap tech tilt, which is exposure, not signal. Picking the investor to follow (Pelosi) after their trades worked is lookahead. Amounts are ranges, spouse trades and options are mixed in, and many followers act on the same disclosure.
- **Copying insider (Form 4) purchases: tested, rejected on the S&P 500.** Clusters of purchases beat random entry dates (100th percentile on the 2017–2026 hold-out) but trailed SPY over the same dates (−1.2% per trade). They mark market-wide selloffs, not stock-specific information. The S&P 400 hold-out is still pending. Results are in `backtest/README.md`. Data: `backtest/fetch_insider.py`.

**Rejected outright:**
- **An LLM in the decision loop.** "New filing → compare to holdings" is a deterministic filter. An LLM adds non-determinism and can't be backtested (it has read the future; see `research/repos/TradingAgents.md`).
- **An unattended agent with brokerage write access.** It reads third-party text (filings, connector output), so prompt injection could reach orders.
- **Paper trading as evidence.** With a few dozen signals a year it would take years to tell an edge from noise. Paper trading tests plumbing and fills, not edge.
