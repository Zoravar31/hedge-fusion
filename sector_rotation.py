"""
HedgeFusion Sector Rotation Tracker
=====================================
Tracks which sectors are gaining momentum and where FII/DII money
is flowing in Indian markets. Helps identify:

  - Sectors in accumulation (smart money buying)
  - Sectors in distribution (smart money selling)
  - Relative strength of each sector vs Nifty 50

Uses NSE sector indices via yfinance and AI analysis.

Sector indices tracked (NSE):
  ^CNXAUTO, ^CNXBANK, ^CNXFMCG, ^CNXIT, ^CNXMETAL,
  ^CNXPHARMA, ^CNXREALTY, ^CNXENERGY, ^CNXINFRA, ^CNXMEDIA

Usage:
    python sector_rotation.py              # full sector analysis
    python sector_rotation.py --top 3     # top 3 sectors only
    python sector_rotation.py --ai        # add AI narrative
"""

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import yfinance as yf
from dotenv import load_dotenv
from loguru import logger
from openai import OpenAI

load_dotenv(Path(__file__).parent / ".env")

client     = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
MODEL      = os.getenv("MODEL_NAME", "gpt-4o-mini")
OUTPUT_DIR = Path(__file__).parent / "outputs"
OUTPUT_DIR.mkdir(exist_ok=True)

# NSE sector indices — yfinance symbols
SECTOR_INDICES = {
    "Banking":       "^CNXBANK",
    "IT":            "^CNXIT",
    "Auto":          "^CNXAUTO",
    "FMCG":          "^CNXFMCG",
    "Pharma":        "^CNXPHARMA",
    "Metal":         "^CNXMETAL",
    "Energy":        "^CNXENERGY",
    "Realty":        "^CNXREALTY",
    "Infra":         "^CNXINFRA",
    "Media":         "^CNXMEDIA",
    "MidCap":        "NIFTY_MID_SELECT.NS",
    "SmallCap":      "^CNXSC",
}

# Representative stocks for each sector (used when index data is unavailable)
SECTOR_PROXIES = {
    "Banking":  ["HDFCBANK", "ICICIBANK", "SBIN", "AXISBANK", "KOTAKBANK"],
    "IT":       ["TCS", "INFY", "WIPRO", "HCLTECH", "LTIM"],
    "Auto":     ["MARUTI", "M&M", "TATAMOTORS", "BAJAJ-AUTO", "HEROMOTOCO"],
    "FMCG":     ["HINDUNILVR", "NESTLEIND", "BRITANNIA", "DABUR", "MARICO"],
    "Pharma":   ["SUNPHARMA", "CIPLA", "DRREDDY", "DIVISLAB", "TORNTPHARM"],
    "Metal":    ["TATASTEEL", "JSWSTEEL", "HINDALCO", "VEDL", "HINDZINC"],
    "Energy":   ["RELIANCE", "ONGC", "NTPC", "POWERGRID", "ADANIPOWER"],
    "Defence":  ["HAL", "BEL", "MAZDOCK", "BEML", "COCHINSHIP"],
    "CapGoods":  ["LT", "SIEMENS", "ABB", "BHEL", "THERMAX"],
    "Telecom":  ["BHARTIARTL", "IDEA", "HFCL", "TEJASNET"],
}


# ── Data fetcher ──────────────────────────────────────────────

