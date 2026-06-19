"""
HedgeFusion Portfolio Runner
==============================
Runs the complete 9-agent pipeline on ALL your Zerodha holdings
at once and produces a unified portfolio report.

Two modes:
  ANALYSE  → runs all 9 agents per stock, no orders placed
  EXECUTE  → runs all 9 agents per stock, places paper/live orders if PM approves

How it works:
  1. Runs stocks in batches of BATCH_SIZE (default 3) to avoid rate limits
  2. Each stock runs the full Director→Quant→Risk→Execution pipeline
  3. Aggregates all results into a ranked portfolio report
  4. Saves HTML + JSON report to outputs/

Usage:
    python portfolio_runner.py                   # analyse all holdings
    python portfolio_runner.py --execute         # analyse + execute approved
    python portfolio_runner.py --batch 5         # 5 stocks in parallel
    python portfolio_runner.py --tickers RELIANCE,TCS,INFY  # custom list

Cost estimate:
    gpt-4o-mini: ~₹5-8 per stock × 10 stocks = ₹50-80 total
    gpt-4o:      ~₹30-50 per stock × 10 stocks = ₹300-500 total
"""

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

from loguru import logger

from pipeline import run_pipeline
from tools.kite_execution import get_paper_portfolio

OUTPUT_DIR = Path(__file__).parent / "outputs"
OUTPUT_DIR.mkdir(exist_ok=True)

# ── Holdings from config (edit config.py, not here) ─────────
from config import HOLDINGS, PORTFOLIO_SIZE_INR


# ── Per-stock runner ──────────────────────────────────────────

def run_one(holding: dict, execute: bool) -> dict:
    """Run full pipeline for one holding. Returns enriched state dict."""
    ticker = holding["ticker"]
    try:
        state = run_pipeline(
            ticker=ticker,
            portfolio_size_inr=PORTFOLIO_SIZE_INR,
            allow_execution=execute,
            parallel_analysts=True,
        )
        # Attach holding metadata
        state["holding_qty"]           = holding.get("qty", 0)
        state["holding_avg_price"]     = holding.get("avg_buy_price", 0)
        state["holding_sector"]        = holding.get("sector", "")
        state["holding_invested_inr"]  = (
            holding.get("qty", 0) * holding.get("avg_buy_price", 0)
        )
        return state
    except Exception as e:
        logger.error("Pipeline failed for {}: {}", ticker, e)
        return {
            "ticker":  ticker,
            "error":   str(e),
            "holding_qty":       holding.get("qty", 0),
            "holding_sector":    holding.get("sector", ""),
            "pm_decision":       {"decision": "ERROR"},
            "research_verdict":  {"recommendation": "ERROR", "confidence": "N/A"},
            "execution_result":  {"status": "FAILED"},
        }


# ── Report builders ───────────────────────────────────────────

def _rec_color_html(rec: str) -> str:
    return {
        "BUY":  "#22c55e",
        "SELL": "#ef4444",
        "HOLD": "#f59e0b",
    }.get(rec.upper() if rec else "", "#64748b")

def _pm_color_html(pm: str) -> str:
    return "#22c55e" if pm == "APPROVE" else "#ef4444" if pm == "VETO" else "#64748b"


