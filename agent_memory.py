"""
HedgeFusion Agent Memory
===========================
Per-ticker persistent memory so agents don't start from zero every run.

Without memory: every pipeline run treats a stock as if it's the first
time HedgeFusion has ever looked at it — no awareness that you already
analysed HDFCBANK three times this month, or that the Research Manager
flagged a specific risk last time that's worth re-checking.

With memory: each pipeline run's key verdict is stored to
data/agent_memory.json, keyed by ticker. Future pipeline runs can pull
the last N verdicts for that ticker and feed them to agents as context
("last time we analysed this stock, here's what we concluded and what
happened since").

Storage format (data/agent_memory.json):
{
  "RELIANCE": {
    "history": [
      {
        "date": "2026-06-15T09:30:00",
        "recommendation": "BUY",
        "pm_decision": "APPROVE",
        "confidence": "HIGH",
        "entry_zone": "1270-1285",
        "stop_loss": 1220,
        "target1": 1380,
        "key_thesis": "...",
        "price_at_analysis": 1278.50
      },
      ...
    ],
    "last_updated": "2026-06-15T09:30:00"
  }
}

Usage:
    python agent_memory.py --ticker RELIANCE          # show history
    python agent_memory.py --ticker RELIANCE --clear  # wipe history for one ticker
    python agent_memory.py --stats                    # memory-wide stats
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger

DATA_DIR    = Path(__file__).parent / "data"
MEMORY_FILE = DATA_DIR / "agent_memory.json"
DATA_DIR.mkdir(exist_ok=True)

MAX_HISTORY_PER_TICKER = 10  # keep last 10 verdicts per stock, prune older


# ──────────────────────────────────────────────
# Storage
# ──────────────────────────────────────────────

def _load_all() -> dict:
    if MEMORY_FILE.exists():
        try:
            return json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("agent_memory: corrupt memory file, starting fresh: {}", e)
            return {}
    return {}


def _save_all(data: dict) -> None:
    MEMORY_FILE.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


# ──────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────

def record_verdict(ticker: str, pipeline_state: dict) -> None:
    """
    Extract the key verdict from a completed pipeline run and append
    it to that ticker's memory. Called automatically at the end of
    run_pipeline() — see pipeline.py integration.

    Only stores the essentials (not full agent transcripts) to keep
    the memory file small and the extracted context cheap to re-inject.
    """
    ticker = ticker.strip().upper()
    all_mem = _load_all()

    rv = pipeline_state.get("research_verdict", {})
    pm = pipeline_state.get("pm_decision", {})
    ex = pipeline_state.get("execution_result") or {}

    entry = {
        "date":               pipeline_state.get("completed_at", datetime.now().isoformat()),
        "recommendation":     rv.get("recommendation", "?"),
        "pm_decision":        pm.get("decision", "?"),
        "confidence":         rv.get("confidence", "?"),
        "entry_zone":         rv.get("entry_zone", ""),
        "stop_loss":          rv.get("stop_loss", ""),
        "target1":            rv.get("target1", ""),
        "risk_reward":        rv.get("risk_reward", ""),
        "key_thesis":         (rv.get("debate_verdict") or "")[:300],
        "pm_note":            (pm.get("pm_note") or "")[:200],
        "order_status":       ex.get("order_id") or ex.get("status", ""),
        "elapsed_seconds":    pipeline_state.get("elapsed_seconds", 0),
    }

    if ticker not in all_mem:
        all_mem[ticker] = {"history": [], "last_updated": None}

    all_mem[ticker]["history"].append(entry)
    # Keep only the most recent N entries
    all_mem[ticker]["history"] = all_mem[ticker]["history"][-MAX_HISTORY_PER_TICKER:]
    all_mem[ticker]["last_updated"] = entry["date"]

    _save_all(all_mem)
    logger.info("agent_memory: recorded verdict for {} ({} total)", ticker, len(all_mem[ticker]["history"]))


def get_memory(ticker: str, last_n: int = 3) -> list:
    """Return the last N verdicts for a ticker, most recent first."""
    ticker = ticker.strip().upper()
    all_mem = _load_all()
    history = all_mem.get(ticker, {}).get("history", [])
    return list(reversed(history))[:last_n]


def summarize_memory(ticker: str, last_n: int = 3) -> str:
    """
    Build a short text summary of a ticker's recent verdict history,
    formatted for injection into an agent's user_message context.
    Returns empty string if no prior history exists.
    """
    history = get_memory(ticker, last_n)
    if not history:
        return ""

    lines = [f"PRIOR HEDGEFUSION ANALYSES OF {ticker.upper()} (most recent first):"]
    for i, h in enumerate(history, 1):
        date_short = h.get("date", "")[:10]
        lines.append(
            f"  {i}. [{date_short}] {h.get('recommendation','?')} "
            f"(PM: {h.get('pm_decision','?')}, confidence: {h.get('confidence','?')}) "
            f"— entry {h.get('entry_zone','?')}, SL {h.get('stop_loss','?')}, "
            f"target {h.get('target1','?')}"
        )
        if h.get("key_thesis"):
            lines.append(f"     Thesis: {h['key_thesis'][:150]}")
    lines.append(
        "  Consider whether the prior thesis still holds, or whether new data "
        "changes the picture. Don't just repeat the last verdict without justification."
    )
    return "\n".join(lines)


def clear_memory(ticker: Optional[str] = None) -> None:
    """Clear memory for one ticker, or all tickers if ticker is None."""
    if ticker is None:
        _save_all({})
        print("✅ Cleared all agent memory.")
        return
    ticker = ticker.strip().upper()
    all_mem = _load_all()
    if ticker in all_mem:
        del all_mem[ticker]
        _save_all(all_mem)
        print(f"✅ Cleared memory for {ticker}.")
    else:
        print(f"No memory found for {ticker}.")


def memory_stats() -> dict:
    """Summary stats across the whole memory store."""
    all_mem = _load_all()
    total_verdicts = sum(len(v.get("history", [])) for v in all_mem.values())
    most_analysed  = sorted(
        all_mem.items(), key=lambda kv: len(kv[1].get("history", [])), reverse=True
    )[:5]

    return {
        "tickers_tracked":  len(all_mem),
        "total_verdicts":   total_verdicts,
        "most_analysed": [
            {"ticker": t, "runs": len(v.get("history", []))}
            for t, v in most_analysed
        ],
    }


# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────

def _print_history(ticker: str):
    history = get_memory(ticker, last_n=MAX_HISTORY_PER_TICKER)
    if not history:
        print(f"\n  No memory found for {ticker.upper()}.\n")
        return

    print(f"\n{'━'*60}")
    print(f"  AGENT MEMORY — {ticker.upper()}")
    print(f"  {len(history)} prior analyses")
    print(f"{'━'*60}\n")
    for i, h in enumerate(history, 1):
        print(f"  [{i}] {h.get('date','')[:16]}")
        print(f"      Rec: {h.get('recommendation','?'):<6} PM: {h.get('pm_decision','?'):<8} "
              f"Confidence: {h.get('confidence','?')}")
        print(f"      Entry: {h.get('entry_zone','?')}  SL: {h.get('stop_loss','?')}  "
              f"Target: {h.get('target1','?')}  R:R: {h.get('risk_reward','?')}")
        if h.get("key_thesis"):
            print(f"      Thesis: {h['key_thesis'][:120]}")
        if h.get("order_status"):
            print(f"      Order: {h['order_status']}")
        print()
    print(f"{'━'*60}\n")


def _print_stats():
    stats = memory_stats()
    print(f"\n{'━'*55}")
    print(f"  AGENT MEMORY — SYSTEM STATS")
    print(f"{'━'*55}")
    print(f"  Tickers tracked:   {stats['tickers_tracked']}")
    print(f"  Total verdicts:    {stats['total_verdicts']}")
    if stats["most_analysed"]:
        print(f"\n  Most analysed:")
        for m in stats["most_analysed"]:
            print(f"    {m['ticker']:<14} {m['runs']} runs")
    print(f"{'━'*55}\n")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="HedgeFusion Agent Memory")
    parser.add_argument("--ticker", help="Show memory for one ticker")
    parser.add_argument("--clear",  action="store_true", help="Clear memory (for --ticker, or all if omitted)")
    parser.add_argument("--stats",  action="store_true", help="Show memory-wide stats")
    args = parser.parse_args()

    if args.clear:
        clear_memory(args.ticker)
    elif args.stats:
        _print_stats()
    elif args.ticker:
        _print_history(args.ticker)
    else:
        _print_stats()


if __name__ == "__main__":
    main()
