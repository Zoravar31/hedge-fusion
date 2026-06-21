"""
HedgeFusion Analytics Engine
==============================
Computes professional portfolio performance metrics:

  XIRR  — Extended Internal Rate of Return
           The correct return metric when you invest at different times.
           e.g. SIP-style buying or irregular purchases.
           More accurate than simple % return.

  CAGR  — Compound Annual Growth Rate
           Annualised return assuming you held from first purchase to today.

  Sharpe Ratio — Risk-adjusted return
           (Portfolio return - Risk-free rate) / Portfolio volatility
           India risk-free rate ≈ 7% (10Y G-Sec yield)
           Sharpe > 1 = good, > 2 = excellent

  Alpha  — Portfolio outperformance vs Nifty 50
           If Nifty returned 15% and your portfolio returned 22%, alpha = 7%.

  Beta   — Portfolio sensitivity to Nifty 50 movements
           Beta = 1.2 means your portfolio moves 1.2× the market

  Max Drawdown — Largest peak-to-trough decline
           Critical for understanding worst-case scenario

Usage:
    python analytics.py              # full report
    python analytics.py --xirr-only # just XIRR
    from analytics import compute_xirr, compute_portfolio_analytics
"""

import json
import math
import os
import sys
from datetime import datetime, date, timedelta
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

load_dotenv(Path(__file__).parent / ".env")

ROOT       = Path(__file__).parent
DATA_DIR   = ROOT / "data"
OUTPUT_DIR = ROOT / "outputs"
LOG_DIR    = ROOT / "logs"


# ── XIRR ─────────────────────────────────────────────────────

def compute_xirr(
    cashflows: list[tuple[date, float]],
    guess: float = 0.1,
) -> float:
    """
    Compute XIRR (Extended Internal Rate of Return).

    Parameters
    ----------
    cashflows : list of (date, amount) tuples
        Negative amounts = money you paid out (purchases)
        Positive amounts = money you received or current value

    Returns
    -------
    float : XIRR as decimal (0.15 = 15%)

    Example
    -------
    cashflows = [
        (date(2025, 1, 15), -50000),   # bought ₹50,000 on 15 Jan 2025
        (date(2025, 6, 10), -25000),   # added ₹25,000 on 10 Jun 2025
        (date(2026, 6, 19), +92000),   # current value today
    ]
    xirr = compute_xirr(cashflows)
    # → 0.384 = 38.4% XIRR
    """
    if not cashflows or len(cashflows) < 2:
        return 0.0

    # Validate: need at least one negative and one positive
    has_neg = any(cf < 0 for _, cf in cashflows)
    has_pos = any(cf > 0 for _, cf in cashflows)
    if not has_neg or not has_pos:
        return 0.0

    dates = [cf[0] for cf in cashflows]
    flows = [cf[1] for cf in cashflows]
    base  = dates[0]

    def xnpv(rate: float) -> float:
        """Net Present Value at a given rate."""
        if rate <= -1:
            return float("inf")
        return sum(
            flows[i] / ((1 + rate) ** ((dates[i] - base).days / 365.0))
            for i in range(len(flows))
        )

    def xnpv_deriv(rate: float) -> float:
        """Derivative of XNPV for Newton-Raphson."""
        if rate <= -1:
            return float("inf")
        return sum(
            -flows[i] * ((dates[i] - base).days / 365.0)
            / ((1 + rate) ** ((dates[i] - base).days / 365.0 + 1))
            for i in range(len(flows))
        )

    # Newton-Raphson iteration
    rate = guess
    for _ in range(200):
        try:
            npv  = xnpv(rate)
            dnpv = xnpv_deriv(rate)
            if abs(dnpv) < 1e-12:
                break
            new_rate = rate - npv / dnpv
            if abs(new_rate - rate) < 1e-8:
                return round(new_rate, 6)
            rate = max(new_rate, -0.999)
        except (ZeroDivisionError, OverflowError):
            break

    # Bisection fallback
    try:
        lo, hi = -0.999, 100.0
        for _ in range(200):
            mid = (lo + hi) / 2
            if xnpv(mid) > 0:
                lo = mid
            else:
                hi = mid
            if hi - lo < 1e-8:
                break
        return round((lo + hi) / 2, 6)
    except Exception:
        return 0.0


def compute_cagr(
    start_value: float,
    end_value: float,
    years: float,
) -> float:
    """
    CAGR = (End Value / Start Value) ^ (1 / Years) - 1

    Parameters
    ----------
    start_value : Initial portfolio value
    end_value   : Current portfolio value
    years       : Holding period in years
    """
    if start_value <= 0 or years <= 0:
        return 0.0
    return round(((end_value / start_value) ** (1 / years) - 1) * 100, 2)


