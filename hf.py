"""
HedgeFusion CLI
================
Single command hub for the entire system.
Run everything from one place.

Usage:
    python hf.py                          # interactive menu
    python hf.py run RELIANCE             # analyse single stock
    python hf.py run RELIANCE --execute   # analyse + paper trade
    python hf.py portfolio                # full portfolio pipeline
    python hf.py portfolio --execute      # portfolio + execute orders
    python hf.py screen                   # multibagger screen
    python hf.py screen --sector DEFENCE  # one sector
    python hf.py screen --ticker TITAN    # score one stock
    python hf.py watchlist                # scan watchlist
    python hf.py watchlist --add TITAN 3200
    python hf.py watchlist --show
    python hf.py journal                  # trade journal
    python hf.py backtest                 # backtest signals
    python hf.py risk                     # risk dashboard
    python hf.py sector                   # sector rotation
    python hf.py earnings                 # earnings calendar
    python hf.py alerts --test            # test alert channels
    python hf.py sizer --ticker RELIANCE --entry 1280 --stop 1220
    python hf.py analytics                # XIRR/CAGR/Sharpe/Nifty benchmark
    python hf.py feedback                 # agent confidence calibration
    python hf.py memory --ticker RELIANCE # per-ticker agent memory
    python hf.py export --what trades --format xlsx
    python hf.py scheduler                # start daily scheduler
    python hf.py config                   # show current config
    python hf.py status                   # system health check
"""

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

from loguru import logger

# ── Pretty header ─────────────────────────────────────────────

LOGO = r"""
  ██╗  ██╗███████╗██████╗  ██████╗ ███████╗
  ██║  ██║██╔════╝██╔══██╗██╔════╝ ██╔════╝
  ███████║█████╗  ██║  ██║██║  ███╗█████╗  
  ██╔══██║██╔══╝  ██║  ██║██║   ██║██╔══╝  
  ██║  ██║███████╗██████╔╝╚██████╔╝███████╗
  ╚═╝  ╚═╝╚══════╝╚═════╝  ╚═════╝ ╚══════╝
  FUSION  ·  9-Agent NSE Trading System
"""

MENU = """
  ┌─────────────────────────────────────────────┐
  │  What do you want to do?                    │
  ├─────────────────────────────────────────────┤
  │  1.  Analyse a single stock                 │
  │  2.  Run full portfolio pipeline            │
  │  3.  Multibagger screener                   │
  │  4.  Watchlist scan                         │
  │  5.  Trade journal & P&L                    │
  │  6.  Backtest signals                       │
  │  7.  Risk dashboard                         │
  │  8.  Sector rotation                        │
  │  9.  Earnings calendar                      │
  │  10. FII/DII intelligence dashboard         │
  │  11. Test alerts                            │
  │  12. Start daily scheduler                  │
  │  13. Show config                            │
  │  14. Position size calculator               │
  │  15. Analytics (XIRR/CAGR/Sharpe)           │
  │  16. Feedback engine (agent calibration)    │
  │  17. Agent memory viewer                    │
  │  18. Export data (CSV/Excel/JSON)           │
  │  0.  Exit                                   │
  └─────────────────────────────────────────────┘
"""


def status_check() -> dict:
    """Quick health check of all system components."""
    results = {}

    # Check OpenAI key
    key = os.getenv("OPENAI_API_KEY","")
    results["openai_key"]  = "✅" if key.startswith("sk-") else "❌ missing"

    # Check paper mode
    paper = os.getenv("KITE_PAPER_TRADE","true").lower() in ("true","1","yes")
    results["trading_mode"] = "📄 PAPER" if paper else "🔴 LIVE"

    # Check Kite credentials (only needed for live)
    if not paper:
        results["kite_key"]   = "✅" if os.getenv("KITE_API_KEY") else "❌ missing"
        results["kite_token"] = "✅" if os.getenv("KITE_ACCESS_TOKEN") else "❌ run kite_login.py"

    # Check output directories
    for d in ["logs", "outputs", "data/cache"]:
        p = Path(__file__).parent / d
        results[f"dir_{d.replace('/','_')}"] = "✅" if p.exists() else "creating..."
        p.mkdir(parents=True, exist_ok=True)

    # Check paper trade log
    log = Path(__file__).parent / "logs" / "paper_trades.csv"
    results["paper_log"] = f"✅ {sum(1 for _ in open(log))-1} trades" if log.exists() else "📭 empty"

    # Check last pipeline run
    outputs = list((Path(__file__).parent / "outputs").glob("*.json"))
    if outputs:
        latest  = max(outputs, key=lambda p: p.stat().st_mtime)
        age_min = int((datetime.now().timestamp() - latest.stat().st_mtime) / 60)
        results["last_run"] = f"✅ {latest.stem} ({age_min}m ago)"
    else:
        results["last_run"] = "📭 no runs yet"

    # Check config
    try:
        import config
        results["config"]    = f"✅ {len(config.HOLDINGS)} holdings, {len(config.WATCHLIST)} watchlist"
        results["model"]     = config.MODEL_NAME
    except Exception as e:
        results["config"] = f"❌ {e}"

    return results


