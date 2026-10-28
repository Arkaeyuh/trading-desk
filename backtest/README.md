# Backtest

Walk-forward test of the desk's **deterministic layer**: `macro_pillar.py` + `score.py`'s decision cascade, traded under the Autonomous Execution Mandate's sizing rules. `backtest.py` imports `engine/` unchanged, so what is tested is exactly what the engine emits.

```bash
python3 backtest/fetch_data.py                 # 8 macro ETFs + 30-name universe, 2005→today (Yahoo chart API)
python3 backtest/backtest.py                   # writes backtest/results/{report.txt,summary.json,trades.csv,equity.csv,events.csv}
python3 backtest/backtest.py --no-stops --out backtest/results/nostops
python3 backtest/backtest.py AAPL MSFT XLK --start 2015-01-01 --spread-csv T10Y2Y.csv
```

stdlib only, like the rest of the repo. The full run takes about 10 s on 16 cores.

## Method

| | |
|---|---|
| **Decision** | each day *t*, for each symbol, `score.py` on the trailing **290** closes up to *t*. That is the window the live desk fetches, so EMA200 is seeded the same way. The macro pillar for *t* uses ETF closes up to *t*. |
| **Fill** | next session's **open**, 5 bp slippage against us. No lookahead. |
| **Entries** | `RE-ENTRY` at base size (3% of equity, ≤ $1,200); `TACTICAL REBOUND` at half; both halved again when macro ≤ −1. Max 3 new per day, ranked by pillar total. 15% cash reserve. No buys while holding (no averaging down). No new buys the day after equity falls > 4%. |
| **Exits** | `EXIT` and `EXIT / TRIM` (full exit by default; `--trim-frac 0.5` for partial). The skill's manual stops are on by default: close below the 5-bar pivot low at entry; tactical trades out at close ≥ EMA50 or after 10 sessions. |
| **Prices** | split-adjusted, not dividend-adjusted (the same basis as Robinhood historicals). Benchmarks use total-return `adjclose`. |

Three questions, three tests:
1. **Does it make money, and at what risk?** Equity curve against SPY buy-and-hold, and against SPY held at the *same average exposure*, the fair comparison for a book that is ~75% cash.
2. **Do the entries beat chance?** A null model. Every real trade is replayed 500 times with the same symbol and holding length but a random entry day. If the signals carry information, the strategy's mean trade sits in the right tail.
3. **Which signals carry information?** An event study. For every symbol-day and action label: the forward 5/10/20-day return from the next open, minus that symbol's unconditional forward return.

## Results (2008-01-02 → 2026-10-26, 30 symbols, defaults)

```
Portfolio   CAGR +2.02%  vol 3.52%  Sharpe 0.58  maxDD -6.55%  exposure 23.8%
SPY @ same exposure  CAGR +2.94%  Sharpe 0.64  maxDD -14.50%
Trades 1871  win 43.7%  mean +0.91%  PF 1.34  hold 23.9d
vs random entries: strategy mean +0.91%, random mean +1.43% [5%..95%: +1.11% .. +1.77%]  → percentile 1
```

| Variant | CAGR | maxDD | mean trade | percentile vs random |
|---|---|---|---|---|
| default (stops on, full exits) | +2.02% | −6.6% | +0.91% | 1 |
| `--no-stops` | +2.37% | −7.4% | +1.19% | 1 |
| `--trim-frac 0.5` | +2.21% | −6.1% | +1.11% | 0 |

Event study (excess forward return; entries want > 0, exits want < 0):

| state | action | n | 5d | 10d | 20d |
|---|---|---|---|---|---|
| flat | RE-ENTRY (new cycle) | 2,533 | −0.01% | −0.10% | −0.09% |
| flat | TACTICAL REBOUND | 232 | +0.56% | +0.60% | +0.32% |
| flat | WAIT (do not chase) | 48,053 | −0.08% | −0.11% | −0.24% |
| flat | STAY OUT / AVOID | 26,569 | +0.13% | +0.20% | +0.27% |
| hold | EXIT | 20,968 | +0.17% | +0.26% | +0.42% |
| hold | EXIT / TRIM | 3,376 | −0.02% | +0.29% | +0.45% |
| hold | HOLD (ride the cycle) | 46,999 | −0.08% | −0.15% | −0.29% |

