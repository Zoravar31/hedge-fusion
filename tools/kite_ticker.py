"""
KiteTicker Real-Time Price Streaming
======================================
Feature #13 — replaces yfinance polling with Zerodha WebSocket push.

KiteTicker gives you live tick data (LTP, bid/ask, volume) with sub-second
latency. This module wraps it into two interfaces:

  1. TickerSession  — long-running WebSocket (for scheduler / live mode).
     Stores latest tick for each subscribed instrument in a shared dict.

  2. get_live_price(ticker) — one-shot: returns latest LTP.
     In paper/no-Kite mode, gracefully falls back to yfinance.

  3. get_live_prices(tickers) — batch: returns {ticker: price, ...}.

Usage:
    from tools.kite_ticker import get_live_price, get_live_prices, TickerSession

    # One-shot (paper mode or live)
    price = get_live_price("HDFCBANK")   # → 1783.50

    # Batch
    prices = get_live_prices(["HDFCBANK", "RELIANCE", "INFY"])

    # Long-running session (live mode only — call from scheduler)
    session = TickerSession(["HDFCBANK", "RELIANCE"])
    session.start()
    # ... later ...
    price = session.latest("HDFCBANK")
    session.stop()

Requirements:
    pip install kiteconnect     # only needed for live mode
    pip install yfinance        # used as paper-mode fallback

Notes:
    - KiteConnect instruments use numeric instrument_tokens, not ticker strings.
      This module resolves ticker → token via the Kite instruments CSV (cached daily).
    - Access token expires at 6:30 PM IST. Run tools/kite_login.py each morning.
    - In paper mode (KITE_PAPER_TRADE=true) or when Kite creds are absent,
      all functions fall back silently to yfinance — no crashes.
"""

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from dotenv import load_dotenv
from loguru import logger

load_dotenv(Path(__file__).parent.parent / ".env")

CACHE_DIR = Path(__file__).parent.parent / "data" / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Shared tick store: {instrument_token: {"ltp": float, "ts": str, ...}}
_TICK_STORE: Dict[int, dict] = {}
_TOKEN_MAP:  Dict[str, int]  = {}   # ticker → instrument_token
_REVERSE_MAP: Dict[int, str] = {}   # instrument_token → ticker
_STORE_LOCK = threading.Lock()


# ─────────────────────────────────────────────
# Instrument token resolution
# ─────────────────────────────────────────────

def _is_paper_mode() -> bool:
    return os.getenv("KITE_PAPER_TRADE", "true").lower() in ("true", "1", "yes")


def _get_kite():
    """Return an authenticated KiteConnect instance (raises if unavailable)."""
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


def _load_instrument_tokens(exchange: str = "NSE") -> Dict[str, int]:
    """
    Load ticker → instrument_token map from Kite instruments CSV.
    The CSV is ~10MB and changes rarely — cached for 12 hours.
    """
    cache_path = CACHE_DIR / "kite_instruments_nse.json"

    # Check cache freshness (12-hour TTL)
    if cache_path.exists():
        age_hours = (time.time() - cache_path.stat().st_mtime) / 3600
        if age_hours < 12:
            try:
                return json.loads(cache_path.read_text(encoding="utf-8"))
            except Exception:
                pass

    try:
        kite = _get_kite()
        instruments = kite.instruments(exchange=exchange)
        token_map = {
            inst["tradingsymbol"]: inst["instrument_token"]
            for inst in instruments
            if inst.get("instrument_type") == "EQ"
        }
        cache_path.write_text(json.dumps(token_map), encoding="utf-8")
        logger.info("KiteTicker: loaded {} NSE instrument tokens", len(token_map))
        return token_map
    except Exception as e:
        logger.warning("KiteTicker: could not load instruments: {}", e)
        return {}


def _resolve_tokens(tickers: List[str]) -> Dict[str, int]:
    """Map a list of NSE tickers to their Kite instrument tokens."""
    global _TOKEN_MAP, _REVERSE_MAP
    if not _TOKEN_MAP:
        _TOKEN_MAP = _load_instrument_tokens("NSE")
        _REVERSE_MAP = {v: k for k, v in _TOKEN_MAP.items()}

    result = {}
    for t in tickers:
        tok = _TOKEN_MAP.get(t.upper())
        if tok:
            result[t.upper()] = tok
        else:
            logger.warning("KiteTicker: no instrument token for {}", t)
    return result


# ─────────────────────────────────────────────
# yfinance fallback
# ─────────────────────────────────────────────