def print_status():
    print(f"\n{'━'*55}")
    print(f"  HEDGEFUSION SYSTEM STATUS")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M IST')}")
    print(f"{'━'*55}")
    checks = status_check()
    for key, val in checks.items():
        label = key.replace("_", " ").title()
        print(f"  {label:<22} {val}")
    print(f"{'━'*55}\n")


def cmd_run(args):
    from pipeline import run_pipeline
    import config
    state = run_pipeline(
        ticker=args.ticker.upper(),
        portfolio_size_inr=config.PORTFOLIO_SIZE_INR,
        allow_execution=args.execute,
        parallel_analysts=True,
    )
    rv  = state.get("research_verdict", {})
    pm  = state.get("pm_decision", {})
    ex_ = state.get("execution_result") or {}
    print(f"\n  Result: {rv.get('recommendation','?')} | PM: {pm.get('decision','?')}")
    print(f"  Order:  {ex_.get('order_id') or ex_.get('status','—')}")


def cmd_portfolio(args):
    from portfolio_runner import run_portfolio
    run_portfolio(execute=args.execute, batch_size=getattr(args,'batch',3))


def cmd_screen(args):
    from multibagger_screener import run_screener, NSE_UNIVERSE, score_stock
    if getattr(args, "ticker", None):
        r = score_stock(args.ticker.upper())
        print(f"\n  {r['ticker']} — {r['score']}/100")
        for s in r["signals"]: print(f"  ✓ {s}")
        for f in r["flags"]:   print(f"  ⚠ {f}")
    elif getattr(args, "sector", None):
        run_screener(sector=args.sector)
    else:
        run_screener()


def cmd_watchlist(args):
    from watchlist import run_watchlist_scan, add_stock, remove_stock, show_watchlist
    if getattr(args, "add", None):
        ticker, price = args.add
        add_stock(ticker, float(price))
    elif getattr(args, "remove", None):
        remove_stock(args.remove)
    elif getattr(args, "show", False):
        show_watchlist()
    else:
        run_watchlist_scan()


def cmd_journal(args):
    from trade_journal import run_journal
    run_journal(since_days=getattr(args, "since", 90))


def cmd_backtest(args):
    from backtester import run_backtest, run_walkforward_backtest
    if getattr(args, "walkforward", False):
        run_walkforward_backtest(
            train_start=getattr(args, "train_start", "2022-01-01"),
            train_end=getattr(args, "train_end",   "2023-12-31"),
            test_start=getattr(args, "test_start",  "2024-01-01"),
            test_end=getattr(args, "test_end",    "2025-12-31"),
            stop_loss_pct=getattr(args, "sl", 5.0),
        )
    else:
        run_backtest()


def cmd_risk(args):
    from risk_dashboard import run_risk_dashboard
    run_risk_dashboard()


def cmd_sector(args):
    from sector_rotation import run_sector_rotation
    run_sector_rotation(
        use_ai=not getattr(args, "no_ai", False),
        top_n=getattr(args, "top", None),
    )


def cmd_earnings(args):
    from earnings_calendar import run_earnings_calendar
    ticker = getattr(args, "ticker", None)
    if ticker:
        from earnings_calendar import get_earnings_brief
        import json
        brief = get_earnings_brief(ticker.upper())
        print(json.dumps(brief, indent=2, default=str))
    else:
        run_earnings_calendar(
            days_ahead=getattr(args, "days", 30),
            ai_briefs=not getattr(args, "no_ai", False),
        )