**Reading it.**
- The framework is profitable and has a much smaller drawdown than SPY. That comes from small sizing and a mostly-cash book, though, not from the signals: holding SPY at the same exposure did better on CAGR and Sharpe.
- **The entries do not beat random timing.** The main entry signal, `RE-ENTRY`, has zero excess return. Only `TACTICAL REBOUND` looks positive, and with 232 overlapping observations that is not evidence yet.
- **The exits fire at the wrong time.** Names flagged `EXIT` and `STAY OUT / AVOID` went on to *outperform* their own average, and `HOLD (ride the cycle)` / `WAIT` went on to underperform. On daily bars, the momentum-continuation logic sells into short-term mean reversion. This is the most actionable finding.
- The manual stops cost a little (the `--no-stops` row). The verdict holds across all three variants and in both sub-periods (2008–2016: +1.7% CAGR; 2017–2026: +2.3%).

## What the backtest cannot tell you

- **Survivorship bias.** The universe is today's large caps. It flatters every long strategy, and the random baseline too. The baseline still controls for it within the same names, which is why it is the headline test.
- **No 10Y-2Y component.** FRED was unreachable when these results were produced. The macro pillar redistributes that weight, as the live desk does when egress is blocked. Pass `--spread-csv` with a FRED `T10Y2Y` export to include it.
- **Not modelled:** discretionary trades, news, the earnings blackout, intraday (provisional-bar) decisions, T+1 settlement on a cash account, dividends on held positions (a small conservative bias).
- **The Codex review cannot be backtested honestly.** An LLM shown 2015 prices already knows what 2016 did. Evaluate the reviewer forward instead: `codex_review.py check --ledger` logs every verdict next to its order, and scoring those against realized returns after a few months is the test.
- The event-study windows overlap, so naive significance would be overstated. The random-entry test is the one to trust.

## Experiment: stricter exit rules (held out, 2026-10-27)

The protocol was fixed before any run. Candidates were tested on **2008–2016 only**, and the winner was picked by its **percentile vs random entries** (ties broken by Sharpe). The winner and the baseline were then run **once** on the held-out **2017–2026** period. The options are `--exit-confirm N`, `--exit-below-ema50`, `--trim-confirm N` and `--ignore-trim`.

Development period, 2008–2016:

| Candidate | pct vs random | Sharpe | CAGR | maxDD | trades | hold |
|---|---|---|---|---|---|---|
| A baseline | 7 | 0.52 | +1.74% | −5.6% | 907 | 24d |
| B EXIT confirmed 2 sessions | 11 | 0.61 | +2.24% | −6.2% | 845 | 28d |
| C EXIT confirmed 3 sessions | 14 | 0.66 | +2.59% | −6.8% | 796 | 33d |
| D EXIT only below EMA50 | 7 | 0.52 | +1.74% | −5.6% | 907 | 24d |
| E EXIT and EXIT / TRIM confirmed 2 | 15 | 0.69 | +3.26% | −7.6% | 737 | 41d |
| **F never sell on EXIT / TRIM** | **45** | 0.80 | +4.67% | −8.6% | 748 | 43d |

D is identical to A: `EXIT` never fired with the close above EMA50, so that condition never removed an exit.

Held-out period, 2017–2026:

| | pct vs random | Sharpe | CAGR | maxDD | exposure | SPY at same exposure |
|---|---|---|---|---|---|---|
| A baseline | 2 | 0.64 | +2.28% | −6.5% | 25% | +3.84%, Sharpe 0.87 |
| F never sell on EXIT / TRIM | **1** | 0.78 | +5.20% | −10.8% | 41% | +6.34%, Sharpe 0.87 |

**Verdict: rejected.** F's development-period gain did not hold up out of sample. On 2017–2026 its trades were back at the 1st percentile against random entries. Its higher CAGR is just holding longer and more invested through a bull market, which is exposure, not signal: SPY held at the same exposure beat it again on both return and Sharpe. In the development period, the stricter rules only moved the strategy from worse-than-random to roughly random; none beat chance. `score.py` is unchanged.

The lesson for the framework: tuning the exits will not fix it, because on these names the entries carry no timing information to protect. The more promising directions are a different entry signal, or accepting that the desk is a low-exposure way of holding the market and sizing it as such.

## Experiment: alternative entry signals (held out, 2026-10-27)