def _yf_price(ticker: str) -> Optional[float]:
    """Fetch latest close via yfinance — used when Kite is unavailable."""
    try:
        import yfinance as yf
        sym = ticker.upper()
        if not sym.endswith(".NS"):
            sym += ".NS"
        hist = yf.Ticker(sym).history(period="1d", interval="1m")
        if hist is not None and not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception as e:
        logger.warning("yfinance fallback failed for {}: {}", ticker, e)
    return None


def _yf_prices(tickers: List[str]) -> Dict[str, Optional[float]]:
    """Batch yfinance fetch."""
    result = {}
    for t in tickers:
        result[t] = _yf_price(t)
    return result


# ─────────────────────────────────────────────
# Public one-shot API
# ─────────────────────────────────────────────

def get_live_price(ticker: str) -> Optional[float]:
    """
    Return the latest LTP (last traded price) for a single NSE ticker.

    Live mode:  hits KiteTicker WebSocket or Kite quote API.
    Paper mode: falls back to yfinance 1-minute bar close.

    Returns None if price cannot be obtained.
    """
    ticker = ticker.strip().upper()

    # 1. Check in-memory tick store first (populated by TickerSession if running)
    with _STORE_LOCK:
        token_map = _TOKEN_MAP.copy()

    tok = token_map.get(ticker)
    if tok:
        with _STORE_LOCK:
            tick = _TICK_STORE.get(tok)
        if tick:
            ltp = tick.get("last_price") or tick.get("ltp")
            if ltp:
                logger.debug("KiteTicker cache hit: {} @ ₹{:.2f}", ticker, ltp)
                return float(ltp)

    # 2. Paper / no-Kite → yfinance
    if _is_paper_mode():
        return _yf_price(ticker)

    # 3. Live mode → Kite quote API (single call, no WebSocket setup needed)
    try:
        kite    = _get_kite()
        tokens  = _resolve_tokens([ticker])
        if not tokens:
            logger.warning("No token for {} — falling back to yfinance", ticker)
            return _yf_price(ticker)

        token   = tokens[ticker]
        quotes  = kite.quote([f"NSE:{ticker}"])
        ltp     = quotes.get(f"NSE:{ticker}", {}).get("last_price")
        if ltp is not None:
            # Store in tick cache
            with _STORE_LOCK:
                _TICK_STORE[token] = {
                    "last_price": float(ltp),
                    "fetched_at": datetime.now().isoformat(),
                }
            logger.info("KiteTicker quote: {} @ ₹{:.2f}", ticker, ltp)
            return float(ltp)
    except Exception as e:
        logger.warning("Kite quote failed for {}: {} — using yfinance", ticker, e)

    return _yf_price(ticker)


def get_live_prices(tickers: List[str]) -> Dict[str, Optional[float]]:
    """
    Return {ticker: ltp} for a list of NSE tickers.

    In live mode: single Kite batch quote call (much faster than looping).
    In paper mode: yfinance batch fetch.
    """
    tickers = [t.strip().upper() for t in tickers]

    # Paper or no Kite
    if _is_paper_mode():
        return _yf_prices(tickers)

    try:
        kite     = _get_kite()
        symbols  = [f"NSE:{t}" for t in tickers]
        quotes   = kite.quote(symbols)
        result   = {}
        for t in tickers:
            key = f"NSE:{t}"
            ltp = (quotes.get(key) or {}).get("last_price")
            result[t] = float(ltp) if ltp is not None else None

        logger.info(
            "KiteTicker batch quote: {} prices fetched",
            sum(1 for v in result.values() if v is not None),
        )
        return result

    except Exception as e:
        logger.warning("Kite batch quote failed: {} — using yfinance", e)
        return _yf_prices(tickers)


# ─────────────────────────────────────────────
# TickerSession — long-running WebSocket
# ─────────────────────────────────────────────

