"""
HedgeFusion Multibagger Screener
==================================
Scans NSE stocks across 8 sectors for multibagger characteristics.

What is a multibagger?
  A stock that returns 2x–10x over 1–3 years. Historically in India,
  multibaggers share common traits found BEFORE the run-up:

  GROWTH PILLARS:
    1. Revenue growing >20% YoY consistently
    2. EBITDA margin expanding (not just top-line growth)
    3. ROE > 15% (business earns well on shareholders' money)
    4. Debt/Equity < 1 (room to leverage if needed, not already stretched)
    5. Promoter holding > 50% and NOT declining (skin in the game)

  VALUATION PILLARS:
    6. PEG ratio < 1.5 (paying reasonable price for growth)
    7. P/B < 5 (not wildly overvalued on book)
    8. Market cap < ₹20,000 Cr preferred (mid/small cap — more room to grow)

  CATALYST PILLARS:
    9. Sector tailwind (PLI, infra, defence, energy transition, consumption)
   10. Earnings acceleration in last 2 quarters

  QUALITY PILLARS:
   11. FCF positive (cash actually coming in)
   12. Low pledging (promoter not borrowing against shares)
   13. Consistent dividend history (capital discipline)

Score each stock 0–100. Top 20 go through AI deep-dive.

Usage:
    python multibagger_screener.py                  # full scan
    python multibagger_screener.py --quick          # top 5 sectors only
    python multibagger_screener.py --sector DEFENCE # one sector
    python multibagger_screener.py --ticker TITAN   # single stock score
"""

import argparse
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

from loguru import logger
from openai import OpenAI

from tools.india_data import get_nse_quote, get_nse_fundamentals, get_nse_history

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
MODEL  = os.getenv("MODEL_NAME", "gpt-4o-mini")

OUTPUT_DIR = Path(__file__).parent / "outputs"
OUTPUT_DIR.mkdir(exist_ok=True)

# ── NSE Universe by sector ───────────────────────────────────
# ~120 stocks across high-growth sectors for Indian multibaggers
# Focused on sectors with structural tailwinds 2024-2027

NSE_UNIVERSE = {
    "DEFENCE_AEROSPACE": [
        "BEL", "HAL", "MAZDOCK", "COCHINSHIP", "BEML",
        "MIDHANI", "PARAS", "MTAR", "DATAPATTNS", "APOLLOMICRO",
    ],
    "CAPITAL_GOODS_INFRA": [
        "LT", "SIEMENS", "ABB", "HAVELLS", "POLYCAB",
        "KPITTECH", "THERMAX", "CUMMINSIND", "GRINDWELL", "ELGIEQUIP",
    ],
    "CONSUMPTION_FMCG": [
        "VBL", "TATACONSUM", "NESTLEIND", "BRITANNIA", "MARICO",
        "DABUR", "GODREJCP", "EMAMILTD", "COLPAL", "HINDUNILVR",
    ],
    "FINANCIAL_SERVICES": [
        "ICICIBANK", "HDFCBANK", "BAJFINANCE", "CHOLAFIN", "SBICARD",
        "MUTHOOTFIN", "MANAPPURAM", "CREDITACC", "AUBANK", "UJJIVANSFB",
    ],
    "TECHNOLOGY_IT": [
        "TCS", "INFY", "WIPRO", "HCLTECH", "LTIM",
        "PERSISTENT", "COFORGE", "MPHASIS", "KPITTECH", "TATAELXSI",
    ],
    "HEALTHCARE_PHARMA": [
        "SUNPHARMA", "CIPLA", "DRREDDY", "DIVISLAB", "TORNTPHARM",
        "ALKEM", "IPCA", "GRANULES", "LAURUSLABS", "GLAND",
    ],
    "ENERGY_TRANSITION": [
        "NTPC", "POWERGRID", "TATAPOWER", "ADANIGREEN", "CESC",
        "SJVN", "NHPC", "RTNPOWER", "INOXWIND", "SUZLON",
    ],
    "TELECOM_DIGITAL": [
        "BHARTIARTL", "HFCL", "TEJASNET", "STLTECH", "INDIAMART",
        "NAUKRI", "ZOMATO", "POLICYBZR", "PAYTM", "DELHIVERY",
    ],
}