def cmd_fii(args):
    from fii_dii_dashboard import run_fii_dii_dashboard
    import json
    from tools.fii_dii import get_fii_dii_summary, get_bulk_deals

    if getattr(args, "flows", False):
        s = json.loads(get_fii_dii_summary())
        print(json.dumps(s, indent=2, default=str))
    elif getattr(args, "bulk", False):
        b = json.loads(get_bulk_deals())
        print(json.dumps(b, indent=2, default=str))
    elif getattr(args, "stock", None):
        from fii_dii_dashboard import analyse_stock_fii_dii
        print(json.dumps(analyse_stock_fii_dii(args.stock.upper()), indent=2, default=str))
    else:
        tickers = [t.strip() for t in args.tickers.split(",")] if getattr(args, "tickers", None) else None
        run_fii_dii_dashboard(stock_tickers=tickers, use_ai=not getattr(args, "no_ai", False))


def cmd_alerts(args):
    from alert_system import send_test_alert
    send_test_alert()


def cmd_sizer(args):
    from position_sizer import calculate_position_size, print_result
    method = "kelly" if getattr(args, "kelly", False) else "atr" if getattr(args, "atr", False) else "fixed"
    result = calculate_position_size(
        ticker=args.ticker,
        entry_price=args.entry,
        stop_loss_price=getattr(args, "stop", None),
        risk_pct=getattr(args, "risk", 1.0),
        method=method,
    )
    print_result(result)


def cmd_analytics(args):
    from analytics import run_analytics
    run_analytics(since_days=getattr(args, "since", 365))


def cmd_feedback(args):
    from feedback_engine import run_feedback_engine
    run_feedback_engine(
        since_days=getattr(args, "since", 365),
        forward_days=getattr(args, "forward", 30),
    )


def cmd_memory(args):
    from agent_memory import _print_history, _print_stats, clear_memory
    if getattr(args, "clear", False):
        clear_memory(getattr(args, "ticker", None))
    elif getattr(args, "ticker", None):
        _print_history(args.ticker)
    else:
        _print_stats()


def cmd_export(args):
    from data_exporter import run_export
    run_export(
        what=getattr(args, "what", "all"),
        fmt=getattr(args, "format", "csv"),
        since_days=getattr(args, "since", 365),
    )


def cmd_scheduler(args):
    from scheduler import main as scheduler_main
    scheduler_main()


def cmd_config(args):
    import config
    print(f"\n  HedgeFusion Configuration")
    print(f"{'━'*55}")
    print(f"  Model:           {config.MODEL_NAME}")
    print(f"  Mode:            {config.mode_label()}")
    print(f"  Portfolio:       ₹{config.PORTFOLIO_SIZE_INR:,.0f}")
    print(f"  Holdings:        {len(config.HOLDINGS)} stocks")
    print(f"  Watchlist:       {len(config.WATCHLIST)} stocks")
    print(f"  Schedule:        {config.SCHEDULE_TIME} IST {config.SCHEDULE_MODE}")
    print(f"  Max position:    {config.MAX_POSITION_PCT}% of portfolio")
    print(f"  Max sector:      {config.MAX_SECTOR_PCT}% of portfolio")
    print(f"  Min R:R:         1:{config.MIN_RISK_REWARD}")
    print(f"\n  Holdings:")
    for h in config.HOLDINGS:
        print(f"    {h['ticker']:<14} {h['qty']:>4} shares  "
              f"[{h['sector']}]")
    print()


# ── Interactive menu ──────────────────────────────────────────

