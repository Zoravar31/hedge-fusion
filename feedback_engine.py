"""
HedgeFusion Feedback Engine
==============================
Tracks what happened after each AI recommendation.

After the pipeline says SELL on ICICIBANK at ₹1,358:
  - Was the agent right? Did the price go down?
  - Did it hit the target? Or did it hit the stop loss?
  - What was the actual return?

This feedback is stored and fed back into:
  1. Agent memory (win_rate per ticker)
  2. The multibagger screener (adjust scoring for stocks AI is accurate on)
  3. The Research Manager prompt (context: "AI has been 70% accurate on ICICIBANK")
  4. The dashboard (show actual vs predicted performance)

Evaluation logic:
  For SELL signals:
    WIN  = price fell below target within holding period
    LOSS = price rose above stop loss
    OPEN = neither hit yet (still in holding period)

  For BUY signals:
    WIN  = price rose above target within holding period
    LOSS = price fell below stop loss
    OPEN = neither hit yet

  Holding period: 30 trading days (configurable)

Usage:
    python feedback_engine.py              # evaluate all open signals
    python feedback_engine.py --report     # show accuracy report
    python feedback_engine.py --ticker ICICIBANK  # one stock
"""

import csv
import json
from datetime import datetime, timedelta
from pathlib import Path

import yfinance as yf
from dotenv import load_dotenv
from loguru import logger

load_dotenv(Path(__file__).parent / ".env")

ROOT       = Path(__file__).parent
DATA_DIR   = ROOT / "data"
FB_DIR     = DATA_DIR / "feedback"
MEM_DIR    = DATA_DIR / "memory"
OUTPUT_DIR = ROOT / "outputs"
LOG_DIR    = ROOT / "logs"
PAPER_LOG  = LOG_DIR / "paper_trades.csv"

FB_DIR.mkdir(parents=True, exist_ok=True)
OUTCOMES_FILE = FB_DIR / "outcomes.json"
HOLDING_DAYS  = 30   # evaluate after 30 trading days


# ── Load / save outcomes ──────────────────────────────────────

