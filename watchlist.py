"""
HedgeFusion Watchlist Manager
================================
Manages a watchlist of stocks you're monitoring but haven't bought yet.
Runs lightweight daily scans (no full 9-agent pipeline — just quant + AI)
and alerts when a watchlist stock hits a buy zone.

Usage:
    python watchlist.py                    # scan all watchlist stocks
    python watchlist.py --add TITAN 3400  # add stock with target entry price
    python watchlist.py --remove TITAN    # remove from watchlist
    python watchlist.py --show            # show current watchlist

Watchlist is saved to data/watchlist.json and persists across runs.
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

from loguru import logger
from openai import OpenAI

from tools.india_data import get_nse_quote, get_nse_history, DATA_TOOL_DEFINITIONS, DATA_TOOL_MAP
from agents.runner import run_agent, parse_json_response

client   = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
MODEL    = os.getenv("MODEL_NAME", "gpt-4o-mini")
DATA_DIR = Path(__file__).parent / "data"
WL_FILE  = DATA_DIR / "watchlist.json"
DATA_DIR.mkdir(exist_ok=True)

# ── Default watchlist — curated multibagger candidates ────────
DEFAULT_WATCHLIST = [
    {"ticker": "POLYCAB",    "entry_target": 5800,  "reason": "Cable & wires PLI beneficiary, strong ROE", "added": "2026-06-01"},
    {"ticker": "TITAN",      "entry_target": 3200,  "reason": "Premium consumption play, brand moat",      "added": "2026-06-01"},
    {"ticker": "BAJFINANCE", "entry_target": 6800,  "reason": "India credit cycle, retail lending leader",  "added": "2026-06-01"},
    {"ticker": "PERSISTENT", "entry_target": 5200,  "reason": "Mid-cap IT, AI services growth >30% YoY",   "added": "2026-06-01"},
    {"ticker": "TATAELXSI",  "entry_target": 6800,  "reason": "EV software, design engineering moat",      "added": "2026-06-01"},
    {"ticker": "COFORGE",    "entry_target": 7500,  "reason": "IT mid-cap, strong deal wins",               "added": "2026-06-01"},
    {"ticker": "GRINDWELL",  "entry_target": 2200,  "reason": "Abrasives + ceramics, capex cycle proxy",   "added": "2026-06-01"},
    {"ticker": "MTAR",       "entry_target": 1800,  "reason": "Defence precision components, order book",   "added": "2026-06-01"},
    {"ticker": "ELGIEQUIP",  "entry_target": 680,   "reason": "Compressors export play, clean balance sheet","added": "2026-06-01"},
    {"ticker": "CREDITACC",  "entry_target": 1400,  "reason": "MFI leader, rural credit penetration",      "added": "2026-06-01"},
]


# ── Watchlist persistence ─────────────────────────────────────

def load_watchlist() -> list[dict]:
    if WL_FILE.exists():
        return json.loads(WL_FILE.read_text(encoding="utf-8"))
    # First run: save and return defaults
    save_watchlist(DEFAULT_WATCHLIST)
    return DEFAULT_WATCHLIST


def save_watchlist(wl: list[dict]) -> None:
    WL_FILE.write_text(json.dumps(wl, indent=2, default=str), encoding="utf-8")


def add_stock(ticker: str, entry_target: float, reason: str = "") -> None:
    wl = load_watchlist()
    tickers = [w["ticker"].upper() for w in wl]
    if ticker.upper() in tickers:
        print(f"{ticker} already in watchlist.")
        return
    wl.append({
        "ticker":       ticker.upper(),
        "entry_target": entry_target,
        "reason":       reason,
        "added":        datetime.now().strftime("%Y-%m-%d"),
    })
    save_watchlist(wl)
    print(f"✅ Added {ticker.upper()} to watchlist (entry target ₹{entry_target:,.0f})")


def remove_stock(ticker: str) -> None:
    wl = load_watchlist()
    before = len(wl)
    wl = [w for w in wl if w["ticker"].upper() != ticker.upper()]
    save_watchlist(wl)
    if len(wl) < before:
        print(f"✅ Removed {ticker.upper()} from watchlist")
    else:
        print(f"{ticker} not found in watchlist")


# ── Quick scan agent ──────────────────────────────────────────

WATCHLIST_SCAN_PROMPT = """
You are a stock alert system for Indian equity markets.

You are given a watchlist stock with:
- Current live price data
- The investor's target entry price
- The reason they're watching this stock

Produce a concise scan result in JSON:
{
  "ticker": "NSE symbol",
  "current_price": float,
  "entry_target": float,
  "distance_to_target_pct": float (negative = already below target),
  "alert_level": "BUY_ZONE / APPROACHING / WATCH / OVERVALUED",
  "alert_reason": "one sentence — why this alert level",
  "technical_bias": "bullish / neutral / bearish",
  "action": "ENTER_NOW / WAIT_FOR_DIP / SET_ALERT_AT / AVOID",
  "suggested_entry": float (specific price to enter),
  "stop_loss": float,
  "target_3m": float (3-month price target),
  "note": "one key observation about this stock right now"
}

Alert levels:
  BUY_ZONE    → price is at or below entry target (time to consider buying)
  APPROACHING → within 5% of entry target (set alerts)
  WATCH       → 5–15% above target (monitor for pullback)
  OVERVALUED  → >15% above target (wait for correction)
