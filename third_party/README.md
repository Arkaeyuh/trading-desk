# Third-party code

`engine/indicators.py`, `engine/score.py`, `engine/macro_pillar.py` and `engine/prepare_inputs.py` are copied unchanged from [Oft3r/agentic-trading-desk](https://github.com/Oft3r/agentic-trading-desk) at commit `2cdfe07`, under the MIT license in `agentic-trading-desk.LICENSE`. Changes we make to them from here on are ours; keep this note and the license file.

`analysis/residual_edge.py` (unchanged copy of `skills/residual-edge-analyzer/scripts/analyze_residual_edge.py`) and `analysis/residual_edge_methodology.md` / `analysis/residual_edge_input_contract.md` (its `references/`) are from [tradermonty/claude-trading-skills](https://github.com/tradermonty/claude-trading-skills) at commit `c9b8419`, under the MIT license in `claude-trading-skills.LICENSE`. Their documentation paths (`skills/residual-edge-analyzer/...`) refer to the upstream layout; here, run it via `backtest/attribution.py`.
