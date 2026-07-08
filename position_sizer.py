"""
HedgeFusion Position Sizer
=============================
Risk-based position sizing so no single trade can blow up the portfolio.

Three sizing methods:

  1. FIXED FRACTIONAL (default) — risk a fixed % of portfolio per trade.
     qty = (portfolio_size × risk_pct) / (entry_price − stop_loss_price)

  2. KELLY CRITERION (optional, needs trade history) — sizes based on
     your actual historical win rate and win/loss ratio from trade_journal.
     f* = W − [(1−W) / R]   where W=win rate, R=avg_win/avg_loss
     HedgeFusion uses HALF-KELLY (f*/2) — full Kelly is too aggressive
     for real capital given estimation error in W and R.

  3. VOLATILITY-ADJUSTED (ATR-based) — stop distance derived from
     14-day Average True Range instead of a manual stop price.

All methods respect config.MAX_POSITION_PCT as a hard ceiling —
sizing suggestions never exceed max single-position exposure.

Usage:
    python position_sizer.py --ticker RELIANCE --entry 1280 --stop 1220
    python position_sizer.py --ticker RELIANCE --entry 1280 --stop 1220 --risk 1.5
    python position_sizer.py --ticker RELIANCE --entry 1280 --atr           # ATR-based stop
    python position_sizer.py --ticker RELIANCE --entry 1280 --stop 1220 --kelly
"""

import argparse
import json
import os
from datetime import datetime
from pathlib import Path

import yfinance as yf
from dotenv import load_dotenv
from loguru import logger

load_dotenv(Path(__file__).parent / ".env")

from config import PORTFOLIO_SIZE_INR, MAX_POSITION_PCT, STOP_LOSS_DEFAULT_PCT


# ──────────────────────────────────────────────
# ATR (Average True Range) for volatility-based stops
# ──────────────────────────────────────────────

def compute_atr(ticker: str, period_days: int = 14) -> float:
    """
    Compute 14-day Average True Range for a stock.
    ATR gives a volatility-scaled stop distance — wider for volatile
    stocks, tighter for calm ones, instead of a flat % for everything.
    """
    symbol = ticker.upper()
    if not symbol.endswith(".NS"):
        symbol += ".NS"
    try:
        hist = yf.Ticker(symbol).history(period="2mo", interval="1d")
        if hist is None or len(hist) < period_days + 1:
            return 0.0

        high, low, close = hist["High"], hist["Low"], hist["Close"]
        prev_close = close.shift(1)

        tr1 = high - low
        tr2 = (high - prev_close).abs()
        tr3 = (low - prev_close).abs()
        true_range = tr1.combine(tr2, max).combine(tr3, max)

        atr = true_range.rolling(period_days).mean().iloc[-1]
        return round(float(atr), 2)
    except Exception as e:
        logger.warning("compute_atr({}) failed: {}", ticker, e)
        return 0.0


# ──────────────────────────────────────────────
# Fixed fractional sizing (default method)
# ──────────────────────────────────────────────

def size_fixed_fractional(
    entry_price: float,
    stop_loss_price: float,
    portfolio_size_inr: float = PORTFOLIO_SIZE_INR,
    risk_pct: float = 1.0,
) -> dict:
    """
    Size a position so that if the stop loss is hit, you lose exactly
    `risk_pct`% of your total portfolio — no more.

    This is the standard professional risk-management approach:
    the position SIZE varies with the STOP DISTANCE, not the other
    way around. A tight stop → bigger position. A wide stop → smaller
    position. Risk per trade stays constant.
    """
    if entry_price <= 0 or stop_loss_price <= 0:
        return {"error": "entry_price and stop_loss_price must be > 0"}
    if stop_loss_price >= entry_price:
        return {"error": "stop_loss_price must be below entry_price for a long trade"}

    risk_per_share = entry_price - stop_loss_price
    risk_amount_inr = portfolio_size_inr * (risk_pct / 100)
    raw_qty = int(risk_amount_inr / risk_per_share)

    # Cap at MAX_POSITION_PCT of portfolio regardless of stop distance
    max_position_inr = portfolio_size_inr * (MAX_POSITION_PCT / 100)
    max_qty_by_cap    = int(max_position_inr / entry_price)
    qty = min(raw_qty, max_qty_by_cap)
    capped = raw_qty > max_qty_by_cap

    position_value = qty * entry_price
    actual_risk_inr = qty * risk_per_share
    actual_risk_pct = (actual_risk_inr / portfolio_size_inr * 100) if portfolio_size_inr else 0

    return {
        "method":             "fixed_fractional",
        "entry_price":        entry_price,
        "stop_loss_price":    stop_loss_price,
        "risk_per_share_inr": round(risk_per_share, 2),
        "target_risk_pct":    risk_pct,
        "suggested_qty":      max(qty, 0),
        "position_value_inr": round(position_value, 2),
        "position_pct_of_portfolio": round(position_value / portfolio_size_inr * 100, 2) if portfolio_size_inr else 0,
        "actual_risk_inr":    round(actual_risk_inr, 2),
        "actual_risk_pct":    round(actual_risk_pct, 2),
        "capped_by_max_position_pct": capped,
        "max_position_pct":  MAX_POSITION_PCT,
    }


