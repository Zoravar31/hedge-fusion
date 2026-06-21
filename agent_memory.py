"""
HedgeFusion Agent Memory
==========================
Gives agents persistent memory between pipeline runs.

Without memory:
  Every run starts from zero. The Research Manager doesn't know
  it said SELL on ICICIBANK 3 times in a row last week.

With memory:
  - Tracks signal history per ticker
  - Computes consecutive signal streak
  - Tracks historical accuracy (was the agent right?)
  - Feeds context into Research Manager prompt
  - Detects regime changes (signal flip after streak)

Memory file: data/memory/{TICKER}.json

Usage:
    from agent_memory import load_memory, save_memory, get_memory_context
    
    # Before pipeline run:
    ctx = get_memory_context("ICICIBANK")
    # Inject ctx into Research Manager prompt
    
    # After pipeline run:
    save_memory("ICICIBANK", recommendation="SELL", confidence="High", rr="1:2.5")
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

from loguru import logger

ROOT     = Path(__file__).parent
MEM_DIR  = ROOT / "data" / "memory"
MEM_DIR.mkdir(parents=True, exist_ok=True)


# ── Memory schema ─────────────────────────────────────────────

def _default_memory(ticker: str) -> dict:
    return {
        "ticker":               ticker,
        "created_at":           datetime.now().isoformat(),
        "last_updated":         None,
        "total_runs":           0,
        "signal_history":       [],   # last 10 runs
        "consecutive_signal":   0,    # how many runs in a row with same rec
        "consecutive_direction": None, # BUY / SELL / HOLD
        "accuracy": {
            "total_evaluated":  0,    # trades where outcome is known
            "correct":          0,    # AI called direction right
            "win_rate":         0.0,
        },
        "regime": {
            "current":          None, # BULLISH / BEARISH / NEUTRAL
            "changed_at":       None,
            "flips":            0,    # times signal direction changed
        },
        "price_context": {
            "last_known_ltp":   None,
            "52w_high":         None,
            "52w_low":          None,
        }
    }


# ── Load / save ───────────────────────────────────────────────

def load_memory(ticker: str) -> dict:
    """Load memory for a ticker. Returns default if no memory exists."""
    path = MEM_DIR / f"{ticker.upper()}.json"
    if path.exists():
        try:
            mem = json.loads(path.read_text(encoding="utf-8"))
            # Ensure all keys exist (backward compat)
            default = _default_memory(ticker)
            for k, v in default.items():
                if k not in mem:
                    mem[k] = v
            return mem
        except Exception as e:
            logger.warning("Memory read failed {}: {}", ticker, e)
    return _default_memory(ticker)


def save_memory(
    ticker:         str,
    recommendation: str,
    confidence:     str  = "",
    risk_reward:    str  = "",
    stop_loss:      float | None = None,
    target1:        float | None = None,
    pm_decision:    str  = "",
    ltp:            float | None = None,
) -> dict:
    """
    Save the result of a pipeline run to memory.
    Call this at the end of every run_pipeline() call.
    """
    mem  = load_memory(ticker)
    now  = datetime.now().isoformat()
    rec  = recommendation.upper().strip()

    # Add to signal history (keep last 10)
    entry = {
        "timestamp":     now,
        "recommendation":rec,
        "confidence":    confidence,
        "risk_reward":   risk_reward,
        "stop_loss":     stop_loss,
        "target1":       target1,
        "pm_decision":   pm_decision,
        "ltp":           ltp,
        "outcome":       None,   # filled by feedback_engine later
    }
    mem["signal_history"] = ([entry] + mem["signal_history"])[:10]

    # Update consecutive signal streak
    if rec in ("BUY","SELL","HOLD"):
        if rec == mem["consecutive_direction"]:
            mem["consecutive_signal"] += 1
        else:
            # Signal flipped
            if mem["consecutive_direction"] is not None:
                mem["regime"]["flips"] += 1
                mem["regime"]["changed_at"] = now
            mem["consecutive_signal"]   = 1
            mem["consecutive_direction"] = rec

    # Update regime
    if rec == "BUY":
        mem["regime"]["current"] = "BULLISH"
    elif rec == "SELL":
        mem["regime"]["current"] = "BEARISH"
    else:
        mem["regime"]["current"] = "NEUTRAL"

    # Update price context
    if ltp:
        mem["price_context"]["last_known_ltp"] = ltp

    mem["total_runs"]   += 1
    mem["last_updated"]  = now

    path = MEM_DIR / f"{ticker.upper()}.json"
    path.write_text(json.dumps(mem, indent=2, default=str), encoding="utf-8")
    logger.debug("Memory saved: {} → {} (streak: {})", ticker, rec, mem["consecutive_signal"])
    return mem


def update_outcome(ticker: str, outcome: str, pnl_pct: float = 0) -> None:
    """
    Called by feedback_engine when a trade closes.
    outcome: 'WIN' or 'LOSS' or 'STOPPED_OUT'
    """
    mem = load_memory(ticker)
    mem["accuracy"]["total_evaluated"] += 1
    if outcome == "WIN":
        mem["accuracy"]["correct"] += 1
    total = mem["accuracy"]["total_evaluated"]
    if total > 0:
        mem["accuracy"]["win_rate"] = round(mem["accuracy"]["correct"] / total * 100, 1)

    # Mark the most recent evaluated signal
    for entry in mem["signal_history"]:
        if entry.get("outcome") is None:
            entry["outcome"]  = outcome
            entry["pnl_pct"]  = pnl_pct
            break

    path = MEM_DIR / f"{ticker.upper()}.json"
    path.write_text(json.dumps(mem, indent=2, default=str), encoding="utf-8")


# ── Memory context for prompts ────────────────────────────────

def get_memory_context(ticker: str) -> str:
    """
    Returns a formatted string to inject into the Research Manager prompt.
    This gives the agent historical context about this stock.
    """
    mem = load_memory(ticker)

    if mem["total_runs"] == 0:
        return f"No prior analysis history for {ticker}. This is the first run."

    lines = [f"PRIOR ANALYSIS HISTORY FOR {ticker}:"]

    # Streak info
    streak = mem["consecutive_signal"]
    direc  = mem["consecutive_direction"]
    if streak >= 2:
        lines.append(
            f"  ⚠️ STREAK: {streak} consecutive {direc} signals in a row. "
            f"{'High conviction — trend confirming.' if streak >= 3 else 'Developing pattern.'}"
        )

    # Regime
    regime = mem["regime"]["current"]
    flips  = mem["regime"]["flips"]
    if regime:
        lines.append(f"  Current regime: {regime} ({flips} regime changes in history)")

    # Historical accuracy for this stock
    acc = mem["accuracy"]
    if acc["total_evaluated"] >= 3:
        lines.append(
            f"  Agent accuracy on {ticker}: {acc['win_rate']:.0f}% "
            f"({acc['correct']}/{acc['total_evaluated']} correct calls)"
        )

    # Last 3 signals
    hist = mem["signal_history"][:3]
    if hist:
        lines.append("  Recent signals:")
        for h in hist:
            ts    = h.get("timestamp","")[:10]
            rec   = h.get("recommendation","?")
            conf  = h.get("confidence","?")
            pm    = h.get("pm_decision","?")
            out   = h.get("outcome")
            out_s = f" → {out}" if out else ""
            lines.append(f"    {ts}: {rec} ({conf} confidence) PM:{pm}{out_s}")

    # Price context
    ltp = mem["price_context"].get("last_known_ltp")
    if ltp:
        lines.append(f"  Last known LTP: ₹{ltp:,.2f}")

    lines.append("")
    return "\n".join(lines)


def get_all_memory_summary() -> list[dict]:
    """Get summary of all ticker memories for the dashboard."""
    summaries = []
    for path in sorted(MEM_DIR.glob("*.json")):
        try:
            mem = json.loads(path.read_text())
            summaries.append({
                "ticker":      mem.get("ticker",""),
                "total_runs":  mem.get("total_runs",0),
                "streak":      mem.get("consecutive_signal",0),
                "direction":   mem.get("consecutive_direction",""),
                "regime":      mem.get("regime",{}).get("current",""),
                "win_rate":    mem.get("accuracy",{}).get("win_rate",0),
                "evaluated":   mem.get("accuracy",{}).get("total_evaluated",0),
                "last_signal": mem.get("signal_history",[{}])[0].get("recommendation","") if mem.get("signal_history") else "",
                "last_run":    mem.get("last_updated","")[:10] if mem.get("last_updated") else "",
                "flips":       mem.get("regime",{}).get("flips",0),
            })
        except Exception:
            pass
    return sorted(summaries, key=lambda x: x["total_runs"], reverse=True)


def print_memory_report():
    """Print all agent memories to terminal."""
    summaries = get_all_memory_summary()
    if not summaries:
        print("No agent memory yet. Run python hf.py run TICKER first.")
        return
    sep = "━" * 60
    print(f"\n{sep}")
    print(f"  AGENT MEMORY REPORT")
    print(f"  {datetime.now().strftime('%d %b %Y %H:%M')}")
    print(f"{sep}")
    print(f"\n  {'Ticker':<14} {'Runs':>5} {'Signal':>6} {'Streak':>7} {'Win Rate':>9} {'Regime'}")
    print(f"  {'─'*56}")
    for s in summaries:
        wr = f"{s['win_rate']:.0f}%" if s["evaluated"] >= 3 else "N/A"
        streak_str = f"{s['streak']}× {s['direction']}" if s["direction"] else "—"
        print(f"  {s['ticker']:<14} {s['total_runs']:>5} {s['last_signal']:>6} "
              f"{streak_str:>7} {wr:>9} {s['regime']}")
    print(f"\n{sep}\n")


if __name__ == "__main__":
    print_memory_report()
