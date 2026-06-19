"""
HedgeFusion Config
===================
Single source of truth for all settings.
All other modules import from here — no more duplicated HOLDINGS
or PORTFOLIO_SIZE across files.

Edit this file to:
  - Add / remove holdings
  - Change portfolio size
  - Update avg buy prices from Zerodha Console
  - Set watchlist targets
  - Configure alert thresholds
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
load_dotenv(Path(__file__).parent / ".env")

# ── Paths ─────────────────────────────────────────────────────
ROOT_DIR   = Path(__file__).parent
DATA_DIR   = ROOT_DIR / "data"
CACHE_DIR  = DATA_DIR / "cache"
LOG_DIR    = ROOT_DIR / "logs"
OUTPUT_DIR = ROOT_DIR / "outputs"

for d in [DATA_DIR, CACHE_DIR, LOG_DIR, OUTPUT_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ── API Keys ──────────────────────────────────────────────────
OPENAI_API_KEY     = os.getenv("OPENAI_API_KEY", "")
MODEL_NAME         = os.getenv("MODEL_NAME", "gpt-4o-mini")

KITE_API_KEY       = os.getenv("KITE_API_KEY", "")
KITE_API_SECRET    = os.getenv("KITE_API_SECRET", "")
KITE_ACCESS_TOKEN  = os.getenv("KITE_ACCESS_TOKEN", "")
KITE_PAPER_TRADE   = os.getenv("KITE_PAPER_TRADE", "true").lower() in ("true","1","yes")

# ── Portfolio ─────────────────────────────────────────────────
# Update qty and avg_buy_price from Zerodha Console → Portfolio → Holdings
# Set avg_buy_price=0 if you don't want P&L tracking

PORTFOLIO_SIZE_INR = float(os.getenv("PORTFOLIO_SIZE_INR", "500000"))

HOLDINGS = [
    # ── Your Zerodha holdings ────────────────────────────────
    {
        "ticker":         "ICICIBANK",
        "qty":            10,
        "avg_buy_price":  0,       # ← update from Zerodha
        "sector":         "Banking",
        "stop_loss_pct":  5.0,     # exit if down 5% from entry
        "target_pct":     15.0,    # target 15% gain
    },
    {
        "ticker":         "BHARTIARTL",
        "qty":            5,
        "avg_buy_price":  0,
        "sector":         "Telecom",
        "stop_loss_pct":  6.0,
        "target_pct":     20.0,
    },
    {
        "ticker":         "ZOMATO",
        "qty":            50,
        "avg_buy_price":  0,
        "sector":         "Consumer Tech",
        "stop_loss_pct":  8.0,
        "target_pct":     30.0,
    },
    {
        "ticker":         "M&M",
        "qty":            4,
        "avg_buy_price":  0,
        "sector":         "Auto",
        "stop_loss_pct":  5.0,
        "target_pct":     18.0,
    },
    {
        "ticker":         "LT",
        "qty":            3,
        "avg_buy_price":  0,
        "sector":         "Capital Goods",
        "stop_loss_pct":  5.0,
        "target_pct":     20.0,
    },
    {
        "ticker":         "MAZDOCK",
        "qty":            2,
        "avg_buy_price":  0,
        "sector":         "Defence",
        "stop_loss_pct":  7.0,
        "target_pct":     25.0,
    },
    {
        "ticker":         "BEL",
        "qty":            30,
        "avg_buy_price":  0,
        "sector":         "Defence",
        "stop_loss_pct":  6.0,
        "target_pct":     20.0,
    },
    {
        "ticker":         "HDFCBANK",
        "qty":            8,
        "avg_buy_price":  0,
        "sector":         "Banking",
        "stop_loss_pct":  5.0,
        "target_pct":     15.0,
    },
    {
        "ticker":         "HINDZINC",
        "qty":            15,
        "avg_buy_price":  0,
        "sector":         "Metals",
        "stop_loss_pct":  7.0,
        "target_pct":     22.0,
    },
    {
        "ticker":         "VBL",
        "qty":            10,
        "avg_buy_price":  0,
        "sector":         "FMCG",
        "stop_loss_pct":  6.0,
        "target_pct":     18.0,
    },
]

# ── Watchlist ─────────────────────────────────────────────────
# Stocks you're monitoring but haven't bought yet
WATCHLIST = [
    {"ticker": "POLYCAB",    "entry_target": 5800,  "reason": "Cable & wires PLI beneficiary"},
    {"ticker": "TITAN",      "entry_target": 3200,  "reason": "Premium consumption brand moat"},
    {"ticker": "BAJFINANCE", "entry_target": 6800,  "reason": "India credit cycle leader"},
    {"ticker": "PERSISTENT", "entry_target": 5200,  "reason": "Mid-cap IT, AI services >30% growth"},
    {"ticker": "TATAELXSI",  "entry_target": 6800,  "reason": "EV software design moat"},
    {"ticker": "COFORGE",    "entry_target": 7500,  "reason": "IT mid-cap, strong deal pipeline"},
    {"ticker": "GRINDWELL",  "entry_target": 2200,  "reason": "Abrasives + ceramics, capex proxy"},
    {"ticker": "MTAR",       "entry_target": 1800,  "reason": "Defence precision, growing order book"},
    {"ticker": "ELGIEQUIP",  "entry_target": 680,   "reason": "Compressors export, clean B/S"},
    {"ticker": "CREDITACC",  "entry_target": 1400,  "reason": "MFI leader, rural credit"},
]

# ── Scheduler ─────────────────────────────────────────────────
SCHEDULE_MODE             = os.getenv("SCHEDULE_MODE", "daily")
SCHEDULE_TIME             = os.getenv("SCHEDULE_TIME", "09:30")
SCHEDULE_EXECUTE          = os.getenv("SCHEDULE_EXECUTE", "false").lower() in ("true","1","yes")
SCHEDULE_INTERVAL_HOURS   = int(os.getenv("SCHEDULE_INTERVAL_HOURS", "24"))
SCHEDULE_TICKERS          = [
    t.strip().upper()
    for t in os.getenv("SCHEDULE_TICKERS", ",".join(h["ticker"] for h in HOLDINGS)).split(",")
    if t.strip()
]

# ── Risk thresholds ───────────────────────────────────────────
MAX_POSITION_PCT          = 15.0   # alert if any stock > 15% of portfolio
MAX_SECTOR_PCT            = 30.0   # alert if any sector > 30%
MIN_RISK_REWARD           = 2.0    # PM veto if R:R < 2.0
MAX_DAILY_LOSS_PCT        = 2.0    # stop trading if portfolio down 2% in a day
STOP_LOSS_DEFAULT_PCT     = 5.0    # default SL if agent doesn't set one

# ── Alert thresholds ──────────────────────────────────────────
ALERT_BUY_ZONE_PCT        = 2.0    # alert when stock within 2% of watchlist entry target
ALERT_STOP_LOSS_PCT       = 80.0   # alert when stock hits 80% of SL distance
ALERT_DRAWDOWN_PCT        = 15.0   # alert when holding drawdown > 15%

# ── Backtester ────────────────────────────────────────────────
BACKTEST_STOP_LOSS_PCT    = 5.0
BACKTEST_HOLDING_PERIOD   = "6m"

# ── Market holidays 2026 (NSE) ────────────────────────────────
MARKET_HOLIDAYS_2026 = {
    "2026-01-26", "2026-03-25", "2026-04-02", "2026-04-10",
    "2026-04-14", "2026-05-01", "2026-08-15", "2026-10-02",
    "2026-10-20", "2026-10-21", "2026-11-05", "2026-12-25",
}

# ── FII/DII Dashboard settings ───────────────────────────────
# Tickers to include in the daily FII/DII institutional scan
# Defaults to your holdings — add watchlist stocks for broader view
FII_DII_TICKERS = [h["ticker"] for h in HOLDINGS]  # extend as needed

# Alert when FII net flow crosses these thresholds (₹ crore)
FII_ALERT_BUY_THRESHOLD  =  1000   # FII net buy >₹1000Cr = bullish alert
FII_ALERT_SELL_THRESHOLD = -1000   # FII net sell <-₹1000Cr = caution alert

# ── Convenience helpers ───────────────────────────────────────
HOLDING_TICKERS  = [h["ticker"] for h in HOLDINGS]
WATCHLIST_TICKERS = [w["ticker"] for w in WATCHLIST]

def get_holding(ticker: str) -> dict | None:
    """Return holding dict for a given ticker, or None."""
    return next((h for h in HOLDINGS if h["ticker"].upper() == ticker.upper()), None)

def is_paper_mode() -> bool:
    return KITE_PAPER_TRADE

def mode_label() -> str:
    return "📄 PAPER" if KITE_PAPER_TRADE else "🔴 LIVE"


if __name__ == "__main__":
    print(f"HedgeFusion Config")
    print(f"  Portfolio size:   ₹{PORTFOLIO_SIZE_INR:,.0f}")
    print(f"  Holdings:         {len(HOLDINGS)} stocks")
    print(f"  Watchlist:        {len(WATCHLIST)} stocks")
    print(f"  Mode:             {mode_label()}")
    print(f"  Model:            {MODEL_NAME}")
    print(f"  Schedule:         {SCHEDULE_TIME} IST daily")
    print(f"  OpenAI key:       {'✓ set' if OPENAI_API_KEY.startswith('sk-') else '✗ missing'}")
    print(f"  Kite API key:     {'✓ set' if KITE_API_KEY else '✗ not set (paper mode OK)'}")
