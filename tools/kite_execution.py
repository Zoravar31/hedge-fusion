"""
Kite Connect Trade Execution
=============================
Paper mode (default): simulates orders, logs to CSV, tracks P&L.
Live mode: places real orders on NSE via Zerodha Kite Connect.

Switch modes in .env:
  KITE_PAPER_TRADE=true   → paper (safe, default)
  KITE_PAPER_TRADE=false  → live (real money, requires Kite credentials)

Getting live credentials:
  1. Create app at https://developers.kite.trade/ (₹2000/year)
  2. Add KITE_API_KEY and KITE_API_SECRET to .env
  3. Run 'python tools/kite_login.py' every morning → auto-fills KITE_ACCESS_TOKEN
"""

import csv
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger

PAPER_LOG = Path(__file__).parent.parent / "logs" / "paper_trades.csv"
PAPER_LOG.parent.mkdir(exist_ok=True)

_PORTFOLIO: dict = {}          # in-memory paper positions
_ORDER_COUNTER: list = [1000]  # mutable counter for order IDs


# ── Helpers ──────────────────────────────────────────────────

def _paper_mode() -> bool:
    return os.getenv("KITE_PAPER_TRADE", "true").strip().lower() in ("true", "1", "yes")


def _get_kite():
    try:
        from kiteconnect import KiteConnect
    except ImportError:
        raise RuntimeError("Run: pip install kiteconnect")
    api_key      = os.getenv("KITE_API_KEY", "").strip()
    access_token = os.getenv("KITE_ACCESS_TOKEN", "").strip()
    if not api_key:
        raise RuntimeError("KITE_API_KEY missing in .env")
    if not access_token:
        raise RuntimeError("KITE_ACCESS_TOKEN missing — run: python tools/kite_login.py")
    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)
    return kite


def _log_paper(record: dict) -> None:
    write_header = not PAPER_LOG.exists()
    with open(PAPER_LOG, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(record.keys()))
        if write_header:
            w.writeheader()
        w.writerow(record)


# ── Paper execution ───────────────────────────────────────────

def _paper_execute(
    symbol: str,
    transaction_type: str,
    quantity: int,
    order_type: str,
    price: Optional[float],
    stop_loss: Optional[float],
    take_profit: Optional[float],
) -> str:
    from tools.india_data import get_nse_quote
    quote = json.loads(get_nse_quote(symbol))
    ltp = quote.get("info", {}).get("currentPrice") or quote.get("latest_close") or price or 0.0
    fill = float(price) if (order_type == "LIMIT" and price) else float(ltp)

    order_id = f"PAPER-{_ORDER_COUNTER[0]:06d}"
    _ORDER_COUNTER[0] += 1

    key = symbol.upper()
    if key not in _PORTFOLIO:
        _PORTFOLIO[key] = {"qty": 0, "avg_price": 0.0}
    pos = _PORTFOLIO[key]

    if transaction_type == "BUY":
        total = pos["qty"] * pos["avg_price"] + quantity * fill
        pos["qty"] += quantity
        pos["avg_price"] = total / pos["qty"] if pos["qty"] else 0.0
    elif transaction_type == "SELL":
        pos["qty"] = max(0, pos["qty"] - quantity)

    record = {
        "order_id":        order_id,
        "timestamp":       datetime.now().isoformat(),
        "symbol":          key,
        "transaction_type": transaction_type,
        "quantity":        quantity,
        "order_type":      order_type,
        "fill_price":      fill,
        "stop_loss":       stop_loss or "",
        "take_profit":     take_profit or "",
        "value_inr":       round(fill * quantity, 2),
        "status":          "PAPER_EXECUTED",
    }
    _log_paper(record)
    logger.info("[PAPER] {} {} × {} @ ₹{:.2f} | {}", transaction_type, quantity, key, fill, order_id)

    return json.dumps({
        "mode":             "PAPER",
        "order_id":         order_id,
        "symbol":           key,
        "transaction_type": transaction_type,
        "quantity":         quantity,
        "fill_price":       fill,
        "value_inr":        round(fill * quantity, 2),
        "status":           "PAPER_EXECUTED",
        "message":          "Simulated — no real money used.",
    })


# ── Public tools ──────────────────────────────────────────────

