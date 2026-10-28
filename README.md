# Trading Desk

Our own research and trading tool. It is built from what we learn by reviewing other trading repositories (notes in `research/repos/`), and nothing in it is trusted until it passes a backtest against a null model.

Rule: **a signal has to beat random entries into the same names, out of sample, on a survivorship-reduced universe, after costs, and beat holding the index at the same exposure, before it gets anywhere near an order.** So far nothing has.

## Layout

| Path | What |
|---|---|
| `engine/` | Deterministic indicator stack, three-pillar scorer and macro pillar (stdlib). Vendored from agentic-trading-desk, see `third_party/`. |
| `backtest/` | Walk-forward backtester with random-entry null model, event study, entry/exit variants, slippage; data fetchers for Yahoo and a point-in-time S&P 500 universe (Tiingo for delisted names). Method, all results and caveats: [`backtest/README.md`](backtest/README.md). |
| `review/codex_review.py` | Second-opinion gate: blind-then-reveal review of a proposed buy by an independent Codex session over MCP; fixed skip / halve / proceed policy, fail closed. |
| `research/repos/` | One note per reviewed repository: what we kept, what we tested, what we rejected. |
| `.mcp.json.example` | Project-scoped Codex MCP registration (`cp .mcp.json.example .mcp.json`, or `claude mcp add codex -- codex mcp-server`). |

## Quick start

```bash
for s in engine/indicators.py engine/score.py engine/macro_pillar.py review/codex_review.py; do python3 $s >/dev/null && echo "$s ok"; done   # self-tests
python3 backtest/fetch_data.py && python3 backtest/backtest.py       # 30-name backtest, ~10 s
```

The point-in-time universe needs a Tiingo key in `$TIINGO_API_KEY` or `~/.config/tiingo/api_key`; see `backtest/README.md`.

Python 3.9+, standard library only.