def fetch_sector_performance(sector: str, symbol: str) -> dict:
    """Fetch sector index performance over multiple timeframes."""
    try:
        ticker = yf.Ticker(symbol)
        hist   = ticker.history(period="1y", interval="1d")

        if hist is None or hist.empty or len(hist) < 20:
            return {"sector": sector, "symbol": symbol, "error": "no data"}

        closes = hist["Close"].dropna()

        def ret(n): 
            if len(closes) >= n:
                return round((closes.iloc[-1] - closes.iloc[-n]) / closes.iloc[-n] * 100, 2)
            return None

        # Returns over multiple periods
        ret_1w  = ret(5)
        ret_1m  = ret(21)
        ret_3m  = ret(63)
        ret_6m  = ret(126)
        ret_1y  = ret(252)

        # Momentum score: recent returns weighted more
        weights = [0.3, 0.25, 0.2, 0.15, 0.1]
        rets    = [r for r in [ret_1w, ret_1m, ret_3m, ret_6m, ret_1y] if r is not None]
        weights = weights[:len(rets)]
        w_sum   = sum(weights)
        momentum = round(sum(r*w/w_sum for r,w in zip(rets, weights)), 2) if rets else 0

        # Relative strength vs Nifty
        try:
            nifty = yf.Ticker("^NSEI").history(period="3mo", interval="1d")["Close"].dropna()
            nifty_3m = round((nifty.iloc[-1]-nifty.iloc[0])/nifty.iloc[0]*100, 2)
            rs_3m = round((ret_3m or 0) - nifty_3m, 2)
        except Exception:
            rs_3m = None

        # Trend: 20d EMA vs 50d EMA
        if len(closes) >= 50:
            ema20 = closes.ewm(span=20).mean().iloc[-1]
            ema50 = closes.ewm(span=50).mean().iloc[-1]
            trend = "uptrend" if ema20 > ema50 else "downtrend"
        else:
            trend = "unknown"

        # Volume trend (if available)
        vol_trend = "unknown"
        if "Volume" in hist.columns:
            vols = hist["Volume"].dropna()
            if len(vols) >= 20:
                avg_vol_10d = vols.iloc[-10:].mean()
                avg_vol_30d = vols.iloc[-30:].mean()
                vol_trend = "increasing" if avg_vol_10d > avg_vol_30d * 1.1 else \
                            "decreasing" if avg_vol_10d < avg_vol_30d * 0.9 else "stable"

        return {
            "sector":      sector,
            "symbol":      symbol,
            "ret_1w_pct":  ret_1w,
            "ret_1m_pct":  ret_1m,
            "ret_3m_pct":  ret_3m,
            "ret_6m_pct":  ret_6m,
            "ret_1y_pct":  ret_1y,
            "momentum_score": momentum,
            "rs_vs_nifty_3m": rs_3m,
            "trend":       trend,
            "vol_trend":   vol_trend,
            "current":     round(float(closes.iloc[-1]), 2),
        }

    except Exception as e:
        logger.warning("Sector fetch failed {} ({}): {}", sector, symbol, e)
        return {"sector": sector, "symbol": symbol, "error": str(e)}


# ── AI narrative ──────────────────────────────────────────────

ROTATION_AI_PROMPT = """
You are a macro equity strategist at an Indian fund.

You have sector performance data for NSE indices over the last week, month, quarter, and year.
Current date: {date}.

Based on this data, provide:

1. ROTATION NARRATIVE (2-3 sentences)
   Which sectors are in favour / out of favour right now? What is the macro story?

2. TOP 3 SECTORS TO OVERWEIGHT
   With specific reasons tied to India macro: RBI, capex cycle, PLI, consumption, exports.

3. TOP 3 SECTORS TO UNDERWEIGHT
   With reasoning.

4. FII FLOW READ
   Based on sector performance, where does institutional money appear to be going?

5. TRADE IDEA
   One specific actionable idea — which sector and why to enter now.

Output as JSON:
{{
  "rotation_narrative": "string",
  "overweight": [{{"sector":"","reason":""}}],
  "underweight": [{{"sector":"","reason":""}}],
  "fii_read": "string",
  "trade_idea": {{"sector":"","rationale":"","representative_stocks":[]}}
}}
"""


def get_ai_rotation_narrative(sector_data: list) -> dict:
    """Get AI interpretation of sector rotation data."""
    try:
        compact = [{
            "sector":     d["sector"],
            "ret_1w":     d.get("ret_1w_pct"),
            "ret_1m":     d.get("ret_1m_pct"),
            "ret_3m":     d.get("ret_3m_pct"),
            "momentum":   d.get("momentum_score"),
            "rs_nifty":   d.get("rs_vs_nifty_3m"),
            "trend":      d.get("trend"),
            "vol_trend":  d.get("vol_trend"),
        } for d in sector_data if "error" not in d]

        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": "You are a macro equity strategist specialising in Indian markets."},
                {"role": "user", "content": (
                    ROTATION_AI_PROMPT.format(date=datetime.now().strftime("%B %Y")) +
                    f"\n\nSector data:\n{json.dumps(compact, indent=2)}"
                )},
            ],
            temperature=0.3,
        )
        raw = response.choices[0].message.content or "{}"
        import re
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            parsed = json.loads(match.group())
            if isinstance(parsed, dict):
                return parsed
    except Exception as e:
        logger.error("AI rotation analysis failed: {}", e)
    return {}