def place_nse_order(
    symbol: str,
    transaction_type: str,
    quantity: int,
    order_type: str = "MARKET",
    price: Optional[float] = None,
    stop_loss: Optional[float] = None,
    take_profit: Optional[float] = None,
) -> str:
    """
    Place a BUY or SELL order on NSE.

    Parameters
    ----------
    symbol           : NSE ticker without suffix, e.g. RELIANCE, TCS.
    transaction_type : BUY or SELL.
    quantity         : Number of shares (integer > 0).
    order_type       : MARKET (default) or LIMIT.
    price            : Limit price in ₹ (only for LIMIT orders).
    stop_loss        : Stop-loss price in ₹.
    take_profit      : Target price in ₹.

    Returns
    -------
    JSON string with order_id, fill_price, status, mode.
    """
    if not symbol:
        return json.dumps({"error": "symbol required"})
    if transaction_type.upper() not in ("BUY", "SELL"):
        return json.dumps({"error": "transaction_type must be BUY or SELL"})
    if quantity <= 0:
        return json.dumps({"error": "quantity must be > 0"})

    if _paper_mode():
        return _paper_execute(
            symbol=symbol.strip().upper(),
            transaction_type=transaction_type.upper(),
            quantity=quantity,
            order_type=order_type.upper(),
            price=price,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )

    # ── LIVE ────────────────────────────────────────────────
    try:
        kite = _get_kite()
        params = {
            "tradingsymbol":  symbol.strip().upper(),
            "exchange":       kite.EXCHANGE_NSE,
            "transaction_type": (
                kite.TRANSACTION_TYPE_BUY if transaction_type.upper() == "BUY"
                else kite.TRANSACTION_TYPE_SELL
            ),
            "quantity":   quantity,
            "order_type": (
                kite.ORDER_TYPE_MARKET if order_type.upper() == "MARKET"
                else kite.ORDER_TYPE_LIMIT
            ),
            "product":  kite.PRODUCT_CNC,
            "validity": kite.VALIDITY_DAY,
        }
        if order_type.upper() == "LIMIT" and price:
            params["price"] = float(price)

        order_id = kite.place_order(variety=kite.VARIETY_REGULAR, **params)
        logger.info("[LIVE] {} {} × {} | ID:{}", transaction_type, quantity, symbol, order_id)
        return json.dumps({
            "mode":             "LIVE",
            "order_id":         str(order_id),
            "symbol":           symbol.upper(),
            "transaction_type": transaction_type.upper(),
            "quantity":         quantity,
            "status":           "PLACED",
        })
    except Exception as e:
        logger.error("Live order failed: {}", e)
        return json.dumps({"error": str(e), "mode": "LIVE"})


def get_paper_portfolio() -> str:
    """Return current paper portfolio with live P&L."""
    from tools.india_data import get_nse_quote
    positions = []
    for sym, pos in _PORTFOLIO.items():
        if pos["qty"] <= 0:
            continue
        q = json.loads(get_nse_quote(sym))
        ltp = q.get("info", {}).get("currentPrice") or q.get("latest_close") or pos["avg_price"]
        invested = pos["qty"] * pos["avg_price"]
        current  = pos["qty"] * float(ltp)
        pnl      = current - invested
        positions.append({
            "symbol":        sym,
            "qty":           pos["qty"],
            "avg_price":     round(pos["avg_price"], 2),
            "current_price": round(float(ltp), 2),
            "invested_inr":  round(invested, 2),
            "current_inr":   round(current, 2),
            "pnl_inr":       round(pnl, 2),
            "pnl_pct":       round(pnl / invested * 100 if invested else 0, 2),
        })
    return json.dumps({"mode": "PAPER", "positions": positions, "count": len(positions)})


def get_live_portfolio() -> str:
    """Fetch holdings from live Zerodha account (live mode only)."""
    if _paper_mode():
        return get_paper_portfolio()
    try:
        kite = _get_kite()
        return json.dumps({"mode": "LIVE", "holdings": kite.holdings()})
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── Tool definitions for OpenAI function calling ──────────────

EXECUTION_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "place_nse_order",
            "description": "Place a BUY or SELL order on NSE (paper or live via Zerodha).",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol":           {"type": "string", "description": "NSE ticker e.g. RELIANCE"},
                    "transaction_type": {"type": "string", "enum": ["BUY", "SELL"]},
                    "quantity":         {"type": "integer"},
                    "order_type":       {"type": "string", "enum": ["MARKET", "LIMIT"], "default": "MARKET"},
                    "price":            {"type": "number", "description": "Limit price in ₹"},
                    "stop_loss":        {"type": "number"},
                    "take_profit":      {"type": "number"},
                },
                "required": ["symbol", "transaction_type", "quantity"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_paper_portfolio",
            "description": "View paper portfolio positions and P&L.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

EXECUTION_TOOL_MAP = {
    "place_nse_order":    place_nse_order,
    "get_paper_portfolio": get_paper_portfolio,
}
