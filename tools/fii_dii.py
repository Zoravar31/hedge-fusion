"""
FII/DII Intelligence Module
==============================
Fetches real institutional money flow data from NSE India.

Data sources:
  1. NSE FII/DII daily activity API
     https://www.nseindia.com/api/fiidiiTradeReact
     Returns: net buy/sell by FII and DII in equity and derivatives

  2. NSE Bulk Deals
     https://www.nseindia.com/api/bulk-deals
     Returns: large single-transaction trades (>0.5% of equity)

  3. NSE Block Deals
     https://www.nseindia.com/api/block-deals
     Returns: negotiated block trades (>500k shares or >₹5cr)

  4. NSE Shareholding Pattern (quarterly)
     Promoter %, FII %, DII %, Public % per stock

  5. SEBI FPI data (monthly aggregate)
     Sector-level foreign portfolio investment

What the data tells you:
  FII buying  → foreign confidence in India, risk-on signal
  FII selling → foreign risk-off, INR pressure, watch for correction
  DII buying  → domestic funds (MF, insurance) absorbing FII selling, floor signal
  DII selling → domestic funds also exiting, red flag
  Both buying → maximum bullish signal for Indian markets
  Bulk deal BUY on a stock → institution accumulating, strong signal
  Bulk deal SELL → institution distributing, exit signal

Usage:
    from tools.fii_dii import (
        get_fii_dii_daily,
        get_bulk_deals,
        get_block_deals,
        get_stock_shareholding,
        get_fii_dii_summary,
        FII_DII_TOOL_DEFINITIONS,
        FII_DII_TOOL_MAP,
    )
"""

import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests
from loguru import logger

CACHE_DIR = Path(__file__).parent.parent / "data" / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# NSE requires browser-like headers + cookie handshake
NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":         "https://www.nseindia.com/",
    "Connection":      "keep-alive",
}

_NSE_SESSION: Optional[requests.Session] = None


def _get_nse_session() -> requests.Session:
    """
    Returns an authenticated NSE session.
    NSE blocks direct API calls — you must first visit the homepage
    to get cookies, then call the API.
    """
    global _NSE_SESSION
    if _NSE_SESSION is not None:
        return _NSE_SESSION

    session = requests.Session()
    session.headers.update(NSE_HEADERS)
    try:
        # Step 1: visit homepage to set initial cookies
        session.get("https://www.nseindia.com", timeout=10)
        time.sleep(0.8)
        # Step 2: visit a market data page to get full cookie set
        session.get("https://www.nseindia.com/market-data/live-equity-market", timeout=10)
        time.sleep(0.5)
        _NSE_SESSION = session
    except Exception as e:
        logger.warning("NSE session init failed: {}", e)
        _NSE_SESSION = session  # use anyway, may partially work
    return _NSE_SESSION


def _cache_get(key: str, ttl_min: int = 30) -> Optional[str]:
    p = CACHE_DIR / f"fii_{key}.json"
    if p.exists():
        age = (datetime.now() - datetime.fromtimestamp(p.stat().st_mtime)).total_seconds()
        if age < ttl_min * 60:
            return p.read_text(encoding="utf-8")
    return None


def _cache_set(key: str, data: str) -> None:
    (CACHE_DIR / f"fii_{key}.json").write_text(data, encoding="utf-8")


# ── 1. FII / DII Daily Activity ───────────────────────────────