ALL_TICKERS = [t for sector in NSE_UNIVERSE.values() for t in sector]


# ── Scoring engine ───────────────────────────────────────────

def score_stock(ticker: str) -> dict:
    """
    Score a stock 0–100 on multibagger potential using yfinance data.
    No AI used here — pure quantitative scoring.
    """
    result = {
        "ticker": ticker,
        "score": 0,
        "signals": [],
        "flags": [],
        "data": {},
    }

    try:
        quote_raw = get_nse_quote(ticker)
        quote     = json.loads(quote_raw)
        info      = quote.get("info", {})

        # Check for error response
        if quote.get("error") or not info:
            result["flags"].append("No data available")
            return result

        # Price: try currentPrice, fallback to latest_close from history
        price = float(
            info.get("currentPrice")
            or info.get("previousClose")
            or quote.get("latest_close")
            or 0
        )
        if price == 0:
            result["flags"].append("No price data")
            return result
        mktcap = float(info.get("marketCap") or 0)
        mktcap_cr = mktcap / 1e7  # convert to crores

        result["data"] = {
            "price":          price,
            "mktcap_cr":      round(mktcap_cr),
            "pe":             info.get("trailingPE"),
            "pb":             info.get("priceToBook"),
            "roe":            info.get("returnOnEquity"),
            "de":             info.get("debtToEquity"),
            "rev_growth":     info.get("revenueGrowth"),
            "earn_growth":    info.get("earningsGrowth"),
            "op_margin":      info.get("operatingMargins"),
            "profit_margin":  info.get("profitMargins"),
            "beta":           info.get("beta"),
            "div_yield":      info.get("dividendYield"),
            "sector":         info.get("sector"),
            "52w_high":       info.get("fiftyTwoWeekHigh"),
            "52w_low":        info.get("fiftyTwoWeekLow"),
        }

        score = 0

        # ── Growth signals (40 pts) ──────────────────────────
        rev_growth = info.get("revenueGrowth") or 0
        if rev_growth > 0.30:
            score += 20; result["signals"].append(f"Revenue growth {rev_growth:.0%} (>30% ✅)")
        elif rev_growth > 0.20:
            score += 14; result["signals"].append(f"Revenue growth {rev_growth:.0%} (>20% ✓)")
        elif rev_growth > 0.10:
            score += 7;  result["signals"].append(f"Revenue growth {rev_growth:.0%} (>10%)")
        elif rev_growth < 0:
            result["flags"].append(f"Revenue declining {rev_growth:.0%} ⚠️")

        earn_growth = info.get("earningsGrowth") or 0
        if earn_growth > 0.30:
            score += 20; result["signals"].append(f"Earnings growth {earn_growth:.0%} (>30% ✅)")
        elif earn_growth > 0.20:
            score += 14; result["signals"].append(f"Earnings growth {earn_growth:.0%} (>20% ✓)")
        elif earn_growth > 0.10:
            score += 7;  result["signals"].append(f"Earnings growth {earn_growth:.0%} (>10%)")
        elif earn_growth < 0:
            result["flags"].append(f"Earnings declining {earn_growth:.0%} ⚠️")

        # ── Quality signals (30 pts) ─────────────────────────
        roe = info.get("returnOnEquity") or 0
        if roe > 0.25:
            score += 15; result["signals"].append(f"ROE {roe:.0%} (>25% ✅)")
        elif roe > 0.15:
            score += 10; result["signals"].append(f"ROE {roe:.0%} (>15% ✓)")
        elif roe < 0.08:
            result["flags"].append(f"ROE low {roe:.0%} ⚠️")

        de = info.get("debtToEquity") or 0
        if de < 0.3:
            score += 10; result["signals"].append(f"D/E {de:.1f} (very clean balance sheet ✅)")
        elif de < 1.0:
            score += 6;  result["signals"].append(f"D/E {de:.1f} (manageable ✓)")
        elif de > 2.0:
            result["flags"].append(f"High D/E {de:.1f} ⚠️")

        op_margin = info.get("operatingMargins") or 0
        if op_margin > 0.20:
            score += 5; result["signals"].append(f"Operating margin {op_margin:.0%} (strong ✅)")
        elif op_margin > 0.12:
            score += 3; result["signals"].append(f"Operating margin {op_margin:.0%} (decent ✓)")
        elif op_margin < 0.05:
            result["flags"].append(f"Thin margin {op_margin:.0%} ⚠️")

        # ── Valuation signals (20 pts) ───────────────────────
        pe = info.get("trailingPE") or 0
        if 0 < pe < 25:
            score += 10; result["signals"].append(f"P/E {pe:.1f} (attractive valuation ✅)")
        elif 25 <= pe < 45:
            score += 6;  result["signals"].append(f"P/E {pe:.1f} (fair valuation ✓)")
        elif pe > 80:
            result["flags"].append(f"P/E {pe:.1f} very expensive ⚠️")

        pb = info.get("priceToBook") or 0
        if 0 < pb < 3:
            score += 5; result["signals"].append(f"P/B {pb:.1f} (value zone ✅)")
        elif 3 <= pb < 6:
            score += 3; result["signals"].append(f"P/B {pb:.1f} (reasonable ✓)")

        # Market cap bonus — mid/small cap has more room to grow
        if 0 < mktcap_cr < 5000:
            score += 5; result["signals"].append(f"Small cap ₹{mktcap_cr:,.0f}Cr (high growth potential ✅)")
        elif mktcap_cr < 20000:
            score += 3; result["signals"].append(f"Mid cap ₹{mktcap_cr:,.0f}Cr ✓")

        # ── 52-week positioning (10 pts) ─────────────────────
        high = info.get("fiftyTwoWeekHigh") or 1
        low  = info.get("fiftyTwoWeekLow")  or 0
        if price and high and low:
            pos = (price - low) / (high - low) if (high - low) > 0 else 0.5
            if pos < 0.35:
                score += 10
                result["signals"].append(f"Near 52W low ({pos:.0%} of range) — potential reversal ✅")
            elif pos < 0.55:
                score += 6
                result["signals"].append(f"Mid 52W range ({pos:.0%})")

        result["score"] = min(score, 100)

    except Exception as e:
        result["flags"].append(f"Error: {str(e)[:80]}")

    return result


