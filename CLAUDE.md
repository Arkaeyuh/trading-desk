# Trading Desk

- The user feeds trading repositories to review. For each one: clone it as a sibling under `~/Documents/code/trading/` (keep it clean upstream as a reference), write `research/repos/<name>.md` (what it does, what we keep, what we test, what we reject), and bring adopted code in under `engine/` or a new module, with its license in `third_party/`.
- Any new signal goes through `backtest/` before it is used: dev period 2008–2016, held-out 2017–2026 run once, selection criterion fixed in advance, point-in-time universe (`--data backtest/data_pit --membership backtest/data_pit/membership.json`), slippage sweep. 2017–2026 has been looked at several times already; plan a fresh hold-out for new tuning.
- Stdlib-only Python, matching the existing scripts. Run each script with no arguments for its self-test.
- Data and results are gitignored; never commit market data or the Tiingo key.
