"""
HedgeFusion Analytics
=======================
Real portfolio performance metrics — beyond simple win rate.

  XIRR   — Extended Internal Rate of Return. Handles irregular cash
           flows (you didn't invest a lump sum on day 1 — you bought
           into positions at different times and sizes). This is the
           ONLY correct way to measure annualised return for a real
           trading account. Simple "total P&L / total invested" lies
           when your capital was deployed at different times.

  CAGR   — Compound Annual Growth Rate of realised + open equity.

  Sharpe — Risk-adjusted return: (return - risk_free_rate) / volatility.
           A high win rate with wild swings scores worse than a modest,
           steady win rate — Sharpe captures that.

  Benchmark — Your XIRR vs Nifty 50's return over the same period.
           Are you actually beating the index, or would a Nifty ETF
           have done better with zero effort?

Usage:
    python analytics.py                    # full report
    python analytics.py --since 180        # last 180 days only

Output: terminal + outputs/analytics_YYYYMMDD.html
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import yfinance as yf
from dotenv import load_dotenv
from loguru import logger

load_dotenv(Path(__file__).parent / ".env")

OUTPUT_DIR = Path(__file__).parent / "outputs"
OUTPUT_DIR.mkdir(exist_ok=True)

from config import PORTFOLIO_SIZE_INR


# ──────────────────────────────────────────────
# XIRR — Newton's method on the NPV function
# ──────────────────────────────────────────────

def _npv(rate: float, cashflows: list) -> float:
    """Net present value of a list of (date, amount) cashflows at a given rate."""
    if not cashflows:
        return 0.0
    t0 = cashflows[0][0]
    total = 0.0
    for date, amount in cashflows:
        days = (date - t0).days
        total += amount / ((1 + rate) ** (days / 365.0))
    return total


def _npv_derivative(rate: float, cashflows: list) -> float:
    if not cashflows:
        return 0.0
    t0 = cashflows[0][0]
    total = 0.0
    for date, amount in cashflows:
        days = (date - t0).days
        years = days / 365.0
        if years == 0:
            continue
        total += -years * amount / ((1 + rate) ** (years + 1))
    return total


def compute_xirr(cashflows: list, guess: float = 0.15, max_iter: int = 100, tol: float = 1e-6) -> float:
    """
    Compute XIRR via Newton-Raphson iteration.

    Parameters
    ----------
    cashflows : list of (datetime, amount) tuples.
                Negative amount = money going out (buy).
                Positive amount = money coming in (sell / current value).
    guess     : initial rate guess (15% default — reasonable equity starting point)

    Returns
    -------
    float: annualised rate as a decimal (0.18 = 18% p.a.), or 0.0 if it doesn't converge.
    """
    if len(cashflows) < 2:
        return 0.0

    # Must have at least one negative and one positive cashflow
    amounts = [c[1] for c in cashflows]
    if not (any(a < 0 for a in amounts) and any(a > 0 for a in amounts)):
        return 0.0

    rate = guess
    for _ in range(max_iter):
        npv = _npv(rate, cashflows)
        d_npv = _npv_derivative(rate, cashflows)
        if abs(d_npv) < 1e-10:
            break
        new_rate = rate - npv / d_npv
        if abs(new_rate - rate) < tol:
            return round(new_rate, 4)
        # Guard against divergence
        if new_rate < -0.99:
            new_rate = -0.5
        rate = new_rate

    return round(rate, 4) if abs(_npv(rate, cashflows)) < 1000 else 0.0


# ──────────────────────────────────────────────
# CAGR
# ──────────────────────────────────────────────

def compute_cagr(start_value: float, end_value: float, years: float) -> float:
    """Compound Annual Growth Rate as a percentage."""
    if start_value <= 0 or years <= 0:
        return 0.0
    return round(((end_value / start_value) ** (1 / years) - 1) * 100, 2)


# ──────────────────────────────────────────────
# Sharpe ratio
# ──────────────────────────────────────────────

def compute_sharpe(daily_returns: list, risk_free_rate_annual: float = 0.065) -> float:
    """
    Annualised Sharpe ratio from a list of daily returns (decimals, not %).
    risk_free_rate_annual: India 10Y G-Sec yield proxy, ~6.5%.
    """
    if not daily_returns or len(daily_returns) < 5:
        return 0.0

    n = len(daily_returns)
    mean_daily = sum(daily_returns) / n
    variance   = sum((r - mean_daily) ** 2 for r in daily_returns) / n
    std_daily  = variance ** 0.5

    if std_daily == 0:
        return 0.0

    rf_daily = risk_free_rate_annual / 252
    sharpe_daily = (mean_daily - rf_daily) / std_daily
    sharpe_annual = sharpe_daily * (252 ** 0.5)
    return round(sharpe_annual, 2)


# ──────────────────────────────────────────────
# Benchmark comparison (vs Nifty 50)
# ──────────────────────────────────────────────

def get_nifty_return(start_date: datetime, end_date: datetime) -> dict:
    """Nifty 50 return over the same period as your trades, for fair comparison."""
    try:
        hist = yf.Ticker("^NSEI").history(
            start=start_date.strftime("%Y-%m-%d"),
            end=(end_date + timedelta(days=1)).strftime("%Y-%m-%d"),
            interval="1d",
        )
        if hist is None or hist.empty:
            return {"error": "no Nifty data"}

        closes = hist["Close"].dropna()
        start_px = float(closes.iloc[0])
        end_px   = float(closes.iloc[-1])
        ret_pct  = (end_px - start_px) / start_px * 100
        years    = max((end_date - start_date).days / 365.0, 1/365)
        cagr     = compute_cagr(start_px, end_px, years)

        return {
            "start_price": round(start_px, 2),
            "end_price":   round(end_px, 2),
            "return_pct":  round(ret_pct, 2),
            "cagr_pct":    cagr,
            "period_days": (end_date - start_date).days,
        }
    except Exception as e:
        logger.warning("get_nifty_return failed: {}", e)
        return {"error": str(e)}


# ──────────────────────────────────────────────
# Main analytics builder
# ──────────────────────────────────────────────

def build_cashflow_series(trades: list, current_portfolio_value: float) -> list:
    """
    Convert a trade log into (date, amount) cashflow tuples for XIRR:
      BUY  → negative cashflow (money leaving your pocket)
      SELL → positive cashflow (money returning)
      Final: add current portfolio value as a positive cashflow "today"
             (as if you liquidated everything now) so XIRR reflects
             realised + unrealised performance together.
    """
    cashflows = []
    for t in sorted(trades, key=lambda x: x["ts"]):
        amount = t["value_inr"]
        if t.get("transaction_type", "").upper() == "BUY":
            cashflows.append((t["ts"], -amount))
        elif t.get("transaction_type", "").upper() == "SELL":
            cashflows.append((t["ts"], amount))

    if current_portfolio_value > 0:
        cashflows.append((datetime.now(), current_portfolio_value))

    return cashflows


def run_analytics(since_days: int = 365) -> dict:
    """
    Full analytics run: XIRR, CAGR, Sharpe, Nifty benchmark comparison.
    Pulls trade history from trade_journal and current portfolio from kite_execution.
    """
    from trade_journal import load_paper_trades, compute_stats
    from tools.kite_execution import get_paper_portfolio

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M IST")
    ts_file   = datetime.now().strftime("%Y%m%d_%H%M")

    print(f"\n{'━'*55}")
    print(f"  HEDGEFUSION ANALYTICS")
    print(f"  Period: last {since_days} days")
    print(f"{'━'*55}\n")

    trades = load_paper_trades(since_days=since_days)
    stats  = compute_stats(trades)

    if not trades:
        print("  📭 No trades found. Run the pipeline with --execute first.\n")
        return {"error": "no trades"}

    # Current open portfolio value
    try:
        portfolio_raw = json.loads(get_paper_portfolio())
        current_value = sum(p.get("current_inr", 0) for p in portfolio_raw.get("positions", []))
    except Exception:
        current_value = 0.0

    # XIRR
    cashflows = build_cashflow_series(trades, current_value)
    xirr = compute_xirr(cashflows)
    xirr_pct = round(xirr * 100, 2)

    # CAGR (using total invested → total value across the observed period)
    first_trade_date = min(t["ts"] for t in trades)
    years_elapsed = max((datetime.now() - first_trade_date).days / 365.0, 1/365)
    total_invested = stats.get("total_invested_inr", 0)
    total_value    = total_invested + stats.get("total_pnl_inr", 0)
    cagr = compute_cagr(total_invested, total_value, years_elapsed) if total_invested else 0

    # Sharpe — approximate from closed trade returns as a daily-return proxy
    closed = stats.get("closed_trade_detail", [])
    pseudo_daily_returns = [c["pnl_pct"] / 100 for c in closed] if closed else []
    sharpe = compute_sharpe(pseudo_daily_returns)

    # Benchmark vs Nifty
    nifty = get_nifty_return(first_trade_date, datetime.now())

    result = {
        "period_days":        since_days,
        "first_trade_date":   first_trade_date.strftime("%Y-%m-%d"),
        "years_elapsed":      round(years_elapsed, 2),
        "xirr_pct":           xirr_pct,
        "cagr_pct":           cagr,
        "sharpe_ratio":       sharpe,
        "total_invested_inr": total_invested,
        "current_value_inr":  round(current_value, 2),
        "realised_pnl_inr":   stats.get("total_pnl_inr", 0),
        "win_rate_pct":       stats.get("win_rate_pct", 0),
        "profit_factor":      stats.get("profit_factor", 0),
        "closed_trades":      stats.get("closed_trades", 0),
        "open_positions":     stats.get("open_positions", 0),
        "nifty_benchmark":    nifty,
        "alpha_vs_nifty_pct": round(xirr_pct - nifty.get("cagr_pct", 0), 2) if "error" not in nifty else None,
    }

    # Print
    print(f"  First trade:       {result['first_trade_date']} ({result['years_elapsed']}y ago)")
    print(f"  Total invested:    ₹{result['total_invested_inr']:,.0f}")
    print(f"  Current value:     ₹{result['current_value_inr']:,.0f}")
    print(f"  Realised P&L:      ₹{result['realised_pnl_inr']:,.0f}")
    print(f"  {'─'*51}")
    print(f"  XIRR:              {result['xirr_pct']:+.2f}% p.a.")
    print(f"  CAGR:              {result['cagr_pct']:+.2f}% p.a.")
    print(f"  Sharpe ratio:      {result['sharpe_ratio']:.2f}")
    print(f"  Win rate:          {result['win_rate_pct']:.1f}%")
    print(f"  Profit factor:     {result['profit_factor']:.2f}")
    if "error" not in nifty:
        print(f"  {'─'*51}")
        print(f"  Nifty 50 CAGR:     {nifty['cagr_pct']:+.2f}% p.a. (same period)")
        alpha = result["alpha_vs_nifty_pct"]
        verdict = "✅ Beating Nifty" if alpha and alpha > 0 else "⚠️ Underperforming Nifty"
        print(f"  Your alpha:        {alpha:+.2f}%  —  {verdict}")
    print(f"{'━'*55}\n")

    # HTML report
    html = _build_analytics_html(result, timestamp)
    html_path = OUTPUT_DIR / f"analytics_{ts_file}.html"
    html_path.write_text(html, encoding="utf-8")
    json_path = OUTPUT_DIR / f"analytics_{ts_file}.json"
    json_path.write_text(json.dumps(result, default=str, indent=2), encoding="utf-8")
    print(f"✅ Analytics report: {html_path}\n")

    return result


def _build_analytics_html(r: dict, timestamp: str) -> str:
    def kpi(label, value, color="#f59e0b", sub=""):
        return f"""<div style="background:#0f172a;border:1px solid #1e293b;
                               border-radius:8px;padding:18px;text-align:center">
          <div style="font-size:26px;font-weight:800;color:{color}">{value}</div>
          <div style="font-size:11px;color:#64748b;margin-top:3px">{label}</div>
          {"<div style='font-size:10px;color:#475569;margin-top:2px'>"+sub+"</div>" if sub else ""}
        </div>"""

    xirr_color = "#22c55e" if r["xirr_pct"] >= 0 else "#ef4444"
    sharpe_color = "#22c55e" if r["sharpe_ratio"] >= 1 else "#f59e0b" if r["sharpe_ratio"] >= 0 else "#ef4444"
    nifty = r.get("nifty_benchmark", {})
    alpha = r.get("alpha_vs_nifty_pct")
    alpha_color = "#22c55e" if alpha and alpha > 0 else "#ef4444"

    kpi_row = f"""
    <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:24px">
      {kpi("XIRR (annualised)", f"{r['xirr_pct']:+.1f}%", xirr_color, "money-weighted return")}
      {kpi("CAGR", f"{r['cagr_pct']:+.1f}%", xirr_color)}
      {kpi("Sharpe Ratio", f"{r['sharpe_ratio']:.2f}", sharpe_color, "risk-adjusted")}
      {kpi("Win Rate", f"{r['win_rate_pct']:.0f}%", "#60a5fa")}
    </div>"""

    benchmark_html = ""
    if "error" not in nifty:
        benchmark_html = f"""
        <div style="background:#0f172a;border:1px solid #1e293b;border-radius:8px;padding:20px">
          <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px;text-align:center">
            <div>
              <div style="font-size:22px;font-weight:800;color:{xirr_color}">{r['xirr_pct']:+.1f}%</div>
              <div style="font-size:11px;color:#64748b">Your XIRR</div>
            </div>
            <div>
              <div style="font-size:22px;font-weight:800;color:#94a3b8">{nifty['cagr_pct']:+.1f}%</div>
              <div style="font-size:11px;color:#64748b">Nifty 50 CAGR</div>
            </div>
            <div>
              <div style="font-size:22px;font-weight:800;color:{alpha_color}">{alpha:+.1f}%</div>
              <div style="font-size:11px;color:#64748b">Your Alpha</div>
            </div>
          </div>
        </div>"""
    else:
        benchmark_html = "<div style='color:#64748b;padding:20px'>Nifty benchmark unavailable.</div>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><title>HedgeFusion Analytics — {timestamp}</title>
<style>
  body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
       background:#050d1a;color:#e2e8f0;margin:0;padding:20px}}
  .wrap{{max-width:900px;margin:0 auto}}
  h1{{font-size:22px;font-weight:800;color:#f8fafc;margin-bottom:4px}}
  h2{{font-size:15px;font-weight:700;color:#e2e8f0;margin:28px 0 12px;
      padding-bottom:8px;border-bottom:1px solid #1e293b}}
  .meta{{font-size:12px;color:#64748b;margin-bottom:24px}}
  .explain{{background:#0f172a;border:1px solid #1e293b;border-radius:8px;padding:18px;
            font-size:13px;color:#94a3b8;line-height:1.7;margin-bottom:16px}}
  .disc{{background:#1e1a0a;border:1px solid #78350f;border-radius:8px;
         padding:14px 18px;font-size:12px;color:#92400e;margin-top:24px}}
</style>
</head>
<body><div class="wrap">
  <h1>📊 HedgeFusion Analytics</h1>
  <div class="meta">{timestamp} &nbsp;·&nbsp; First trade: {r['first_trade_date']} ({r['years_elapsed']}y ago)</div>

  {kpi_row}

  <h2>vs Nifty 50 Benchmark</h2>
  {benchmark_html}

  <h2>What these numbers mean</h2>
  <div class="explain">
    <b>XIRR</b> accounts for exactly when each rupee entered or left your account —
    the correct way to measure return when you didn't invest a lump sum on day one.<br><br>
    <b>Sharpe Ratio</b> above 1.0 is considered good, above 2.0 is excellent.
    It penalises volatility — a bumpy path to the same return scores lower.<br><br>
    <b>Alpha vs Nifty</b> is the return you generated beyond what a passive
    Nifty 50 index fund would have delivered over the same period, with zero effort.
  </div>

  <div class="disc">
    ⚠️ Based on paper trading data. Past performance does not guarantee future results.
    XIRR/CAGR/Sharpe assume trade log accuracy — verify against your actual Zerodha
    contract notes before making capital allocation decisions.
  </div>
</div></body></html>"""


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="HedgeFusion Analytics")
    parser.add_argument("--since", type=int, default=365, help="Days of history (default 365)")
    args = parser.parse_args()
    run_analytics(since_days=args.since)