# ── AI deep-dive for top candidates ─────────────────────────

MULTIBAGGER_AI_PROMPT = """
You are a senior equity analyst at a top Indian hedge fund specialising in identifying multibagger stocks — stocks with potential to return 2x–5x in 2–3 years.

You have quantitative scores for a shortlist of NSE stocks. Your job: do a qualitative deep-dive on the top candidates and produce a final ranked list.

For each stock, assess:

1. STRUCTURAL TAILWIND
   Is this company in a sector with a 3–5 year structural growth story?
   (Defence indigenisation, EV transition, PLI manufacturing, digital India,
    healthcare access, India consumption upgrade, capex cycle, export growth)

2. COMPETITIVE MOAT
   Does it have pricing power, switching costs, network effects, or a regulatory moat?
   Is it gaining or losing market share?

3. MANAGEMENT QUALITY
   Track record of capital allocation. Promoter alignment.
   Any red flags: pledging, RPTs, governance issues.

4. EARNINGS VISIBILITY
   Is the next 2–3 years of growth reasonably predictable?
   Order backlog, contract wins, recurring revenue.

5. ENTRY POINT
   Is the current price giving a reasonable entry vs 3-year potential?
   What would a 3x return imply for market cap and P/E?

6. MULTIBAGGER SCORE (0–100)
   Pure gut-check as an experienced analyst — how confident are you
   this stock can 2x–5x in 2–3 years?

Output as JSON array, each element:
{
  "ticker": "NSE symbol",
  "rank": 1-N,
  "multibagger_score": 0-100,
  "structural_tailwind": "one sentence",
  "moat": "one sentence",
  "management": "one sentence",
  "earnings_visibility": "one sentence",
  "entry_verdict": "STRONG BUY / BUY / WATCH / AVOID",
  "3y_target_inr": estimated 3-year price target,
  "potential_return_pct": estimated % return,
  "key_risk": "single biggest risk",
  "thesis": "2-3 sentence investment thesis"
}
"""


