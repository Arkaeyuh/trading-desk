#!/usr/bin/env python3
"""
codex_review.py
===============
Second-opinion gate: an independent Codex session (via the Codex MCP server)
reviews every proposed BUY before review_equity_order / place_equity_order.
This script makes the review deterministic around the model call: it builds
the prompts, parses Codex's replies, and applies a fixed policy. The agent
only relays text between this script and the MCP tool.

Two stages, so Codex forms its own view before it sees ours (anti-anchoring):

  1. blind   Codex gets the input files and the scripts, recomputes the
             scorecard itself, checks the data, and states its own stance.
  2. reveal  In the same Codex thread (codex-reply), Codex sees the proposed
             order and the agent's written reasoning, and returns a verdict.

Policy (check), for buys:
  - no parseable final verdict              -> SKIP  (fail closed)
  - verdict "veto"                          -> SKIP
  - verdict "reduce"                        -> REDUCE to half the proposed size
  - "concur" but blind stance avoid/sell,
    numbers_match false, or confidence<0.5  -> REDUCE
  - otherwise                               -> PROCEED at the proposed size
  Codex can only lower the size, never raise it, never originate a trade.
For sells the gate is advisory: always PROCEED, and a veto is surfaced in the
report. Exits reduce exposure; a reviewer that can block them adds risk.

  python3 codex_review.py blind  --ticker T.json --macro-score 1 --holding false [--evidence summary.json]
  python3 codex_review.py reveal --order order.json --rationale "..."
  python3 codex_review.py check  --order order.json --blind blind.txt --final final.txt [--ledger L.jsonl]
  python3 codex_review.py         # self-test

order.json: {"symbol", "side": "buy"|"sell", "dollar_amount"?, "quantity"?,
             "action": "<score.py action or DISCRETIONARY>"}

check prints {gate, approved_dollar_amount, reasons, ...} and exits 0 on
PROCEED/REDUCE, 3 on SKIP, 2 on bad input. stdlib only. Python 3.9+.
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import Optional

SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "engine")
STANCES = ("buy", "sell", "hold", "avoid")
VERDICTS = ("concur", "reduce", "veto")

BLIND_SCHEMA = """{
  "stance": "buy" | "sell" | "hold" | "avoid",
  "confidence": <number 0..1>,
  "recomputed_action": "<action string from your own score.py run, or null>",
  "numbers_match": true | false | null,
  "data_issues": ["<stale bar, gap, too few bars, ...>"],
  "key_risks": ["<short phrases>"],
  "notes": "<= 3 sentences"
}"""

FINAL_SCHEMA = """{
  "verdict": "concur" | "reduce" | "veto",
  "confidence": <number 0..1>,
  "reasons": ["<short phrases>"],
  "invalidation": "<the price/condition that would prove the trade wrong>"
}"""


# --------------------------------------------------------------------------
# Prompts
# --------------------------------------------------------------------------

def _evidence_line(evidence_path: Optional[str], action: Optional[str]) -> str:
    """Backtest base rate for the action, from backtest/results/summary.json."""
    if not evidence_path or not os.path.exists(evidence_path):
        return "No backtest evidence supplied."
    with open(evidence_path) as f:
        ev = json.load(f)
    rows = [r for r in ev.get("events", []) if r["state"] == "flat" and (action is None or r["action"] == action)]
    rb = ev.get("random_baseline", {})
    out = [f"Backtest {ev['period'][0]}..{ev['period'][1]} on {ev['n_symbols']} symbols "
           "(forward return from next open minus the ticker's unconditional return):"]
    for r in rows:
        out.append(f"  - {r['action']}: n={r['n']}, excess 5d {r['excess_5d']*100:+.2f}%, "
                   f"10d {r['excess_10d']*100:+.2f}%, 20d {r['excess_20d']*100:+.2f}%")
    if rb:
        out.append(f"  - Whole strategy vs random entries: percentile {rb['percentile']*100:.0f} "
                   f"(mean trade {rb['strategy_mean_ret']*100:+.2f}% vs random {rb['random_mean_ret']*100:+.2f}%)")
    return "\n".join(out)


def blind_prompt(ticker: str, macro_score: int, holding: bool, evidence: Optional[str],
                 macro_file: Optional[str]) -> str:
    with open(ticker) as f:
        sym = json.load(f).get("symbol", "?")
    h = "true" if holding else "false"
    macro = (f"The macro pillar input is `{os.path.abspath(macro_file)}`; you may rerun "
             f"`python3 {SCRIPTS}/macro_pillar.py <that file> --json` to check pillar_score={macro_score}."
             if macro_file else f"The session's macro pillar_score is {macro_score} (computed by macro_pillar.py).")
    return f"""You are the second analyst on a short-term, long-only trading desk. Another analyst
