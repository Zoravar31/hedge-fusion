"""
HedgeFusion Scheduler
======================
Runs the pipeline automatically on a schedule.
Use this for autonomous operation after paper testing.

Schedule options:
  SCHEDULE_MODE=daily     → runs once at SCHEDULE_TIME each trading day
  SCHEDULE_MODE=interval  → runs every SCHEDULE_INTERVAL_HOURS hours

Usage:
    python scheduler.py

Configure in .env:
    SCHEDULE_MODE=daily
    SCHEDULE_TIME=09:30           # 9:30 AM IST (after market open)
    SCHEDULE_TICKERS=RELIANCE,TCS,HDFCBANK
    SCHEDULE_EXECUTE=false        # set true to auto-execute approved orders
"""

import os
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

from loguru import logger
from pipeline import run_pipeline

SCHEDULE_MODE     = os.getenv("SCHEDULE_MODE", "daily")
SCHEDULE_TIME     = os.getenv("SCHEDULE_TIME", "09:30")          # HH:MM IST
SCHEDULE_INTERVAL = int(os.getenv("SCHEDULE_INTERVAL_HOURS", "24"))
SCHEDULE_TICKERS  = [
    t.strip().upper()
    for t in os.getenv("SCHEDULE_TICKERS", "RELIANCE,HDFCBANK,ICICIBANK").split(",")
    if t.strip()
]
EXECUTE           = os.getenv("SCHEDULE_EXECUTE", "false").lower() in ("true", "1", "yes")
PORTFOLIO_SIZE    = float(os.getenv("PORTFOLIO_SIZE_INR", "500000"))

# Indian market holidays 2026 — update annually
MARKET_HOLIDAYS_2026 = {
    "2026-01-26",  # Republic Day
    "2026-03-25",  # Holi
    "2026-04-02",  # Ram Navami
    "2026-04-10",  # Good Friday
    "2026-04-14",  # Dr. Ambedkar Jayanti
    "2026-05-01",  # Maharashtra Day
    "2026-08-15",  # Independence Day
    "2026-10-02",  # Gandhi Jayanti
    "2026-10-20",  # Diwali Laxmi Puja
    "2026-10-21",  # Diwali Balipratipada
    "2026-11-05",  # Gurunanak Jayanti
    "2026-12-25",  # Christmas
}


def is_trading_day() -> bool:
    """Return True if today is a NSE trading day."""
    today = datetime.now()
    if today.weekday() >= 5:      # Saturday = 5, Sunday = 6
        return False
    if today.strftime("%Y-%m-%d") in MARKET_HOLIDAYS_2026:
        return False
    return True


def run_scheduled():
    """Run the pipeline for all scheduled tickers."""
    if not is_trading_day():
        logger.info("Not a trading day today — skipping run")
        return

    mode = "PAPER" if os.getenv("KITE_PAPER_TRADE", "true").lower() in ("true","1","yes") else "LIVE 🔴"
    logger.info("━━━ Scheduled run | {} | {} tickers | {} ━━━",
                datetime.now().strftime("%Y-%m-%d %H:%M"),
                len(SCHEDULE_TICKERS), mode)

    for ticker in SCHEDULE_TICKERS:
        try:
            logger.info("Running pipeline for {}...", ticker)
            state = run_pipeline(
                ticker=ticker,
                portfolio_size_inr=PORTFOLIO_SIZE,
                allow_execution=EXECUTE,
                parallel_analysts=True,
            )
            pm = state.get("pm_decision", {})
            logger.info("{}: {} | {}", ticker,
                        state.get("research_verdict", {}).get("recommendation", "?"),
                        pm.get("decision", "?"))
        except Exception as e:
            logger.error("Scheduled run failed for {}: {}", ticker, e)


def main():
    logger.info("HedgeFusion Scheduler started")
    logger.info("Mode: {} | Time: {} IST | Tickers: {}",
                SCHEDULE_MODE, SCHEDULE_TIME, ", ".join(SCHEDULE_TICKERS))
    logger.info("Execute orders: {}", EXECUTE)

    if SCHEDULE_MODE == "interval":
        logger.info("Running every {} hours", SCHEDULE_INTERVAL)
        while True:
            run_scheduled()
            logger.info("Next run in {} hours", SCHEDULE_INTERVAL)
            time.sleep(SCHEDULE_INTERVAL * 3600)

    else:  # daily
        logger.info("Running daily at {} IST on trading days", SCHEDULE_TIME)
        while True:
            now = datetime.now()
            target_h, target_m = map(int, SCHEDULE_TIME.split(":"))
            if now.hour == target_h and now.minute == target_m:
                run_scheduled()
                time.sleep(61)  # avoid double-firing within the same minute
            time.sleep(30)  # check every 30 seconds


if __name__ == "__main__":
    main()