# ──────────────────────────────────────────────
# Half-Kelly sizing (needs historical win rate)
# ──────────────────────────────────────────────

def size_kelly(
    entry_price: float,
    stop_loss_price: float,
    portfolio_size_inr: float = PORTFOLIO_SIZE_INR,
    win_rate_pct: float = None,
    avg_win_pct: float = None,
    avg_loss_pct: float = None,
) -> dict:
    """
    Half-Kelly position sizing using your actual trade history stats
    (pulled from trade_journal if not supplied explicitly).

    f* = W - (1-W)/R    where W = win rate (0-1), R = avg_win / avg_loss

    HedgeFusion uses f*/2 (half-Kelly) because:
      - Full Kelly assumes W and R are known with certainty — they're not,
        they're estimated from a limited trade sample.
      - Full Kelly produces violent equity curve swings even when correct.
      - Half-Kelly captures ~75% of full-Kelly's growth rate with much
        lower volatility — the standard practitioner adjustment.
    """
    if win_rate_pct is None or avg_win_pct is None or avg_loss_pct is None:
        try:
            from trade_journal import load_paper_trades, compute_stats
            trades = load_paper_trades(since_days=365)
            stats  = compute_stats(trades)
            if stats.get("closed_trades", 0) < 10:
                return {
                    "error": (
                        f"Only {stats.get('closed_trades',0)} closed trades on file — "
                        "need at least 10 for reliable Kelly stats. "
                        "Use --risk (fixed fractional) instead, or trade more first."
                    )
                }
            win_rate_pct  = stats["win_rate_pct"]
            avg_win_pct   = abs(stats["avg_win_inr"] / (stats["total_invested_inr"] or 1) * 100) or 5.0
            avg_loss_pct  = abs(stats["avg_loss_inr"] / (stats["total_invested_inr"] or 1) * 100) or 3.0
        except Exception as e:
            return {"error": f"Could not load trade history for Kelly sizing: {e}"}

    W = win_rate_pct / 100
    R = avg_win_pct / avg_loss_pct if avg_loss_pct else 1.5

    kelly_f = W - (1 - W) / R
    half_kelly_f = max(kelly_f / 2, 0)  # never negative-size

    if half_kelly_f <= 0:
        return {
            "error": (
                f"Kelly fraction is negative or zero (W={W:.0%}, R={R:.2f}) — "
                "your historical edge doesn't support sizing up. Consider paper "
                "trading longer before increasing size, or skip this trade."
            )
        }

    # Cap half-Kelly at MAX_POSITION_PCT regardless of what the formula says
    position_pct = min(half_kelly_f * 100, MAX_POSITION_PCT)
    position_value = portfolio_size_inr * (position_pct / 100)
    qty = int(position_value / entry_price) if entry_price else 0

    risk_per_share  = entry_price - stop_loss_price if stop_loss_price else entry_price * (STOP_LOSS_DEFAULT_PCT / 100)
    actual_risk_inr = qty * risk_per_share

    return {
        "method":              "half_kelly",
        "win_rate_pct":        round(win_rate_pct, 1),
        "avg_win_pct":         round(avg_win_pct, 2),
        "avg_loss_pct":        round(avg_loss_pct, 2),
        "reward_risk_ratio_R": round(R, 2),
        "full_kelly_pct":      round(kelly_f * 100, 2),
        "half_kelly_pct":      round(half_kelly_f * 100, 2),
        "position_pct_used":   round(position_pct, 2),
        "suggested_qty":       max(qty, 0),
        "position_value_inr":  round(position_value, 2),
        "actual_risk_inr":     round(actual_risk_inr, 2),
        "capped_by_max_position_pct": (half_kelly_f * 100) > MAX_POSITION_PCT,
    }


# ──────────────────────────────────────────────
# ATR-based sizing
# ──────────────────────────────────────────────

def size_atr_based(
    ticker: str,
    entry_price: float,
    portfolio_size_inr: float = PORTFOLIO_SIZE_INR,
    risk_pct: float = 1.0,
    atr_multiplier: float = 2.0,
) -> dict:
    """
    Derive the stop-loss distance from 14-day ATR instead of a manual price.
    Stop = entry_price - (ATR × atr_multiplier).

    Volatile stocks get proportionally wider stops (fewer whipsaws);
    calm stocks get tighter stops (better risk-adjusted sizing) —
    versus a flat % stop that ignores each stock's actual volatility.
    """
    atr = compute_atr(ticker)
    if atr <= 0:
        return {"error": f"Could not compute ATR for {ticker} — insufficient price history"}

    stop_distance   = atr * atr_multiplier
    stop_loss_price = round(entry_price - stop_distance, 2)

    result = size_fixed_fractional(entry_price, stop_loss_price, portfolio_size_inr, risk_pct)
    result["method"]         = "atr_based"
    result["atr_14d"]        = atr
    result["atr_multiplier"] = atr_multiplier
    result["stop_distance"]  = round(stop_distance, 2)
    return result


