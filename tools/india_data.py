"""
India Data Feeds
================
All data tools used by the analyst agents:
  - NSE/BSE market data via yfinance
  - Indian financial news via RSS (Economic Times, Moneycontrol, Business Standard)
  - Macro context (RBI, SEBI bulletins via RSS)
  - FII/DII flow data via NSE website
"""

import json
import re
import time
import traceback
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests
import yfinance as yf
from loguru import logger

CACHE_DIR = Path(__file__).parent.parent / "data" / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; HedgeFusion/1.0)"}


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def _nse(ticker: str) -> str:
    t = ticker.strip().upper()
    return t if t.endswith((".NS", ".BO")) else t + ".NS"


def _cache_read(key: str, ttl_minutes: int = 15) -> Optional[str]:
    p = CACHE_DIR / f"{key}.json"
    if p.exists():
        age = (datetime.now() - datetime.fromtimestamp(p.stat().st_mtime)).total_seconds()
        if age < ttl_minutes * 60:
            return p.read_text(encoding="utf-8")
    return None


def _cache_write(key: str, data: str) -> None:
    (CACHE_DIR / f"{key}.json").write_text(data, encoding="utf-8")


# ──────────────────────────────────────────────
# Market Data Tools
# ──────────────────────────────────────────────

def get_nse_quote(ticker: str) -> str:
    """Live NSE quote: price, PE, 52W, sector, volume, market cap."""
    cache_key = f"quote_{ticker.upper()}"
    cached = _cache_read(cache_key, ttl_minutes=5)
    if cached:
        return cached

    symbol = _nse(ticker)
    try:
        t = yf.Ticker(symbol)
        info = {}
        try:
            info = t.info or {}
        except Exception:
            pass
        hist = None
        try:
            hist = t.history(period="5d", interval="1d")
        except Exception:
            pass

        keys = [
            "currentPrice", "previousClose", "open", "dayHigh", "dayLow",
            "regularMarketVolume", "averageVolume", "marketCap",
            "trailingPE", "forwardPE", "priceToBook", "dividendYield",
            "fiftyTwoWeekHigh", "fiftyTwoWeekLow", "fiftyDayAverage",
            "twoHundredDayAverage", "sector", "industry", "longName",
            "beta", "returnOnEquity", "debtToEquity", "earningsGrowth",
            "revenueGrowth", "operatingMargins", "profitMargins",
        ]
        filtered = {k: info.get(k) for k in keys if k in info}
        result = {
            "symbol": symbol,
            "ticker": ticker.upper(),
            "info": filtered,
            "history_rows": len(hist) if hist is not None and not hist.empty else 0,
        }
        if hist is not None and not hist.empty:
            result["latest_close"] = float(hist["Close"].iloc[-1])
            result["latest_volume"] = int(hist["Volume"].iloc[-1])

        out = json.dumps(result, default=str)
        _cache_write(cache_key, out)
        return out
    except Exception as e:
        logger.error("get_nse_quote {}: {}", ticker, e)
        return json.dumps({"error": str(e), "ticker": ticker})


def get_nse_history(ticker: str, period: str = "6mo", interval: str = "1d") -> str:
    """Historical OHLCV for technical analysis. Returns last 60 rows max."""
    cache_key = f"hist_{ticker.upper()}_{period}_{interval}"
    cached = _cache_read(cache_key, ttl_minutes=30)
    if cached:
        return cached

    symbol = _nse(ticker)
    try:
        hist = yf.Ticker(symbol).history(period=period, interval=interval)
        if hist is None or hist.empty:
            return json.dumps({"error": "no data", "symbol": symbol})

        # Keep last 60 rows to stay within token budget
        hist = hist.tail(60)
        records = []
        for idx, row in hist.iterrows():
            records.append({
                "date": str(idx.date()),
                "open": round(float(row["Open"]), 2),
                "high": round(float(row["High"]), 2),
                "low":  round(float(row["Low"]), 2),
                "close": round(float(row["Close"]), 2),
                "volume": int(row["Volume"]),
            })
        out = json.dumps({"symbol": symbol, "period": period, "data": records}, default=str)
        _cache_write(cache_key, out)
        return out
    except Exception as e:
        logger.error("get_nse_history {}: {}", ticker, e)
        return json.dumps({"error": str(e)})