This uses the same protocol as above: candidates on 2008–2016, the winner picked by percentile vs random entries with ties broken by Sharpe and a minimum of 100 trades, then one run on 2017–2026. The universe was widened to **89 names** (the original 30 plus 59 large caps; BK failed to download) to give rare signals enough trades. Thresholds are textbook values, fixed in advance. Mean-reversion entries use a pre-set exit (`--exit-mode revert`: close ≥ EMA20, or 10 sessions). They cannot use the framework's exits, which fire on bearish momentum: the very state an oversold entry starts in.

Note: `backtest.py` with no symbol arguments now trades every cached name, i.e. these 89. The headline results above are on the original 30.

Development period, 2008–2016:

| Candidate | `--entry` | pct vs random | mean trade (random) | Sharpe | CAGR | maxDD | exposure | n |
|---|---|---|---|---|---|---|---|---|
| E0 framework | `framework` | 9 | +0.89% (+1.13%) | 0.69 | +5.06% | −11.5% | 53% | 2,113 |
| E1 tactical rebound only | `tactical` | 4 | −0.26% (0.00%) | −0.39 | −0.12% | −1.2% | 0% | 382 |
| E2 RSI14<35 above EMA200 | `trend_pullback` | 85 | +0.52% (+0.32%) | 0.31 | +0.88% | −6.6% | 6% | 702 |
| **E3 RSI14<30** | `oversold` | **100** | +1.17% (+0.34%) | 0.50 | +2.74% | −9.1% | 10% | 1,161 |
| E4 %B<0 above EMA200 | `band_pullback` | 76 | +0.36% (+0.26%) | 0.38 | +1.40% | −7.2% | 11% | 1,434 |
| E5 252-day closing high | `breakout` | 0 | +0.35% (+0.90%) | 0.37 | +2.17% | −9.1% | 41% | 2,047 |

On 89 names, `TACTICAL REBOUND`'s excess return turned negative (10d −0.44%, n=431), so its +0.6% on 30 names was noise. Its trades also last 2 days, because the framework's own `EXIT` fires on a counter-trend position almost immediately. Breakouts did worse than random. Every mean-reversion entry beat random, and every momentum-continuation entry lost to it.

Held-out period, 2017–2026:

| | pct vs random | mean trade (random 5–95%) | Sharpe | CAGR | maxDD | exposure | SPY at same exposure |
|---|---|---|---|---|---|---|---|
| E0 framework | 2 | +0.89% (+0.97..+1.48%) | 0.65 | +4.61% | −17.5% | 57% | +8.75%, Sharpe 0.87, DD −20.3% |
| **E3 RSI14<30** | **99** | +0.68% (+0.12..+0.59%) | 0.52 | +2.83% | −12.8% | 14% | +2.12%, Sharpe 0.87, DD −5.1% |

**Verdict: E3 passes the timing test out of sample, but is not ready to trade.**
- **The timing edge held up.** 1,465 held-out trades sat at the 99th percentile against random entries into the same names held for the same time. It made money in 9 of 10 years, including +4.8% in 2022, when SPY fell. It beat SPY held at the same exposure on CAGR.
- **Risk-adjusted, it is still worse than SPY at the same exposure:** Sharpe 0.52 vs 0.87, and a −12.8% drawdown vs −5.1% (2020: −4.2%). Buying several oversold names at once concentrates the book in a selloff. 62% of exits were time stops: the bounce often did not reach EMA20 within 10 sessions.
- **Survivorship bias favors exactly this strategy.** The universe is companies that are large today, so every dip in it eventually recovered. The random baseline uses the same names, but the bias helps dip-buying more than random timing. Names that crashed and never came back are missing. **A point-in-time universe test, including delisted names, is required before any live use.**

## Point-in-time universe (survivorship-reduced, 2026-10-27)

Every name that was in the S&P 500 at any point since 2007 (933 tickers, 966 membership periods), **buyable only on days it was actually a member**. A position whose data ends is closed at the last print (`delisted`, or `left index (data end)`), and the random baseline is treated the same way.