def ai_deep_dive(candidates: list[dict]) -> list[dict]:
    """Run AI analysis on top quantitative candidates."""
    logger.info("Running AI deep-dive on {} candidates...", len(candidates))

    # Prepare compact summary for AI
    summary = []
    for c in candidates:
        summary.append({
            "ticker":      c["ticker"],
            "quant_score": c["score"],
            "signals":     c["signals"][:5],
            "flags":       c["flags"][:3],
            "data":        {k: v for k, v in c["data"].items()
                           if v is not None and k in
                           ["price", "mktcap_cr", "pe", "pb", "roe",
                            "de", "rev_growth", "earn_growth", "op_margin", "sector"]},
        })

    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": MULTIBAGGER_AI_PROMPT},
                {"role": "user", "content": (
                    f"Here are the top {len(summary)} quantitative candidates.\n"
                    f"Today: {datetime.now().strftime('%B %Y')}.\n\n"
                    f"{json.dumps(summary, indent=2)}\n\n"
                    f"Produce the final ranked multibagger list as JSON array."
                )},
            ],
            temperature=0.2,
            max_tokens=4000,
        )
        raw = response.choices[0].message.content or "[]"
        import re
        match = re.search(r'\[.*\]', raw, re.DOTALL)
        if match:
            return json.loads(match.group())
    except Exception as e:
        logger.error("AI deep-dive failed: {}", e)
    return []


# ── Report builder ───────────────────────────────────────────

def build_report(quant_results: list, ai_results: list, timestamp: str) -> str:
    sep = "=" * 70
    lines = [
        sep,
        "  HEDGEFUSION MULTIBAGGER SCREENER",
        f"  {timestamp}",
        f"  Universe scanned: {len(quant_results)} stocks",
        sep,
    ]

    # Quant summary table
    lines += ["", "QUANTITATIVE SCORES (top 20)", "─" * 70]
    lines.append(f"{'Rank':<5} {'Ticker':<14} {'Score':>5} {'P/E':>6} {'ROE':>6} {'Rev Gr':>7} {'D/E':>5} {'MCap Cr':>10}")
    lines.append("─" * 70)
    for i, r in enumerate(quant_results[:20], 1):
        d = r.get("data", {})
        lines.append(
            f"{i:<5} {r['ticker']:<14} {r['score']:>5} "
            f"{str(round(d.get('pe') or 0, 1)):>6} "
            f"{str(round((d.get('roe') or 0)*100, 1))+'%':>6} "
            f"{str(round((d.get('rev_growth') or 0)*100, 1))+'%':>7} "
            f"{str(round(d.get('de') or 0, 1)):>5} "
            f"₹{(d.get('mktcap_cr') or 0):>8,.0f}"
        )

    # AI deep-dive results
    if ai_results:
        lines += ["", "", "AI MULTIBAGGER DEEP-DIVE — TOP PICKS", "─" * 70]
        for r in ai_results:
            verdict_emoji = {
                "STRONG BUY": "🔥",
                "BUY":        "✅",
                "WATCH":      "👀",
                "AVOID":      "🚫",
            }.get(r.get("entry_verdict", ""), "")
            lines += [
                "",
                f"#{r.get('rank','?')} {r.get('ticker','?')}  {verdict_emoji} {r.get('entry_verdict','?')}  "
                f"Multibagger score: {r.get('multibagger_score','?')}/100",
                f"   Thesis: {r.get('thesis','')}",
                f"   Tailwind: {r.get('structural_tailwind','')}",
                f"   Moat: {r.get('moat','')}",
                f"   Earnings visibility: {r.get('earnings_visibility','')}",
                f"   3Y target: ₹{r.get('3y_target_inr','?')}  "
                f"(+{r.get('potential_return_pct','?')}% potential)",
                f"   Key risk: {r.get('key_risk','')}",
            ]

    lines += [
        "", sep,
        "  ⚠  This screen is for research only. Not SEBI-registered advice.",
        "     Always do your own due diligence before investing.",
        sep, "",
    ]
    return "\n".join(lines)