def load_outcomes() -> dict:
    if OUTCOMES_FILE.exists():
        try:
            return json.loads(OUTCOMES_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_outcomes(outcomes: dict) -> None:
    OUTCOMES_FILE.write_text(
        json.dumps(outcomes, indent=2, default=str), encoding="utf-8"
    )


# ── Price fetcher ─────────────────────────────────────────────

def get_price_at(ticker: str, target_date: datetime) -> float | None:
    """Get the closing price of a stock on or after a given date."""
    try:
        sym  = ticker.upper() + ".NS"
        t    = yf.Ticker(sym)
        start= target_date - timedelta(days=3)
        end  = target_date + timedelta(days=5)
        hist = t.history(start=start.strftime("%Y-%m-%d"),
                         end=end.strftime("%Y-%m-%d"), interval="1d")
        if hist is None or hist.empty:
            return None
        return float(hist["Close"].iloc[0])
    except Exception:
        return None


def get_current_price(ticker: str) -> float | None:
    """Get current LTP."""
    try:
        sym  = ticker.upper() + ".NS"
        hist = yf.Ticker(sym).history(period="2d", interval="1d")
        if hist is None or hist.empty:
            return None
        return float(hist["Close"].iloc[-1])
    except Exception:
        return None


def get_price_range(ticker: str, start: datetime, end: datetime) -> dict:
    """Get high/low/close range between two dates."""
    try:
        sym  = ticker.upper() + ".NS"
        hist = yf.Ticker(sym).history(
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            interval="1d",
        )
        if hist is None or hist.empty:
            return {}
        return {
            "high":  float(hist["High"].max()),
            "low":   float(hist["Low"].min()),
            "close": float(hist["Close"].iloc[-1]),
            "days":  len(hist),
        }
    except Exception:
        return {}


# ── Read signals from pipeline outputs ───────────────────────

def read_pipeline_signals() -> list[dict]:
    """Read all pipeline JSON outputs and extract signal data."""
    signals = []
    for path in OUTPUT_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or "ticker" not in data:
                continue
            rv  = data.get("research_verdict") or {}
            pm  = data.get("pm_decision") or {}
            ex  = data.get("execution_result") or {}
            rec = rv.get("recommendation","")
            if rec not in ("BUY","SELL"):
                continue
            mtime = datetime.fromtimestamp(path.stat().st_mtime)
            signals.append({
                "id":            path.stem,
                "ticker":        data.get("ticker",""),
                "run_date":      mtime,
                "recommendation":rec,
                "confidence":    rv.get("confidence",""),
                "entry_price":   ex.get("fill_price") or rv.get("entry_price"),
                "stop_loss":     rv.get("stop_loss"),
                "target1":       rv.get("target1"),
                "risk_reward":   rv.get("risk_reward",""),
                "pm_decision":   pm.get("decision",""),
                "order_id":      ex.get("order_id",""),
            })
        except Exception:
            pass
    signals.sort(key=lambda x: x["run_date"])
    return signals


# ── Evaluate a single signal ──────────────────────────────────

def evaluate_signal(signal: dict, outcomes: dict) -> dict | None:
    """
    Evaluate whether a signal was correct.
    Returns outcome dict or None if still open / insufficient data.
    """
    sig_id = signal["id"]
    if sig_id in outcomes and outcomes[sig_id].get("outcome") not in (None, "OPEN"):
        return outcomes[sig_id]   # already evaluated

    ticker   = signal["ticker"]
    rec      = signal["recommendation"]
    run_date = signal["run_date"]
    entry    = signal.get("entry_price")
    sl       = signal.get("stop_loss")
    tgt      = signal.get("target1")

    if not entry or not sl or not tgt:
        return None

    entry = float(entry)
    sl    = float(sl)
    tgt   = float(tgt)

    # Holding period end
    eval_date = run_date + timedelta(days=HOLDING_DAYS * 1.4)  # ~30 trading days
    now       = datetime.now()

    if now < run_date + timedelta(days=3):
        # Too early to evaluate
        return {"outcome": "OPEN", "signal_id": sig_id, "ticker": ticker}

    # Get price range from signal date to eval date
    end_dt   = min(eval_date, now)
    pr       = get_price_range(ticker, run_date, end_dt)
    if not pr:
        return None

    high  = pr.get("high", 0)
    low   = pr.get("low",  999999)
    close = pr.get("close", entry)
    days  = pr.get("days", 0)

    # Still within holding period and neither SL nor target hit
    if now < eval_date:
        # Check if SL or target already hit
        if rec == "BUY":
            if low <= sl:
                outcome = "STOPPED_OUT"
                pnl_pct = (sl - entry) / entry * 100
            elif high >= tgt:
                outcome = "WIN"
                pnl_pct = (tgt - entry) / entry * 100
            else:
                return {"outcome": "OPEN", "signal_id": sig_id, "ticker": ticker,
                        "days_held": days, "current_pnl": (close - entry)/entry*100}
        else:  # SELL
            if high >= sl:
                outcome = "STOPPED_OUT"
                pnl_pct = (entry - sl) / entry * 100  # loss for short
            elif low <= tgt:
                outcome = "WIN"
                pnl_pct = (entry - tgt) / entry * 100
            else:
                return {"outcome": "OPEN", "signal_id": sig_id, "ticker": ticker,
                        "days_held": days, "current_pnl": (entry - close)/entry*100}
    else:
        # Holding period over — evaluate based on close
        if rec == "BUY":
            pnl_pct = (close - entry) / entry * 100
            outcome = "WIN" if close >= tgt else "LOSS" if close <= sl else ("WIN" if pnl_pct > 0 else "LOSS")
        else:
            pnl_pct = (entry - close) / entry * 100
            outcome = "WIN" if close <= tgt else "LOSS" if close >= sl else ("WIN" if pnl_pct > 0 else "LOSS")

    result = {
        "signal_id":      sig_id,
        "ticker":         ticker,
        "recommendation": rec,
        "confidence":     signal.get("confidence",""),
        "risk_reward":    signal.get("risk_reward",""),
        "run_date":       run_date.isoformat(),
        "entry_price":    entry,
        "stop_loss":      sl,
        "target1":        tgt,
        "high_in_period": high,
        "low_in_period":  low,
        "close_at_eval":  close,
        "days_held":      days,
        "outcome":        outcome,
        "pnl_pct":        round(pnl_pct, 2),
        "evaluated_at":   now.isoformat(),
    }
    return result


# ── Accuracy report ───────────────────────────────────────────

def compute_accuracy(outcomes: dict) -> dict:
    """Compute overall and per-ticker accuracy from all outcomes."""
    closed = [v for v in outcomes.values()
              if isinstance(v, dict) and v.get("outcome") not in (None,"OPEN")]

    if not closed:
        return {"total":0,"wins":0,"losses":0,"stopped":0,"win_rate":0,"avg_pnl":0}

    wins    = [o for o in closed if o["outcome"] == "WIN"]
    losses  = [o for o in closed if o["outcome"] == "LOSS"]
    stopped = [o for o in closed if o["outcome"] == "STOPPED_OUT"]
    total   = len(closed)
    win_rate= len(wins) / total * 100
    avg_pnl = sum(o.get("pnl_pct",0) for o in closed) / total

    # Per-ticker breakdown
    by_ticker = {}
    for o in closed:
        t = o.get("ticker","")
        if t not in by_ticker:
            by_ticker[t] = {"total":0,"wins":0,"avg_pnl":0,"pnl_sum":0}
        by_ticker[t]["total"]   += 1
        by_ticker[t]["pnl_sum"] += o.get("pnl_pct",0)
        if o["outcome"] == "WIN":
            by_ticker[t]["wins"] += 1
    for t in by_ticker:
        b = by_ticker[t]
        b["win_rate"] = round(b["wins"]/b["total"]*100, 1)
        b["avg_pnl"]  = round(b["pnl_sum"]/b["total"], 2)
        del b["pnl_sum"]

    # By confidence
    by_conf = {}
    for o in closed:
        c = o.get("confidence","")
        if c not in by_conf:
            by_conf[c] = {"total":0,"wins":0}
        by_conf[c]["total"] += 1
        if o["outcome"] == "WIN":
            by_conf[c]["wins"] += 1
    for c in by_conf:
        b = by_conf[c]
        b["win_rate"] = round(b["wins"]/b["total"]*100,1)

    return {
        "total":        total,
        "wins":         len(wins),
        "losses":       len(losses),
        "stopped_out":  len(stopped),
        "win_rate":     round(win_rate, 1),
        "avg_pnl_pct":  round(avg_pnl, 2),
        "by_ticker":    by_ticker,
        "by_confidence":by_conf,
        "open_signals": len([v for v in outcomes.values()
                             if isinstance(v,dict) and v.get("outcome")=="OPEN"]),
        "computed_at":  datetime.now().isoformat(),
    }


def print_accuracy_report(acc: dict):
    sep = "━" * 60
    print(f"\n{sep}")
    print(f"  FEEDBACK ENGINE — ACCURACY REPORT")
    print(f"  {datetime.now().strftime('%d %b %Y %H:%M')}")
    print(sep)
    print(f"\n  Total evaluated: {acc['total']}")
    print(f"  Wins:            {acc['wins']}")
    print(f"  Losses:          {acc['losses']}")
    print(f"  Stopped out:     {acc['stopped_out']}")
    print(f"  Open signals:    {acc['open_signals']}")
    wr = acc['win_rate']
    print(f"  Win rate:        {wr:.1f}% {'✅ above target' if wr >= 55 else '⚠️ below 55% target'}")
    print(f"  Avg P&L:         {acc['avg_pnl_pct']:+.2f}%")

    if acc.get("by_ticker"):
        print(f"\n  PER-TICKER ACCURACY:")
        for ticker, stats in sorted(acc["by_ticker"].items(),
                                    key=lambda x: x[1]["win_rate"], reverse=True):
            print(f"    {ticker:<14} {stats['win_rate']:>5.1f}% win  "
                  f"avg P&L {stats['avg_pnl']:>+6.2f}%  "
                  f"({stats['wins']}/{stats['total']} calls)")

    if acc.get("by_confidence"):
        print(f"\n  BY CONFIDENCE LEVEL:")
        for conf, stats in sorted(acc["by_confidence"].items(),
                                  key=lambda x: x[1]["win_rate"], reverse=True):
            print(f"    {conf:<10} {stats['win_rate']:>5.1f}% win  ({stats['wins']}/{stats['total']})")

    print(f"\n{sep}\n")


# ── Main runner ───────────────────────────────────────────────

def run_feedback_engine(ticker: str | None = None) -> dict:
    """
    Evaluate all open signals and update outcomes + agent memory.
    """
    from agent_memory import update_outcome

    print(f"\nFeedback engine starting...")
    signals  = read_pipeline_signals()
    outcomes = load_outcomes()

    if ticker:
        signals = [s for s in signals if s["ticker"].upper() == ticker.upper()]

    print(f"  Signals to evaluate: {len(signals)}")
    newly_closed = 0

    for sig in signals:
        result = evaluate_signal(sig, outcomes)
        if result is None:
            continue

        sig_id  = sig["id"]
        outcome = result.get("outcome")

        if outcome and outcome != "OPEN":
            outcomes[sig_id] = result
            newly_closed += 1
            print(f"  {sig['ticker']:<12} {sig['recommendation']:>4} → {outcome:<12} "
                  f"P&L: {result.get('pnl_pct',0):+.2f}%")
            # Update agent memory
            update_outcome(sig["ticker"], outcome, result.get("pnl_pct",0))
        elif outcome == "OPEN" and sig_id not in outcomes:
            outcomes[sig_id] = result

    save_outcomes(outcomes)
    acc = compute_accuracy(outcomes)

    # Save accuracy to feedback dir
    acc_file = FB_DIR / "accuracy.json"
    acc_file.write_text(json.dumps(acc, indent=2, default=str), encoding="utf-8")

    print(f"\n  Newly closed: {newly_closed}")
    print(f"  Total evaluated: {acc['total']}")
    print(f"  Win rate: {acc['win_rate']:.1f}%")
    print(f"  Avg P&L:  {acc['avg_pnl_pct']:+.2f}%")

    return acc


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="HedgeFusion Feedback Engine")
    parser.add_argument("--ticker",  help="Evaluate one ticker only")
    parser.add_argument("--report",  action="store_true", help="Show accuracy report only")
    args = parser.parse_args()

    if args.report:
        outcomes = load_outcomes()
        acc      = compute_accuracy(outcomes)
        print_accuracy_report(acc)
    else:
        acc = run_feedback_engine(ticker=args.ticker)
        print_accuracy_report(acc)