```bash
# membership: fja05680/sp500 (MIT) + Tiingo's public ticker list, into backtest/universe_src/
curl -sL -o backtest/universe_src/sp500_ticker_start_end.csv https://raw.githubusercontent.com/fja05680/sp500/master/sp500_ticker_start_end.csv
curl -sL -o backtest/universe_src/tiingo_tickers.zip https://apimedia.tiingo.com/docs/tiingo/daily/supported_tickers.zip
python3 backtest/fetch_pit.py      # Yahoo first, then Tiingo (key: $TIINGO_API_KEY or ~/.config/tiingo/api_key); resumable, ~3 h on the free tier
python3 backtest/backtest.py --data backtest/data_pit --membership backtest/data_pit/membership.json [--entry oversold --exit-mode revert]
```

**Coverage** (`data_pit/coverage.json`): current members, 498/503 membership periods (99%). Removed members, **277/463 (60%)**: 615 tickers from Yahoo, 120 from Tiingo, plus 19 hand-checked renames (`RENAMES` in `fetch_pit.py`). Renames are deliberately not inferred: a same-day handoff also describes every ordinary index replacement. Every series is cut at its first trading gap over 20 days (stray bars after a buyout), and Tiingo extensions were accepted only when daily returns matched over the overlap. **Still missing** are mostly the names that matter most for a dip-buying test. In 2008: Bear Stearns, Merrill, Countrywide, Lehman (LEHMQ), WaMu, Wachovia. In 2023: SVB, First Republic. Plus 13 names Tiingo lists but returned no prices for (EMC, Monsanto, Anadarko, CA, …). So this universe is **less** biased, not bias-free, and it still flatters buying oversold names.

| Universe | Period | Entry | pct vs random | mean trade (random) | Sharpe | CAGR | maxDD | exposure | SPY at same exposure |
|---|---|---|---|---|---|---|---|---|---|
| point-in-time (735) | 2008–2016 | framework | **0** | +0.38% (+1.14%) | 0.34 | +3.21% | −29.5% | 74% | +5.71%, Sharpe 0.43 |
| point-in-time (735) | 2008–2016 | RSI14<30 | 80 (p≈0.20) | +0.31% (+0.20%) | 0.22 | +1.92% | −28.7% | 30% | +2.60%, Sharpe 0.43 |
| point-in-time (735) | 2017–2026 | framework | **1** | +0.70% (+1.09%) | 0.40 | +3.67% | −20.5% | 79% | +12.00%, Sharpe 0.87 |
| point-in-time (735) | 2017–2026 | RSI14<30 | **99** (p≈0.01) | +0.40% (+0.15%) | 0.39 | +3.67% | −17.1% | 44% | +6.79%, Sharpe 0.87 |
| survivors (89), for comparison | 2017–2026 | RSI14<30 | 99 | +0.68% (+0.36%) | 0.52 | +2.83% | −12.8% | 14% | +2.12%, Sharpe 0.87 |

**Verdict.**
- **The framework's entries are confirmed worse than random, now without the survivor universe:** 0th and 1st percentile in both periods. Held at the same average exposure, SPY returned 12.0% a year to the framework's 3.7% on 2017–2026.
- **RSI14<30 keeps a small timing edge out of sample** (99th percentile on 2017–2026). But it is less than half what the survivor universe showed: +0.25% per trade over random, not +0.32% on smaller absolute returns. On 2008–2016 it is not significant (80th percentile, p≈0.20). It still loses to passive SPY at the same exposure on both CAGR and Sharpe, and its drawdowns are SPY-sized.
- **The survivor universe overstated dip-buying, as expected.** On 2008–2016 the mean trade fell from +1.17% to +0.31% and the max drawdown went from −9% to −29%. That is with the worst 2008 failures still missing.
- **Conclusion:** neither entry rule justifies active trading over holding the index at the same exposure. RSI<30's 2017–2026 edge is real enough to keep as a research lead, not a live signal.

## Slippage sweep: RSI14<30, point-in-time (2026-10-27)

Costs are charged per side at entry and exit (`--slippage-bps`). The random baseline pays the same costs, so the percentile vs random does not move with cost. What moves is whether the strategy makes money at all, and its gap to SPY, which costs almost nothing to hold.

