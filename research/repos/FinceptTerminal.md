# Fincept-Corporation/FinceptTerminal

Reviewed 2026-10-27 at `7716ee8` (v4.5.1). A Bloomberg-style **desktop terminal**: native C++20 / Qt6 app with embedded Python 3.11. It has about 250 data-connector scripts, analytics modules wrapping well-known libraries (vectorbt, zipline, backtesting.py, bt, QuantLib, skfolio, PyPortfolioOpt, statsmodels, quantstats…), LLM "investor persona" agents, a paper-trading engine and broker integrations. **Open core:** the public repo is the AGPL edition and ships monthly; development happens in a closed Enterprise build ($15–45/user/month). Per its own comparison table, point-in-time data is Enterprise-only.

## License: the deciding fact

- **AGPL-3.0-or-later, strong copyleft.** Copying any of its code into `trading-desk` would oblige us to release our tool under AGPL if we ever distribute it or run it as a service others use. **No code is taken from this repo.**
- **Provenance is muddled.** The `strategies/alphas/` files carry a "Copyright Fincept Corporation… Licensed under the MIT License" header inside an AGPL repo. Yet their own docstrings say they are QuantConnect's *Benchmark Alpha Series*, i.e. QuantConnect LEAN code (Apache-2.0), with LEAN's header replaced. Anything we want from them, we take from the original source (LEAN or the cited paper/book), not from here.

## Useful to us

1. **Two OHLC-only mean-reversion hypotheses**, both testable on data we already cache. We would implement them from the original definitions, not from Fincept's code:
   - **IBS (internal bar strength)** = (close − low) / (high − low). Buy when IBS is low (closed near the day's low), hold about a day. The cited source is Kakushadze & Serur, *151 Trading Strategies* (2018), §4. It's a classic daily mean-reversion signal, and it's the right kind to test given that mean reversion is the only family that has beaten random entries so far.
   - **Outlier gap reversion**: fade a daily move larger than 3× the trailing volatility.
2. **CBOE VIX history: free, no key**, daily since 1990, from `cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv` (verified). Uses: a regime input independent of price trend, and a stress proxy for **cost modelling** (charge more slippage on high-VIX days, which is when RSI<30 and IBS fire).
3. **A lead on the delisted-names gap**: Nasdaq Data Link's `WIKI/PRICES` (free key; community dataset frozen in March 2018) may cover some of the 2008 failures Tiingo lacks (Lehman, Bear Stearns, Countrywide, WaMu). Not verified: it needs a key.

## Not useful

- **The terminal itself:** a GUI research workstation, not a library. Nothing in it can be imported into a stdlib engine.
- **Backtesting folder:** adapters around vectorbt / zipline / backtesting.py / bt. It has walk-forward splitters (vectorbt's), but nothing on survivorship, null models or multiple-testing corrections that our backtester lacks.
- **Persona agents** ("Warren Buffett", "Benjamin Graham", "Peter Lynch"…): LLM role-play with no evaluation at all, which is weaker than TradingAgents.
- **Most of the ~250 data connectors:** macro, government, climate, shipping, crypto, China A-shares, mostly thin request wrappers. When we need a source, it's quicker to write our own than to untangle AGPL code.