class TickerSession:
    """
    Long-running KiteTicker WebSocket session.

    Subscribes to a list of NSE tickers and pushes every tick into
    the module-level _TICK_STORE dict. Other parts of the system
    can read live prices via session.latest(ticker) or get_live_price().

    Usage:
        session = TickerSession(["HDFCBANK", "RELIANCE", "INFY"])
        session.start()
        # TickerSession runs in background thread
        time.sleep(5)
        price = session.latest("HDFCBANK")   # → 1783.50
        session.stop()

    In paper mode this is a no-op — start() returns immediately.
    """

    def __init__(self, tickers: List[str]):
        self.tickers    = [t.strip().upper() for t in tickers]
        self._ws        = None
        self._thread    = None
        self._running   = False
        self._token_map : Dict[str, int] = {}

    def start(self) -> bool:
        """Start the WebSocket session. Returns True if started, False if paper/unavailable."""
        if _is_paper_mode():
            logger.info("TickerSession: paper mode — WebSocket not started (using yfinance)")
            return False

        try:
            from kiteconnect import KiteTicker as _KT
        except ImportError:
            logger.warning("TickerSession: kiteconnect not installed — pip install kiteconnect")
            return False

        try:
            self._token_map = _resolve_tokens(self.tickers)
            if not self._token_map:
                logger.warning("TickerSession: no instrument tokens resolved — aborting")
                return False

            api_key      = os.getenv("KITE_API_KEY", "").strip()
            access_token = os.getenv("KITE_ACCESS_TOKEN", "").strip()
            tokens       = list(self._token_map.values())

            ws = _KT(api_key=api_key, access_token=access_token)

            def on_ticks(ws_, ticks):
                with _STORE_LOCK:
                    for tick in ticks:
                        _TICK_STORE[tick["instrument_token"]] = tick
                logger.debug("TickerSession: {} ticks received", len(ticks))

            def on_connect(ws_, response):
                logger.info("TickerSession: WebSocket connected — subscribing {} instruments", len(tokens))
                ws_.subscribe(tokens)
                ws_.set_mode(ws_.MODE_QUOTE, tokens)

            def on_error(ws_, code, reason):
                logger.error("TickerSession: WebSocket error {}: {}", code, reason)

            def on_close(ws_, code, reason):
                logger.info("TickerSession: WebSocket closed {}: {}", code, reason)
                self._running = False

            ws.on_ticks   = on_ticks
            ws.on_connect = on_connect
            ws.on_error   = on_error
            ws.on_close   = on_close

            self._ws      = ws
            self._running = True

            # Run WebSocket in a daemon thread so it doesn't block main
            self._thread = threading.Thread(
                target=ws.connect,
                kwargs={"threaded": False},
                daemon=True,
                name="KiteTickerWS",
            )
            self._thread.start()
            logger.info("TickerSession: started for tickers: {}", self.tickers)
            return True

        except Exception as e:
            logger.error("TickerSession.start failed: {}", e)
            return False

    def stop(self):
        """Close the WebSocket connection."""
        if self._ws:
            try:
                self._ws.close()
                logger.info("TickerSession: WebSocket closed")
            except Exception as e:
                logger.warning("TickerSession.stop error: {}", e)
        self._running = False

    def latest(self, ticker: str) -> Optional[float]:
        """
        Return the latest LTP for a ticker from the WebSocket tick store.
        Falls back to get_live_price() if no tick received yet.
        """
        ticker = ticker.strip().upper()
        tok    = self._token_map.get(ticker)
        if tok:
            with _STORE_LOCK:
                tick = _TICK_STORE.get(tok)
            if tick:
                ltp = tick.get("last_price") or tick.get("ltp")
                if ltp:
                    return float(ltp)
        # No tick yet — use one-shot quote
        return get_live_price(ticker)

    def all_latest(self) -> Dict[str, Optional[float]]:
        """Return {ticker: ltp} for all subscribed tickers."""
        return {t: self.latest(t) for t in self.tickers}

    @property
    def is_connected(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    def __repr__(self):
        status = "connected" if self.is_connected else "stopped"
        return f"TickerSession({self.tickers}, status={status})"


# ─────────────────────────────────────────────
# Tool definition for agents (optional — agents
# can call get_live_price via get_nse_quote,
# but this lets them explicitly request live LTP)
# ─────────────────────────────────────────────

TICKER_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "get_live_price",
            "description": (
                "Get the latest live traded price (LTP) for an NSE stock. "
                "Uses Zerodha KiteTicker WebSocket in live mode, yfinance in paper mode. "
                "Use this immediately before placing an order to get the exact current price."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "NSE ticker e.g. HDFCBANK, RELIANCE",
                    }
                },
                "required": ["ticker"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_live_prices",
            "description": (
                "Get latest live prices for multiple NSE stocks in one call. "
                "Returns a dict of {ticker: price}. Faster than calling get_live_price in a loop."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "tickers": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of NSE tickers e.g. ['HDFCBANK', 'RELIANCE']",
                    }
                },
                "required": ["tickers"],
            },
        },
    },
]

TICKER_TOOL_MAP = {
    "get_live_price":  lambda ticker: json.dumps({"ticker": ticker, "ltp": get_live_price(ticker)}),
    "get_live_prices": lambda tickers: json.dumps(get_live_prices(tickers)),
}
