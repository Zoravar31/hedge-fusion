"""
HedgeFusion Earnings Calendar
================================
Tracks upcoming quarterly results for your holdings and watchlist.
Provides pre-earnings AI positioning advice:
  - Should you hold through results or exit before?
  - What is the street expectation?
  - Historical post-results moves for this stock

Indian earnings season runs:
  Q1 (Apr-Jun): results in Jul-Aug
  Q2 (Jul-Sep): results in Oct-Nov
  Q3 (Oct-Dec): results in Jan-Feb
  Q4 (Jan-Mar): results in Apr-May

Usage:
    python earnings_calendar.py               # upcoming results next 30 days
    python earnings_calendar.py --days 60     # next 60 days
    python earnings_calendar.py --ai HDFCBANK # AI pre-earnings brief for one stock
    python earnings_calendar.py --all         # full calendar for all holdings
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import requests
import yfinance as yf
from dotenv import load_dotenv
from loguru import logger
from openai import OpenAI

load_dotenv(Path(__file__).parent / ".env")

client     = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
MODEL      = os.getenv("MODEL_NAME", "gpt-4o-mini")
OUTPUT_DIR = Path(__file__).parent / "outputs"
OUTPUT_DIR.mkdir(exist_ok=True)

# ── Known earnings dates (manually maintained) ────────────────
# yfinance doesn't reliably have Indian earnings dates
# Update this from NSE website / Moneycontrol each quarter
# Format: "TICKER": "YYYY-MM-DD"

KNOWN_EARNINGS_DATES = {
    # Q4 FY26 results (Apr-Jun 2026 quarter, announced Jul-Aug 2026)
    "TCS":        "2026-07-10",
    "INFY":       "2026-07-17",
    "HDFCBANK":   "2026-07-19",
    "ICICIBANK":  "2026-07-26",
    "RELIANCE":   "2026-07-25",
    "WIPRO":      "2026-07-16",
    "HCLTECH":    "2026-07-12",
    "BHARTIARTL": "2026-07-30",
    "LT":         "2026-07-28",
    "M&M":        "2026-07-31",
    "ZOMATO":     "2026-08-05",
    "BEL":        "2026-08-12",
    "MAZDOCK":    "2026-08-08",
    "HDFCBANK":   "2026-07-19",
    "HINDZINC":   "2026-07-24",
    "VBL":        "2026-07-22",
    # Watchlist stocks
    "POLYCAB":    "2026-07-29",
    "TITAN":      "2026-07-30",
    "BAJFINANCE": "2026-07-28",
    "PERSISTENT": "2026-07-17",
    "TATAELXSI":  "2026-07-18",
    "COFORGE":    "2026-07-25",
}


# ── Historical post-earnings moves ────────────────────────────
# Typical post-results gap (%) for Indian large caps — rough guide
HISTORICAL_MOVES = {
    "TCS":        {"avg_move": 2.1, "beat_pct": 65, "miss_pct": 35},
    "INFY":       {"avg_move": 3.8, "beat_pct": 58, "miss_pct": 42},
    "HDFCBANK":   {"avg_move": 1.8, "beat_pct": 62, "miss_pct": 38},
    "ICICIBANK":  {"avg_move": 2.4, "beat_pct": 70, "miss_pct": 30},
    "ZOMATO":     {"avg_move": 6.2, "beat_pct": 55, "miss_pct": 45},
    "BHARTIARTL": {"avg_move": 3.1, "beat_pct": 60, "miss_pct": 40},
    "RELIANCE":   {"avg_move": 2.2, "beat_pct": 55, "miss_pct": 45},
    "DEFAULT":    {"avg_move": 3.0, "beat_pct": 55, "miss_pct": 45},
}


# ── Pre-earnings AI brief ─────────────────────────────────────

PRE_EARNINGS_PROMPT = """
You are a senior equity analyst at an Indian fund preparing a pre-earnings brief.

Stock: {ticker}
Earnings date: {earnings_date}
Days until results: {days_until}
Current price: ₹{current_price}
Historical avg post-earnings move: ±{avg_move}%
Historical beat rate: {beat_pct}%

Based on available data and your knowledge of this company, provide:

1. STREET EXPECTATIONS
   What is the consensus estimate for this quarter?
   Revenue growth expected? Margin direction?

2. KEY METRICS TO WATCH
   The 2-3 numbers that will decide market reaction

