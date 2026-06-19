"""
HedgeFusion Trade Journal
===========================
Reads your paper_trades.csv and pipeline JSON outputs and produces
a performance dashboard showing:

  - Win rate, average R:R, total P&L
  - Per-stock performance breakdown
  - Agent accuracy: how often did the AI recommendation play out?
  - Best and worst calls
  - Equity curve over time

Usage:
    python trade_journal.py              # full report
    python trade_journal.py --live       # include live trades
    python trade_journal.py --since 7d  # last 7 days
"""

import csv
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

LOG_DIR    = Path(__file__).parent / "logs"
OUTPUT_DIR = Path(__file__).parent / "outputs"
PAPER_LOG  = LOG_DIR / "paper_trades.csv"
OUTPUT_DIR.mkdir(exist_ok=True)


# ── Read trade log ────────────────────────────────────────────

def load_paper_trades(since_days: int = 90) -> list[dict]:
    """Load paper trades from CSV log."""
    if not PAPER_LOG.exists():
        return []
    cutoff = datetime.now() - timedelta(days=since_days)
    trades = []
    with open(PAPER_LOG, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                ts = datetime.fromisoformat(row.get("timestamp",""))
                if ts >= cutoff:
                    row["fill_price"] = float(row.get("fill_price") or 0)
                    row["quantity"]   = int(row.get("quantity") or 0)
                    row["value_inr"]  = float(row.get("value_inr") or 0)
                    row["stop_loss"]  = float(row.get("stop_loss") or 0)
                    row["take_profit"]= float(row.get("take_profit") or 0)
                    row["ts"]         = ts
                    trades.append(row)
            except Exception:
                pass
    return trades


def load_pipeline_outputs(since_days: int = 90) -> list[dict]:
    """Load all pipeline JSON outputs from outputs/ folder."""
    cutoff = datetime.now() - timedelta(days=since_days)
    outputs = []
    for p in OUTPUT_DIR.glob("*.json"):
        try:
            mtime = datetime.fromtimestamp(p.stat().st_mtime)
            if mtime < cutoff:
                continue
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "ticker" in data:
                outputs.append(data)
        except Exception:
            pass
    return outputs


# ── Stats engine ──────────────────────────────────────────────

def compute_stats(trades: list[dict]) -> dict:
    """Compute performance statistics from trade list."""
    if not trades:
        return {"total_trades": 0}

    buys  = [t for t in trades if t.get("transaction_type","").upper() == "BUY"]
    sells = [t for t in trades if t.get("transaction_type","").upper() == "SELL"]

    # Match buys to sells by symbol (simple FIFO)
    closed_trades = []
    open_positions: dict[str, list] = {}

    for t in sorted(trades, key=lambda x: x["ts"]):
        sym = t.get("symbol","")
        tt  = t.get("transaction_type","").upper()
        if tt == "BUY":
            if sym not in open_positions:
                open_positions[sym] = []
            open_positions[sym].append(t)
        elif tt == "SELL" and sym in open_positions and open_positions[sym]:
            entry = open_positions[sym].pop(0)
            pnl   = (t["fill_price"] - entry["fill_price"]) * min(t["quantity"], entry["quantity"])
            closed_trades.append({
                "symbol":      sym,
                "entry_price": entry["fill_price"],
                "exit_price":  t["fill_price"],
                "quantity":    min(t["quantity"], entry["quantity"]),
                "pnl_inr":     round(pnl, 2),
                "pnl_pct":     round((t["fill_price"] - entry["fill_price"]) / entry["fill_price"] * 100, 2),
                "entry_date":  entry["ts"].strftime("%Y-%m-%d"),
                "exit_date":   t["ts"].strftime("%Y-%m-%d"),
                "won":         pnl > 0,
                "sl":          entry.get("stop_loss", 0),
                "tp":          entry.get("take_profit", 0),
            })

    total_pnl   = sum(c["pnl_inr"] for c in closed_trades)
    winners     = [c for c in closed_trades if c["won"]]
    losers      = [c for c in closed_trades if not c["won"]]
    win_rate    = len(winners) / len(closed_trades) * 100 if closed_trades else 0
    avg_win     = sum(c["pnl_inr"] for c in winners) / len(winners) if winners else 0
    avg_loss    = sum(c["pnl_inr"] for c in losers) / len(losers) if losers else 0
    profit_factor = abs(avg_win / avg_loss) if avg_loss != 0 else float("inf")

    total_invested = sum(t["value_inr"] for t in buys)

    return {
        "total_trades":     len(trades),
        "buy_orders":       len(buys),
        "sell_orders":      len(sells),
        "closed_trades":    len(closed_trades),
        "open_positions":   sum(len(v) for v in open_positions.values()),
        "win_rate_pct":     round(win_rate, 1),
        "total_pnl_inr":    round(total_pnl, 2),
        "total_invested_inr": round(total_invested, 2),
        "return_pct":       round(total_pnl / total_invested * 100, 2) if total_invested else 0,
        "avg_win_inr":      round(avg_win, 2),
        "avg_loss_inr":     round(avg_loss, 2),
        "profit_factor":    round(profit_factor, 2),
        "best_trade":       max(closed_trades, key=lambda x: x["pnl_inr"]) if closed_trades else None,
        "worst_trade":      min(closed_trades, key=lambda x: x["pnl_inr"]) if closed_trades else None,
        "closed_trade_detail": closed_trades,
        "open_position_detail": {
            sym: [{"qty": t["quantity"], "entry": t["fill_price"]} for t in pos]
            for sym, pos in open_positions.items()
            if pos
        },
    }


def compute_agent_accuracy(pipeline_outputs: list[dict]) -> dict:
    """
    Measures pipeline consistency — how well each agent's output
    flows through to the final PM decision.

    Alignment definition (reflects pipeline health, not trade quality):
      ALIGNED   = Research says BUY/SELL + PM APPROVES  (intended path)
      ALIGNED   = Research says HOLD  + PM VETOS        (correctly blocked)
      DIVERGED  = Research says BUY/SELL + PM VETOS     (pipeline friction — investigate)
      DIVERGED  = Research says HOLD  + PM APPROVES     (shouldn't happen)

    Note: VETO on BUY/SELL before the prompt fixes was a pipeline bug,
    not a sign the AI was wrong about the stock direction.
    """
    total    = 0
    aligned  = 0
    approved = 0
    vetoed   = 0
    by_ticker = []

    for state in pipeline_outputs:
        rv  = state.get("research_verdict", {})
        pm  = state.get("pm_decision", {})
        if not rv or not pm:
            continue

        rec  = str(rv.get("recommendation","")).upper().strip()
        dec  = str(pm.get("decision","")).upper().strip()
        conf = rv.get("confidence","")
        rr   = rv.get("risk_reward","")

        if not rec or not dec:
            continue

        total += 1

        # Count approvals and vetoes
        if dec == "APPROVE":
            approved += 1
        elif dec == "VETO":
            vetoed += 1

        # Alignment: pipeline delivered intended outcome
        is_aligned = (
            (rec in ("BUY","SELL") and dec == "APPROVE") or
            (rec == "HOLD" and dec == "VETO")
        )
        if is_aligned:
            aligned += 1

        # Determine outcome label
        if rec in ("BUY","SELL") and dec == "APPROVE":
            outcome = "EXECUTED ✅"
        elif rec == "HOLD" and dec == "VETO":
            outcome = "CORRECTLY BLOCKED ✅"
        elif rec in ("BUY","SELL") and dec == "VETO":
            outcome = "PIPELINE FRICTION ⚠️"
        else:
            outcome = "UNEXPECTED"

        by_ticker.append({
            "ticker":         state.get("ticker",""),
            "recommendation": rec,
            "confidence":     conf,
            "pm_decision":    dec,
            "risk_reward":    rr,
            "aligned":        is_aligned,
            "outcome":        outcome,
            "elapsed_s":      state.get("elapsed_seconds",""),
        })

    # Sort: executed first, then friction cases
    priority = {"EXECUTED ✅": 0, "CORRECTLY BLOCKED ✅": 1,
                "PIPELINE FRICTION ⚠️": 2, "UNEXPECTED": 3}
    by_ticker.sort(key=lambda x: priority.get(x.get("outcome",""), 4))

    return {
        "total_analysed":    total,
        "total_approved":    approved,
        "total_vetoed":      vetoed,
        "agent_alignment":   round(aligned / total * 100, 1) if total else 0,
        "approval_rate":     round(approved / total * 100, 1) if total else 0,
        "by_ticker":         by_ticker,
        "health_note": (
            "Pipeline healthy — agents and PM aligned" if aligned/total >= 0.6
            else "Pipeline friction — many BUY/SELL signals being vetoed. Check prompts."
            if total else "No data yet"
        ) if total else "No pipeline runs found",
    }


# ── Report builder ────────────────────────────────────────────

def build_journal_html(stats: dict, accuracy: dict, trades: list, timestamp: str) -> str:
    def stat_card(label, value, color="#f59e0b", sub=""):
        return f"""<div style="background:#0f172a;border:1px solid #1e293b;
                               border-radius:8px;padding:18px;text-align:center">
          <div style="font-size:28px;font-weight:800;color:{color}">{value}</div>
          <div style="font-size:11px;color:#64748b;margin-top:3px">{label}</div>
          {"<div style='font-size:10px;color:#475569;margin-top:2px'>"+sub+"</div>" if sub else ""}
        </div>"""

    pnl_color = "#22c55e" if stats.get("total_pnl_inr",0) >= 0 else "#ef4444"
    wr_color  = "#22c55e" if stats.get("win_rate_pct",0) >= 50 else "#ef4444"
    pf        = stats.get("profit_factor", 0)
    pf_color  = "#22c55e" if pf >= 1.5 else "#f59e0b" if pf >= 1 else "#ef4444"

    kpi_row = f"""
    <div style="display:grid;grid-template-columns:repeat(6,1fr);gap:8px;margin-bottom:24px">
      {stat_card("Total Trades",    stats.get("total_trades",0),            "#94a3b8")}
      {stat_card("Win Rate",        f"{stats.get('win_rate_pct',0):.1f}%",  wr_color)}
      {stat_card("Total P&L",       f"₹{stats.get('total_pnl_inr',0):,.0f}", pnl_color)}
      {stat_card("Return",          f"{stats.get('return_pct',0):.1f}%",    pnl_color)}
      {stat_card("Profit Factor",   f"{pf:.2f}",                            pf_color, "≥1.5 = good")}
      {stat_card("Approved", f"{accuracy.get('total_approved',0)}", "#22c55e", f"of {accuracy.get('total_analysed',0)} runs")}
    </div>"""

    # Closed trades table
    trade_rows = ""
    for c in stats.get("closed_trade_detail", []):
        color = "#22c55e" if c["won"] else "#ef4444"
        sign  = "+" if c["pnl_inr"] >= 0 else ""
        trade_rows += f"""<tr>
          <td style="font-family:monospace;font-weight:700">{c['symbol']}</td>
          <td style="font-size:12px;color:#64748b">{c['entry_date']}</td>
          <td style="font-size:12px;color:#64748b">{c['exit_date']}</td>
          <td style="font-family:monospace">₹{c['entry_price']:,.2f}</td>
          <td style="font-family:monospace">₹{c['exit_price']:,.2f}</td>
          <td>{c['quantity']}</td>
          <td style="color:{color};font-weight:700;font-family:monospace">
            {sign}₹{c['pnl_inr']:,.2f}</td>
          <td style="color:{color}">{sign}{c['pnl_pct']:.2f}%</td>
        </tr>"""

    # Open positions table
    open_rows = ""
    for sym, pos_list in stats.get("open_position_detail", {}).items():
        for p in pos_list:
            open_rows += f"""<tr>
              <td style="font-family:monospace;font-weight:700">{sym}</td>
              <td>{p['qty']}</td>
              <td style="font-family:monospace">₹{p['entry']:,.2f}</td>
              <td style="color:#64748b">Mark-to-market pending</td>
            </tr>"""

    # Agent accuracy table
    acc_rows = ""
    for a in accuracy.get("by_ticker", []):
        ac     = "#22c55e" if a.get("aligned") else "#ef4444"
        oc     = {"EXECUTED ✅":"#22c55e","CORRECTLY BLOCKED ✅":"#86efac",
                  "PIPELINE FRICTION ⚠️":"#f59e0b","UNEXPECTED":"#ef4444"}.get(a.get("outcome",""),"#94a3b8")
        acc_rows += f"""<tr>
          <td style="font-family:monospace;font-weight:700">{a.get('ticker','')}</td>
          <td style="color:#f59e0b;font-weight:600">{a.get('recommendation','')}</td>
          <td>{a.get('confidence','')}</td>
          <td style="color:#60a5fa">{a.get('pm_decision','')}</td>
          <td>{a.get('risk_reward','—')}</td>
          <td style="color:{oc};font-weight:600">{a.get('outcome','')}</td>
        </tr>"""

    # Recent trade log
    recent_rows = ""
    for t in sorted(trades, key=lambda x: x["ts"], reverse=True)[:20]:
        color = "#22c55e" if t.get("transaction_type","").upper() == "BUY" else "#ef4444"
        recent_rows += f"""<tr>
          <td style="font-size:11px;color:#64748b">{t['ts'].strftime('%m-%d %H:%M')}</td>
          <td style="font-family:monospace;font-weight:700">{t.get('symbol','')}</td>
          <td style="color:{color};font-weight:600">{t.get('transaction_type','')}</td>
          <td>{t.get('quantity','')}</td>
          <td style="font-family:monospace">₹{float(t.get('fill_price',0)):,.2f}</td>
          <td style="font-family:monospace">₹{float(t.get('value_inr',0)):,.0f}</td>
          <td style="font-size:11px;color:#64748b">{t.get('order_id','')}</td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>HedgeFusion Trade Journal — {timestamp}</title>
<style>
  body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
       background:#050d1a;color:#e2e8f0;margin:0;padding:20px;
       -webkit-font-smoothing:antialiased}}
  .wrap{{max-width:1060px;margin:0 auto}}
  h1{{font-size:22px;font-weight:800;color:#f8fafc;margin-bottom:4px}}
  h2{{font-size:15px;font-weight:700;color:#cbd5e1;margin:28px 0 12px;
      padding-bottom:8px;border-bottom:1px solid #1e293b}}
  .meta{{font-size:12px;color:#64748b;margin-bottom:24px}}
  table{{width:100%;border-collapse:collapse;background:#0f172a;
         border-radius:8px;overflow:hidden;margin-bottom:8px}}
  th{{background:#1e293b;color:#64748b;padding:9px 12px;text-align:left;
      font-size:11px;font-weight:600;letter-spacing:.04em}}
  td{{padding:9px 12px;border-bottom:1px solid #1e293b;font-size:13px;color:#cbd5e1}}
  tr:last-child td{{border-bottom:none}}
  tr:hover td{{background:#0a1628}}
  .empty{{color:#64748b;font-style:italic;padding:20px;text-align:center}}
  .disc{{background:#1e1a0a;border:1px solid #78350f;border-radius:8px;
         padding:14px 18px;font-size:12px;color:#92400e;margin-top:24px}}
</style>
</head>
<body>
<div class="wrap">
  <h1>📓 HedgeFusion Trade Journal</h1>
  <div class="meta">Generated: {timestamp} &nbsp;·&nbsp; Paper mode</div>

  {kpi_row}

  <h2>📈 Closed Trades P&L</h2>
  {"<table><thead><tr><th>Symbol</th><th>Entry Date</th><th>Exit Date</th><th>Entry ₹</th><th>Exit ₹</th><th>Qty</th><th>P&L ₹</th><th>Return</th></tr></thead><tbody>" + trade_rows + "</tbody></table>" if trade_rows else "<div class='empty'>No closed trades yet. Paper trade for a few weeks to see P&L data.</div>"}

  <h2>📂 Open Positions</h2>
  {"<table><thead><tr><th>Symbol</th><th>Qty</th><th>Avg Entry</th><th>Status</th></tr></thead><tbody>" + open_rows + "</tbody></table>" if open_rows else "<div class='empty'>No open positions currently.</div>"}

  <h2>🤖 Agent Accuracy Log</h2>
  {"<table><thead><tr><th>Ticker</th><th>Rec</th><th>Conf</th><th>PM</th><th>R:R</th><th>Outcome</th></tr></thead><tbody>" + acc_rows + "</tbody></table>" if acc_rows else "<div class='empty'>No pipeline runs found in outputs/. Run python hf.py run RELIANCE first.</div>"}

  <h2>📋 Recent Trades Log</h2>
  {"<table><thead><tr><th>Time</th><th>Symbol</th><th>Type</th><th>Qty</th><th>Price</th><th>Value</th><th>Order ID</th></tr></thead><tbody>" + recent_rows + "</tbody></table>" if recent_rows else "<div class='empty'>No trades yet. Run portfolio_runner.py --execute to start.</div>"}

  <div class="disc">
    ⚠️ Paper trade performance does not guarantee live trade results.
    This journal is for tracking and learning, not performance benchmarking.
  </div>
</div>
</body>
</html>"""


def run_journal(since_days: int = 90):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M IST")
    ts_file   = datetime.now().strftime("%Y%m%d_%H%M")

    print("\nLoading trade data (last " + str(since_days) + " days)...")
    trades    = load_paper_trades(since_days)
    pipelines = load_pipeline_outputs(since_days)

    print("  Paper trades found: " + str(len(trades)))
    print("  Pipeline runs found: " + str(len(pipelines)))

    stats    = compute_stats(trades)
    accuracy = compute_agent_accuracy(pipelines)

    n_runs     = accuracy.get("total_analysed", 0)
    n_approved = accuracy.get("total_approved", 0)
    n_vetoed   = accuracy.get("total_vetoed", 0)

    sep = "=" * 55
    print("\n" + sep)
    print("  TRADE JOURNAL SUMMARY")
    print(sep)
    print("  Total trades:    ", stats.get("total_trades", 0))
    print("  Closed trades:   ", stats.get("closed_trades", 0))
    print("  Open positions:  ", stats.get("open_positions", 0))
    wr     = stats.get("win_rate_pct", 0)
    no_cls = stats.get("closed_trades", 0) == 0
    wr_note = "  (need closed trades)" if no_cls else ""
    print("  Win rate:         " + str(wr) + "%" + wr_note)
    pnl = stats.get("total_pnl_inr", 0)
    print("  Total P&L:        Rs." + f"{pnl:,.2f}")
    ret = stats.get("return_pct", 0)
    print("  Return:          ", str(ret) + "%")
    pf = stats.get("profit_factor", 0)
    pf_str = "inf" if pf == float("inf") else f"{pf:.2f}"
    print("  Profit factor:   ", pf_str)
    print()
    print("  Pipeline runs:   ", n_runs)
    rate = accuracy.get("approval_rate", 0)
    print("  Orders approved:  " + str(n_approved) + " (" + f"{rate:.0f}" + "%)")
    print("  Orders vetoed:   ", n_vetoed)
    print("  Pipeline health: ", accuracy.get("health_note", ""))
    print()
    if accuracy.get("by_ticker"):
        print("  Per-stock outcomes:")
        for a in accuracy["by_ticker"]:
            icon   = "OK" if a.get("aligned") else "--"
            ticker = a.get("ticker", "")
            rec    = a.get("recommendation", "")
            pm_dec = a.get("pm_decision", "")
            outc   = a.get("outcome", "")
            print("    [" + icon + "] " + ticker.ljust(14) + " " + rec.rjust(4) +
                  " -> " + pm_dec.rjust(6) + " | " + outc)

    if stats.get("best_trade"):
        b   = stats["best_trade"]
        print("  Best trade:  " + b["symbol"] +
              " Rs." + f"{b['pnl_inr']:,.2f}" +
              " (" + f"{b['pnl_pct']:+.1f}" + "%)")
    if stats.get("worst_trade"):
        w   = stats["worst_trade"]
        print("  Worst trade: " + w["symbol"] +
              " Rs." + f"{w['pnl_inr']:,.2f}" +
              " (" + f"{w['pnl_pct']:+.1f}" + "%)")
    print(sep + "\n")

    html      = build_journal_html(stats, accuracy, trades, timestamp)
    html_path = OUTPUT_DIR / ("journal_" + ts_file + ".html")
    html_path.write_text(html, encoding="utf-8")
    print("Journal saved: " + str(html_path))
    print("  Open in browser for full formatted report.\n")

    return stats, accuracy