def interactive():
    print(LOGO)
    print_status()

    while True:
        print(MENU)
        choice = input("  Enter choice (0-18): ").strip()

        if choice == "0":
            print("\n  Goodbye.\n")
            sys.exit(0)

        elif choice == "1":
            ticker = input("  Enter NSE ticker (e.g. RELIANCE): ").strip().upper()
            if not ticker:
                continue
            execute = input("  Execute paper trade if approved? (y/n): ").strip().lower() == "y"
            from pipeline import run_pipeline
            import config
            run_pipeline(ticker, config.PORTFOLIO_SIZE_INR,
                         allow_execution=execute, parallel_analysts=True)

        elif choice == "2":
            execute = input("  Execute approved orders? (y/n): ").strip().lower() == "y"
            from portfolio_runner import run_portfolio
            run_portfolio(execute=execute)

        elif choice == "3":
            sec = input("  Sector (leave blank for all): ").strip().upper() or None
            from multibagger_screener import run_screener
            run_screener(sector=sec)

        elif choice == "4":
            from watchlist import run_watchlist_scan
            run_watchlist_scan()

        elif choice == "5":
            from trade_journal import run_journal
            run_journal()

        elif choice == "6":
            mode = input("  (1) Standard backtest  (2) Walk-forward [train/test split]: ").strip()
            if mode == "2":
                from backtester import run_walkforward_backtest
                run_walkforward_backtest()
            else:
                from backtester import run_backtest
                run_backtest()

        elif choice == "7":
            from risk_dashboard import run_risk_dashboard
            run_risk_dashboard()

        elif choice == "8":
            from sector_rotation import run_sector_rotation
            run_sector_rotation()

        elif choice == "9":
            days = int(input("  Days ahead (default 30): ").strip() or "30")
            from earnings_calendar import run_earnings_calendar
            run_earnings_calendar(days_ahead=days)

        elif choice == "10":
            from fii_dii_dashboard import run_fii_dii_dashboard
            run_fii_dii_dashboard()

        elif choice == "11":
            from alert_system import send_test_alert
            send_test_alert()

        elif choice == "12":
            print("  Starting scheduler... (Ctrl+C to stop)")
            from scheduler import main as sm
            sm()

        elif choice == "13":
            class FakeArgs: pass
            cmd_config(FakeArgs())

        elif choice == "14":
            ticker = input("  Ticker: ").strip().upper()
            entry  = float(input("  Entry price ₹: ").strip())
            stop_s = input("  Stop loss ₹ (blank = auto): ").strip()
            stop   = float(stop_s) if stop_s else None
            from position_sizer import calculate_position_size, print_result
            print_result(calculate_position_size(ticker, entry, stop))

        elif choice == "15":
            from analytics import run_analytics
            run_analytics()

        elif choice == "16":
            from feedback_engine import run_feedback_engine
            run_feedback_engine()

        elif choice == "17":
            ticker = input("  Ticker (blank = system stats): ").strip().upper()
            from agent_memory import _print_history, _print_stats
            _print_history(ticker) if ticker else _print_stats()

        elif choice == "18":
            what = input("  Export what? (trades/journal/holdings/watchlist/portfolio/memory/all) [all]: ").strip() or "all"
            fmt  = input("  Format? (csv/xlsx/json) [csv]: ").strip() or "csv"
            from data_exporter import run_export
            run_export(what, fmt)

        else:
            print("  Invalid choice. Please enter 0-18.")


# ── CLI parser ────────────────────────────────────────────────