| per side | 2008–2016 mean trade | CAGR | Sharpe | 2017–2026 mean trade | CAGR | Sharpe | maxDD |
|---|---|---|---|---|---|---|---|
| 5 bp | +0.31% | +1.92% | 0.22 | +0.40% | +3.67% | 0.39 | −17.1% |
| 10 bp | +0.21% | +1.05% | 0.15 | +0.30% | +2.39% | 0.27 | −18.1% |
| 25 bp | −0.09% | −1.50% | −0.08 | 0.00% | −1.35% | −0.07 | −25.8% |
| 50 bp | −0.59% | −5.61% | −0.46 | −0.50% | −7.27% | −0.65 | −53.6% |
| SPY at same exposure | | +2.60% | 0.43 | | +6.79% | 0.87 | −15.9% |

**Break-even is about 20 bp per side** (≈ 0.4% round trip), at roughly 460 trades a year. For $375–$1,200 market orders in S&P 500 names at the open, normal costs are a few bp, so 5–10 bp is a realistic range. It can be well above that in a selloff, which is exactly when RSI<30 fires; per-day spread estimates (Corwin-Schultz) would measure that. Even at 5 bp, the strategy trails SPY held at the same exposure in both periods.

## Parameter plateau: RSI threshold × time stop (dev 2008–2016 only, point-in-time, 2026-10-27)

This is the "plateaus, not peaks" check, adopted from claude-trading-skills' `backtest-expert`. It was run on the development period only, so the hold-out is not spent again. `--rsi-max` sets the threshold, `--revert-max-days` the time stop.

Percentile vs random entries (mean trade vs random in brackets):

| RSI14 below | 5-day stop | 10-day stop | 15-day stop |
|---|---|---|---|
| 25 | 99 (+0.34% vs +0.04%) | 99 (+0.61% vs +0.14%) | 99 (+0.85% vs +0.25%) |
| **30** (tested so far) | **13** (−0.04% vs +0.07%) | 80 (+0.31% vs +0.20%) | 61 (+0.33% vs +0.29%) |
| 35 | 95 (+0.20% vs +0.07%) | 95 (+0.36% vs +0.20%) | 88 (+0.41% vs +0.28%) |

Portfolio Sharpe ranged from 0.04 to 0.44 across the grid; SPY at the same exposure scored 0.43 over this period.

**Verdict: no plateau.** The threshold we carried forward sits in a *valley*: worse than both neighbours, and below chance with a 5-day stop. Edge that jumps from the 13th to the 99th percentile when the threshold moves 5 RSI points is noise in the timing, not a stable effect. RSI<25 looks consistent across all three stops, but picking it now would be choosing the best cell after seeing the grid. It is a hypothesis for a fresh hold-out, not a result. No cell beats SPY at the same exposure on Sharpe.

## Experiment: IBS and outlier down-moves (dev only, point-in-time, 2026-10-28)

These two OHLC-only mean-reversion signals come from the FinceptTerminal review and were implemented from their original definitions. Rules and pass criterion were fixed before any run: textbook thresholds, 2008–2016 on the point-in-time universe, **≥95th percentile vs random entries with ≥100 trades** to earn one run on 2017–2026.

- `--entry ibs_low`: IBS = (close − low) / (high − low) < 0.2, held one session (`--exit-mode revert --revert-max-days 1`: next open to the open after).
- `--entry down_outlier`: close-to-close return below −3 standard deviations of the prior 20 daily returns, with the same exit as RSI<30 (`--exit-mode revert`: close ≥ EMA20, or 10 sessions).

| Candidate | pct vs random | mean trade (random 5–95%) | Sharpe | CAGR | maxDD | exposure | SPY at same exposure | n |
|---|---|---|---|---|---|---|---|---|
| E6 IBS < 0.2, 1 day | **3** | −0.10% (−0.09..+0.02%) | −0.85 | −1.66% | −14.5% | 7% | +0.63%, Sharpe 0.43 | 6,617 |
| E7 return < −3σ | **46** | +0.16% (+0.00..+0.37%) | 0.10 | +0.45% | −14.1% | 26% | +2.23%, Sharpe 0.43 | 3,559 |

**Verdict: both rejected in development; the hold-out was not run.**
- **The outlier down-move carries no timing information:** 46th percentile, which is what random entries would score. Its 10-day excess return in the event study is −0.01% (n=10,101).
- **IBS has a real effect, but it is too small to trade.** Across every member-day in 2008–2016, the next day's close-to-close return after IBS < 0.2 beats the average day by **+0.13%**: +0.06% overnight and +0.07% the next session. That is about the size of a 10 bp round trip. The desk fills at the next open, so it misses the overnight half. Even filling at the signal day's close would leave ≈ +0.03% after costs. In the backtest the trades did worse than random (3rd percentile). One likely reason is that roughly 90 names qualify on a typical day and the top 3 are picked by the framework's pillar total, which earlier tests found to be anti-informative.
- Mean reversion remains the only family with any edge here, and so far only RSI<30 has been strong enough to survive costs.

