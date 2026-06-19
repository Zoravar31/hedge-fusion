"""
HedgeFusion Scheduler
======================
Autonomous daily runner. Runs every trading day at SCHEDULE_TIME (IST).

What it does each morning:
  1. Checks if today is a trading day (skips weekends + NSE holidays)
  2. Runs the 9-agent pipeline on all SCHEDULE_TICKERS
  3. Executes PM-approved orders (paper or live, per KITE_PAPER_TRADE)
  4. Checks open paper positions against stop-loss levels
  5. Runs FII/DII market flow summary
  6. Sends Telegram/email alerts for BUY ZONEs and SL hits
  7. Sends daily portfolio digest

Usage:
    python scheduler.py          # starts scheduler loop
    python hf.py scheduler       # same via CLI

Configure in .env:
    SCHEDULE_TIME=09:30          # 9:30 AM IST
    SCHEDULE_TICKERS=RELIANCE,TCS,HDFCBANK
    SCHEDULE_EXECUTE=false       # set true to auto-execute
"""

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

from loguru import logger

from config import (
    SCHEDULE_MODE, SCHEDULE_TIME, SCHEDULE_TICKERS,
    SCHEDULE_EXECUTE, SCHEDULE_INTERVAL_HOURS,
    MARKET_HOLIDAYS_2026, HOLDINGS, PORTFOLIO_SIZE_INR,
)
from pipeline import run_pipeline


# ── Trading day check ─────────────────────────────────────────

def is_trading_day() -> bool:
    today = datetime.now()
    if today.weekday() >= 5:
        return False
    if today.strftime("%Y-%m-%d") in MARKET_HOLIDAYS_2026:
        return False
    return True


# ── Stop-loss monitor ─────────────────────────────────────────

def check_stop_losses() -> list[dict]:
    """
    Check all open paper positions against their stop-loss levels.
    Returns list of positions that have breached their SL.
    """
    from tools.kite_execution import _PORTFOLIO
    from tools.india_data import get_nse_quote

    breaches = []
    for sym, pos in _PORTFOLIO.items():
        if pos["qty"] <= 0:
            continue
        try:
            quote   = json.loads(get_nse_quote(sym))
            ltp     = quote.get("info", {}).get("currentPrice") or \
                      quote.get("latest_close") or pos["avg_price"]
            ltp     = float(ltp)
            avg     = float(pos["avg_price"])
            loss_pct = (ltp - avg) / avg * 100

            # Get the holding's stop loss config
            from config import get_holding
            holding  = get_holding(sym) or {}
            sl_pct   = holding.get("stop_loss_pct", 5.0)

            if loss_pct < -sl_pct:
                breaches.append({
                    "symbol":       sym,
                    "qty":          pos["qty"],
                    "avg_price":    avg,
                    "current_price":ltp,
                    "loss_pct":     round(loss_pct, 2),
                    "sl_pct":       sl_pct,
                })
                logger.warning("SL BREACH: {} down {:.1f}% from avg ₹{:.2f}",
                               sym, abs(loss_pct), avg)
        except Exception as e:
            logger.debug("SL check failed {}: {}", sym, e)

    return breaches


# ── FII/DII morning brief ──────────────────────────────────────

def run_fii_brief() -> str:
    """Quick FII/DII market summary for the morning."""
    try:
        from tools.fii_dii import get_fii_dii_summary
        summary = json.loads(get_fii_dii_summary())
        signal  = summary.get("market_signal_5d", "UNKNOWN")
        interp  = summary.get("interpretation", "")[:120]
        fii_5d  = summary.get("fii_flows", {}).get("5day_cr")
        dii_5d  = summary.get("dii_flows", {}).get("5day_cr")

        brief = f"FII/DII Signal: {signal}"
        if fii_5d is not None:
            brief += f" | FII 5d: {'+'if fii_5d>0 else ''}₹{fii_5d:,.0f}Cr"
        if dii_5d is not None:
            brief += f" | DII 5d: {'+'if(dii_5d or 0)>0 else ''}₹{(dii_5d or 0):,.0f}Cr"
        return brief
    except Exception as e:
        logger.debug("FII brief failed: {}", e)
        return "FII/DII data unavailable"


# ── Main scheduled run ────────────────────────────────────────