def main():
    if len(sys.argv) == 1:
        interactive()
        return

    parser = argparse.ArgumentParser(
        prog="hf",
        description="HedgeFusion — 9-Agent NSE Trading System",
    )
    sub = parser.add_subparsers(dest="command")

    # run
    p_run = sub.add_parser("run", help="Analyse one stock")
    p_run.add_argument("ticker",   help="NSE ticker e.g. RELIANCE")
    p_run.add_argument("--execute",action="store_true", help="Execute if approved")

    # portfolio
    p_port = sub.add_parser("portfolio", help="Full portfolio pipeline")
    p_port.add_argument("--execute", action="store_true")
    p_port.add_argument("--batch",   type=int, default=3)

    # screen
    p_sc = sub.add_parser("screen", help="Multibagger screener")
    p_sc.add_argument("--sector", help="One sector")
    p_sc.add_argument("--ticker", help="Score one stock")

    # watchlist
    p_wl = sub.add_parser("watchlist", help="Watchlist manager")
    p_wl.add_argument("--add",    nargs=2, metavar=("TICKER","PRICE"))
    p_wl.add_argument("--remove", metavar="TICKER")
    p_wl.add_argument("--show",   action="store_true")

    # journal
    p_jn = sub.add_parser("journal", help="Trade journal")
    p_jn.add_argument("--since", type=int, default=90)

    # others
    p_bt = sub.add_parser("backtest", help="Backtest signals")
    p_bt.add_argument("--walkforward", action="store_true",
                      help="Run walk-forward backtest (train/test split)")
    p_bt.add_argument("--train-start", default="2022-01-01", dest="train_start")
    p_bt.add_argument("--train-end",   default="2023-12-31", dest="train_end")
    p_bt.add_argument("--test-start",  default="2024-01-01", dest="test_start")
    p_bt.add_argument("--test-end",    default="2025-12-31", dest="test_end")
    p_bt.add_argument("--sl",          default=5.0, type=float, help="Stop-loss %%")
    sub.add_parser("risk",     help="Risk dashboard")

    p_sec = sub.add_parser("sector", help="Sector rotation")
    p_sec.add_argument("--top",    type=int)
    p_sec.add_argument("--no-ai",  action="store_true")

    p_ec = sub.add_parser("earnings", help="Earnings calendar")
    p_ec.add_argument("--days",   type=int, default=30)
    p_ec.add_argument("--ticker", help="AI brief for one stock")
    p_ec.add_argument("--no-ai",  action="store_true")

    p_fii = sub.add_parser("fii", help="FII/DII intelligence dashboard")
    p_fii.add_argument("--stock",   help="Deep-dive one stock e.g. HDFCBANK")
    p_fii.add_argument("--tickers", help="Comma-separated tickers")
    p_fii.add_argument("--flows",   action="store_true", help="Market-level FII/DII flows only")
    p_fii.add_argument("--bulk",    action="store_true", help="Bulk/block deals only")
    p_fii.add_argument("--no-ai",   action="store_true")

    p_sz = sub.add_parser("sizer", help="Position size calculator")
    p_sz.add_argument("--ticker", required=True, help="NSE ticker e.g. RELIANCE")
    p_sz.add_argument("--entry",  required=True, type=float, help="Entry price ₹")
    p_sz.add_argument("--stop",   type=float, help="Stop loss price ₹")
    p_sz.add_argument("--risk",   type=float, default=1.0, help="Risk %% of portfolio")
    p_sz.add_argument("--kelly",  action="store_true", help="Use half-Kelly sizing")
    p_sz.add_argument("--atr",    action="store_true", help="Use ATR-based stop")

    p_an = sub.add_parser("analytics", help="XIRR / CAGR / Sharpe / Nifty benchmark")
    p_an.add_argument("--since", type=int, default=365, help="Days of history")

    p_fb = sub.add_parser("feedback", help="Agent calibration & veto effectiveness")
    p_fb.add_argument("--since",   type=int, default=365, help="Days of pipeline history")
    p_fb.add_argument("--forward", type=int, default=30,  help="Forward days to evaluate outcome")

    p_mem = sub.add_parser("memory", help="Per-ticker agent memory")
    p_mem.add_argument("--ticker", help="Show memory for one ticker")
    p_mem.add_argument("--clear",  action="store_true", help="Clear memory (ticker or all)")

    p_exp = sub.add_parser("export", help="Export data to CSV/Excel/JSON")
    p_exp.add_argument("--what",   default="all",
                       choices=["trades", "journal", "holdings", "watchlist", "portfolio", "memory", "all"])
    p_exp.add_argument("--format", default="csv", choices=["csv", "xlsx", "json"])
    p_exp.add_argument("--since",  type=int, default=365)

    sub.add_parser("alerts",    help="Test alert channels")
    sub.add_parser("scheduler", help="Start daily scheduler")
    sub.add_parser("config",    help="Show configuration")
    sub.add_parser("status",    help="System health check")

    args = parser.parse_args()

    dispatch = {
        "run":       cmd_run,
        "portfolio": cmd_portfolio,
        "screen":    cmd_screen,
        "watchlist": cmd_watchlist,
        "journal":   cmd_journal,
        "backtest":  cmd_backtest,
        "risk":      cmd_risk,
        "sector":    cmd_sector,
        "earnings":  cmd_earnings,
        "fii":       cmd_fii,
        "sizer":     cmd_sizer,
        "analytics": cmd_analytics,
        "feedback":  cmd_feedback,
        "memory":    cmd_memory,
        "export":    cmd_export,
        "alerts":    cmd_alerts,
        "scheduler": cmd_scheduler,
        "config":    cmd_config,
        "status":    lambda _: print_status(),
    }

    fn = dispatch.get(args.command)
    if fn:
        fn(args)
    else:
        interactive()


if __name__ == "__main__":
    main()