def get_nse_fundamentals(ticker: str) -> str:
    """Income statement, balance sheet, cashflow for fundamentals analyst."""
    cache_key = f"fundamentals_{ticker.upper()}"
    cached = _cache_read(cache_key, ttl_minutes=120)
    if cached:
        return cached

    symbol = _nse(ticker)
    try:
        t = yf.Ticker(symbol)
        info = {}
        try:
            info = t.info or {}
        except Exception:
            pass

        fundamental_keys = [
            "totalRevenue", "grossProfits", "ebitda", "netIncomeToCommon",
            "totalDebt", "totalCash", "freeCashflow", "returnOnEquity",
            "returnOnAssets", "operatingMargins", "profitMargins",
            "revenueGrowth", "earningsGrowth", "currentRatio",
            "quickRatio", "debtToEquity", "totalCurrentAssets",
            "totalCurrentLiabilities", "bookValue", "priceToBook",
        ]
        fin_info = {k: info.get(k) for k in fundamental_keys if k in info}
        out_data = {"symbol": symbol, "financial_info": fin_info}

        for attr in ["quarterly_income_stmt", "quarterly_balance_sheet"]:
            try:
                df = getattr(t, attr, None)
                if df is not None and hasattr(df, "empty") and not df.empty:
                    out_data[attr] = json.loads(df.tail(4).to_json(orient="split", date_format="iso"))
            except Exception:
                pass

        out = json.dumps(out_data, default=str)
        _cache_write(cache_key, out)
        return out
    except Exception as e:
        logger.error("get_nse_fundamentals {}: {}", ticker, e)
        return json.dumps({"error": str(e)})


# ──────────────────────────────────────────────
# News & Sentiment Tools
# ──────────────────────────────────────────────

INDIA_NEWS_FEEDS = {
    "economic_times_markets": "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    "moneycontrol_markets":   "https://www.moneycontrol.com/rss/MCtopnews.xml",
    "business_standard":      "https://www.business-standard.com/rss/markets-106.rss",
    "livemint_markets":       "https://www.livemint.com/rss/markets",
    "nse_announcements":      "https://archives.nseindia.com/content/RSS/corpannouncement.xml",
}