def get_fii_dii_daily(days: int = 10) -> str:
    """
    Fetch FII and DII net buy/sell activity for the last N trading days.

    Returns JSON with:
      - date
      - fii_net_inr_cr: FII net (positive = net buy, negative = net sell) in ₹ crore
      - dii_net_inr_cr: DII net in ₹ crore
      - fii_buy_inr_cr, fii_sell_inr_cr
      - dii_buy_inr_cr, dii_sell_inr_cr
      - combined_signal: "BOTH_BUYING" / "FII_BUYING_DII_SELLING" / etc.

    Parameters
    ----------
    days : int
        Number of recent trading days to return (max 30).
    """
    cache_key = f"daily_{days}"
    cached = _cache_get(cache_key, ttl_min=60)
    if cached:
        return cached

    try:
        session = _get_nse_session()
        url     = "https://www.nseindia.com/api/fiidiiTradeReact"
        resp    = session.get(url, timeout=12)
        resp.raise_for_status()
        raw_data = resp.json()

        processed = []
        for item in raw_data[:days]:
            try:
                fii_buy  = float(str(item.get("buyValue",  "0")).replace(",","") or 0)
                fii_sell = float(str(item.get("sellValue", "0")).replace(",","") or 0)
                fii_net  = fii_buy - fii_sell

                # DII data is in a separate field or alternating rows in the NSE API
                # The API returns rows: [FII_row, DII_row, ...] alternating
                # We handle this by checking the 'category' field
                category = str(item.get("category","")).upper()

                entry = {
                    "date":            item.get("date", ""),
                    "category":        category,
                    "buy_inr_cr":      round(fii_buy / 1e7, 2),
                    "sell_inr_cr":     round(fii_sell / 1e7, 2),
                    "net_inr_cr":      round(fii_net / 1e7, 2),
                }
                processed.append(entry)
            except Exception:
                pass

        # Pair FII and DII rows
        paired = []
        i = 0
        while i < len(processed) - 1:
            row_a = processed[i]
            row_b = processed[i+1]

            # Identify which is FII vs DII
            fii_row = row_a if "FII" in row_a.get("category","") or "FPI" in row_a.get("category","") else row_b
            dii_row = row_b if fii_row is row_a else row_a

            fii_net = fii_row.get("net_inr_cr", 0)
            dii_net = dii_row.get("net_inr_cr", 0)

            if fii_net > 0 and dii_net > 0:
                signal = "BOTH_BUYING 🔥"
            elif fii_net > 0 and dii_net < 0:
                signal = "FII_BUYING_DII_SELLING"
            elif fii_net < 0 and dii_net > 0:
                signal = "FII_SELLING_DII_BUYING (DII absorbing)"
            elif fii_net < 0 and dii_net < 0:
                signal = "BOTH_SELLING 🚨"
            else:
                signal = "NEUTRAL"

            paired.append({
                "date":            fii_row.get("date", ""),
                "fii_buy_cr":      fii_row.get("buy_inr_cr", 0),
                "fii_sell_cr":     fii_row.get("sell_inr_cr", 0),
                "fii_net_cr":      fii_net,
                "dii_buy_cr":      dii_row.get("buy_inr_cr", 0),
                "dii_sell_cr":     dii_row.get("sell_inr_cr", 0),
                "dii_net_cr":      dii_net,
                "combined_signal": signal,
            })
            i += 2

        out = json.dumps({
            "source":     "NSE FII/DII Trade React API",
            "days":       len(paired),
            "fetched_at": datetime.now().isoformat(),
            "data":       paired,
            "summary": {
                "fii_net_10d": round(sum(p["fii_net_cr"] for p in paired), 2),
                "dii_net_10d": round(sum(p["dii_net_cr"] for p in paired), 2),
                "fii_trend":   "BUYING" if sum(p["fii_net_cr"] for p in paired) > 0 else "SELLING",
                "dii_trend":   "BUYING" if sum(p["dii_net_cr"] for p in paired) > 0 else "SELLING",
            }
        }, default=str)
        _cache_set(cache_key, out)
        return out

    except Exception as e:
        logger.warning("FII/DII daily fetch failed: {} — using fallback", e)
        return _fii_dii_fallback(days)


def _fii_dii_fallback(days: int) -> str:
    """
    Fallback: scrape from alternative source or return structured placeholder.
    When NSE API is unavailable (common during off-hours), return
    a structured response indicating data is unavailable.
    """
    return json.dumps({
        "source":     "fallback",
        "note":       "NSE API unavailable. Check during market hours (9 AM - 6 PM IST).",
        "days":       0,
        "data":       [],
        "summary": {
            "fii_net_10d": None,
            "dii_net_10d": None,
            "fii_trend":   "UNKNOWN",
            "dii_trend":   "UNKNOWN",
        },
        "manual_sources": [
            "https://www.nseindia.com/reports-indices-capital-market-fii-dii",
            "https://www.moneycontrol.com/stocks/marketstats/fii_dii_activity/index.php",
            "https://economictimes.indiatimes.com/markets/stocks/fii-dii-data",
        ]
    })