## Cost model: VIX-scaled slippage, RSI14<30 (point-in-time, 2026-10-28)

The flat-cost sweep assumed the same slippage every day, but RSI<30 is a dip-buying signal, and spreads widen in a selloff. This test scales costs with market stress. The model was fixed before any run: **cost per side = `--slippage-bps` × VIX / 20**, using VIX at the close of the decision day (known before the next-open fill). 20 is about the 2008–2026 mean (19.8), so an average day costs the same as the flat model and a VIX-80 day costs 4×. The random baseline pays the costs of its own decision days, so the percentile vs random can now move.

```bash
curl -sL -o backtest/data/VIX_History.csv https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv   # CBOE, free, no key
python3 backtest/backtest.py --data backtest/data_pit --membership backtest/data_pit/membership.json \
    --entry oversold --exit-mode revert --vix-csv backtest/data/VIX_History.csv --slippage-bps 5
```

Without `--vix-csv` the trades are identical to the earlier runs (regression-checked).

| base | period | cost model | pct vs random | mean trade (random) | Sharpe | CAGR | maxDD |
|---|---|---|---|---|---|---|---|
| 5 bp | 2008–2016 | flat | 80 | +0.31% (+0.20%) | 0.22 | +1.92% | −28.7% |
| 5 bp | 2008–2016 | × VIX/20 | 79 | +0.30% (+0.20%) | 0.22 | +1.88% | −28.9% |
| 10 bp | 2008–2016 | flat | — | +0.21% | 0.15 | +1.05% | — |
| 10 bp | 2008–2016 | × VIX/20 | 78 | +0.19% (+0.09%) | 0.14 | +0.98% | −29.6% |
| 5 bp | 2017–2026 | flat | 99 | +0.40% (+0.15%) | 0.39 | +3.67% | −17.1% |
| 5 bp | 2017–2026 | × VIX/20 | 99 | +0.41% (+0.16%) | 0.40 | +3.78% | −17.1% |
| 10 bp | 2017–2026 | flat | — | +0.30% | 0.27 | +2.39% | −18.1% |
| 10 bp | 2017–2026 | × VIX/20 | 99 | +0.32% (+0.07%) | 0.29 | +2.61% | −18.0% |

SPY held at the same exposure: +2.60% CAGR, Sharpe 0.43 (2008–2016); +6.79%, Sharpe 0.87 (2017–2026).

**Verdict: market-wide stress costs do not hurt RSI<30, because its trades are not concentrated in stressed markets.**
- Entry-day VIX averaged 1.10× the reference in 2008–2016 (all days: 1.05×) and 0.93× in 2017–2026 (all days: 0.94×). Only 14% and 6% of entries came on days with VIX above 30.
- **Why:** in a broad selloff hundreds of names go oversold at once, but the book takes at most 3 new positions a day and runs into its cash reserve within days. Most of its trades are therefore stock-specific dips on ordinary days.
- **The edge versus random entries is unchanged** (79th/99th percentile), and the gap to SPY at the same exposure is unchanged too. The earlier conclusion stands: a research lead, not a live signal.
- **What VIX cannot see:** a single stock gapping down on its own news, on a calm day, with its spread widened. That is the typical RSI<30 entry here. Measuring it needs a per-stock, per-day spread estimate from daily high/low (Corwin & Schultz 2012), not a market index.

## Experiment: volume signals (dev only, point-in-time, 2026-10-28)

These three signals come from the claude-trading-skills review. Their rules are the skills' defaults turned into hard rules, and those rules and the pass criteria were fixed before any run. They need daily volume, which the fetchers used to drop. `fetch_volume.py` re-fetches each cached file from its own source and merges volume in by date without changing any price, so every earlier result still reproduces:

```bash
python3 backtest/fetch_volume.py --only yahoo     # ~5 min
python3 backtest/fetch_volume.py --only tiingo    # ~2.5 h: 120 files at Tiingo's 50 requests/hour
```

