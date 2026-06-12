"""
HedgeFusion — Main Entry Point
================================
Run a single stock or your entire Zerodha portfolio through
the 9-agent pipeline.

Usage:
    # Single stock, paper trade
    python main.py RELIANCE

    # Single stock, analysis only (no execution)
    python main.py RELIANCE --no-execute

    # Full portfolio analysis
    python main.py --portfolio

    # Full portfolio with execution
    python main.py --portfolio --execute
"""

import argparse
import json
import os
import sys
from pathlib import Path
from datetime import datetime

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

from loguru import logger
from pipeline import run_pipeline
from tools.kite_execution import get_paper_portfolio

# ── Configure Loguru ──────────────────────────────────────────
logger.remove()
logger.add(
    sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{message}</level>",
    level="INFO",
    colorize=True,
)
logger.add(
    Path(__file__).parent / "logs" / "hedge_fusion_{time:YYYYMMDD}.log",
    rotation="1 day",
    retention="14 days",
    level="DEBUG",
    encoding="utf-8",
)

# ── Your Zerodha holdings ─────────────────────────────────────
# Update qty and avg_buy_price from your Zerodha Console
HOLDINGS = [
    {"ticker": "ICICIBANK",  "qty": 10,  "avg_buy_price": 0},
    {"ticker": "BHARTIARTL", "qty": 5,   "avg_buy_price": 0},
    {"ticker": "ZOMATO",     "qty": 50,  "avg_buy_price": 0},
    {"ticker": "M&M",        "qty": 4,   "avg_buy_price": 0},
    {"ticker": "LT",         "qty": 3,   "avg_buy_price": 0},
    {"ticker": "MAZDOCK",    "qty": 2,   "avg_buy_price": 0},
    {"ticker": "BEL",        "qty": 30,  "avg_buy_price": 0},
    {"ticker": "HDFCBANK",   "qty": 8,   "avg_buy_price": 0},
    {"ticker": "HINDZINC",   "qty": 15,  "avg_buy_price": 0},
    {"ticker": "VBL",        "qty": 10,  "avg_buy_price": 0},
]

PORTFOLIO_SIZE_INR = 500_000  # Your total portfolio value in ₹


def run_single(ticker: str, execute: bool):
    print(f"\n{'━'*60}")
    print(f"  HedgeFusion: {ticker.upper()}")
    print(f"  Execution: {'ENABLED' if execute else 'DISABLED (analysis only)'}")
    print(f"  Mode: {'PAPER' if os.getenv('KITE_PAPER_TRADE','true').lower() in ('true','1','yes') else '🔴 LIVE'}")
    print(f"{'━'*60}\n")

    state = run_pipeline(
        ticker=ticker.upper(),
        portfolio_size_inr=PORTFOLIO_SIZE_INR,
        allow_execution=execute,
        parallel_analysts=True,
    )

    print_summary(state)
    return state


def run_portfolio(execute: bool):
    print(f"\n{'━'*60}")
    print(f"  HedgeFusion: Full Portfolio ({len(HOLDINGS)} stocks)")
    print(f"  This will take ~8-15 minutes and cost ~₹80-150 in OpenAI credits")
    print(f"{'━'*60}\n")

    confirm = input("Continue? (y/n): ").strip().lower()
    if confirm != "y":
        print("Cancelled.")
        return

    results = []
    for i, holding in enumerate(HOLDINGS, 1):
        print(f"\n[{i}/{len(HOLDINGS)}] Starting {holding['ticker']}...")
        try:
            state = run_pipeline(
                ticker=holding["ticker"],
                portfolio_size_inr=PORTFOLIO_SIZE_INR,
                allow_execution=execute,
                parallel_analysts=True,
            )
            results.append(state)
            pm = state.get("pm_decision", {})
            print(f"  → {pm.get('decision','?')} | {state.get('research_verdict',{}).get('recommendation','?')}")
        except Exception as e:
            logger.error("Pipeline failed for {}: {}", holding["ticker"], e)
            results.append({"ticker": holding["ticker"], "error": str(e)})

    print_portfolio_summary(results)

    # Save combined report
    out = Path(__file__).parent / "outputs" / f"portfolio_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    out.write_text(json.dumps(results, default=str, indent=2), encoding="utf-8")
    print(f"\n✅ Portfolio report saved: {out}")


def print_summary(state: dict):
    pm = state.get("pm_decision", {})
    rv = state.get("research_verdict", {})
    ex = state.get("execution_result") or {}
    ticker = state.get("ticker", "?")

    print(f"\n{'━'*60}")
    print(f"  RESULT: {ticker}")
    print(f"{'━'*60}")
    print(f"  Recommendation : {rv.get('recommendation', '?')}")
    print(f"  Confidence     : {rv.get('confidence', '?')}")
    print(f"  Signal align   : {rv.get('signal_alignment', '?')}")
    print(f"  R:R ratio      : {rv.get('risk_reward', '?')}")
    print(f"  Stop loss      : ₹{rv.get('stop_loss', '?')}")
    print(f"  Target 1       : ₹{rv.get('target1', '?')}")
    print(f"  Target 2       : ₹{rv.get('target2', '?')}")
    print(f"  PM decision    : {pm.get('decision', '?')}")
    print(f"  PM note        : {pm.get('pm_note', '?')}")
    print(f"  Order status   : {ex.get('status') or ex.get('order_id', '?')}")
    print(f"  Time taken     : {state.get('elapsed_seconds', '?')}s")
    print(f"{'━'*60}\n")


def print_portfolio_summary(results: list):
    print(f"\n{'━'*60}")
    print(f"  PORTFOLIO SUMMARY")
    print(f"{'━'*60}")
    print(f"  {'Stock':<14} {'Rec':>5} {'PM':>6} {'R:R':>6} {'Conf':>6}")
    print(f"  {'-'*50}")
    for s in results:
        if "error" in s:
            print(f"  {s.get('ticker','?'):<14} ERROR")
            continue
        rv = s.get("research_verdict", {})
        pm = s.get("pm_decision", {})
        print(
            f"  {s.get('ticker','?'):<14} "
            f"{rv.get('recommendation','?'):>5} "
            f"{pm.get('decision','?'):>6} "
            f"{str(rv.get('risk_reward','?')):>6} "
            f"{rv.get('confidence','?'):>6}"
        )
    print(f"{'━'*60}\n")


def main():
    parser = argparse.ArgumentParser(description="HedgeFusion — 9-agent NSE trading system")
    parser.add_argument("ticker", nargs="?", help="NSE ticker e.g. RELIANCE")
    parser.add_argument("--portfolio",  action="store_true", help="Run all holdings")
    parser.add_argument("--execute",    action="store_true", help="Enable order execution")
    parser.add_argument("--no-execute", action="store_true", help="Analysis only (default)")
    args = parser.parse_args()

    execute = args.execute and not args.no_execute

    if args.portfolio:
        run_portfolio(execute=execute)
    elif args.ticker:
        run_single(args.ticker, execute=execute)
    else:
        # Interactive mode
        print("\nHedgeFusion — 9-Agent NSE Trading System")
        print("─" * 40)
        ticker = input("Enter NSE ticker (or 'portfolio' for all holdings): ").strip().upper()
        if ticker == "PORTFOLIO":
            run_portfolio(execute=False)
        else:
            run_single(ticker, execute=False)


if __name__ == "__main__":
    main()