"""


def scan_watchlist_stock(item: dict) -> dict:
    """Run a quick scan on one watchlist stock."""
    ticker = item["ticker"]
    try:
        quote_raw = get_nse_quote(ticker)
        raw = run_agent(
            agent_name=f"Watchlist [{ticker}]",
            system_prompt=WATCHLIST_SCAN_PROMPT,
            user_message=(
                f"Stock: {ticker}\n"
                f"Entry target: ₹{item.get('entry_target',0):,.0f}\n"
                f"Watch reason: {item.get('reason','')}\n\n"
                f"Live data:\n{quote_raw[:1200]}"
            ),
            tools=DATA_TOOL_DEFINITIONS,
            tool_map=DATA_TOOL_MAP,
            max_tool_rounds=2,
        )
        result = parse_json_response(raw)
        if not result:
            result = {"ticker": ticker, "alert_level": "ERROR", "note": raw[:100]}
        result["watch_reason"] = item.get("reason", "")
        result["entry_target"] = item.get("entry_target", 0)
        return result
    except Exception as e:
        logger.error("Watchlist scan failed {}: {}", ticker, e)
        return {
            "ticker":       ticker,
            "alert_level":  "ERROR",
            "note":         str(e)[:80],
            "current_price":0,
            "entry_target": item.get("entry_target", 0),
            "action":       "ERROR",
            "suggested_entry": 0,
            "stop_loss":    0,
            "target_3m":    0,
            "watch_reason": item.get("reason",""),
        }


def run_watchlist_scan() -> list[dict]:
    """Scan all watchlist stocks and return results sorted by alert priority."""
    wl = load_watchlist()
    if not wl:
        print("Watchlist is empty. Use --add TICKER PRICE to add stocks.")
        return []

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M IST")
    print(f"\n{'━'*60}")
    print(f"  HEDGEFUSION WATCHLIST SCAN")
    print(f"  {len(wl)} stocks | {timestamp}")
    print(f"{'━'*60}\n")

    results = []
    for i, item in enumerate(wl, 1):
        print(f"[{i}/{len(wl)}] Scanning {item['ticker']}...")
        result = scan_watchlist_stock(item)
        results.append(result)
        alert = result.get("alert_level", "?")
        price = result.get("current_price", "?")
        note  = result.get("note", "")[:60]
        emoji = {"BUY_ZONE":"🔥","APPROACHING":"⚡","WATCH":"👀","OVERVALUED":"⏳"}.get(alert,"❓")
        print(f"  {emoji} {alert:<12} | ₹{price} | {note}")

    # Sort: BUY_ZONE first, then APPROACHING, WATCH, OVERVALUED
    priority = {"BUY_ZONE":0,"APPROACHING":1,"WATCH":2,"OVERVALUED":3,"ERROR":4}
    results.sort(key=lambda x: priority.get(x.get("alert_level",""),4))

    # Print summary
    print(f"\n{'━'*60}")
    print(f"  WATCHLIST ALERT SUMMARY")
    print(f"{'━'*60}")
    for r in results:
        emoji = {"BUY_ZONE":"🔥","APPROACHING":"⚡","WATCH":"👀","OVERVALUED":"⏳"}.get(
            r.get("alert_level",""), "❓")
        action = r.get("action","?")
        entry  = r.get("suggested_entry","?")
        print(f"  {emoji} {r['ticker']:<12} {r.get('alert_level','?'):<12} "
              f"→ {action} @ ₹{entry}")

    buy_zone = [r for r in results if r.get("alert_level")=="BUY_ZONE"]
    if buy_zone:
        print(f"\n  🔥 BUY ZONE ALERTS ({len(buy_zone)} stocks):")
        for r in buy_zone:
            print(f"     {r['ticker']}: {r.get('note','')}")
            print(f"     Entry: ₹{r.get('suggested_entry','?')} | "
                  f"SL: ₹{r.get('stop_loss','?')} | "
                  f"Target: ₹{r.get('target_3m','?')}")

    # Save results
    out_path = Path(__file__).parent / "outputs" / \
               f"watchlist_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    out_path.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print(f"\n✅ Results saved: {out_path}\n")

    return results


def show_watchlist():
    wl = load_watchlist()
    print(f"\n{'━'*55}")
    print(f"  YOUR WATCHLIST ({len(wl)} stocks)")
    print(f"{'━'*55}")
    print(f"{'Ticker':<14} {'Entry Target':>12} {'Reason'}")
    print(f"{'─'*55}")
    for w in wl:
        print(f"{w['ticker']:<14} ₹{w.get('entry_target',0):>10,.0f}  "
              f"{w.get('reason','')[:45]}")
    print(f"{'━'*55}\n")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="HedgeFusion Watchlist Manager")
    parser.add_argument("--add",    nargs=2, metavar=("TICKER","PRICE"),
                        help="Add stock to watchlist: --add TITAN 3400")
    parser.add_argument("--remove", metavar="TICKER",
                        help="Remove stock from watchlist")
    parser.add_argument("--show",   action="store_true",
                        help="Show current watchlist")
    args = parser.parse_args()

    if args.add:
        ticker, price = args.add
        reason = input(f"Why are you watching {ticker.upper()}? (press Enter to skip): ").strip()
        add_stock(ticker, float(price), reason)
    elif args.remove:
        remove_stock(args.remove)
    elif args.show:
        show_watchlist()
    else:
        run_watchlist_scan()


if __name__ == "__main__":
    main()