Every file got volume. One exception needed a manual step: CPWR's Tiingo prices now differ from the cached ones by a constant ×2.17, a revised split adjustment. The script refuses any file whose prices differ, so CPWR's volume was merged by hand. The signals only compare volume with nearby days, so the scale does not matter.

**Stock signals.** Pass rule: ≥95th percentile vs random entries, with ≥100 trades.
- `--entry burst` (Stockbee momentum burst): close ≥ 1.04 × the prior close, on higher volume than the prior day. Exit on a close below the trigger day's low, or after 5 sessions (`--exit-mode lowstop --revert-max-days 5`).
- `--entry hammer` (Stockbee exhaustion hammer):
  - the highest high of the prior 40 bars came 3–30 sessions ago, and the close is 6–35% below it;
  - the low undercuts the prior 5-day low and the close is back above it;
  - lower wick ≥ 40% of the day's range, body ≤ 35%, close in the top 40%;
  - volume ≥ the 20-day average.
  Exit on a close below the hammer day's low, or after 10 sessions.

| Candidate | pct vs random | mean trade (random 5–95%) | Sharpe | CAGR | maxDD | exposure | SPY at same exposure | n |
|---|---|---|---|---|---|---|---|---|
| E8 momentum burst | **1** | −0.12% (−0.02..+0.28%) | −0.13 | −1.13% | −20.2% | 23% | +1.99%, Sharpe 0.43 | 4,955 |
| E9 exhaustion hammer | **5** | −0.02% (−0.01..+0.40%) | 0.08 | +0.27% | −13.8% | 16% | +1.42%, Sharpe 0.43 | 2,270 |

**Market gate.** `distribution_gate.py` applies IBD distribution days to SPY:
- a distribution day is a close down ≥ 0.2% on higher volume than the prior day;
- it stays active for 25 sessions, unless a later high reaches 5% above its close;
- the gate goes to cash when the skill's risk level is HIGH or worse (d25 ≥ 5, d15 ≥ 3 or d5 ≥ 2).

The null model shifts the same in/out mask to random offsets, which keeps how often and how long the gate is out and moves only when. Pass rule: Sharpe ≥ the 95th percentile of the shifted masks.

| 2008–2016 | CAGR | Sharpe | maxDD | in market |
|---|---|---|---|---|
| gate | −3.90% | −0.38 | −34.6% | 24% |
| SPY buy & hold | +7.28% | 0.44 | −52.0% | 100% |
| SPY at same exposure | +2.09% | 0.44 | −14.5% | 24% |
| shifted masks (null) | median Sharpe 0.18, 5–95% −0.26..0.66 | | | |

**Verdict: all three rejected in development; the hold-out was not run.**
- **The momentum burst is worse than random entries** (1st percentile). It joins every other momentum-continuation entry tested here (breakouts, the framework's `RE-ENTRY`), all of which lost to random timing. On these names, daily momentum does not continue; it reverts.
- **The exhaustion hammer is also worse than random** (5th percentile), even though it is a mean-reversion signal. In the event study its 10-day excess return is −0.39% (n=4,642). The day-low stop exits more than half the trades, 54%, often on the next session's noise. Its rules require a stock that is still pulling back from a recent high, and that may select names in steady decline rather than exhausted ones.
- **The distribution-day gate times the market worse than chance** (2nd percentile). Under the skill's rules, 23.5% of SPY sessions count as distribution days, so a typical 25-session window holds about 6 and the gate sits in cash 76% of the time. SPY's next 20 sessions averaged +0.69% after risk-off days against +0.87% after risk-on days, which is almost no difference.

## Suggested next experiments

These are hypotheses. Each one should go through a held-out period, not be fitted on the full sample:
1. ~~Make `EXIT` require confirmation.~~ Done above; rejected out of sample.
2. ~~Re-run E3 on a point-in-time universe.~~ Done above, with 60% coverage of removed members. Closing the remaining gap (the 2008 failures, SVB, First Republic) needs a full-coverage source such as Norgate Platinum or CRSP.
3. ~~Test `TACTICAL REBOUND` on a wider universe.~~ Done above: it did not survive.
4. If E3 survives (2): limit how many oversold names the book holds at once (or cap per-day entries in a broad selloff) to address the drawdown. Test that on a fresh held-out split, since 2017–2026 has now been looked at.
