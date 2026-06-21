"""
HedgeFusion Data Exporter
===========================
Reads all pipeline JSON outputs, paper trade CSV, and live prices —
then writes a single dashboard_data.json that the portfolio dashboard
reads on every page load.

Run this after every pipeline run:
    python data_exporter.py

Or it auto-runs at the end of portfolio_runner.py when called with --export.

Output: data/dashboard_data.json
"""

import csv
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

from loguru import logger

ROOT       = Path(__file__).parent
DATA_DIR   = ROOT / "data"
OUTPUT_DIR = ROOT / "outputs"
LOG_DIR    = ROOT / "logs"
PAPER_LOG  = LOG_DIR / "paper_trades.csv"
DASH_FILE  = DATA_DIR / "dashboard_data.json"
DATA_DIR.mkdir(exist_ok=True)


# ── Live price fetcher ────────────────────────────────────────

def fetch_live_prices(tickers: list[str]) -> dict[str, dict]:
    """Fetch current prices for all holdings via yfinance."""
    prices = {}
    try:
        import yfinance as yf
        syms = [t.upper() + ".NS" for t in tickers]
        data = yf.download(
            syms, period="2d", interval="1d",
            auto_adjust=True, progress=False, show_errors=False,
        )
        close = data.get("Close", data)
        for ticker in tickers:
            sym = ticker.upper() + ".NS"
            try:
                hist = close[sym].dropna()
                if len(hist) >= 2:
                    ltp  = float(hist.iloc[-1])
                    prev = float(hist.iloc[-2])
                    prices[ticker] = {
                        "ltp":      round(ltp, 2),
                        "prev":     round(prev, 2),
                        "day_chg":  round((ltp - prev) / prev * 100, 2),
                        "day_abs":  round(ltp - prev, 2),
                    }
                elif len(hist) == 1:
                    prices[ticker] = {"ltp": float(hist.iloc[-1]), "prev": 0, "day_chg": 0, "day_abs": 0}
            except Exception:
                pass
    except Exception as e:
        logger.warning("Price fetch failed: {}", e)
    return prices


def fetch_nifty_data(period: str = "1y") -> dict:
    """Fetch Nifty 50 data for benchmark comparison."""
    try:
        import yfinance as yf
        nifty = yf.Ticker("^NSEI")
        hist  = nifty.history(period=period, interval="1d")
        if hist is None or hist.empty:
            return {}
        closes = hist["Close"].dropna()
        dates  = [d.strftime("%d %b") for d in closes.index]
        vals   = [round(float(v), 2) for v in closes]

        ret_1w  = (vals[-1] - vals[-5])  / vals[-5]  * 100 if len(vals) >= 5   else 0
        ret_1m  = (vals[-1] - vals[-22]) / vals[-22] * 100 if len(vals) >= 22  else 0
        ret_3m  = (vals[-1] - vals[-65]) / vals[-65] * 100 if len(vals) >= 65  else 0
        ret_1y  = (vals[-1] - vals[0])   / vals[0]   * 100

        return {
            "current":  vals[-1],
            "dates":    dates[-252:],
            "values":   vals[-252:],
            "ret_1w":   round(ret_1w, 2),
            "ret_1m":   round(ret_1m, 2),
            "ret_3m":   round(ret_3m, 2),
            "ret_1y":   round(ret_1y, 2),
            "day_chg":  round((vals[-1] - vals[-2]) / vals[-2] * 100, 2) if len(vals) >= 2 else 0,
        }
    except Exception as e:
        logger.warning("Nifty fetch failed: {}", e)
        return {}


# ── Pipeline output reader ────────────────────────────────────

def read_pipeline_outputs(days: int = 30) -> list[dict]:
    """Read all pipeline JSON outputs from the last N days."""
    cutoff  = datetime.now() - timedelta(days=days)
    outputs = []
    for p in OUTPUT_DIR.glob("*.json"):
        try:
            mtime = datetime.fromtimestamp(p.stat().st_mtime)
            if mtime < cutoff:
                continue
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "ticker" in data:
                data["_file"]  = p.name
                data["_mtime"] = mtime.isoformat()
                outputs.append(data)
        except Exception:
            pass
    # Sort: newest first, deduplicate by ticker (keep most recent per ticker)
    outputs.sort(key=lambda x: x["_mtime"], reverse=True)
    seen    = set()
    deduped = []
    for o in outputs:
        t = o.get("ticker","")
        if t not in seen:
            seen.add(t)
            deduped.append(o)
    return deduped