# ── 2. Bulk Deals ─────────────────────────────────────────────

def get_bulk_deals(ticker: Optional[str] = None, days: int = 30) -> str:
    """
    Fetch bulk deal data from NSE.
    Bulk deals = single trades >0.5% of total equity shares.

    Parameters
    ----------
    ticker : str, optional
        Filter to a specific NSE ticker. If None, returns all bulk deals.
    days   : int
        Look back period in days.

    Returns JSON with deals including:
      - symbol, client_name, buy_sell, qty, price, deal_value_cr
      - client_type inference (FII/MF/Insurance/HNI/Promoter)
    """
    cache_key = f"bulk_{ticker or 'all'}_{days}"
    cached = _cache_get(cache_key, ttl_min=60)
    if cached:
        return cached

    try:
        session  = _get_nse_session()
        url      = "https://www.nseindia.com/api/bulk-deals"
        resp     = session.get(url, timeout=12)
        resp.raise_for_status()
        raw      = resp.json()
        deals    = raw.get("data", raw) if isinstance(raw, dict) else raw

        cutoff   = datetime.now() - timedelta(days=days)
        filtered = []

        for d in (deals or []):
            try:
                sym   = str(d.get("symbol","") or d.get("SYMBOL","")).upper()
                if ticker and sym != ticker.upper():
                    continue

                deal_date_str = d.get("date","") or d.get("DATE","") or d.get("BD_DT_DATE","")
                try:
                    deal_dt = datetime.strptime(str(deal_date_str)[:10], "%d-%b-%Y")
                except Exception:
                    try:
                        deal_dt = datetime.strptime(str(deal_date_str)[:10], "%Y-%m-%d")
                    except Exception:
                        deal_dt = datetime.now()

                if deal_dt < cutoff:
                    continue

                qty    = float(str(d.get("BD_QTY_TRD","0") or d.get("quantity","0")).replace(",","") or 0)
                price  = float(str(d.get("BD_TP_WATP","0") or d.get("price","0")).replace(",","") or 0)
                client = str(d.get("BD_CLIENT_NAME","") or d.get("clientName","") or "").strip()
                bs     = str(d.get("BD_BUY_SELL","") or d.get("buySell","") or "").upper()
                val_cr = round(qty * price / 1e7, 2)

                # Infer client type from name patterns
                client_up = client.upper()
                if any(x in client_up for x in ["FOREIGN","FPI","FII","MAURITIUS","SINGAPORE","CAYMAN","GOLDMAN","MORGAN","NOMURA","CITIBANK","HSBC","DEUTSCHE","CREDIT SUISSE","BARCLAYS"]):
                    client_type = "FII/FPI"
                elif any(x in client_up for x in ["MUTUAL FUND","MF","HDFC AMC","SBI FUNDS","NIPPON","MIRAE","KOTAK AMC","AXIS AMC","ICICI AMC","FRANKLIN","MOTILAL","DSP"]):
                    client_type = "DOMESTIC MF"
                elif any(x in client_up for x in ["INSURANCE","LIC","LIFE","GENERAL","GIC","NEW INDIA","ORIENTAL"]):
                    client_type = "INSURANCE"
                elif any(x in client_up for x in ["PROMOTER","PROMOTER GROUP","HOLDING"]):
                    client_type = "PROMOTER"
                elif val_cr > 50:
                    client_type = "LARGE INSTITUTION"
                else:
                    client_type = "HNI/OTHER"

                filtered.append({
                    "date":        deal_date_str,
                    "symbol":      sym,
                    "client":      client,
                    "client_type": client_type,
                    "buy_sell":    bs,
                    "qty":         int(qty),
                    "price_inr":   price,
                    "value_cr":    val_cr,
                    "signal":      (
                        f"{'🟢 ACCUMULATION' if bs=='BUY' else '🔴 DISTRIBUTION'} by {client_type}"
                    ),
                })
            except Exception:
                continue

        # Sort by deal value
        filtered.sort(key=lambda x: x.get("value_cr",0), reverse=True)

        # Aggregate signals
        buy_value  = sum(d["value_cr"] for d in filtered if d["buy_sell"]=="BUY")
        sell_value = sum(d["value_cr"] for d in filtered if d["buy_sell"]=="SELL")
        fii_deals  = [d for d in filtered if d["client_type"]=="FII/FPI"]
        mf_deals   = [d for d in filtered if d["client_type"]=="DOMESTIC MF"]

        out = json.dumps({
            "source":         "NSE Bulk Deals API",
            "ticker":         ticker or "ALL",
            "days":           days,
            "total_deals":    len(filtered),
            "buy_value_cr":   round(buy_value, 2),
            "sell_value_cr":  round(sell_value, 2),
            "net_value_cr":   round(buy_value - sell_value, 2),
            "fii_deals":      len(fii_deals),
            "mf_deals":       len(mf_deals),
            "top_deals":      filtered[:20],
            "aggregate_signal": (
                "STRONG ACCUMULATION 🔥" if buy_value > sell_value * 2 else
                "ACCUMULATION" if buy_value > sell_value else
                "DISTRIBUTION 🚨" if sell_value > buy_value * 2 else
                "MILD DISTRIBUTION" if sell_value > buy_value else
                "NEUTRAL"
            ),
        }, default=str)
        _cache_set(cache_key, out)
        return out

    except Exception as e:
        logger.warning("Bulk deals fetch failed: {}", e)
        return json.dumps({
            "source": "fallback",
            "ticker": ticker or "ALL",
            "error":  str(e),
            "note":   "Bulk deals unavailable. Check NSE website during market hours.",
            "manual": "https://www.nseindia.com/market-data/bulk-deals",
        })