# ── Report builder ────────────────────────────────────────────

def build_rotation_html(sector_data: list, ai: dict, timestamp: str) -> str:
    valid = [d for d in sector_data if "error" not in d]
    valid.sort(key=lambda x: x.get("momentum_score", -999), reverse=True)

    def ret_color(v):
        if v is None: return "#64748b"
        return "#22c55e" if v > 3 else "#86efac" if v > 0 else "#ef4444"

    def trend_badge(t):
        if t == "uptrend":   return "<span style='color:#22c55e'>↗ uptrend</span>"
        if t == "downtrend": return "<span style='color:#ef4444'>↘ downtrend</span>"
        return "<span style='color:#64748b'>→ sideways</span>"

    rows = ""
    for d in valid:
        mom   = d.get("momentum_score", 0)
        bar_w = max(0, min(100, (mom + 30) / 60 * 100))
        bar_c = "#22c55e" if mom > 5 else "#f59e0b" if mom > 0 else "#ef4444"
        rs    = d.get("rs_vs_nifty_3m")
        rows += f"""<tr>
          <td style="font-weight:700">{d['sector']}</td>
          <td style="color:{ret_color(d.get('ret_1w_pct'))}">{(d.get('ret_1w_pct') or 0):+.1f}%</td>
          <td style="color:{ret_color(d.get('ret_1m_pct'))}">{(d.get('ret_1m_pct') or 0):+.1f}%</td>
          <td style="color:{ret_color(d.get('ret_3m_pct'))}">{(d.get('ret_3m_pct') or 0):+.1f}%</td>
          <td style="color:{ret_color(d.get('ret_1y_pct'))}">{(d.get('ret_1y_pct') or 0):+.1f}%</td>
          <td style="color:{'#22c55e' if (rs or 0)>0 else '#ef4444'}">{(rs or 0):+.1f}%</td>
          <td>
            <div style="display:flex;align-items:center;gap:6px">
              <div style="width:60px;height:5px;background:#1e293b;border-radius:2px">
                <div style="width:{bar_w:.0f}%;height:100%;background:{bar_c};border-radius:2px"></div>
              </div>
              <span style="font-size:12px;color:{bar_c}">{mom:+.1f}</span>
            </div>
          </td>
          <td>{trend_badge(d.get('trend',''))}</td>
        </tr>"""

    ai_html = ""
    if ai:
        ow_items = "".join(
            f"<li><strong style='color:#22c55e'>{o['sector']}</strong>: {o.get('reason','')}</li>"
            for o in ai.get("overweight", [])
        )
        uw_items = "".join(
            f"<li><strong style='color:#ef4444'>{u['sector']}</strong>: {u.get('reason','')}</li>"
            for u in ai.get("underweight", [])
        )
        ti = ai.get("trade_idea", {})
        stocks_str = ", ".join(ti.get("representative_stocks", []))

        ai_html = f"""
        <div style="background:#0f172a;border:1px solid #1e293b;border-radius:10px;
                    padding:22px;margin-bottom:16px">
          <h3 style="color:#f59e0b;margin-bottom:12px">🧠 AI Macro Rotation Narrative</h3>
          <p style="color:#94a3b8;line-height:1.7;margin-bottom:16px">
            {ai.get('rotation_narrative','')}</p>

          <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px">
            <div>
              <div style="font-size:12px;color:#22c55e;font-weight:700;margin-bottom:8px">
                ↑ OVERWEIGHT</div>
              <ul style="color:#94a3b8;font-size:13px;padding-left:16px;line-height:1.8">
                {ow_items}</ul>
            </div>
            <div>
              <div style="font-size:12px;color:#ef4444;font-weight:700;margin-bottom:8px">
                ↓ UNDERWEIGHT</div>
              <ul style="color:#94a3b8;font-size:13px;padding-left:16px;line-height:1.8">
                {uw_items}</ul>
            </div>
          </div>

          <div style="background:#1e293b;border-radius:8px;padding:14px;margin-bottom:12px">
            <div style="font-size:11px;color:#64748b;margin-bottom:4px">FII FLOW READ</div>
            <div style="font-size:13px;color:#cbd5e1">{ai.get('fii_read','')}</div>
          </div>

          {"<div style='background:#052e16;border:1px solid #166534;border-radius:8px;padding:14px'><div style='font-size:11px;color:#16a34a;margin-bottom:4px'>💡 TRADE IDEA — " + ti.get('sector','') + "</div><div style='font-size:13px;color:#86efac'>" + ti.get('rationale','') + "</div>" + ("<div style='font-size:12px;color:#64748b;margin-top:6px'>Stocks: " + stocks_str + "</div>" if stocks_str else "") + "</div>" if ti else ""}
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>HedgeFusion Sector Rotation — {timestamp}</title>
<style>
  body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
       background:#050d1a;color:#e2e8f0;margin:0;padding:20px;
       -webkit-font-smoothing:antialiased}}
  .wrap{{max-width:1060px;margin:0 auto}}
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
  tr:hover td{{background:#0a1628}}
  .disc{{background:#1e1a0a;border:1px solid #78350f;border-radius:8px;
         padding:14px 18px;font-size:12px;color:#92400e;margin-top:24px}}
</style>
</head>
<body>
<div class="wrap">
  <h1>🔄 HedgeFusion Sector Rotation</h1>
  <div class="meta">{timestamp}</div>

  {ai_html}

  <h2>📊 Sector Performance Table</h2>
  <table>
    <thead><tr>
      <th>Sector</th><th>1 Week</th><th>1 Month</th><th>3 Month</th><th>1 Year</th>
      <th>vs Nifty (3M)</th><th>Momentum</th><th>Trend</th>
    </tr></thead>
    <tbody>{rows}</tbody>
  </table>

  <div class="disc">
    ⚠️ Sector rotation analysis is for research only. Past sector performance
    does not predict future returns. Not SEBI-registered advice.
  </div>
</div>
</body>
</html>"""


