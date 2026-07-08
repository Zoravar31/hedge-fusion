"""
HedgeFusion Feedback Engine
==============================
Closes the loop: was the AI actually right?

The trade journal tells you WHAT happened (win rate, P&L).
The feedback engine tells you whether the AGENT'S CONFIDENCE was
CALIBRATED — did "HIGH confidence BUY" calls actually win more often
than "LOW confidence BUY" calls? If not, the confidence field is
decorative, not predictive, and that's a real problem worth knowing.

What it computes:

  1. OUTCOME MATCHING
     For every closed paper trade, finds the pipeline run that
     generated it (matched by ticker + nearest date) and records
     whether the trade won or lost.

  2. CONFIDENCE CALIBRATION
     Groups closed trades by the Research Manager's stated confidence
     (HIGH / MEDIUM / LOW) and computes actual win rate per bucket.
     Well-calibrated: HIGH confidence wins more than LOW confidence.
     Miscalibrated: no difference, or inverted — confidence is noise.

  3. RECOMMENDATION ACCURACY
     BUY calls that were followed → did they go up?
     SELL calls that were followed → did they go down?
     HOLD/VETO calls → did the stock avoid a large drawdown
       (i.e. was blocking it the right call)?

  4. VETO EFFECTIVENESS
     Of all Portfolio Manager VETOs, how many would have been losing
     trades if executed? (Estimated using price movement since the
     verdict date, even though no order was placed.)

Usage:
    python feedback_engine.py              # full calibration report
    python feedback_engine.py --since 180  # last 180 days

Output: terminal + outputs/feedback_YYYYMMDD.html
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

import yfinance as yf
from dotenv import load_dotenv
from loguru import logger

load_dotenv(Path(__file__).parent / ".env")

OUTPUT_DIR = Path(__file__).parent / "outputs"
OUTPUT_DIR.mkdir(exist_ok=True)


# ──────────────────────────────────────────────
# Data loading
# ──────────────────────────────────────────────

def _load_pipeline_outputs(since_days: int = 365) -> list:
    """Load all pipeline JSON outputs (same source as trade_journal)."""
    cutoff = datetime.now() - timedelta(days=since_days)
    outputs = []
    for p in OUTPUT_DIR.glob("*.json"):
        # Skip our own reports (analytics_, feedback_, portfolio_, watchlist_, etc.)
        if p.stem.split("_")[0].islower():
            continue
        try:
            mtime = datetime.fromtimestamp(p.stat().st_mtime)
            if mtime < cutoff:
                continue
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "ticker" in data and "research_verdict" in data:
                outputs.append(data)
        except Exception:
            pass
    return outputs


def _get_forward_return(ticker: str, from_date: datetime, days_forward: int = 30) -> float:
    """
    Actual price return for a ticker over N days following a verdict date.
    Used to check if BUY/SELL/HOLD calls were directionally correct,
    independent of whether a paper trade was actually placed.
    """
    symbol = ticker.upper()
    if not symbol.endswith(".NS"):
        symbol += ".NS"
    try:
        start = from_date - timedelta(days=3)
        end   = from_date + timedelta(days=days_forward + 3)
        hist  = yf.Ticker(symbol).history(
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            interval="1d",
        )
        if hist is None or hist.empty:
            return None

        hist.index = hist.index.tz_localize(None)
        # Price nearest to verdict date
        before = hist[hist.index <= from_date]
        after  = hist[hist.index >= from_date + timedelta(days=days_forward)]
        if before.empty:
            return None

        start_px = float(before["Close"].iloc[-1])
        end_px   = float(after["Close"].iloc[0]) if not after.empty else float(hist["Close"].iloc[-1])

        return round((end_px - start_px) / start_px * 100, 2)
    except Exception as e:
        logger.warning("_get_forward_return({}) failed: {}", ticker, e)
        return None


# ──────────────────────────────────────────────
# Calibration analysis
# ──────────────────────────────────────────────

def compute_confidence_calibration(pipeline_outputs: list, forward_days: int = 30) -> dict:
    """
    For each pipeline run with a BUY/SELL recommendation, check the
    actual forward return and bucket by stated confidence level.
    """
    buckets = {"HIGH": [], "MEDIUM": [], "LOW": []}

    for state in pipeline_outputs:
        rv = state.get("research_verdict", {})
        rec = rv.get("recommendation", "")
        conf = str(rv.get("confidence", "")).upper()
        if rec not in ("BUY", "SELL") or conf not in buckets:
            continue

        try:
            verdict_date = datetime.fromisoformat(state.get("completed_at", ""))
        except Exception:
            continue

        # Don't evaluate verdicts too recent to have forward data
        if (datetime.now() - verdict_date).days < forward_days:
            continue

        fwd_ret = _get_forward_return(state["ticker"], verdict_date, forward_days)
        if fwd_ret is None:
            continue

        # For a BUY: correct if price went up. For a SELL: correct if price went down.
        correct = (fwd_ret > 0) if rec == "BUY" else (fwd_ret < 0)

        buckets[conf].append({
            "ticker": state["ticker"],
            "recommendation": rec,
            "forward_return_pct": fwd_ret,
            "correct": correct,
        })

    calibration = {}
    for level, results in buckets.items():
        if not results:
            calibration[level] = {"n": 0, "hit_rate_pct": None, "avg_return_pct": None}
            continue
        hits = sum(1 for r in results if r["correct"])
        calibration[level] = {
            "n": len(results),
            "hit_rate_pct": round(hits / len(results) * 100, 1),
            "avg_return_pct": round(sum(r["forward_return_pct"] for r in results) / len(results), 2),
            "detail": results,
        }

    return calibration


def compute_veto_effectiveness(pipeline_outputs: list, forward_days: int = 30) -> dict:
    """
    For every PM VETO, check what the stock actually did afterward.
    A good veto blocked a trade that would have lost money.
    A bad veto blocked a trade that would have won — an opportunity cost.
    """
    vetoes = []
    for state in pipeline_outputs:
        pm = state.get("pm_decision", {})
        rv = state.get("research_verdict", {})
        if pm.get("decision") != "VETO":
            continue

        try:
            verdict_date = datetime.fromisoformat(state.get("completed_at", ""))
        except Exception:
            continue
        if (datetime.now() - verdict_date).days < forward_days:
            continue

        fwd_ret = _get_forward_return(state["ticker"], verdict_date, forward_days)
        if fwd_ret is None:
            continue

        # What would have happened if the (blocked) BUY/SELL had gone through?
        rec = rv.get("recommendation", "HOLD")
        would_have_won = (fwd_ret > 0) if rec == "BUY" else (fwd_ret < 0) if rec == "SELL" else None

        vetoes.append({
            "ticker":            state["ticker"],
            "recommendation":    rec,
            "forward_return_pct": fwd_ret,
            "veto_was_correct":  would_have_won is False if would_have_won is not None else None,
            "pm_note":           (pm.get("pm_note") or "")[:100],
        })

    good_vetoes = [v for v in vetoes if v["veto_was_correct"] is True]
    bad_vetoes  = [v for v in vetoes if v["veto_was_correct"] is False]

    return {
        "total_vetoes":       len(vetoes),
        "good_vetoes":        len(good_vetoes),
        "bad_vetoes":         len(bad_vetoes),
        "veto_accuracy_pct":  round(len(good_vetoes) / len(vetoes) * 100, 1) if vetoes else None,
        "detail":             vetoes,
    }


# ──────────────────────────────────────────────
# Main runner
# ──────────────────────────────────────────────

def run_feedback_engine(since_days: int = 365, forward_days: int = 30) -> dict:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M IST")
    ts_file   = datetime.now().strftime("%Y%m%d_%H%M")

    print(f"\n{'━'*60}")
    print(f"  HEDGEFUSION FEEDBACK ENGINE")
    print(f"  Checking {forward_days}-day forward outcomes for all past verdicts")
    print(f"{'━'*60}\n")

    outputs = _load_pipeline_outputs(since_days)
    print(f"  Pipeline runs found: {len(outputs)}")

    eligible = [
        o for o in outputs
        if (datetime.now() - datetime.fromisoformat(o.get("completed_at", datetime.now().isoformat()))).days >= forward_days
    ]
    print(f"  Eligible for {forward_days}-day evaluation: {len(eligible)}")
    print(f"  (Verdicts newer than {forward_days} days can't be scored yet)\n")

    if not eligible:
        print(f"  📭 No verdicts old enough to evaluate yet. Run the pipeline more,")
        print(f"     wait {forward_days} days, then re-run this report.\n")
        return {"error": "insufficient aged data", "total_runs": len(outputs)}

    print("  Computing confidence calibration...")
    calibration = compute_confidence_calibration(outputs, forward_days)

    print("  Computing veto effectiveness...")
    veto_stats = compute_veto_effectiveness(outputs, forward_days)

    # Print summary
    print(f"\n{'━'*60}")
    print(f"  CONFIDENCE CALIBRATION ({forward_days}-day forward return)")
    print(f"{'━'*60}")
    for level in ["HIGH", "MEDIUM", "LOW"]:
        c = calibration.get(level, {})
        if c.get("n", 0) == 0:
            print(f"  {level:<8} — no scored calls yet")
            continue
        print(f"  {level:<8} n={c['n']:<4} hit_rate={c['hit_rate_pct']}%  "
              f"avg_fwd_return={c['avg_return_pct']:+.2f}%")

    hi = calibration.get("HIGH", {}).get("hit_rate_pct")
    lo = calibration.get("LOW", {}).get("hit_rate_pct")
    if hi is not None and lo is not None:
        if hi > lo:
            print(f"\n  ✅ Well-calibrated: HIGH confidence ({hi}%) beats LOW confidence ({lo}%)")
        else:
            print(f"\n  ⚠️ Poorly calibrated: HIGH confidence ({hi}%) does NOT beat LOW ({lo}%)")
            print(f"     Confidence field may not be predictive — treat with caution.")

    print(f"\n{'━'*60}")
    print(f"  PORTFOLIO MANAGER VETO EFFECTIVENESS")
    print(f"{'━'*60}")
    print(f"  Total vetoes evaluated: {veto_stats['total_vetoes']}")
    if veto_stats["total_vetoes"]:
        print(f"  Correct vetoes:  {veto_stats['good_vetoes']} "
              f"(blocked a trade that would have lost)")
        print(f"  Bad vetoes:      {veto_stats['bad_vetoes']} "
              f"(blocked a trade that would have won — opportunity cost)")
        print(f"  Veto accuracy:   {veto_stats['veto_accuracy_pct']}%")
    print(f"{'━'*60}\n")

    result = {
        "since_days":         since_days,
        "forward_days":       forward_days,
        "total_runs":         len(outputs),
        "eligible_runs":      len(eligible),
        "confidence_calibration": calibration,
        "veto_effectiveness": veto_stats,
    }

    html = _build_feedback_html(result, timestamp)
    html_path = OUTPUT_DIR / f"feedback_{ts_file}.html"
    html_path.write_text(html, encoding="utf-8")
    json_path = OUTPUT_DIR / f"feedback_{ts_file}.json"
    json_path.write_text(json.dumps(result, default=str, indent=2), encoding="utf-8")
    print(f"✅ Feedback report: {html_path}\n")

    return result


def _build_feedback_html(r: dict, timestamp: str) -> str:
    cal = r.get("confidence_calibration", {})
    veto = r.get("veto_effectiveness", {})

    cal_rows = ""
    for level in ["HIGH", "MEDIUM", "LOW"]:
        c = cal.get(level, {})
        n = c.get("n", 0)
        hr = c.get("hit_rate_pct")
        ar = c.get("avg_return_pct")
        color = "#22c55e" if hr and hr >= 55 else "#f59e0b" if hr and hr >= 45 else "#ef4444" if hr is not None else "#64748b"
        cal_rows += f"""<tr>
          <td style="font-weight:700">{level}</td>
          <td>{n}</td>
          <td style="color:{color};font-weight:600">{f'{hr}%' if hr is not None else '—'}</td>
          <td style="color:{'#22c55e' if ar and ar>=0 else '#ef4444'}">{f'{ar:+.2f}%' if ar is not None else '—'}</td>
        </tr>"""

    veto_acc = veto.get("veto_accuracy_pct")
    veto_color = "#22c55e" if veto_acc and veto_acc >= 55 else "#f59e0b" if veto_acc and veto_acc >= 40 else "#ef4444"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><title>HedgeFusion Feedback — {timestamp}</title>
<style>
  body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
       background:#050d1a;color:#e2e8f0;margin:0;padding:20px}}
  .wrap{{max-width:900px;margin:0 auto}}
  h1{{font-size:22px;font-weight:800;color:#f8fafc;margin-bottom:4px}}
  h2{{font-size:15px;font-weight:700;color:#e2e8f0;margin:28px 0 12px;
      padding-bottom:8px;border-bottom:1px solid #1e293b}}
  .meta{{font-size:12px;color:#64748b;margin-bottom:24px}}
  table{{width:100%;border-collapse:collapse;background:#0f172a;
         border-radius:8px;overflow:hidden;margin-bottom:16px}}
  th{{background:#1e293b;color:#64748b;padding:9px 12px;text-align:left;
      font-size:11px;font-weight:600}}
  td{{padding:9px 12px;border-bottom:1px solid #1e293b;font-size:13px}}
  .kpi{{background:#0f172a;border:1px solid #1e293b;border-radius:8px;
        padding:18px;text-align:center}}
  .kv{{font-size:26px;font-weight:800}}
  .kl{{font-size:11px;color:#64748b;margin-top:3px}}
  .disc{{background:#1e1a0a;border:1px solid #78350f;border-radius:8px;
         padding:14px 18px;font-size:12px;color:#92400e;margin-top:24px}}
</style>
</head>
<body><div class="wrap">
  <h1>🔄 HedgeFusion Feedback Engine</h1>
  <div class="meta">
    {timestamp} &nbsp;·&nbsp; {r.get('eligible_runs',0)}/{r.get('total_runs',0)} runs old
    enough to score &nbsp;·&nbsp; {r.get('forward_days',30)}-day forward window
  </div>

  <h2>Confidence Calibration</h2>
  <table>
    <thead><tr><th>Confidence</th><th>n</th><th>Hit Rate</th><th>Avg Fwd Return</th></tr></thead>
    <tbody>{cal_rows}</tbody>
  </table>
  <div style="font-size:12px;color:#64748b;margin-bottom:20px">
    A well-calibrated system shows HIGH confidence winning more often than LOW confidence.
    If the order is flat or inverted, treat the confidence field as decorative, not predictive.
  </div>

  <h2>Portfolio Manager Veto Effectiveness</h2>
  <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:16px">
    <div class="kpi"><div class="kv" style="color:#94a3b8">{veto.get('total_vetoes',0)}</div>
      <div class="kl">Vetoes evaluated</div></div>
    <div class="kpi"><div class="kv" style="color:{veto_color}">
      {f"{veto_acc}%" if veto_acc is not None else "—"}</div>
      <div class="kl">Veto accuracy</div></div>
    <div class="kpi"><div class="kv" style="color:#f59e0b">{veto.get('bad_vetoes',0)}</div>
      <div class="kl">Opportunity-cost vetoes</div></div>
  </div>

  <div class="disc">
    ⚠️ This report scores past AI verdicts against what the stock actually did afterward —
    it does not account for transaction costs, slippage, or whether you would have actually
    held the full {r.get('forward_days',30)}-day window. Use as a directional calibration
    check, not a precise backtest.
  </div>
</div></body></html>"""


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="HedgeFusion Feedback Engine")
    parser.add_argument("--since",   type=int, default=365, help="Days of pipeline history to scan")
    parser.add_argument("--forward", type=int, default=30,  help="Forward days to evaluate outcome")
    args = parser.parse_args()
    run_feedback_engine(since_days=args.since, forward_days=args.forward)