# ── 3. Block Deals ────────────────────────────────────────────

def get_block_deals(ticker: Optional[str] = None) -> str:
    """
    Fetch block deal data from NSE.
    Block deals = negotiated trades >500k shares OR >₹5 crore.
    More secretive than bulk deals — signals serious institutional intent.

    Parameters
    ----------
    ticker : str, optional
        Filter to a specific NSE ticker.
    """
    cache_key = f"block_{ticker or 'all'}"
    cached = _cache_get(cache_key, ttl_min=120)
    if cached:
        return cached

    try:
        session = _get_nse_session()
        url     = "https://www.nseindia.com/api/block-deals"
        resp    = session.get(url, timeout=12)
        resp.raise_for_status()
        raw     = resp.json()
        deals   = raw.get("data", raw) if isinstance(raw, dict) else raw

        filtered = []
        for d in (deals or []):
            sym = str(d.get("symbol","") or d.get("SYMBOL","")).upper()
            if ticker and sym != ticker.upper():
                continue
            qty   = float(str(d.get("qty","0") or d.get("BLOCK_DEAL_QTY","0")).replace(",","") or 0)
            price = float(str(d.get("price","0") or d.get("BLOCK_DEAL_PRICE","0")).replace(",","") or 0)
            val   = round(qty * price / 1e7, 2)
            filtered.append({
                "date":     d.get("date","") or d.get("BLOCK_DEAL_DATE",""),
                "symbol":   sym,
                "client":   d.get("clientName","") or d.get("BLOCK_DEAL_CLIENT_NAME",""),
                "buy_sell": str(d.get("buySell","") or d.get("BLOCK_DEAL_BUY_SELL_FLAG","")).upper(),
                "qty":      int(qty),
                "price_inr":price,
                "value_cr": val,
            })

        filtered.sort(key=lambda x: x.get("value_cr",0), reverse=True)
        out = json.dumps({
            "source":      "NSE Block Deals API",
            "ticker":      ticker or "ALL",
            "total_deals": len(filtered),
            "deals":       filtered[:15],
        }, default=str)
        _cache_set(cache_key, out)
        return out

    except Exception as e:
        logger.warning("Block deals fetch failed: {}", e)
        return json.dumps({"source":"fallback","error":str(e),"deals":[]})