def extract_signals(outputs: list[dict]) -> list[dict]:
    """Extract clean signal data from pipeline outputs for the dashboard."""
    signals = []
    for o in outputs:
        rv  = o.get("research_verdict") or {}
        pm  = o.get("pm_decision") or {}
        ex  = o.get("execution_result") or {}
        bull= o.get("bull") or {}
        bear= o.get("bear") or {}
        signals.append({
            "ticker":      o.get("ticker",""),
            "run_time":    o.get("_mtime",""),
            "recommendation": rv.get("recommendation",""),
            "confidence":  rv.get("confidence",""),
            "entry_zone":  rv.get("entry_zone",""),
            "stop_loss":   rv.get("stop_loss"),
            "target1":     rv.get("target1"),
            "target2":     rv.get("target2"),
            "risk_reward": rv.get("risk_reward",""),
            "debate_verdict": rv.get("debate_verdict","")[:120] if rv.get("debate_verdict") else "",
            "pm_decision": pm.get("decision",""),
            "pm_note":     pm.get("pm_note","")[:100] if pm.get("pm_note") else "",
            "order_id":    ex.get("order_id",""),
            "fill_price":  ex.get("fill_price"),
            "bull_conviction": bull.get("conviction"),
            "bear_conviction": bear.get("conviction"),
            "elapsed_s":   o.get("elapsed_seconds"),
        })
    return signals


# ── Paper trade reader ────────────────────────────────────────