def build_text_report(results: list, timestamp: str, execute: bool) -> str:
    sep  = "=" * 70
    dash = "-" * 70
    lines = [
        sep,
        "  HEDGEFUSION — FULL PORTFOLIO PIPELINE REPORT",
        f"  {timestamp}",
        f"  Stocks: {len(results)} | Execution: {'ENABLED' if execute else 'DISABLED'}",
        f"  Mode: {'PAPER' if os.getenv('KITE_PAPER_TRADE','true').lower() in ('true','1') else '🔴 LIVE'}",
        sep, "",
        "PORTFOLIO SUMMARY TABLE", dash,
        f"{'Stock':<14} {'Sector':<14} {'Rec':>5} {'Conf':>6} {'PM':>8} "
        f"{'R:R':>6} {'SL ₹':>10} {'Target ₹':>10} {'Status':>10}",
        dash,
    ]

    buy_count  = 0
    sell_count = 0
    hold_count = 0
    veto_count = 0
    appr_count = 0

    for r in results:
        if "error" in r:
            lines.append(f"{r['ticker']:<14} {'ERROR':>60}")
            continue
        rv  = r.get("research_verdict", {})
        pm  = r.get("pm_decision", {})
        ex  = r.get("execution_result") or {}
        rec = rv.get("recommendation", "?")
        pmd = pm.get("decision", "?")

        if rec == "BUY":  buy_count  += 1
        if rec == "SELL": sell_count += 1
        if rec == "HOLD": hold_count += 1
        if pmd == "APPROVE": appr_count += 1
        if pmd == "VETO":    veto_count += 1

        lines.append(
            f"{r['ticker']:<14} {r.get('holding_sector',''):<14} "
            f"{rec:>5} {rv.get('confidence','?'):>6} {pmd:>8} "
            f"{str(rv.get('risk_reward','?')):>6} "
            f"₹{str(rv.get('stop_loss','?')):>8} "
            f"₹{str(rv.get('target1','?')):>8} "
            f"{ex.get('order_id') or ex.get('status','?'):>10}"
        )

    lines += [
        dash,
        f"  BUY: {buy_count}  SELL: {sell_count}  HOLD: {hold_count}  "
        f"| APPROVED: {appr_count}  VETOED: {veto_count}",
        "",
    ]

    # Individual detail blocks
    lines += ["", "INDIVIDUAL STOCK DEEP-DIVE", ""]
    for r in results:
        if "error" in r:
            lines += [f"{'─'*70}", f"  {r['ticker']} — ERROR: {r['error']}", ""]
            continue
        rv  = r.get("research_verdict", {})
        pm  = r.get("pm_decision", {})
        ex  = r.get("execution_result") or {}
        bull = r.get("bull", {})
        bear = r.get("bear", {})

        lines += [
            f"{'─'*70}",
            f"  {r['ticker']}  |  {rv.get('recommendation','?')}  "
            f"|  {pm.get('decision','?')}  |  {rv.get('confidence','?')} confidence",
            f"  Sector: {r.get('holding_sector','')}  "
            f"|  Holdings: {r.get('holding_qty',0)} shares",
            "",
            f"  Research verdict: {rv.get('debate_verdict','')[:120]}",
            f"  Entry zone:  ₹{rv.get('entry_zone','?')}",
            f"  Stop loss:   ₹{rv.get('stop_loss','?')}",
            f"  Target 1:    ₹{rv.get('target1','?')}",
            f"  Target 2:    ₹{rv.get('target2','?')}",
            f"  R:R ratio:   {rv.get('risk_reward','?')}",
            "",
            f"  Bull conviction: {bull.get('conviction','?')}  |  "
            f"Bear conviction: {bear.get('conviction','?')}",
            f"  PM note: {pm.get('pm_note','')[:100]}",
            f"  Order: {ex.get('order_id') or ex.get('status','?')}",
            "",
        ]

    lines += [
        sep,
        "  ⚠  AI-generated research. Not SEBI-registered advice.",
        "     Apply your own judgment before acting on any recommendation.",
        sep, "",
    ]
    return "\n".join(lines)