has already looked at {sym}; you have NOT been told their conclusion, on purpose. Form your
own view. You have no broker access and must not try to trade or modify any file.

Inputs (read-only):
- Daily closes, oldest -> newest: `{os.path.abspath(ticker)}` (already cleaned: interpolated
  bars dropped, today's bar appended if the session traded).
- {macro}
- Position status: holding={h}.
- The desk's deterministic framework: `{SCRIPTS}/score.py` (three pillars + decision cascade),
  `{SCRIPTS}/indicators.py`. Read them; do not trust a summary of them.

Do this:
1. Run `python3 {SCRIPTS}/score.py {os.path.abspath(ticker)} --json --macro-score {macro_score} --holding {h}`
   and record the action it emits.
2. Sanity-check the data: number of bars, any implausible jumps (splits, bad prints), whether
   the last bar looks current. Report problems in data_issues.
3. Independently of the framework, judge the setup on the numbers: trend structure, momentum,
   how stretched price is, and what the framework does NOT look at (volatility regime, gap
   risk, a single-name vs ETF difference). You may compute anything else you like from the file.
4. Weigh the evidence on how this framework's signals have actually performed:
{_evidence_line(evidence, None)}

Reply with ONLY a ```json fenced block matching:
{BLIND_SCHEMA}"""


def reveal_prompt(order: dict, rationale: str, evidence: Optional[str]) -> str:
    size = (f"${order['dollar_amount']:,.2f}" if order.get("dollar_amount") is not None
            else f"{order.get('quantity')} shares")
    return f"""Now the other analyst's proposal. Review it as a risk manager whose job is to stop
bad trades, not to be agreeable. Agreeing with them gives no credit; you are accountable if you
pass a trade your own analysis argued against.

Proposed order: {order['side'].upper()} {order['symbol']}, {size}, market, gfd.
Framework action cited: {order.get('action', 'n/a')}.
Their written reasoning:
\"\"\"{rationale.strip()}\"\"\"

Backtest base rate for the cited action:
{_evidence_line(evidence, order.get('action'))}

Rules for your verdict:
- "veto" if the reasoning contradicts the data, relies on a number you could not reproduce,
  the data is unsound, or the edge is not there.
- "reduce" if the trade is defensible but the risk or the evidence does not justify full size.
- "concur" only if, after your own analysis in the previous turn, you would take this trade too.
You cannot increase the size or propose a different trade.

Reply with ONLY a ```json fenced block matching:
{FINAL_SCHEMA}"""


# --------------------------------------------------------------------------
# Parsing + policy
# --------------------------------------------------------------------------

def extract_json(text: str) -> Optional[dict]:
    """Last ```json block, else the last decodable top-level {...} in the text."""
    blocks = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    for b in reversed(blocks):
        try:
            v = json.loads(b)
            if isinstance(v, dict):
                return v
        except json.JSONDecodeError:
            continue
    dec = json.JSONDecoder()
    found, i = None, text.find("{")
    while i != -1:
        try:
            v, end = dec.raw_decode(text, i)
            if isinstance(v, dict):
                found = v
            i = text.find("{", end)  # skip nested objects: keep top-level only
        except json.JSONDecodeError:
            i = text.find("{", i + 1)
    return found


def _conf(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if 0.0 <= f <= 1.0 else None


def gate(order: dict, blind: Optional[dict], final: Optional[dict]) -> dict:
    side = order.get("side")
    if side not in ("buy", "sell"):
        raise ValueError(f"order.side must be buy/sell, got {side!r}")
    proposed = order.get("dollar_amount")
    reasons: list[str] = []

    verdict = final.get("verdict") if isinstance(final, dict) else None
    if verdict not in VERDICTS:
        verdict = None
    conf = _conf(final.get("confidence")) if isinstance(final, dict) else None
    stance = blind.get("stance") if isinstance(blind, dict) else None
    if stance not in STANCES:
        stance = None

    if side == "sell":
        g = "PROCEED"
        if verdict == "veto":
            reasons.append("ADVISORY: Codex vetoed this exit; exits are not gated — surface it in the report")
        elif verdict is None:
            reasons.append("ADVISORY: no parseable Codex verdict on this exit")
        return {"gate": g, "approved_dollar_amount": proposed, "reasons": reasons,
                "verdict": verdict, "blind_stance": stance, "confidence": conf}

    if proposed is None:
        raise ValueError("buys must be sized in dollar_amount (Mandate)")
    if verdict is None:
        g, approved = "SKIP", 0.0
        reasons.append("no parseable Codex verdict — fail closed")
    elif verdict == "veto":
        g, approved = "SKIP", 0.0
        reasons.append("Codex vetoed")
    else:
        g, approved = "PROCEED", float(proposed)
        if verdict == "reduce":
            g = "REDUCE"
            reasons.append("Codex asked to reduce")
        if stance in ("avoid", "sell"):
            g = "REDUCE"
            reasons.append(f"blind stance was '{stance}' before seeing the proposal")
        if isinstance(blind, dict) and blind.get("numbers_match") is False:
            g = "REDUCE"
            reasons.append("Codex could not reproduce the scorecard")
        if conf is None or conf < 0.5:
            g = "REDUCE"
            reasons.append(f"low/missing confidence ({conf})")
        if g == "REDUCE":
            approved = round(float(proposed) * 0.5, 2)
    if isinstance(final, dict) and final.get("reasons"):
        reasons.extend(f"codex: {r}" for r in final["reasons"][:5])
    return {"gate": g, "approved_dollar_amount": approved, "reasons": reasons,
            "verdict": verdict, "blind_stance": stance, "confidence": conf,
            "invalidation": final.get("invalidation") if isinstance(final, dict) else None}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _read(p: str) -> str:
    with open(p) as f:
        return f.read()


def main() -> int:
    ap = argparse.ArgumentParser(description="Codex second-opinion gate for proposed orders.")
    sub = ap.add_subparsers(dest="cmd")
    b = sub.add_parser("blind")
    b.add_argument("--ticker", required=True)
    b.add_argument("--macro-score", type=int, required=True)
    b.add_argument("--holding", required=True, choices=("true", "false"))
    b.add_argument("--macro-file")
    b.add_argument("--evidence")
    r = sub.add_parser("reveal")
    r.add_argument("--order", required=True)
    r.add_argument("--rationale", required=True, help="Text, or @file to read it from a file.")
    r.add_argument("--evidence")
    c = sub.add_parser("check")
    c.add_argument("--order", required=True)
    c.add_argument("--blind", required=True, help="File with Codex's blind-stage reply.")
    c.add_argument("--final", required=True, help="File with Codex's reveal-stage reply.")
    c.add_argument("--ledger", help="Append the decision to this JSONL file.")
    a = ap.parse_args()

    if a.cmd is None:
        failures = _self_test()
        for msg in failures:
            print(f"SELF-TEST FAIL: {msg}", file=sys.stderr)
        if not failures:
            print("self-test OK", file=sys.stderr)
        return 1 if failures else 0
    if a.cmd == "blind":
        print(blind_prompt(a.ticker, a.macro_score, a.holding == "true", a.evidence, a.macro_file))
        return 0
    order = json.loads(_read(a.order))
    if a.cmd == "reveal":
        rationale = _read(a.rationale[1:]) if a.rationale.startswith("@") else a.rationale
        print(reveal_prompt(order, rationale, a.evidence))
        return 0
    try:
        blind = extract_json(_read(a.blind))
        final = extract_json(_read(a.final))
        res = gate(order, blind, final)
    except (ValueError, OSError) as e:
        print(f"input error: {e}", file=sys.stderr)
        return 2
    res["order"] = order
    if a.ledger:
        os.makedirs(os.path.dirname(os.path.abspath(a.ledger)), exist_ok=True)
        with open(a.ledger, "a") as f:
            f.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                **res, "blind": blind, "final": final}) + "\n")
    print(json.dumps(res, indent=1))
    return 3 if res["gate"] == "SKIP" else 0


def _self_test() -> list[str]:
    buy = {"symbol": "X", "side": "buy", "dollar_amount": 1000, "action": "RE-ENTRY (new cycle)"}
    sell = {"symbol": "X", "side": "sell", "quantity": 2, "action": "EXIT"}
    ok_blind = {"stance": "buy", "confidence": 0.7, "numbers_match": True}
    cases = [
        ("concur", buy, ok_blind, {"verdict": "concur", "confidence": 0.8}, "PROCEED", 1000),
        ("reduce", buy, ok_blind, {"verdict": "reduce", "confidence": 0.8}, "REDUCE", 500),
        ("veto", buy, ok_blind, {"verdict": "veto", "confidence": 0.9}, "SKIP", 0),
        ("no reply", buy, None, None, "SKIP", 0),
        ("bad verdict", buy, ok_blind, {"verdict": "strong buy", "confidence": 1}, "SKIP", 0),
        ("anchoring", buy, {"stance": "avoid", "confidence": 0.8}, {"verdict": "concur", "confidence": 0.9}, "REDUCE", 500),
        ("mismatch", buy, {**ok_blind, "numbers_match": False}, {"verdict": "concur", "confidence": 0.9}, "REDUCE", 500),
        ("low conf", buy, ok_blind, {"verdict": "concur", "confidence": 0.3}, "REDUCE", 500),
        ("sell veto", sell, ok_blind, {"verdict": "veto"}, "PROCEED", None),
    ]
    failures = []
    for name, order, bl, fi, want_gate, want_amt in cases:
        g = gate(order, bl, fi)
        if g["gate"] != want_gate or g["approved_dollar_amount"] != want_amt:
            failures.append(f"{name}: got {g['gate']} {g['approved_dollar_amount']}, want {want_gate} {want_amt}")
        if order["side"] == "buy" and (g["approved_dollar_amount"] or 0) > order["dollar_amount"]:
            failures.append(f"{name}: approved more than proposed")
    reply = 'Thinking...\n```json\n{"verdict": "veto", "confidence": 0.9}\n```\nignored {"verdict": "concur"}'
    if (extract_json(reply) or {}).get("verdict") != "veto":
        failures.append("extract_json did not prefer the fenced block")
    if (extract_json('text {"a": {"b": 1}} more') or {}).get("a") != {"b": 1}:
        failures.append("extract_json bare object")
    try:
        gate({"symbol": "X", "side": "buy", "quantity": 3}, ok_blind, {"verdict": "concur"})
        failures.append("gate accepted a quantity-sized buy")
    except ValueError:
        pass
    return failures


if __name__ == "__main__":
    raise SystemExit(main())