def read_paper_trades() -> list[dict]:
    """Read all paper trades from CSV log."""
    if not PAPER_LOG.exists():
        return []
    trades = []
    with open(PAPER_LOG, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                row["fill_price"]  = float(row.get("fill_price") or 0)
                row["quantity"]    = int(row.get("quantity") or 0)
                row["value_inr"]   = float(row.get("value_inr") or 0)
                row["stop_loss"]   = float(row.get("stop_loss") or 0) if row.get("stop_loss") else None
                row["take_profit"] = float(row.get("take_profit") or 0) if row.get("take_profit") else None
                trades.append(row)
            except Exception:
                pass
    return trades


# ── Portfolio builder ─────────────────────────────────────────

def build_portfolio_data(
    holdings: list[dict],
    prices:   dict[str, dict],
    trades:   list[dict],
) -> dict:
    """
    Build complete portfolio data combining config holdings,
    live prices, and paper trade history.
    """
    rows = []
    for h in holdings:
        ticker = h["ticker"]
        price  = prices.get(ticker, {})
        ltp    = price.get("ltp", 0)
        prev   = price.get("prev", 0)

        # Use live LTP if available, else fall back to avg_buy_price
        if ltp == 0:
            ltp = h.get("avg_buy_price", 0) or 0

        avg    = h.get("avg_buy_price") or 0
        qty    = h.get("qty") or 0
        inv    = qty * avg
        cur    = qty * ltp
        pnl    = cur - inv if avg > 0 else 0
        pnlp   = pnl / inv * 100 if inv > 0 else 0
        day_chg= price.get("day_chg", 0)
        day_abs= price.get("day_abs", 0)
        day_pnl= qty * day_abs

        rows.append({
            "ticker":    ticker,
            "sector":    h.get("sector",""),
            "qty":       qty,
            "avg":       avg,
            "ltp":       ltp,
            "invested":  round(inv, 2),
            "current":   round(cur, 2),
            "pnl":       round(pnl, 2),
            "pnl_pct":   round(pnlp, 2),
            "day_chg":   day_chg,
            "day_pnl":   round(day_pnl, 2),
            "data_live": ltp > 0 and prev > 0,
        })

    total_inv  = sum(r["invested"] for r in rows)
    total_cur  = sum(r["current"]  for r in rows)
    total_pnl  = total_cur - total_inv
    total_pct  = total_pnl / total_inv * 100 if total_inv > 0 else 0
    day_pnl    = sum(r["day_pnl"]  for r in rows)
    day_pct    = day_pnl / total_cur * 100 if total_cur > 0 else 0

    # Sector breakdown
    sectors = {}
    for r in rows:
        s = r["sector"] or "Other"
        sectors[s] = sectors.get(s, 0) + r["current"]

    return {
        "rows":       rows,
        "total_inv":  round(total_inv, 2),
        "total_cur":  round(total_cur, 2),
        "total_pnl":  round(total_pnl, 2),
        "total_pct":  round(total_pct, 2),
        "day_pnl":    round(day_pnl, 2),
        "day_pct":    round(day_pct, 2),
        "sectors":    {k: round(v, 2) for k,v in
                       sorted(sectors.items(), key=lambda x: x[1], reverse=True)},
        "paper_trades": len(trades),
        "open_positions": len([r for r in rows if r["qty"] > 0]),
    }


# ── Equity curve builder ──────────────────────────────────────

def build_equity_curve(
    holdings: list[dict],
    prices:   dict[str, dict],
    period:   str = "1y",
) -> dict:
    """Build portfolio equity curve by summing all holdings historical prices."""
    try:
        import yfinance as yf
        tickers = [h["ticker"].upper() + ".NS" for h in holdings]
        n_days  = {"1W":7,"1M":22,"3M":65,"6M":130,"1Y":252}.get(period, 252)

        data  = yf.download(tickers, period="1y", interval="1d",
                            auto_adjust=True, progress=False, show_errors=False)
        close = data.get("Close", data)

        # Build daily portfolio value
        dates  = []
        values = []
        idx    = close.index[-n_days:]

        for date in idx:
            day_val = 0
            for h in holdings:
                sym = h["ticker"].upper() + ".NS"
                try:
                    price = float(close.loc[date, sym])
                    day_val += h.get("qty", 0) * price
                except Exception:
                    # Use avg_buy_price as fallback
                    day_val += h.get("qty",0) * (h.get("avg_buy_price") or 0)
            dates.append(date.strftime("%d %b"))
            values.append(round(day_val, 2))

        return {
            "dates":   dates,
            "values":  values,
            "start":   values[0] if values else 0,
            "end":     values[-1] if values else 0,
            "return":  round((values[-1]-values[0])/values[0]*100, 2) if values and values[0] else 0,
        }
    except Exception as e:
        logger.warning("Equity curve build failed: {}", e)
        return {"dates": [], "values": [], "start": 0, "end": 0, "return": 0}


# ── Main exporter ─────────────────────────────────────────────

def export_dashboard_data(silent: bool = False) -> dict:
    """
    Build and write the complete dashboard data JSON.
    Called after every pipeline run.
    """
    from config import HOLDINGS

    if not silent:
        print("Building dashboard data...")

    tickers = [h["ticker"] for h in HOLDINGS]

    # Fetch live data
    if not silent: print("  Fetching live prices...")
    prices = fetch_live_prices(tickers)
    if not silent: print(f"  Got prices for {len(prices)}/{len(tickers)} stocks")

    if not silent: print("  Fetching Nifty 50 data...")
    nifty = fetch_nifty_data()

    # Read pipeline outputs
    if not silent: print("  Reading pipeline outputs...")
    outputs = read_pipeline_outputs(days=30)
    signals = extract_signals(outputs)
    if not silent: print(f"  Found {len(signals)} recent pipeline runs")

    # Read paper trades
    trades = read_paper_trades()
    if not silent: print(f"  Paper trades: {len(trades)}")

    # Build portfolio
    portfolio = build_portfolio_data(HOLDINGS, prices, trades)

    # Read agent memory
    memory = {}
    mem_dir = DATA_DIR / "memory"
    if mem_dir.exists():
        for f in mem_dir.glob("*.json"):
            try:
                memory[f.stem] = json.loads(f.read_text())
            except Exception:
                pass

    # Read feedback
    feedback = {}
    fb_file = DATA_DIR / "feedback" / "outcomes.json"
    if fb_file.exists():
        try:
            feedback = json.loads(fb_file.read_text())
        except Exception:
            pass

    dashboard = {
        "generated_at":  datetime.now().isoformat(),
        "market_open":   _is_market_open(),
        "portfolio":     portfolio,
        "signals":       signals,
        "paper_trades":  trades[-50:],  # last 50 trades
        "nifty":         nifty,
        "agent_memory":  memory,
        "feedback":      feedback,
        "meta": {
            "total_pipeline_runs": len(outputs),
            "last_run":           outputs[0].get("_mtime","") if outputs else "",
            "paper_mode":         os.getenv("KITE_PAPER_TRADE","true").lower() in ("true","1"),
        }
    }

    DASH_FILE.write_text(
        json.dumps(dashboard, indent=2, default=str),
        encoding="utf-8"
    )
    if not silent:
        print(f"\n✅ Dashboard data written: {DASH_FILE}")
        print(f"   Portfolio value: ₹{portfolio['total_cur']:,.0f}")
        print(f"   Total P&L:       ₹{portfolio['total_pnl']:+,.0f} ({portfolio['total_pct']:+.2f}%)")
        print(f"   Signals:         {len(signals)}")

    return dashboard


def _is_market_open() -> bool:
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    h, m = now.hour, now.minute
    return (h == 9 and m >= 15) or (10 <= h <= 14) or (h == 15 and m <= 30)


if __name__ == "__main__":
    export_dashboard_data()