def run_scheduled():
    """Run the full daily trading pipeline."""
    if not is_trading_day():
        logger.info("Not a trading day — skipping run")
        return

    mode  = "PAPER" if os.getenv("KITE_PAPER_TRADE","true").lower() in ("true","1","yes") else "LIVE"
    now   = datetime.now().strftime("%Y-%m-%d %H:%M")
    logger.info("=" * 60)
    logger.info("  HedgeFusion Daily Run | {} | {} | {} tickers",
                now, mode, len(SCHEDULE_TICKERS))
    logger.info("=" * 60)

    # ── Step 1: FII/DII morning brief ────────────────────────
    logger.info("Step 1: FII/DII Market Brief")
    fii_brief = run_fii_brief()
    logger.info("  {}", fii_brief)

    # ── Step 2: Run pipeline on all tickers ──────────────────
    logger.info("Step 2: Running pipeline on {} stocks", len(SCHEDULE_TICKERS))
    results = []
    for ticker in SCHEDULE_TICKERS:
        try:
            logger.info("  Analysing {}...", ticker)
            state = run_pipeline(
                ticker=ticker,
                portfolio_size_inr=PORTFOLIO_SIZE_INR,
                allow_execution=SCHEDULE_EXECUTE,
                parallel_analysts=True,
            )
            results.append(state)
            rv  = state.get("research_verdict", {})
            pm  = state.get("pm_decision", {})
            ex  = state.get("execution_result") or {}
            logger.info("  {} -> {} | PM: {} | Order: {}",
                        ticker,
                        rv.get("recommendation", "?"),
                        pm.get("decision", "?"),
                        ex.get("order_id") or ex.get("status", "—"))
        except Exception as e:
            logger.error("  Pipeline failed for {}: {}", ticker, e)

    # ── Step 3: Stop-loss monitoring ─────────────────────────
    logger.info("Step 3: Stop-loss monitoring")
    try:
        breaches = check_stop_losses()
        if breaches:
            logger.warning("  {} stop-loss breach(es) detected:", len(breaches))
            for b in breaches:
                logger.warning("    {} down {:.1f}% (SL: {:.1f}%)",
                               b["symbol"], abs(b["loss_pct"]), b["sl_pct"])
                # Send alert
                try:
                    from alert_system import alert_stop_loss_hit
                    alert_stop_loss_hit(
                        ticker=b["symbol"],
                        entry_price=b["avg_price"],
                        current_price=b["current_price"],
                        loss_pct=b["loss_pct"],
                    )
                except Exception:
                    pass
        else:
            logger.info("  All positions within stop-loss limits")
    except Exception as e:
        logger.debug("SL monitoring error: {}", e)

    # ── Step 4: Watchlist BUY ZONE scan ──────────────────────
    logger.info("Step 4: Watchlist scan")
    try:
        from watchlist import run_watchlist_scan
        wl_results = run_watchlist_scan()
        buy_zones  = [r for r in wl_results if r.get("alert_level") == "BUY_ZONE"]
        if buy_zones:
            logger.info("  BUY ZONE alerts: {}", ", ".join(r["ticker"] for r in buy_zones))
            try:
                from alert_system import alert_buy_zone
                for r in buy_zones:
                    alert_buy_zone(
                        ticker=r["ticker"],
                        current_price=float(r.get("current_price", 0)),
                        target_price=float(r.get("entry_target", 0)),
                        stop_loss=float(r.get("stop_loss", 0)),
                        note=r.get("note", ""),
                    )
            except Exception:
                pass
        else:
            logger.info("  No BUY ZONE alerts today")
    except Exception as e:
        logger.debug("Watchlist scan error: {}", e)

    # ── Step 5: Daily digest alert ────────────────────────────
    logger.info("Step 5: Sending daily digest")
    try:
        from alert_system import send_daily_summary
        send_daily_summary(results)
    except Exception as e:
        logger.debug("Daily digest error: {}", e)

    logger.info("Daily run complete. Next run tomorrow at {}", SCHEDULE_TIME)


# ── Scheduler loop ────────────────────────────────────────────

def main():
    logger.info("HedgeFusion Scheduler started")
    logger.info("  Mode:     {}", SCHEDULE_MODE)
    logger.info("  Time:     {} IST (trading days only)", SCHEDULE_TIME)
    logger.info("  Tickers:  {}", ", ".join(SCHEDULE_TICKERS))
    logger.info("  Execute:  {}", SCHEDULE_EXECUTE)
    logger.info("  Press Ctrl+C to stop")

    if SCHEDULE_MODE == "interval":
        hours = int(SCHEDULE_INTERVAL_HOURS)
        logger.info("  Interval: every {} hours", hours)
        while True:
            run_scheduled()
            logger.info("Sleeping {} hours...", hours)
            time.sleep(hours * 3600)

    else:  # daily mode — fire once per day at SCHEDULE_TIME
        h, m = map(int, SCHEDULE_TIME.split(":"))
        last_run_date = None
        logger.info("Waiting for {} IST each trading day...", SCHEDULE_TIME)
        while True:
            now = datetime.now()
            today = now.strftime("%Y-%m-%d")
            if (now.hour == h and now.minute == m and today != last_run_date):
                last_run_date = today
                run_scheduled()
            time.sleep(30)


if __name__ == "__main__":
    main()