# ──────────────────────────────────────────────
# Unified entry point
# ──────────────────────────────────────────────

def calculate_position_size(
    ticker: str,
    entry_price: float,
    stop_loss_price: float = None,
    portfolio_size_inr: float = PORTFOLIO_SIZE_INR,
    risk_pct: float = 1.0,
    method: str = "fixed",
) -> dict:
    """
    Main entry point — routes to the requested sizing method.

    method: "fixed" (default), "kelly", or "atr"
    """
    ticker = ticker.strip().upper()

    if method == "kelly":
        result = size_kelly(entry_price, stop_loss_price, portfolio_size_inr)
    elif method == "atr":
        result = size_atr_based(ticker, entry_price, portfolio_size_inr, risk_pct)
    else:
        if stop_loss_price is None:
            stop_loss_price = round(entry_price * (1 - STOP_LOSS_DEFAULT_PCT / 100), 2)
        result = size_fixed_fractional(entry_price, stop_loss_price, portfolio_size_inr, risk_pct)

    result["ticker"] = ticker
    result["portfolio_size_inr"] = portfolio_size_inr
    result["calculated_at"] = datetime.now().isoformat()
    return result


# ──────────────────────────────────────────────
# Tool definition (for agent function-calling — Trader agent uses this)
# ──────────────────────────────────────────────

POSITION_SIZER_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "calculate_position_size",
            "description": (
                "Calculate the correct share quantity for a trade using risk-based "
                "position sizing. Given an entry price and stop loss, returns the "
                "quantity that risks exactly the target % of portfolio if stopped out. "
                "Always use this before finalising order quantity — never guess a round number."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker":            {"type": "string"},
                    "entry_price":       {"type": "number"},
                    "stop_loss_price":   {"type": "number"},
                    "risk_pct":          {"type": "number", "description": "% of portfolio to risk on this trade, default 1.0"},
                },
                "required": ["ticker", "entry_price", "stop_loss_price"],
            },
        },
    }
]

POSITION_SIZER_TOOL_MAP = {
    "calculate_position_size": lambda ticker, entry_price, stop_loss_price, risk_pct=1.0: json.dumps(
        calculate_position_size(ticker, entry_price, stop_loss_price, risk_pct=risk_pct)
    ),
}


# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────

def print_result(result: dict):
    if "error" in result:
        print(f"\n  ❌ {result['error']}\n")
        return

    print(f"\n{'━'*55}")
    print(f"  POSITION SIZE — {result.get('ticker','')}")
    print(f"  Method: {result.get('method','').replace('_',' ').title()}")
    print(f"{'━'*55}")

    if result["method"] == "half_kelly":
        print(f"  Win rate:          {result['win_rate_pct']}%")
        print(f"  Avg win / loss:    {result['avg_win_pct']}% / {result['avg_loss_pct']}%")
        print(f"  Reward:Risk (R):   {result['reward_risk_ratio_R']}")
        print(f"  Full Kelly:        {result['full_kelly_pct']}%")
        print(f"  Half Kelly used:   {result['half_kelly_pct']}%")
    else:
        print(f"  Entry price:       ₹{result.get('entry_price',0):,.2f}")
        print(f"  Stop loss:         ₹{result.get('stop_loss_price',0):,.2f}")
        print(f"  Risk per share:    ₹{result.get('risk_per_share_inr',0):,.2f}")
        if result["method"] == "atr_based":
            print(f"  ATR (14d):         ₹{result.get('atr_14d',0):,.2f}")

    print(f"  {'─'*51}")
    print(f"  Suggested qty:     {result.get('suggested_qty',0)} shares")
    print(f"  Position value:    ₹{result.get('position_value_inr',0):,.2f}")
    print(f"  Actual risk:       ₹{result.get('actual_risk_inr',0):,.2f} "
          f"({result.get('actual_risk_pct', result.get('target_risk_pct','?'))}%)")
    if result.get("capped_by_max_position_pct"):
        print(f"  ⚠ Capped at MAX_POSITION_PCT ({result.get('max_position_pct', MAX_POSITION_PCT)}%)")
    print(f"{'━'*55}\n")


def main():
    parser = argparse.ArgumentParser(description="HedgeFusion Position Sizer")
    parser.add_argument("--ticker", required=True, help="NSE ticker e.g. RELIANCE")
    parser.add_argument("--entry",  required=True, type=float, help="Entry price ₹")
    parser.add_argument("--stop",   type=float, help="Stop loss price ₹")
    parser.add_argument("--risk",   type=float, default=1.0, help="Risk %% of portfolio (default 1.0)")
    parser.add_argument("--kelly",  action="store_true", help="Use half-Kelly sizing (needs 10+ trade history)")
    parser.add_argument("--atr",    action="store_true", help="Use ATR-based stop instead of manual --stop")
    args = parser.parse_args()

    method = "kelly" if args.kelly else "atr" if args.atr else "fixed"
    result = calculate_position_size(
        ticker=args.ticker,
        entry_price=args.entry,
        stop_loss_price=args.stop,
        risk_pct=args.risk,
        method=method,
    )
    print_result(result)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