3. BULL SCENARIO (if beats)
   What would a positive surprise look like? Price target?

4. BEAR SCENARIO (if misses)
   What would a negative surprise look like? Downside?

5. POSITIONING ADVICE
   Should an investor:
   a) Hold full position through results (high conviction)
   b) Reduce 50% before results, add back after clarity
   c) Exit fully before results, re-enter on dip
   d) Add before results (high conviction of beat)

6. OPTION STRATEGY (awareness only, not advice)
   What options strategy would institutional traders use?
   (e.g., straddle, protective put, covered call)

7. RISK RATING: LOW / MEDIUM / HIGH
   How binary is this results event?

Output as JSON with keys:
street_expectations, key_metrics, bull_scenario, bear_scenario,
positioning_advice, positioning_action (a/b/c/d),
option_awareness, risk_rating, confidence
"""


def get_earnings_brief(ticker: str) -> dict:
    """Get AI pre-earnings positioning brief for one stock."""
    symbol = ticker.upper()

    # Get current price
    price = 0
    try:
        t     = yf.Ticker(symbol + ".NS")
        info  = t.info or {}
        price = info.get("currentPrice") or info.get("previousClose") or 0
    except Exception:
        pass

    earnings_date = KNOWN_EARNINGS_DATES.get(symbol)
    if not earnings_date:
        return {
            "ticker":  symbol,
            "error":   "No earnings date on file. Check NSE/Moneycontrol for actual date.",
        }

    try:
        ed          = datetime.strptime(earnings_date, "%Y-%m-%d")
        days_until  = (ed - datetime.now()).days
    except Exception:
        days_until = 0

    hist_moves = HISTORICAL_MOVES.get(symbol, HISTORICAL_MOVES["DEFAULT"])

    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": "You are a senior equity analyst covering Indian markets."},
                {"role": "user",   "content": PRE_EARNINGS_PROMPT.format(
                    ticker=symbol,
                    earnings_date=earnings_date,
                    days_until=days_until,
                    current_price=f"{price:,.2f}" if price else "N/A",
                    avg_move=hist_moves["avg_move"],
                    beat_pct=hist_moves["beat_pct"],
                )},
            ],
            temperature=0.3,
        )
        raw = response.choices[0].message.content or "{}"
        import re
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            result = json.loads(match.group())
            if isinstance(result, dict):
                result["ticker"]        = symbol
                result["earnings_date"] = earnings_date
                result["days_until"]    = days_until
                result["current_price"] = price
                result["hist_moves"]    = hist_moves
                return result
    except Exception as e:
        logger.error("Earnings brief failed {}: {}", ticker, e)

    return {
        "ticker":        symbol,
        "earnings_date": earnings_date,
        "days_until":    days_until,
        "current_price": price,
        "error":         "AI brief unavailable",
        "hist_moves":    hist_moves,
    }


# ── Calendar builder ──────────────────────────────────────────

def build_earnings_calendar(tickers: list[str], days_ahead: int = 30) -> list[dict]:
    """Return upcoming earnings for a list of tickers within days_ahead."""
    now    = datetime.now()
    cutoff = now + timedelta(days=days_ahead)
    events = []

    for ticker in tickers:
        symbol = ticker.upper()
        date_str = KNOWN_EARNINGS_DATES.get(symbol)
        if not date_str:
            continue
        try:
            ed = datetime.strptime(date_str, "%Y-%m-%d")
            if now <= ed <= cutoff:
                days_until = (ed - now).days
                hist = HISTORICAL_MOVES.get(symbol, HISTORICAL_MOVES["DEFAULT"])
                events.append({
                    "ticker":      symbol,
                    "date":        date_str,
                    "days_until":  days_until,
                    "avg_move":    hist["avg_move"],
                    "beat_pct":    hist["beat_pct"],
                    "urgency":     "🔴 THIS WEEK" if days_until <= 7
                                   else "🟡 THIS MONTH" if days_until <= 21
                                   else "🟢 UPCOMING",
                })
        except Exception:
            pass

    events.sort(key=lambda x: x["days_until"])
    return events


def build_calendar_html(events: list, briefs: list, timestamp: str) -> str:
    rows = ""
    for e in events:
        urgency_color = {
            "🔴 THIS WEEK":  "#ef4444",
            "🟡 THIS MONTH": "#f59e0b",
            "🟢 UPCOMING":   "#22c55e",
        }.get(e.get("urgency",""), "#64748b")

        rows += f"""<tr>
          <td style="font-family:monospace;font-weight:700">{e['ticker']}</td>
          <td style="color:#94a3b8">{e['date']}</td>
          <td><span style="color:{urgency_color};font-weight:600">{e['urgency']}</span>
              <span style="color:#64748b;font-size:11px"> ({e['days_until']}d)</span></td>
          <td style="color:#f59e0b">±{e['avg_move']}%</td>
          <td style="color:#94a3b8">{e['beat_pct']}% beat</td>
        </tr>"""

    brief_cards = ""
    for b in briefs:
        if "error" in b and not b.get("earnings_date"):
            continue
        risk_color = {"LOW":"#22c55e","MEDIUM":"#f59e0b","HIGH":"#ef4444"}.get(
            b.get("risk_rating",""), "#64748b")
        action_map = {
            "a": "Hold full position",
            "b": "Reduce 50% before results",
            "c": "Exit, re-enter after",
            "d": "Add before results",
        }
        action     = action_map.get(b.get("positioning_action",""), b.get("positioning_advice","")[:80])
        bull_sc    = b.get("bull_scenario", {})
        bear_sc    = b.get("bear_scenario", {})

        brief_cards += f"""
        <div style="background:#0f172a;border:1px solid #1e293b;border-radius:10px;
                    padding:20px;margin-bottom:12px">
          <div style="display:flex;justify-content:space-between;align-items:flex-start;
                      margin-bottom:14px">
            <div>
              <span style="font-size:20px;font-weight:800;font-family:monospace;
                           color:#f1f5f9">{b['ticker']}</span>
              <span style="margin-left:10px;font-size:13px;color:#64748b">
                Results: {b.get('earnings_date','?')} ({b.get('days_until','?')}d away)</span>
            </div>
            <span style="padding:4px 12px;border-radius:5px;font-size:12px;font-weight:700;
                         background:{risk_color}22;color:{risk_color}">
              {b.get('risk_rating','?')} RISK</span>
          </div>

          <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:14px">
            <div style="background:#052e16;border:1px solid #166534;border-radius:6px;padding:10px">
              <div style="font-size:10px;color:#16a34a;margin-bottom:4px">BULL SCENARIO</div>
              <div style="font-size:12px;color:#86efac">
                {str(bull_sc)[:120] if isinstance(bull_sc,str) else
                 bull_sc.get('description','') if isinstance(bull_sc,dict) else str(bull_sc)[:120]}
              </div>
            </div>
            <div style="background:#1a0000;border:1px solid #7f1d1d;border-radius:6px;padding:10px">
              <div style="font-size:10px;color:#dc2626;margin-bottom:4px">BEAR SCENARIO</div>
              <div style="font-size:12px;color:#fca5a5">
                {str(bear_sc)[:120] if isinstance(bear_sc,str) else
                 bear_sc.get('description','') if isinstance(bear_sc,dict) else str(bear_sc)[:120]}
              </div>
            </div>
          </div>

          <div style="background:#1e293b;border-radius:6px;padding:12px">
            <div style="font-size:10px;color:#64748b;margin-bottom:4px">POSITIONING ADVICE</div>
            <div style="font-size:13px;color:#f59e0b;font-weight:600">{action}</div>
          </div>
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>HedgeFusion Earnings Calendar — {timestamp}</title>
<style>
  body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
       background:#050d1a;color:#e2e8f0;margin:0;padding:20px;
       -webkit-font-smoothing:antialiased}}
  .wrap{{max-width:900px;margin:0 auto}}
  h1{{font-size:22px;font-weight:800;color:#f8fafc;margin-bottom:4px}}
  h2{{font-size:15px;font-weight:700;color:#e2e8f0;margin:28px 0 12px;
      padding-bottom:8px;border-bottom:1px solid #1e293b}}
  .meta{{font-size:12px;color:#64748b;margin-bottom:24px}}
  table{{width:100%;border-collapse:collapse;background:#0f172a;
         border-radius:8px;overflow:hidden;margin-bottom:16px}}
  th{{background:#1e293b;color:#64748b;padding:9px 12px;text-align:left;
      font-size:11px;font-weight:600;letter-spacing:.04em}}
  td{{padding:9px 12px;border-bottom:1px solid #1e293b;font-size:13px;color:#cbd5e1}}
  tr:last-child td{{border-bottom:none}}
  .disc{{background:#1e1a0a;border:1px solid #78350f;border-radius:8px;
         padding:14px 18px;font-size:12px;color:#92400e;margin-top:24px}}
</style>
</head>
<body>
<div class="wrap">
  <h1>📅 HedgeFusion Earnings Calendar</h1>
  <div class="meta">{timestamp} &nbsp;·&nbsp; {len(events)} results upcoming</div>

  <h2>📆 Upcoming Results</h2>
  {"<table><thead><tr><th>Stock</th><th>Date</th><th>Urgency</th><th>Avg Move</th><th>Beat Rate</th></tr></thead><tbody>" + rows + "</tbody></table>" if rows else "<p style='color:#64748b'>No upcoming results in the calendar window.</p>"}

  <h2>🧠 Pre-Earnings AI Briefs</h2>
  {brief_cards if brief_cards else "<p style='color:#64748b'>No briefs generated.</p>"}

  <div class="disc">
    ⚠️ Earnings dates are approximate and change. Always verify on NSE/Moneycontrol before trading.
    Pre-earnings positioning involves risk. This is not investment advice.
  </div>
</div>
</body>
</html>"""


# ── Main ──────────────────────────────────────────────────────

def run_earnings_calendar(
    tickers: list[str] | None = None,
    days_ahead: int = 30,
    ai_briefs: bool = True,
) -> list:
    from config import HOLDING_TICKERS, WATCHLIST_TICKERS
    all_tickers = tickers or (HOLDING_TICKERS + WATCHLIST_TICKERS)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M IST")
    ts_file   = datetime.now().strftime("%Y%m%d_%H%M")

    print(f"\n{'━'*55}")
    print(f"  HEDGEFUSION EARNINGS CALENDAR")
    print(f"  Checking {len(all_tickers)} stocks | Next {days_ahead} days")
    print(f"{'━'*55}\n")

    events = build_earnings_calendar(all_tickers, days_ahead)

    if not events:
        print(f"  No earnings in the next {days_ahead} days.")
        print(f"  Tip: run with --days 60 or --days 90 for a wider window.\n")
        return []

    print(f"  Upcoming results ({len(events)}):")
    for e in events:
        print(f"  {e['urgency']} {e['ticker']:<12} → {e['date']} ({e['days_until']}d)")

    briefs = []
    if ai_briefs and os.getenv("OPENAI_API_KEY","").startswith("sk-"):
        # Only brief stocks within 14 days
        urgent = [e for e in events if e["days_until"] <= 14]
        if urgent:
            print(f"\n  Generating AI briefs for {len(urgent)} urgent results...")
            for e in urgent:
                print(f"  → {e['ticker']}...")
                brief = get_earnings_brief(e["ticker"])
                briefs.append(brief)
                action_map = {"a":"Hold","b":"Reduce 50%","c":"Exit","d":"Add"}
                action = action_map.get(brief.get("positioning_action",""), "See brief")
                print(f"    Advice: {action} | Risk: {brief.get('risk_rating','?')}")

    html      = build_calendar_html(events, briefs, timestamp)
    html_path = OUTPUT_DIR / f"earnings_{ts_file}.html"
    html_path.write_text(html, encoding="utf-8")
    print(f"\n✅ Calendar saved: {html_path}\n")
    return events


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="HedgeFusion Earnings Calendar")
    parser.add_argument("--days",   type=int, default=30,  help="Days ahead to check")
    parser.add_argument("--ai",     metavar="TICKER",      help="AI brief for one stock")
    parser.add_argument("--no-ai",  action="store_true",   help="Skip AI briefs")
    parser.add_argument("--all",    action="store_true",   help="Show all known dates")
    args = parser.parse_args()

    if args.ai:
        brief = get_earnings_brief(args.ai.upper())
        print(json.dumps(brief, indent=2, default=str))
    elif args.all:
        run_earnings_calendar(
            tickers=list(KNOWN_EARNINGS_DATES.keys()),
            days_ahead=180,
            ai_briefs=not args.no_ai,
        )
    else:
        run_earnings_calendar(
            days_ahead=args.days,
            ai_briefs=not args.no_ai,
        )