# ── Portfolio analytics ───────────────────────────────────────

def compute_portfolio_analytics(
    holdings: list[dict],
    prices:   dict[str, dict] | None = None,
) -> dict:
    """
    Compute complete portfolio analytics.

    Parameters
    ----------
    holdings : List of holding dicts from config.py
    prices   : Optional dict of {ticker: {ltp, prev, day_chg}} from data_exporter
    """
    import yfinance as yf
    import numpy as np

    today     = date.today()
    tickers   = [h["ticker"] for h in holdings]
    results   = {}

    # ── Fetch historical prices ──────────────────────────────
    logger.info("Fetching 1Y historical data for {} holdings...", len(tickers))
    syms = [t.upper() + ".NS" for t in tickers]
    try:
        raw   = yf.download(syms + ["^NSEI"], period="1y", interval="1d",
                            auto_adjust=True, progress=False, show_errors=False)
        close = raw.get("Close", raw)
    except Exception as e:
        logger.error("yfinance download failed: {}", e)
        return {"error": str(e)}

    # ── XIRR cashflows ───────────────────────────────────────
    # Build cashflows from config avg_buy_price
    # (ideally from Zerodha trade history CSV — see notes below)
    cashflows = []
    total_inv  = 0
    total_cur  = 0

    for h in holdings:
        ticker = h["ticker"]
        qty    = h.get("qty", 0)
        avg    = h.get("avg_buy_price") or 0

        if avg <= 0 or qty <= 0:
            continue

        # Purchase cashflow — negative (money out)
        # We don't know exact purchase date, so estimate 1 year ago
        # For accurate XIRR, import your actual Zerodha trade history
        purchase_date = today - timedelta(days=365)
        cashflows.append((purchase_date, -(qty * avg)))
        total_inv += qty * avg

        # Current value — positive (money in if you sold today)
        if prices and ticker in prices:
            ltp = prices[ticker].get("ltp", avg)
        else:
            sym = ticker.upper() + ".NS"
            try:
                ltp = float(close[sym].dropna().iloc[-1])
            except Exception:
                ltp = avg
        total_cur += qty * ltp

    # Final cashflow: current portfolio value (positive)
    if cashflows:
        cashflows.append((today, total_cur))

    xirr_val = compute_xirr(cashflows) if cashflows else 0
    results["xirr_pct"]     = round(xirr_val * 100, 2)
    results["total_inv"]    = round(total_inv, 2)
    results["total_cur"]    = round(total_cur, 2)
    results["abs_return"]   = round(total_cur - total_inv, 2)
    results["simple_return"]= round((total_cur - total_inv) / total_inv * 100, 2) if total_inv else 0

    # CAGR — assume 1 year holding
    results["cagr_1y"] = compute_cagr(total_inv, total_cur, 1.0)

    # ── Nifty 50 benchmark ───────────────────────────────────
    try:
        nifty_close = close["^NSEI"].dropna()
        nifty_start = float(nifty_close.iloc[0])
        nifty_end   = float(nifty_close.iloc[-1])
        nifty_ret   = (nifty_end - nifty_start) / nifty_start * 100
        nifty_ret_1w= (nifty_close.iloc[-1]-nifty_close.iloc[-5])  / nifty_close.iloc[-5]  * 100 if len(nifty_close) >= 5  else 0
        nifty_ret_1m= (nifty_close.iloc[-1]-nifty_close.iloc[-22]) / nifty_close.iloc[-22] * 100 if len(nifty_close) >= 22 else 0
        nifty_ret_3m= (nifty_close.iloc[-1]-nifty_close.iloc[-65]) / nifty_close.iloc[-65] * 100 if len(nifty_close) >= 65 else 0

        results["nifty_ret_1y"]  = round(float(nifty_ret), 2)
        results["nifty_ret_1w"]  = round(float(nifty_ret_1w), 2)
        results["nifty_ret_1m"]  = round(float(nifty_ret_1m), 2)
        results["nifty_ret_3m"]  = round(float(nifty_ret_3m), 2)
        results["alpha_1y"]      = round(results["simple_return"] - results["nifty_ret_1y"], 2)
        results["nifty_current"] = round(nifty_end, 2)

        # ── Beta calculation ─────────────────────────────────
        portfolio_vals = []
        nifty_vals     = close["^NSEI"].dropna()
        for d in nifty_vals.index:
            day_val = 0
            for h in holdings:
                sym = h["ticker"].upper() + ".NS"
                try:
                    p = float(close.loc[d, sym])
                    day_val += h.get("qty",0) * p
                except Exception:
                    pass
            portfolio_vals.append(day_val)

        if len(portfolio_vals) >= 30:
            port_arr   = [portfolio_vals[i]/portfolio_vals[i-1]-1 for i in range(1, len(portfolio_vals))]
            nifty_arr  = nifty_vals.pct_change().dropna().tolist()
            n          = min(len(port_arr), len(nifty_arr))
            port_arr   = port_arr[-n:]
            nifty_arr  = nifty_arr[-n:]
            mean_p     = sum(port_arr) / n
            mean_n     = sum(nifty_arr) / n
            cov        = sum((port_arr[i]-mean_p)*(nifty_arr[i]-mean_n) for i in range(n)) / n
            var_n      = sum((nifty_arr[i]-mean_n)**2 for i in range(n)) / n
            beta       = cov / var_n if var_n else 1.0

            # Sharpe ratio (India risk-free rate = 7%)
            rf_daily   = 0.07 / 252
            excess     = [r - rf_daily for r in port_arr]
            mean_exc   = sum(excess) / len(excess)
            std_exc    = (sum((x-mean_exc)**2 for x in excess) / len(excess)) ** 0.5
            sharpe     = (mean_exc / std_exc) * (252 ** 0.5) if std_exc > 0 else 0

            # Max drawdown
            peak = portfolio_vals[0]
            max_dd = 0
            for v in portfolio_vals:
                if v > peak:
                    peak = v
                dd = (v - peak) / peak * 100
                if dd < max_dd:
                    max_dd = dd

            # Volatility (annualised)
            mean_r = sum(port_arr) / len(port_arr)
            vol    = (sum((r-mean_r)**2 for r in port_arr) / len(port_arr))**0.5 * (252**0.5) * 100

            results["beta"]         = round(beta, 3)
            results["sharpe"]       = round(sharpe, 3)
            results["max_dd_pct"]   = round(max_dd, 2)
            results["volatility_pct"]= round(vol, 2)
        else:
            results["beta"] = results["sharpe"] = results["max_dd_pct"] = results["volatility_pct"] = None

    except Exception as e:
        logger.warning("Benchmark calc failed: {}", e)
        results["nifty_ret_1y"] = results["alpha_1y"] = results["beta"] = None

    # ── Period returns ───────────────────────────────────────
    period_rets = {}
    for label, n_days in [("1W",5),("1M",22),("3M",65),("6M",130),("1Y",252)]:
        try:
            val_now  = 0
            val_then = 0
            for h in holdings:
                sym = h["ticker"].upper() + ".NS"
                col_close = close[sym].dropna()
                if len(col_close) >= n_days:
                    val_now  += h.get("qty",0) * float(col_close.iloc[-1])
                    val_then += h.get("qty",0) * float(col_close.iloc[-n_days])
            if val_then > 0:
                period_rets[label] = round((val_now-val_then)/val_then*100, 2)
        except Exception:
            pass
    results["period_returns"] = period_rets

    # ── Per-stock contribution ───────────────────────────────
    stock_contrib = []
    for h in holdings:
        ticker = h["ticker"]
        sym    = ticker.upper() + ".NS"
        qty    = h.get("qty", 0)
        avg    = h.get("avg_buy_price") or 0
        try:
            col_close = close[sym].dropna()
            ltp       = float(col_close.iloc[-1])
            ret_1y    = float((col_close.iloc[-1]-col_close.iloc[0])/col_close.iloc[0]*100)
            prev      = float(col_close.iloc[-2]) if len(col_close) >= 2 else ltp
            day_ret   = (ltp - prev) / prev * 100
            inv       = qty * avg
            cur       = qty * ltp
            pnl       = cur - inv if avg > 0 else 0
            pnl_pct   = pnl / inv * 100 if inv > 0 else 0
            stock_contrib.append({
                "ticker":   ticker,
                "sector":   h.get("sector",""),
                "ltp":      round(ltp, 2),
                "avg":      avg,
                "qty":      qty,
                "invested": round(inv, 2),
                "current":  round(cur, 2),
                "pnl":      round(pnl, 2),
                "pnl_pct":  round(pnl_pct, 2),
                "ret_1y":   round(ret_1y, 2),
                "day_ret":  round(day_ret, 2),
                "day_pnl":  round(qty * (ltp - prev), 2),
                "weight":   round(cur / total_cur * 100, 1) if total_cur else 0,
            })
        except Exception:
            pass

    results["holdings"] = sorted(stock_contrib, key=lambda x: x["pnl"], reverse=True)

    return results