def build_html_report(results: list, timestamp: str, execute: bool) -> str:
    paper = os.getenv("KITE_PAPER_TRADE", "true").lower() in ("true", "1", "yes")
    mode_label = "📄 PAPER MODE" if paper else "🔴 LIVE MODE"

    rows = ""
    cards = ""
    buy_c = sell_c = hold_c = appr_c = veto_c = 0

    for r in results:
        rv  = r.get("research_verdict", {})
        pm  = r.get("pm_decision", {})
        ex  = r.get("execution_result") or {}
        bull = r.get("bull", {})
        bear = r.get("bear", {})
        rec  = rv.get("recommendation", "ERR")
        pmd  = pm.get("decision", "ERR")
        rc   = _rec_color_html(rec)
        pc   = _pm_color_html(pmd)

        if rec == "BUY":     buy_c  += 1
        if rec == "SELL":    sell_c += 1
        if rec == "HOLD":    hold_c += 1
        if pmd == "APPROVE": appr_c += 1
        if pmd == "VETO":    veto_c += 1

        order_txt = ex.get("order_id") or ex.get("status", "—")

        rows += f"""<tr>
          <td><strong style="font-family:monospace">{r['ticker']}</strong></td>
          <td style="font-size:12px;color:#64748b">{r.get('holding_sector','')}</td>
          <td><span style="color:{rc};font-weight:700">{rec}</span></td>
          <td style="color:#94a3b8">{rv.get('confidence','?')}</td>
          <td><span style="color:{pc};font-weight:600">{pmd}</span></td>
          <td style="color:#94a3b8">{rv.get('risk_reward','?')}</td>
          <td style="font-family:monospace">₹{rv.get('stop_loss','?')}</td>
          <td style="font-family:monospace">₹{rv.get('target1','?')}</td>
          <td style="font-size:11px;color:#64748b">{order_txt}</td>
        </tr>"""

        bull_conv = float(bull.get("conviction", 0) or 0)
        bear_conv = float(bear.get("conviction", 0) or 0)
        bull_w = int(bull_conv * 100)
        bear_w = int(bear_conv * 100)

        cards += f"""
        <div style="background:#0f172a;border:1px solid #1e293b;border-radius:10px;
                    padding:22px;margin-bottom:14px">
          <div style="display:flex;justify-content:space-between;align-items:flex-start;
                      margin-bottom:14px">
            <div>
              <span style="font-size:18px;font-weight:800;font-family:monospace;
                           color:#f1f5f9">{r['ticker']}</span>
              <span style="margin-left:10px;font-size:13px;color:#64748b">
                {r.get('holding_sector','')} &nbsp;·&nbsp; {r.get('holding_qty',0)} shares</span>
            </div>
            <div style="display:flex;gap:8px;align-items:center">
              <span style="padding:4px 12px;border-radius:5px;font-size:13px;
                           font-weight:700;background:{rc}22;color:{rc}">{rec}</span>
              <span style="padding:4px 12px;border-radius:5px;font-size:13px;
                           font-weight:700;background:{pc}22;color:{pc}">{pmd}</span>
            </div>
          </div>

          <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;
                      margin-bottom:14px">
            <div style="background:#1e293b;padding:10px;border-radius:6px">
              <div style="font-size:10px;color:#64748b;margin-bottom:3px">ENTRY ZONE</div>
              <div style="font-family:monospace;font-size:13px;color:#e2e8f0">
                ₹{rv.get('entry_zone','—')}</div>
            </div>
            <div style="background:#1e293b;padding:10px;border-radius:6px">
              <div style="font-size:10px;color:#64748b;margin-bottom:3px">STOP LOSS</div>
              <div style="font-family:monospace;font-size:13px;color:#ef4444">
                ₹{rv.get('stop_loss','—')}</div>
            </div>
            <div style="background:#1e293b;padding:10px;border-radius:6px">
              <div style="font-size:10px;color:#64748b;margin-bottom:3px">TARGET 1</div>
              <div style="font-family:monospace;font-size:13px;color:#22c55e">
                ₹{rv.get('target1','—')}</div>
            </div>
            <div style="background:#1e293b;padding:10px;border-radius:6px">
              <div style="font-size:10px;color:#64748b;margin-bottom:3px">R:R RATIO</div>
              <div style="font-family:monospace;font-size:13px;color:#f59e0b">
                {rv.get('risk_reward','—')}</div>
            </div>
          </div>

          <div style="margin-bottom:12px">
            <div style="font-size:11px;color:#64748b;margin-bottom:6px">
              BULL vs BEAR CONVICTION</div>
            <div style="display:flex;gap:8px;align-items:center;margin-bottom:4px">
              <span style="font-size:11px;color:#22c55e;width:32px">Bull</span>
              <div style="flex:1;height:6px;background:#1e293b;border-radius:3px">
                <div style="width:{bull_w}%;height:100%;background:#22c55e;
                             border-radius:3px;transition:width 0.3s"></div>
              </div>
              <span style="font-size:11px;color:#94a3b8;width:30px">{bull_conv:.0%}</span>
            </div>
            <div style="display:flex;gap:8px;align-items:center">
              <span style="font-size:11px;color:#ef4444;width:32px">Bear</span>
              <div style="flex:1;height:6px;background:#1e293b;border-radius:3px">
                <div style="width:{bear_w}%;height:100%;background:#ef4444;
                             border-radius:3px;transition:width 0.3s"></div>
              </div>
              <span style="font-size:11px;color:#94a3b8;width:30px">{bear_conv:.0%}</span>
            </div>
          </div>

          <div style="font-size:13px;color:#94a3b8;line-height:1.6;margin-bottom:8px">
            {rv.get('debate_verdict','')[:200]}</div>
          <div style="font-size:12px;color:#64748b">
            PM note: {pm.get('pm_note','')[:120]}</div>
          {"<div style='margin-top:10px;padding:8px 12px;background:#052e16;border-radius:5px;font-family:monospace;font-size:12px;color:#86efac'>✅ Order: " + str(ex.get('order_id','')) + " @ ₹" + str(ex.get('fill_price','?')) + "</div>" if ex.get('order_id') else ""}
        </div>"""

    summary_cards = f"""
    <div style="display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin-bottom:28px">
      <div style="background:#052e16;border:1px solid #166534;border-radius:8px;padding:16px;text-align:center">
        <div style="font-size:28px;font-weight:800;color:#22c55e">{buy_c}</div>
        <div style="font-size:11px;color:#16a34a">BUY signals</div>
      </div>
      <div style="background:#7f1d1d;border:1px solid #991b1b;border-radius:8px;padding:16px;text-align:center">
        <div style="font-size:28px;font-weight:800;color:#ef4444">{sell_c}</div>
        <div style="font-size:11px;color:#dc2626">SELL signals</div>
      </div>
      <div style="background:#1e1a0a;border:1px solid #78350f;border-radius:8px;padding:16px;text-align:center">
        <div style="font-size:28px;font-weight:800;color:#f59e0b">{hold_c}</div>
        <div style="font-size:11px;color:#d97706">HOLD signals</div>
      </div>
      <div style="background:#0f2030;border:1px solid #1e4080;border-radius:8px;padding:16px;text-align:center">
        <div style="font-size:28px;font-weight:800;color:#60a5fa">{appr_c}</div>
        <div style="font-size:11px;color:#3b82f6">PM APPROVED</div>
      </div>
      <div style="background:#1a0f2e;border:1px solid #4c1d95;border-radius:8px;padding:16px;text-align:center">
        <div style="font-size:28px;font-weight:800;color:#a78bfa">{veto_c}</div>
        <div style="font-size:11px;color:#7c3aed">PM VETOED</div>
      </div>
    </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>HedgeFusion Portfolio Report — {timestamp}</title>
<style>
  body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
       background:#050d1a;color:#e2e8f0;margin:0;padding:20px;
       -webkit-font-smoothing:antialiased}}
  .wrap{{max-width:1060px;margin:0 auto}}
  h1{{font-size:22px;font-weight:800;color:#f8fafc;margin-bottom:4px}}
  h2{{font-size:16px;font-weight:700;color:#e2e8f0;margin:28px 0 12px}}
  .meta{{font-size:13px;color:#64748b;margin-bottom:24px}}
  table{{width:100%;border-collapse:collapse;background:#0f172a;
         border-radius:10px;overflow:hidden;margin-bottom:16px}}
  th{{background:#1e293b;color:#64748b;padding:10px 12px;
      text-align:left;font-size:11px;font-weight:600;letter-spacing:.05em}}
  td{{padding:10px 12px;border-bottom:1px solid #1e293b;font-size:13px}}
  tr:last-child td{{border-bottom:none}}
  tr:hover td{{background:#0a1628}}
  .disc{{background:#1e1a0a;border:1px solid #78350f;border-radius:8px;
         padding:14px 18px;font-size:12px;color:#92400e;margin-top:24px}}
</style>
</head>
<body>
<div class="wrap">
  <h1>🇮🇳 HedgeFusion — Full Portfolio Report</h1>
  <div class="meta">
    {timestamp} &nbsp;·&nbsp; {len(results)} stocks &nbsp;·&nbsp;
    {mode_label} &nbsp;·&nbsp;
    Execution: {'enabled' if execute else 'disabled'}
  </div>

  {summary_cards}

  <h2>📊 Summary Table</h2>
  <table>
    <thead><tr>
      <th>Stock</th><th>Sector</th><th>Rec</th><th>Conf</th>
      <th>PM</th><th>R:R</th><th>Stop ₹</th><th>Target ₹</th><th>Order</th>
    </tr></thead>
    <tbody>{rows}</tbody>
  </table>

  <h2>📋 Stock Deep-Dive</h2>
  {cards}

  <div class="disc">
    ⚠️ <strong>Disclaimer:</strong> AI-generated research for educational purposes only.
    Not SEBI-registered investment advice. Always apply your own judgment.
  </div>
</div>
</body>
</html>"""