# ── Main ──────────────────────────────────────────────────────

def run_sector_rotation(use_ai: bool = True, top_n: int | None = None) -> list:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M IST")
    ts_file   = datetime.now().strftime("%Y%m%d_%H%M")

    print(f"\n{'━'*55}")
    print(f"  HEDGEFUSION SECTOR ROTATION TRACKER")
    print(f"  Fetching {len(SECTOR_INDICES)} NSE sector indices...")
    print(f"{'━'*55}\n")

    results = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = {ex.submit(fetch_sector_performance, s, sym): s
                   for s, sym in SECTOR_INDICES.items()}
        for future in as_completed(futures):
            sector = futures[future]
            try:
                result = future.result()
                results.append(result)
                if "error" not in result:
                    mom = result.get("momentum_score", 0)
                    r3m = result.get("ret_3m_pct", 0)
                    print(f"  ✓ {sector:<12} 3M: {(r3m or 0):+.1f}%  "
                          f"Momentum: {mom:+.1f}")
            except Exception as e:
                logger.error("Sector fetch failed {}: {}", sector, e)

    results.sort(key=lambda x: x.get("momentum_score", -999), reverse=True)

    if top_n:
        results = results[:top_n]

    print(f"\n  Top sectors by momentum:")
    for r in results[:5]:
        if "error" not in r:
            print(f"    {r['sector']:<14} momentum: {r.get('momentum_score',0):+.1f}")

    ai_data = {}
    if use_ai and os.getenv("OPENAI_API_KEY","").startswith("sk-"):
        print(f"\n  Running AI narrative...")
        ai_data = get_ai_rotation_narrative(results)
        if ai_data.get("rotation_narrative"):
            print(f"  AI: {ai_data['rotation_narrative'][:80]}...")

    html      = build_rotation_html(results, ai_data, timestamp)
    html_path = OUTPUT_DIR / f"sector_rotation_{ts_file}.html"
    html_path.write_text(html, encoding="utf-8")
    print(f"\n✅ Report: {html_path}\n")

    return results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="HedgeFusion Sector Rotation")
    parser.add_argument("--top",    type=int, help="Show top N sectors only")
    parser.add_argument("--no-ai",  action="store_true", help="Skip AI narrative")
    args = parser.parse_args()
    run_sector_rotation(use_ai=not args.no_ai, top_n=args.top)