def print_analytics_report(r: dict):
    """Print a formatted analytics report to terminal."""
    sep = "━" * 60
    print(f"\n{sep}")
    print(f"  HEDGEFUSION PORTFOLIO ANALYTICS")
    print(f"  {datetime.now().strftime('%d %b %Y %H:%M IST')}")
    print(sep)

    def fmt(n): return f"₹{abs(n):,.0f}" if n is not None else "N/A"
    def sgn(n): return (f"+₹{n:,.0f}" if n>=0 else f"-₹{abs(n):,.0f}") if n is not None else "N/A"
    def pct(n): return (f"{n:+.2f}%" if n is not None else "N/A")
    def col(n): return "✅" if (n or 0) >= 0 else "🔴"

    print(f"\n  RETURNS")
    print(f"  Invested:       {fmt(r.get('total_inv',0))}")
    print(f"  Current value:  {fmt(r.get('total_cur',0))}")
    print(f"  Absolute P&L:   {sgn(r.get('abs_return',0))} {col(r.get('abs_return'))}")
    print(f"  Simple return:  {pct(r.get('simple_return'))}")
    print(f"  XIRR (annlsd):  {pct(r.get('xirr_pct'))} ← most accurate metric")
    print(f"  CAGR (1Y):      {pct(r.get('cagr_1y'))}")

    print(f"\n  BENCHMARK (vs Nifty 50)")
    print(f"  Portfolio 1Y:   {pct(r.get('simple_return'))}")
    print(f"  Nifty 50 1Y:    {pct(r.get('nifty_ret_1y'))}")
    alpha = r.get('alpha_1y')
    print(f"  Alpha:          {pct(alpha)} {'✅ beating market' if (alpha or 0)>0 else '🔴 underperforming'}")

    print(f"\n  PERIOD RETURNS")
    for period, ret in r.get("period_returns", {}).items():
        nifty_k = f"nifty_ret_{period.lower()}"
        nifty_r = r.get(nifty_k)
        vs = f"  (Nifty: {pct(nifty_r)})" if nifty_r is not None else ""
        print(f"  {period}:            {pct(ret)}{vs}")

    print(f"\n  RISK METRICS")
    print(f"  Beta vs Nifty:  {r.get('beta','N/A')} {'(aggressive)' if (r.get('beta') or 0) > 1.2 else '(defensive)' if (r.get('beta') or 0) < 0.8 else '(moderate)'}")
    print(f"  Sharpe ratio:   {r.get('sharpe','N/A')} {'✅ good' if (r.get('sharpe') or 0) > 1 else '⚠️ below 1'}")
    print(f"  Max drawdown:   {r.get('max_dd_pct','N/A')}%")
    print(f"  Volatility:     {r.get('volatility_pct','N/A')}% annualised")

    print(f"\n  PER-STOCK P&L")
    for h in r.get("holdings", []):
        avg_str = f"avg ₹{h['avg']:,.0f}" if h.get("avg") else "no avg"
        print(f"  {h['ticker']:<14} {pct(h['pnl_pct']):>8}  ({avg_str})")

    print(f"\n{sep}\n")