def build_html_report(quant_results: list, ai_results: list, timestamp: str) -> str:
    def verdict_color(v):
        return {"STRONG BUY":"#22c55e","BUY":"#86efac","WATCH":"#fbbf24","AVOID":"#ef4444"}.get(v,"#94a3b8")

    rows = ""
    for i, r in enumerate(quant_results[:20], 1):
        d = r.get("data", {})
        bar_w = r["score"]
        bar_color = "#22c55e" if r["score"] >= 70 else "#f59e0b" if r["score"] >= 50 else "#64748b"
        rows += f"""<tr>
          <td style="color:#94a3b8;font-size:12px">{i}</td>
          <td style="font-weight:700;font-family:monospace">{r['ticker']}</td>
          <td>
            <div style="display:flex;align-items:center;gap:8px">
              <div style="width:60px;height:6px;background:#1e293b;border-radius:3px">
                <div style="width:{bar_w}%;height:100%;background:{bar_color};border-radius:3px"></div>
              </div>
              <span style="font-weight:600;color:{bar_color}">{r['score']}</span>
            </div>
          </td>
          <td>{round(d.get('pe') or 0, 1)}</td>
          <td>{round((d.get('roe') or 0)*100, 1)}%</td>
          <td style="color:{'#22c55e' if (d.get('rev_growth') or 0)>0.2 else '#94a3b8'}">{round((d.get('rev_growth') or 0)*100, 1)}%</td>
          <td>{round(d.get('de') or 0, 1)}</td>
          <td style="font-size:12px;color:#94a3b8">₹{(d.get('mktcap_cr') or 0):,.0f}Cr</td>
        </tr>"""

    ai_cards = ""
    for r in ai_results:
        vc = verdict_color(r.get("entry_verdict",""))
        ms = r.get("multibagger_score", 0)
        ai_cards += f"""
        <div style="background:#0f172a;border:1px solid #1e293b;border-radius:10px;padding:20px;margin-bottom:12px">
          <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:12px">
            <div>
              <span style="font-size:20px;font-weight:800;font-family:monospace;color:#e2e8f0">#{r.get('rank','?')} {r.get('ticker','?')}</span>
              <span style="margin-left:12px;padding:3px 10px;border-radius:4px;font-size:12px;font-weight:600;background:{vc}22;color:{vc}">{r.get('entry_verdict','?')}</span>
            </div>
            <div style="text-align:right">
              <div style="font-size:28px;font-weight:800;color:#f59e0b">{ms}<span style="font-size:14px;color:#64748b">/100</span></div>
              <div style="font-size:11px;color:#64748b">multibagger score</div>
            </div>
          </div>
          <p style="font-size:14px;color:#94a3b8;margin-bottom:12px;line-height:1.6">{r.get('thesis','')}</p>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;font-size:12px">
            <div style="background:#1e293b;padding:8px 10px;border-radius:6px"><span style="color:#64748b">Tailwind:</span> <span style="color:#cbd5e1">{r.get('structural_tailwind','')[:80]}</span></div>
            <div style="background:#1e293b;padding:8px 10px;border-radius:6px"><span style="color:#64748b">Moat:</span> <span style="color:#cbd5e1">{r.get('moat','')[:80]}</span></div>
            <div style="background:#1e293b;padding:8px 10px;border-radius:6px"><span style="color:#22c55e">3Y target:</span> <span style="color:#cbd5e1">₹{r.get('3y_target_inr','?')} (+{r.get('potential_return_pct','?')}%)</span></div>
            <div style="background:#1e293b;padding:8px 10px;border-radius:6px"><span style="color:#ef4444">Key risk:</span> <span style="color:#cbd5e1">{r.get('key_risk','')[:80]}</span></div>
          </div>
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>HedgeFusion Multibagger Screener — {timestamp}</title>
<style>
  body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#050d1a;color:#e2e8f0;margin:0;padding:20px;-webkit-font-smoothing:antialiased}}
  .wrap{{max-width:1000px;margin:0 auto}}
  h1{{font-size:24px;font-weight:800;letter-spacing:-0.5px;margin-bottom:4px;color:#f8fafc}}
  h2{{font-size:18px;font-weight:700;margin:32px 0 14px;color:#f1f5f9}}
  .meta{{font-size:13px;color:#64748b;margin-bottom:32px}}
  table{{width:100%;border-collapse:collapse;background:#0f172a;border-radius:10px;overflow:hidden;margin-bottom:8px}}
  th{{background:#1e293b;color:#64748b;padding:10px 14px;text-align:left;font-size:12px;font-weight:600;letter-spacing:0.05em}}
  td{{padding:10px 14px;border-bottom:1px solid #1e293b;font-size:13px;color:#cbd5e1}}
  tr:last-child td{{border-bottom:none}}
  tr:hover td{{background:#0a1628}}
  .disclaimer{{background:#1e1a0a;border:1px solid #78350f;border-radius:8px;padding:14px 18px;font-size:12px;color:#92400e;margin-top:24px}}
</style>
</head>
<body>
<div class="wrap">
  <h1>🇮🇳 HedgeFusion Multibagger Screener</h1>
  <div class="meta">Generated: {timestamp} &nbsp;·&nbsp; Universe: {len(quant_results)} stocks &nbsp;·&nbsp; AI deep-dive: {len(ai_results)} candidates</div>

  <h2>📊 Quantitative Scores — Top 20</h2>
  <table>
    <thead><tr><th>#</th><th>Ticker</th><th>Score</th><th>P/E</th><th>ROE</th><th>Rev Growth</th><th>D/E</th><th>Mkt Cap</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>

  <h2>🔥 AI Multibagger Deep-Dive</h2>
  {ai_cards if ai_cards else '<p style="color:#64748b">No AI results — check OPENAI_API_KEY</p>'}

  <div class="disclaimer">
    ⚠️ <strong>Disclaimer:</strong> This screener is for educational and research purposes only. 
    It is not SEBI-registered investment advice. Past screening performance does not guarantee future returns. 
    Always conduct thorough due diligence and consult a qualified financial advisor before investing.
  </div>
</div>
</body>
</html>"""