# ── 4. Stock Shareholding Pattern ─────────────────────────────

def get_stock_shareholding(ticker: str) -> str:
    """
    Fetch quarterly shareholding pattern for an NSE stock.
    Shows FII %, DII %, Promoter %, Public % changes over time.

    High FII % and rising → institutional confidence
    Promoter pledge rising → RED FLAG
    DII buying when FII selling → floor signal

    Parameters
    ----------
    ticker : str
        NSE ticker without suffix e.g. RELIANCE, HDFCBANK.
    """
    cache_key = f"shp_{ticker.upper()}"
    cached = _cache_get(cache_key, ttl_min=720)  # 12h cache — quarterly data
    if cached:
        return cached

    try:
        session = _get_nse_session()
        # NSE shareholding pattern API
        url  = f"https://www.nseindia.com/api/corporate-shareholding-pattern?symbol={ticker.upper()}&period=Quarterly"
        resp = session.get(url, timeout=12)
        resp.raise_for_status()
        data = resp.json()

        quarters = []
        for q in (data or [])[:6]:   # last 6 quarters
            try:
                quarters.append({
                    "quarter":          q.get("date",""),
                    "promoter_pct":     float(q.get("promoterAndPromoterGroup","0") or 0),
                    "fii_fpi_pct":      float(q.get("foreignInstitutions","0") or q.get("fpiTotal","0") or 0),
                    "dii_pct":          float(q.get("domesticInstitutions","0") or 0),
                    "mutual_fund_pct":  float(q.get("mutualFunds","0") or 0),
                    "insurance_pct":    float(q.get("insurance","0") or 0),
                    "public_pct":       float(q.get("publicAndOthers","0") or 0),
                    "promoter_pledge_pct": float(q.get("promoterPledge","0") or 0),
                })
            except Exception:
                pass

        # Compute trends (latest vs 4 quarters ago)
        trends = {}
        if len(quarters) >= 2:
            latest = quarters[0]
            prev   = quarters[-1]
            trends = {
                "fii_change":      round(latest["fii_fpi_pct"] - prev["fii_fpi_pct"], 2),
                "dii_change":      round(latest["dii_pct"] - prev["dii_pct"], 2),
                "promoter_change": round(latest["promoter_pct"] - prev["promoter_pct"], 2),
                "pledge_change":   round(latest["promoter_pledge_pct"] - prev["promoter_pledge_pct"], 2),
                "fii_trend":       "INCREASING" if latest["fii_fpi_pct"] > prev["fii_fpi_pct"] else "DECREASING",
                "dii_trend":       "INCREASING" if latest["dii_pct"] > prev["dii_pct"] else "DECREASING",
            }
            # Red flags
            red_flags = []
            if trends["pledge_change"] > 2:
                red_flags.append(f"Promoter pledge rising +{trends['pledge_change']:.1f}%")
            if trends["promoter_change"] < -2:
                red_flags.append(f"Promoter stake declining {trends['promoter_change']:.1f}%")
            if trends["fii_change"] < -3:
                red_flags.append(f"FII exiting rapidly {trends['fii_change']:.1f}%")
            trends["red_flags"] = red_flags
            trends["green_flags"] = []
            if trends["fii_change"] > 2:
                trends["green_flags"].append(f"FII increasing stake +{trends['fii_change']:.1f}%")
            if trends["dii_change"] > 1 and trends["fii_trend"] == "DECREASING":
                trends["green_flags"].append("DII absorbing FII selling — floor signal")

        out = json.dumps({
            "ticker":   ticker.upper(),
            "source":   "NSE Shareholding Pattern",
            "quarters": quarters,
            "trends":   trends,
            "latest":   quarters[0] if quarters else {},
        }, default=str)
        _cache_set(cache_key, out)
        return out

    except Exception as e:
        logger.warning("Shareholding fetch failed {}: {}", ticker, e)
        return json.dumps({
            "ticker": ticker.upper(),
            "source": "fallback",
            "error":  str(e),
            "note":   "Check NSE website: https://www.nseindia.com/companies-listing/corporate-filings-shareholding-pattern",
        })