def _fetch_rss(url: str, timeout: int = 8) -> list[dict]:
    """Fetch and parse an RSS feed. Returns list of {title, link, date}."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        r.raise_for_status()
        root = ET.fromstring(r.content)
        items = []
        for item in root.iter("item"):
            title = item.findtext("title", "").strip()
            link  = item.findtext("link", "").strip()
            date  = item.findtext("pubDate", "") or item.findtext("dc:date", "")
            desc  = item.findtext("description", "")
            # Strip HTML tags from description
            desc = re.sub(r"<[^>]+>", " ", desc).strip()[:200]
            if title:
                items.append({"title": title, "link": link, "date": date, "desc": desc})
        return items[:15]  # cap at 15 items per feed
    except Exception as e:
        logger.warning("RSS fetch failed {}: {}", url, e)
        return []


def get_india_news(ticker: str) -> str:
    """
    Fetch recent news for an Indian stock from multiple financial news sources.
    Filters by ticker name / company name mentions.
    """
    cache_key = f"news_{ticker.upper()}"
    cached = _cache_read(cache_key, ttl_minutes=20)
    if cached:
        return cached

    # Get company name for matching
    quote = json.loads(get_nse_quote(ticker))
    company_name = (quote.get("info", {}).get("longName") or ticker).lower()
    ticker_lower = ticker.lower()

    all_articles = []
    for source, url in INDIA_NEWS_FEEDS.items():
        articles = _fetch_rss(url)
        for a in articles:
            title_lower = a["title"].lower()
            if ticker_lower in title_lower or any(
                word in title_lower for word in company_name.split()[:3] if len(word) > 3
            ):
                a["source"] = source
                all_articles.append(a)

    # If no ticker-specific news, return general market news
    if not all_articles:
        general = _fetch_rss(INDIA_NEWS_FEEDS["economic_times_markets"])[:5]
        for a in general:
            a["source"] = "economic_times_general"
        all_articles = general

    out = json.dumps({
        "ticker": ticker.upper(),
        "articles_found": len(all_articles),
        "articles": all_articles[:20],
        "fetched_at": datetime.now().isoformat(),
    })
    _cache_write(cache_key, out)
    return out


def get_macro_india_context() -> str:
    """
    Returns a structured macro context for India:
    RBI stance, recent policy, INR rate, Nifty level.
    Uses RSS + yfinance for indices.
    """
    cache_key = "macro_india"
    cached = _cache_read(cache_key, ttl_minutes=60)
    if cached:
        return cached

    context = {"fetched_at": datetime.now().isoformat()}

    # Nifty 50 level
    try:
        nifty = yf.Ticker("^NSEI")
        info = nifty.info or {}
        context["nifty50"] = {
            "current": info.get("regularMarketPrice"),
            "prev_close": info.get("regularMarketPreviousClose"),
            "52w_high": info.get("fiftyTwoWeekHigh"),
            "52w_low": info.get("fiftyTwoWeekLow"),
        }
    except Exception:
        context["nifty50"] = {}

    # INR/USD
    try:
        inr = yf.Ticker("INR=X")
        context["inr_usd"] = inr.info.get("regularMarketPrice")
    except Exception:
        context["inr_usd"] = None

    # BankNifty
    try:
        bn = yf.Ticker("^NSEBANK")
        bn_info = bn.info or {}
        context["banknifty"] = {
            "current": bn_info.get("regularMarketPrice"),
            "prev_close": bn_info.get("regularMarketPreviousClose"),
        }
    except Exception:
        context["banknifty"] = {}

    # Crude oil (relevant for Indian inflation)
    try:
        crude = yf.Ticker("CL=F")
        context["crude_usd"] = crude.info.get("regularMarketPrice")
    except Exception:
        context["crude_usd"] = None

    # RBI/macro news
    rbi_news = _fetch_rss("https://www.rbi.org.in/RSS/RSSFeed.aspx?Id=4", timeout=5)
    context["rbi_recent"] = rbi_news[:5]

    out = json.dumps(context, default=str)
    _cache_write(cache_key, out)
    return out


def get_bulk_block_deals(ticker: str) -> str:
    """
    Fetch bulk/block deal data from NSE for a given ticker.
    These are high-conviction institutional moves — critical signal for Indian stocks.
    """
    cache_key = f"deals_{ticker.upper()}"
    cached = _cache_read(cache_key, ttl_minutes=60)
    if cached:
        return cached

    # NSE bulk deals API
    url = "https://www.nseindia.com/api/bulk-deals"
    try:
        session = requests.Session()
        # NSE requires a prior visit to set cookies
        session.get("https://www.nseindia.com", headers=HEADERS, timeout=5)
        time.sleep(0.5)
        r = session.get(url, headers=HEADERS, timeout=8)
        r.raise_for_status()
        deals = r.json()
        ticker_deals = [
            d for d in (deals.get("data") or [])
            if d.get("symbol", "").upper() == ticker.upper()
        ][:20]
        out = json.dumps({"ticker": ticker.upper(), "bulk_deals": ticker_deals}, default=str)
        _cache_write(cache_key, out)
        return out
    except Exception as e:
        logger.warning("bulk deals fetch failed: {}", e)
        return json.dumps({"ticker": ticker, "bulk_deals": [], "note": str(e)})


# ──────────────────────────────────────────────
# Tool registry for OpenAI function calling
# ──────────────────────────────────────────────

DATA_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "get_nse_quote",
            "description": "Get live NSE quote: price, PE, 52W high/low, market cap, sector.",
            "parameters": {
                "type": "object",
                "properties": {"ticker": {"type": "string", "description": "NSE ticker e.g. RELIANCE"}},
                "required": ["ticker"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_nse_history",
            "description": "Get historical OHLCV data for technical analysis.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                    "period": {"type": "string", "enum": ["1mo","3mo","6mo","1y"], "default": "6mo"},
                    "interval": {"type": "string", "enum": ["1d","1wk"], "default": "1d"},
                },
                "required": ["ticker"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_nse_fundamentals",
            "description": "Get income statement, balance sheet, and cashflow for fundamental analysis.",
            "parameters": {
                "type": "object",
                "properties": {"ticker": {"type": "string"}},
                "required": ["ticker"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_india_news",
            "description": "Fetch recent news articles for an Indian stock from ET, Moneycontrol, BS.",
            "parameters": {
                "type": "object",
                "properties": {"ticker": {"type": "string"}},
                "required": ["ticker"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_macro_india_context",
            "description": "Get India macro context: Nifty level, INR/USD, crude, BankNifty, RBI news.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_bulk_block_deals",
            "description": "Get bulk and block deal data from NSE for institutional flow signals.",
            "parameters": {
                "type": "object",
                "properties": {"ticker": {"type": "string"}},
                "required": ["ticker"],
            },
        },
    },
]

DATA_TOOL_MAP = {
    "get_nse_quote": get_nse_quote,
    "get_nse_history": get_nse_history,
    "get_nse_fundamentals": get_nse_fundamentals,
    "get_india_news": get_india_news,
    "get_macro_india_context": get_macro_india_context,
    "get_bulk_block_deals": get_bulk_block_deals,
}


# ──────────────────────────────────────────────
# Feature #12 — Nifty 50 PCR (Options Chain)
# ──────────────────────────────────────────────

def get_nifty_pcr() -> str:
    """
    Fetch live Nifty 50 Put/Call Ratio from NSE options chain.

    PCR interpretation (contrarian signal):
      PCR > 1.3  → market heavily hedged / fearful → contrarian BUY signal
      PCR 0.8–1.3 → neutral / balanced market
      PCR < 0.8  → call-heavy / complacent → caution, market may reverse down

    Data source: NSE free API (no auth required, but needs browser headers + cookies).
    Cached for 15 minutes — options PCR shifts intraday.
    """
    cache_key = "nifty_pcr"
    cached = _cache_read(cache_key, ttl_minutes=15)
    if cached:
        return cached

    url = "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY"
    try:
        session = requests.Session()
        # NSE requires a prior page visit to set anti-bot cookies
        session.get(
            "https://www.nseindia.com",
            headers={
                **HEADERS,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.nseindia.com/",
            },
            timeout=8,
        )
        time.sleep(0.6)  # brief pause so NSE doesn't rate-limit

        r = session.get(
            url,
            headers={
                **HEADERS,
                "Accept": "application/json",
                "Referer": "https://www.nseindia.com/option-chain",
                "X-Requested-With": "XMLHttpRequest",
            },
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()

        # NSE response: data.filtered.data = list of strike rows
        # Each row has CE (call) and PE (put) sub-dicts with openInterest
        records = (data.get("filtered") or data.get("records") or {}).get("data") or []

        total_call_oi = 0
        total_put_oi  = 0
        atm_strike    = None
        spot_price    = None

        # Spot price lives in filtered.underlyingValue or records.underlyingValue
        for block in [data.get("filtered", {}), data.get("records", {})]:
            if block.get("underlyingValue"):
                spot_price = float(block["underlyingValue"])
                break

        for row in records:
            ce = row.get("CE") or {}
            pe = row.get("PE") or {}
            total_call_oi += ce.get("openInterest", 0)
            total_put_oi  += pe.get("openInterest", 0)

        if total_call_oi == 0:
            raise ValueError("Zero call OI — likely NSE blocked the request")

        pcr = round(total_put_oi / total_call_oi, 4)

        # Derive ATM strike (nearest to spot)
        if spot_price and records:
            strikes = []
            for row in records:
                ce = row.get("CE") or {}
                pe = row.get("PE") or {}
                strike = ce.get("strikePrice") or pe.get("strikePrice")
                if strike:
                    strikes.append(strike)
            if strikes:
                atm_strike = min(strikes, key=lambda s: abs(s - spot_price))

        # PCR sentiment interpretation
        if pcr >= 1.5:
            signal = "EXTREME FEAR — strong contrarian BUY signal"
            sentiment = "bullish_contrarian"
        elif pcr >= 1.3:
            signal = "HIGH HEDGING — moderate contrarian buy signal"
            sentiment = "mildly_bullish"
        elif pcr >= 0.8:
            signal = "NEUTRAL — balanced put/call activity"
            sentiment = "neutral"
        elif pcr >= 0.6:
            signal = "CALL-HEAVY — mild complacency, watch for reversal"
            sentiment = "mildly_bearish"
        else:
            signal = "EXTREME COMPLACENCY — elevated reversal risk"
            sentiment = "bearish_contrarian"

        result = {
            "symbol":          "NIFTY",
            "pcr":             pcr,
            "total_put_oi":    total_put_oi,
            "total_call_oi":   total_call_oi,
            "spot_price":      spot_price,
            "atm_strike":      atm_strike,
            "signal":          signal,
            "sentiment":       sentiment,
            "interpretation":  (
                f"PCR of {pcr:.2f} — {signal}. "
                f"Total Put OI: {total_put_oi:,} | Total Call OI: {total_call_oi:,}. "
                f"A PCR > 1.3 means market participants are buying more puts (protection/hedges) "
                f"than calls — historically a contrarian bullish signal as retail fear peaks."
            ),
            "fetched_at": datetime.now().isoformat(),
        }

        out = json.dumps(result, default=str)
        _cache_write(cache_key, out)
        logger.info("Nifty PCR fetched: {:.3f} — {}", pcr, signal)
        return out

    except Exception as e:
        logger.warning("get_nifty_pcr failed: {}", e)
        # Return a degraded-gracefully result so agents don't crash
        fallback = {
            "symbol": "NIFTY",
            "pcr": None,
            "signal": "unavailable",
            "sentiment": "unknown",
            "error": str(e),
            "interpretation": (
                "Nifty PCR could not be fetched from NSE (possible rate limit or market closed). "
                "Proceed with other sentiment signals."
            ),
            "fetched_at": datetime.now().isoformat(),
        }
        return json.dumps(fallback)


# Register get_nifty_pcr in the tool registry
DATA_TOOL_DEFINITIONS.append({
    "type": "function",
    "function": {
        "name": "get_nifty_pcr",
        "description": (
            "Get Nifty 50 Put/Call Ratio (PCR) from NSE live options chain. "
            "PCR > 1.3 = market heavily hedged / fearful = contrarian BUY signal. "
            "PCR < 0.8 = call-heavy complacency = caution. "
            "Use this to gauge overall market fear/greed before placing any trade."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
})
DATA_TOOL_MAP["get_nifty_pcr"] = get_nifty_pcr