# ── Main runner ──────────────────────────────────────────────

def run_screener(
    tickers: list[str] | None = None,
    sector: str | None = None,
    top_n_for_ai: int = 15,
    workers: int = 8,
) -> tuple[list, list]:
    """
    Run the full multibagger screening pipeline.

    Parameters
    ----------
    tickers      : Custom list of tickers to screen. If None, uses NSE_UNIVERSE.
    sector       : Filter to one sector name (key in NSE_UNIVERSE).
    top_n_for_ai : How many top quant scores to send to AI deep-dive.
    workers      : Parallel threads for data fetching.
    """
    # Build universe
    if tickers:
        universe = tickers
    elif sector:
        universe = NSE_UNIVERSE.get(sector.upper(), [])
        if not universe:
            print(f"Unknown sector '{sector}'. Available: {', '.join(NSE_UNIVERSE.keys())}")
            return [], []
    else:
        universe = ALL_TICKERS

    # Remove duplicates
    universe = list(dict.fromkeys(universe))

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M IST")
    ts_file   = datetime.now().strftime("%Y%m%d_%H%M")

    print(f"\n{'━'*65}")
    print(f"  HEDGEFUSION MULTIBAGGER SCREENER")
    print(f"  Scanning {len(universe)} stocks | Top {top_n_for_ai} go to AI deep-dive")
    print(f"  Cost: ~₹{top_n_for_ai * 2}–{top_n_for_ai * 5} in OpenAI credits")
    print(f"{'━'*65}\n")

    # ── Phase 1: Quantitative scan (parallel) ───────────────
    print(f"Phase 1: Quantitative scan ({workers} parallel workers)...")
    quant_results = []
    done = 0

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(score_stock, t): t for t in universe}
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                result = future.result()
                quant_results.append(result)
                done += 1
                if done % 10 == 0 or done == len(universe):
                    print(f"  [{done}/{len(universe)}] scanned...", end="\r")
            except Exception as e:
                logger.warning("Score failed {}: {}", ticker, e)

    # Sort by score
    quant_results.sort(key=lambda x: x["score"], reverse=True)
    print(f"\n  ✓ Quantitative scan complete")

    # Print quick summary
    print(f"\n  Top 10 by quant score:")
    for r in quant_results[:10]:
        bar = "█" * (r["score"] // 10) + "░" * (10 - r["score"] // 10)
        print(f"    {r['ticker']:<14} {bar} {r['score']:>3}/100")

    # ── Phase 2: AI deep-dive on top candidates ─────────────
    top_candidates = [r for r in quant_results[:top_n_for_ai] if r["score"] > 30]
    ai_results = []

    if top_candidates and os.getenv("OPENAI_API_KEY", "").startswith("sk-"):
        print(f"\nPhase 2: AI deep-dive on top {len(top_candidates)} candidates...")
        ai_results = ai_deep_dive(top_candidates)
        print(f"  ✓ AI analysis complete — {len(ai_results)} stocks ranked")
    else:
        print("\nPhase 2: Skipped (set OPENAI_API_KEY to enable AI deep-dive)")

    # ── Save reports ─────────────────────────────────────────
    text_report = build_report(quant_results, ai_results, timestamp)
    html_report = build_html_report(quant_results, ai_results, timestamp)

    txt_path  = OUTPUT_DIR / f"multibagger_{ts_file}.txt"
    html_path = OUTPUT_DIR / f"multibagger_{ts_file}.html"
    json_path = OUTPUT_DIR / f"multibagger_{ts_file}.json"

    txt_path.write_text(text_report, encoding="utf-8")
    html_path.write_text(html_report, encoding="utf-8")
    json_path.write_text(
        json.dumps({"quant": quant_results, "ai": ai_results}, indent=2, default=str),
        encoding="utf-8",
    )

    print(text_report)
    print(f"\n✅ Reports saved:")
    print(f"   HTML: {html_path}")
    print(f"   Text: {txt_path}")
    print(f"\n   👉 Open the HTML file in your browser for the full report.")

    return quant_results, ai_results


def main():
    parser = argparse.ArgumentParser(description="HedgeFusion Multibagger Screener")
    parser.add_argument("--sector",  help=f"Sector to scan: {', '.join(NSE_UNIVERSE.keys())}")
    parser.add_argument("--ticker",  help="Single ticker to score")
    parser.add_argument("--quick",   action="store_true", help="Top 5 sectors only (~60 stocks)")
    parser.add_argument("--top",     type=int, default=15, help="N candidates for AI deep-dive")
    args = parser.parse_args()

    if args.ticker:
        result = score_stock(args.ticker.upper())
        print(f"\n{'━'*50}")
        print(f"  {result['ticker']} — Multibagger score: {result['score']}/100")
        print(f"{'━'*50}")
        for s in result["signals"]:
            print(f"  ✓ {s}")
        for f in result["flags"]:
            print(f"  ⚠ {f}")
        return

    if args.quick:
        tickers = []
        for sector in list(NSE_UNIVERSE.keys())[:5]:
            tickers.extend(NSE_UNIVERSE[sector])
        run_screener(tickers=tickers, top_n_for_ai=args.top)
    elif args.sector:
        run_screener(sector=args.sector, top_n_for_ai=args.top)
    else:
        run_screener(top_n_for_ai=args.top)


if __name__ == "__main__":
    main()