# ── 5. FII/DII Summary (market-level) ────────────────────────

def get_fii_dii_summary() -> str:
    """
    Get a comprehensive FII/DII market-level summary including:
    - Net flows over 1d, 5d, 10d, 1mo
    - Recent bulk deals across market
    - Market-level institutional sentiment signal

    Returns structured JSON for use by agents.
    """
    cache_key = "summary"
    cached = _cache_get(cache_key, ttl_min=45)
    if cached:
        return cached

    daily  = json.loads(get_fii_dii_daily(days=20))
    bulk   = json.loads(get_bulk_deals(days=7))

    data   = daily.get("data", [])
    summary_obj = daily.get("summary", {})

    # Compute rolling sums
    def net_sum(n):
        return round(sum(d.get("fii_net_cr",0) for d in data[:n]), 2)

    fii_1d  = data[0]["fii_net_cr"] if data else None
    fii_5d  = net_sum(5) if len(data) >= 5 else None
    fii_10d = net_sum(10) if len(data) >= 10 else None

    def dii_sum(n):
        return round(sum(d.get("dii_net_cr",0) for d in data[:n]), 2)

    dii_1d  = data[0]["dii_net_cr"] if data else None
    dii_5d  = dii_sum(5) if len(data) >= 5 else None
    dii_10d = dii_sum(10) if len(data) >= 10 else None

    # Overall institutional signal
    def signal(fii, dii):
        if fii is None:
            return "DATA_UNAVAILABLE"
        if fii > 500 and dii > 200:
            return "STRONGLY_BULLISH 🔥 (Both buying heavily)"
        if fii > 0 and dii > 0:
            return "BULLISH (Both buying)"
        if fii > 0 and dii < 0:
            return "MIXED (FII buying, DII selling)"
        if fii < 0 and dii > 0:
            return "CAUTIOUS (FII selling, DII absorbing)"
        if fii < -500 and dii < -200:
            return "STRONGLY_BEARISH 🚨 (Both selling heavily)"
        if fii < 0 and dii < 0:
            return "BEARISH (Both selling)"
        return "NEUTRAL"

    result = {
        "source":     "NSE FII/DII API + Bulk Deals",
        "fetched_at": datetime.now().isoformat(),
        "fii_flows": {
            "today_cr":    fii_1d,
            "5day_cr":     fii_5d,
            "10day_cr":    fii_10d,
            "trend":       summary_obj.get("fii_trend","UNKNOWN"),
        },
        "dii_flows": {
            "today_cr":    dii_1d,
            "5day_cr":     dii_5d,
            "10day_cr":    dii_10d,
            "trend":       summary_obj.get("dii_trend","UNKNOWN"),
        },
        "bulk_deals": {
            "7day_count":     bulk.get("total_deals",0),
            "buy_value_cr":   bulk.get("buy_value_cr",0),
            "sell_value_cr":  bulk.get("sell_value_cr",0),
            "signal":         bulk.get("aggregate_signal","UNKNOWN"),
            "top_3":          bulk.get("top_deals",[])[:3],
        },
        "market_signal_1d":  signal(fii_1d, dii_1d),
        "market_signal_5d":  signal(fii_5d, dii_5d),
        "market_signal_10d": signal(fii_10d, dii_10d),
        "interpretation": _interpret_flows(fii_5d, dii_5d, bulk),
        "daily_data":     data[:5],   # last 5 days for context
    }
    out = json.dumps(result, default=str)
    _cache_set(cache_key, out)
    return out