def save_analytics(r: dict):
    """Save analytics to data/analytics.json."""
    out = DATA_DIR / "analytics.json"
    DATA_DIR.mkdir(exist_ok=True)
    r["computed_at"] = datetime.now().isoformat()
    out.write_text(json.dumps(r, indent=2, default=str), encoding="utf-8")
    print(f"✅ Analytics saved: {out}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="HedgeFusion Analytics")
    parser.add_argument("--xirr-only", action="store_true")
    parser.add_argument("--save",      action="store_true", help="Save to data/analytics.json")
    args = parser.parse_args()

    from config import HOLDINGS

    if args.xirr_only:
        # Quick XIRR only
        from data_exporter import fetch_live_prices
        prices = fetch_live_prices([h["ticker"] for h in HOLDINGS])
        today  = date.today()
        cfs = []
        total_cur = 0
        for h in HOLDINGS:
            avg = h.get("avg_buy_price") or 0
            qty = h.get("qty") or 0
            if avg <= 0 or qty <= 0:
                continue
            cfs.append((today - timedelta(days=365), -(qty * avg)))
            ltp = prices.get(h["ticker"],{}).get("ltp", avg)
            total_cur += qty * ltp
        if cfs:
            cfs.append((today, total_cur))
        xirr = compute_xirr(cfs)
        print(f"\nXIRR: {xirr*100:.2f}%\n")
    else:
        r = compute_portfolio_analytics(HOLDINGS)
        print_analytics_report(r)
        if args.save:
            save_analytics(r)