# ── Main runner ───────────────────────────────────────────────

def run_portfolio(
    holdings: list | None = None,
    execute: bool = False,
    batch_size: int = 3,
    custom_tickers: list | None = None,
) -> list:
    """
    Run the full 9-agent pipeline on all holdings.

    Parameters
    ----------
    holdings      : List of holding dicts. Defaults to HOLDINGS constant.
    execute       : If True, PM-approved orders are placed.
    batch_size    : Stocks processed in parallel per batch.
    custom_tickers: If given, overrides holdings with these tickers.
    """
    if custom_tickers:
        stocks = [{"ticker": t.strip().upper(), "qty": 0,
                   "avg_buy_price": 0, "sector": ""} for t in custom_tickers]
    else:
        stocks = holdings or HOLDINGS

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M IST")
    ts_file   = datetime.now().strftime("%Y%m%d_%H%M")
    paper     = os.getenv("KITE_PAPER_TRADE","true").lower() in ("true","1","yes")

    print(f"\n{'━'*65}")
    print(f"  HEDGEFUSION — FULL PORTFOLIO PIPELINE")
    print(f"  Stocks:    {len(stocks)}")
    print(f"  Execution: {'ENABLED' if execute else 'DISABLED (analysis only)'}")
    print(f"  Mode:      {'PAPER' if paper else '🔴 LIVE'}")
    print(f"  Model:     {os.getenv('MODEL_NAME','gpt-4o-mini')}")
    est_min = len(stocks) * 5
    est_cost = len(stocks) * 6
    print(f"  Estimate:  ~{est_min} min | ~₹{est_cost} in OpenAI credits")
    print(f"{'━'*65}")
    print()
    confirm = input("Start? (y/n): ").strip().lower()
    if confirm != "y":
        print("Cancelled.")
        return []

    results = []
    total   = len(stocks)

    # Process in batches
    for batch_start in range(0, total, batch_size):
        batch = stocks[batch_start:batch_start + batch_size]
        batch_num = batch_start // batch_size + 1
        total_batches = (total + batch_size - 1) // batch_size

        print(f"\n[Batch {batch_num}/{total_batches}] "
              f"{', '.join(h['ticker'] for h in batch)}")

        with ThreadPoolExecutor(max_workers=len(batch)) as ex:
            futures = {
                ex.submit(run_one, h, execute): h["ticker"]
                for h in batch
            }
            for future in as_completed(futures):
                ticker = futures[future]
                try:
                    state = future.result()
                    results.append(state)
                    rv  = state.get("research_verdict", {})
                    pm  = state.get("pm_decision", {})
                    ex_ = state.get("execution_result") or {}
                    print(
                        f"  ✓ {ticker:<14} "
                        f"{rv.get('recommendation','?'):>5} | "
                        f"{pm.get('decision','?'):>8} | "
                        f"{ex_.get('order_id') or ex_.get('status','—')}"
                    )
                except Exception as e:
                    logger.error("Batch runner failed {}: {}", ticker, e)
                    results.append({"ticker": ticker, "error": str(e),
                                    "research_verdict": {}, "pm_decision": {},
                                    "execution_result": {}})

        # Pause between batches to avoid rate limits
        if batch_start + batch_size < total:
            print(f"  Pausing 10s before next batch...")
            time.sleep(10)

    # Sort by recommendation priority: BUY > HOLD > SELL > ERR
    def sort_key(r):
        rec = r.get("research_verdict", {}).get("recommendation", "")
        return {"BUY": 0, "HOLD": 1, "SELL": 2}.get(rec, 3)
    results.sort(key=sort_key)

    # Build reports
    text_report = build_text_report(results, timestamp, execute)
    html_report = build_html_report(results, timestamp, execute)

    print("\n" + text_report)

    txt_path  = OUTPUT_DIR / f"portfolio_{ts_file}.txt"
    html_path = OUTPUT_DIR / f"portfolio_{ts_file}.html"
    json_path = OUTPUT_DIR / f"portfolio_{ts_file}.json"

    txt_path.write_text(text_report,  encoding="utf-8")
    html_path.write_text(html_report, encoding="utf-8")
    json_path.write_text(
        json.dumps(results, default=str, indent=2), encoding="utf-8"
    )

    print(f"\n{'━'*65}")
    print(f"  ✅ Reports saved:")
    print(f"     HTML → {html_path}")
    print(f"     Text → {txt_path}")
    print(f"  👉 Open HTML in browser for the full formatted report.")
    print(f"{'━'*65}\n")

    return results


def main():
    parser = argparse.ArgumentParser(
        description="HedgeFusion Portfolio Runner — full 9-agent pipeline on all holdings"
    )
    parser.add_argument("--execute",  action="store_true",
                        help="Execute PM-approved orders (paper or live)")
    parser.add_argument("--batch",    type=int, default=3,
                        help="Stocks per parallel batch (default 3)")
    parser.add_argument("--tickers",  type=str,
                        help="Comma-separated tickers e.g. RELIANCE,TCS,INFY")
    args = parser.parse_args()

    custom = [t.strip() for t in args.tickers.split(",")] if args.tickers else None
    run_portfolio(execute=args.execute, batch_size=args.batch, custom_tickers=custom)


if __name__ == "__main__":
    main()