def _interpret_flows(fii_5d, dii_5d, bulk: dict) -> str:
    """Generate plain-English interpretation of institutional flows."""
    lines = []
    if fii_5d is None:
        return "FII/DII data unavailable — check NSE website during market hours."

    if fii_5d > 1000:
        lines.append(f"FII have been aggressive NET BUYERS of ₹{fii_5d:,.0f}Cr over 5 days — strong foreign conviction in Indian markets.")
    elif fii_5d > 0:
        lines.append(f"FII are NET BUYERS of ₹{fii_5d:,.0f}Cr over 5 days — mild positive signal.")
    elif fii_5d > -1000:
        lines.append(f"FII are NET SELLERS of ₹{abs(fii_5d):,.0f}Cr over 5 days — mild caution.")
    else:
        lines.append(f"FII are HEAVY NET SELLERS of ₹{abs(fii_5d):,.0f}Cr over 5 days — risk-off signal for Indian markets. INR under pressure.")

    if dii_5d and dii_5d > 0 and fii_5d < 0:
        lines.append(f"DII (domestic MFs + insurance) are absorbing FII selling with ₹{dii_5d:,.0f}Cr net buy — acting as market stabiliser. Good support signal.")
    elif dii_5d and dii_5d > 0:
        lines.append(f"DII also buying ₹{dii_5d:,.0f}Cr — domestic funds aligned with foreign buyers.")
    elif dii_5d and dii_5d < 0 and fii_5d < 0:
        lines.append(f"Both FII and DII selling — rare double-exit signal. Exercise extra caution.")

    bulk_signal = bulk.get("aggregate_signal","")
    if "ACCUMULATION" in bulk_signal:
        lines.append(f"Bulk deal analysis confirms institutional accumulation — {bulk.get('buy_value_cr',0):,.0f}Cr in bulk buys this week.")
    elif "DISTRIBUTION" in bulk_signal:
        lines.append(f"Bulk deal analysis shows distribution — {bulk.get('sell_value_cr',0):,.0f}Cr in bulk sells this week.")

    return " ".join(lines)


# ── Tool definitions for OpenAI function calling ──────────────

FII_DII_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "get_fii_dii_daily",
            "description": (
                "Get FII and DII net buy/sell activity for the last N trading days. "
                "Shows foreign institutional investor (FII/FPI) and domestic institutional "
                "investor (DII = mutual funds + insurance + banks) flows in ₹ crore. "
                "Critical for understanding market direction."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "description": "Number of trading days to fetch (default 10, max 30)",
                        "default": 10,
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_bulk_deals",
            "description": (
                "Get bulk deal data from NSE — large single transactions >0.5% of equity. "
                "Identifies which institutions are buying or selling specific stocks. "
                "FII bulk buys on a stock = very strong accumulation signal."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "NSE ticker to filter (e.g. RELIANCE). Leave empty for all stocks.",
                    },
                    "days": {
                        "type": "integer",
                        "description": "Look back days (default 30)",
                        "default": 30,
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_block_deals",
            "description": (
                "Get block deal data from NSE — negotiated trades >500k shares or >₹5cr. "
                "More secretive than bulk deals. Signals serious institutional positioning."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "NSE ticker to filter, or empty for all.",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_stock_shareholding",
            "description": (
                "Get quarterly shareholding pattern for an NSE stock. "
                "Shows FII %, DII %, Promoter %, Promoter Pledge % trends over 6 quarters. "
                "Rising FII % = institutional confidence. Rising pledge = red flag."
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
            "name": "get_fii_dii_summary",
            "description": (
                "Get comprehensive market-level FII/DII summary including net flows "
                "over 1d/5d/10d, bulk deal aggregate signal, and plain-English interpretation. "
                "Use this first to understand overall institutional market sentiment."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
]

FII_DII_TOOL_MAP = {
    "get_fii_dii_daily":      get_fii_dii_daily,
    "get_bulk_deals":         get_bulk_deals,
    "get_block_deals":        get_block_deals,
    "get_stock_shareholding": get_stock_shareholding,
    "get_fii_dii_summary":    get_fii_dii_summary,
}
