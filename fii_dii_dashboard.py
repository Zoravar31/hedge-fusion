"""
HedgeFusion FII/DII Intelligence Dashboard
============================================
Standalone dashboard for tracking institutional money flows in Indian markets.

Answers the key questions every Indian investor needs:
  - What are FII doing today / this week?
  - Which stocks are FII/DII buying in bulk?
  - Is the smart money increasing or reducing holdings in my stocks?
  - What does the institutional flow picture say about market direction?

Data sources:
  - NSE FII/DII Trade React API (daily net flows)
  - NSE Bulk Deals API (large single trades)
  - NSE Block Deals API (negotiated institutional trades)
  - NSE Shareholding Pattern (quarterly FII/DII/Promoter %)

Usage:
    python fii_dii_dashboard.py                    # full dashboard
    python fii_dii_dashboard.py --stock HDFCBANK   # one stock deep-dive
    python fii_dii_dashboard.py --flows            # market flows only
    python fii_dii_dashboard.py --bulk             # bulk/block deals only
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger
from openai import OpenAI

load_dotenv(Path(__file__).parent / ".env")

client     = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
MODEL      = os.getenv("MODEL_NAME", "gpt-4o-mini")
OUTPUT_DIR = Path(__file__).parent / "outputs"
OUTPUT_DIR.mkdir(exist_ok=True)

from tools.fii_dii import (
    get_fii_dii_daily,
    get_fii_dii_summary,
    get_bulk_deals,
    get_block_deals,
    get_stock_shareholding,
)


# ── AI narrative for FII/DII context ─────────────────────────

FII_DII_AI_PROMPT = """
You are a macro strategist at an Indian hedge fund with deep expertise in
tracking foreign and domestic institutional money flows (FII/DII).

You have real NSE institutional flow data. Provide a sharp, actionable analysis:

1. FLOW NARRATIVE (3-4 sentences)
   What story does the FII/DII data tell about where we are in the cycle?
   Are foreign investors risk-on or risk-off on India?

2. MARKET IMPLICATIONS
   - What does this mean for Nifty direction next 2-4 weeks?
   - Which sectors are FII likely buying/selling based on flows?
   - Is DII providing a reliable floor or also exiting?

3. STOCK IMPLICATIONS
   - Based on bulk/block deals, which specific stocks show smart money accumulation?
   - Which stocks show distribution (selling into retail strength)?

4. ACTIONABLE SIGNALS
   - 3 specific trading ideas based purely on institutional flow data
   - Each with: stock name, direction, rationale from flow data

5. RISK FACTORS
   - What could reverse current FII/DII trend?
   - Any global triggers to watch (Fed, China, crude)?

Be specific with ₹ crore numbers. Reference actual data from the input.
Output as JSON with keys: flow_narrative, market_implications,
stock_implications, actionable_signals, risk_factors, overall_verdict
"""


def get_ai_fii_narrative(summary: dict, bulk: dict, shareholding_list: list) -> dict:
    """Get AI interpretation of FII/DII flow data."""
    try:
        context = {
            "fii_flows": summary.get("fii_flows", {}),
            "dii_flows": summary.get("dii_flows", {}),
            "market_signal_5d": summary.get("market_signal_5d",""),
            "interpretation": summary.get("interpretation",""),
            "bulk_deals_7d": {
                "total_deals":   bulk.get("total_deals",0),
                "buy_value_cr":  bulk.get("buy_value_cr",0),
                "sell_value_cr": bulk.get("sell_value_cr",0),
                "signal":        bulk.get("aggregate_signal",""),
                "top_5":         bulk.get("top_deals",[])[:5],
            },
            "daily_last_5": summary.get("daily_data",[])[:5],
        }

        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system",
                 "content": "You are a macro strategist specialising in Indian institutional flows."},
                {"role": "user",
                 "content": FII_DII_AI_PROMPT + f"\n\nData:\n{json.dumps(context, indent=2, default=str)}"},
            ],
            temperature=0.2,
        )
        raw = response.choices[0].message.content or "{}"
        import re
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            result = json.loads(match.group())
            if isinstance(result, dict):
                return result
    except Exception as e:
        logger.error("FII/DII AI narrative failed: {}", e)
    return {}


# ── Per-stock FII/DII analysis ────────────────────────────────

def analyse_stock_fii_dii(ticker: str) -> dict:
    """Deep FII/DII analysis for one stock."""
    bulk         = json.loads(get_bulk_deals(ticker, days=90))
    block        = json.loads(get_block_deals(ticker))
    shareholding = json.loads(get_stock_shareholding(ticker))

    # Score the institutional signal 0-100
    score    = 50  # neutral base
    signals  = []
    flags    = []

    # Shareholding trends
    trends = shareholding.get("trends", {})
    latest = shareholding.get("latest", {})

    fii_change = trends.get("fii_change", 0) or 0
    if fii_change > 3:
        score += 15
        signals.append(f"FII increased stake +{fii_change:.1f}% last year 🟢")
    elif fii_change > 1:
        score += 7
        signals.append(f"FII marginally increasing +{fii_change:.1f}%")
    elif fii_change < -3:
        score -= 15
        flags.append(f"FII reduced stake {fii_change:.1f}% — exiting 🔴")
    elif fii_change < -1:
        score -= 7
        flags.append(f"FII marginally reducing {fii_change:.1f}%")

    dii_change = trends.get("dii_change", 0) or 0
    if dii_change > 1 and fii_change < 0:
        score += 8
        signals.append(f"DII absorbing FII selling +{dii_change:.1f}% 🟢 (floor signal)")
    elif dii_change > 1:
        score += 5
        signals.append(f"DII also increasing +{dii_change:.1f}%")

    pledge_change = trends.get("pledge_change", 0) or 0
    pledge_pct    = latest.get("promoter_pledge_pct", 0) or 0
    if pledge_pct > 20:
        score -= 20
        flags.append(f"HIGH PROMOTER PLEDGE {pledge_pct:.1f}% 🚨 — serious red flag")
    elif pledge_change > 2:
        score -= 10
        flags.append(f"Promoter pledge RISING +{pledge_change:.1f}% — watch closely")
    elif pledge_pct < 5:
        score += 5
        signals.append(f"Clean promoter pledge {pledge_pct:.1f}% 🟢")

    # Bulk deal signals
    bulk_buy  = bulk.get("buy_value_cr", 0)
    bulk_sell = bulk.get("sell_value_cr", 0)
    bulk_fii  = bulk.get("fii_deals", 0)
    if bulk_fii > 0:
        net_bulk = bulk_buy - bulk_sell
        if net_bulk > 50:
            score += 15
            signals.append(f"FII bulk accumulation ₹{net_bulk:,.0f}Cr in last 90 days 🔥")
        elif net_bulk > 0:
            score += 8
            signals.append(f"Mild FII bulk buying ₹{net_bulk:,.0f}Cr")
        elif net_bulk < -50:
            score -= 15
            flags.append(f"FII bulk distribution ₹{abs(net_bulk):,.0f}Cr in last 90 days 🔴")

    score = max(0, min(100, score))

    return {
        "ticker":            ticker,
        "institutional_score": score,
        "fii_shareholding_pct": latest.get("fii_fpi_pct", 0),
        "dii_shareholding_pct": latest.get("dii_pct", 0),
        "promoter_pct":      latest.get("promoter_pct", 0),
        "promoter_pledge_pct": pledge_pct,
        "fii_change_1y":     fii_change,
        "dii_change_1y":     dii_change,
        "pledge_change_1y":  pledge_change,
        "bulk_buy_cr":       bulk_buy,
        "bulk_sell_cr":      bulk_sell,
        "bulk_fii_deals":    bulk_fii,
        "top_bulk_deals":    bulk.get("top_deals", [])[:5],
        "block_deals":       block.get("deals", [])[:3],
        "signals":           signals,
        "flags":             flags,
        "verdict":           (
            "INSTITUTIONAL FAVOURITE 🔥" if score >= 75 else
            "INSTITUTIONAL POSITIVE ✅" if score >= 60 else
            "NEUTRAL ⚖️"               if score >= 45 else
            "INSTITUTIONAL CAUTION ⚠️"  if score >= 30 else
            "INSTITUTIONAL AVOID 🚫"
        ),
        "shareholding_history": shareholding.get("quarters", [])[:4],
    }


# ── HTML report builder ───────────────────────────────────────

def build_fii_dii_html(
    summary: dict,
    bulk: dict,
    ai_narrative: dict,
    stock_analyses: list,
    timestamp: str,
) -> str:
    fii = summary.get("fii_flows", {})
    dii = summary.get("dii_flows", {})
    sig = summary.get("market_signal_5d", "UNKNOWN")
    sig_color = (
        "#22c55e" if "BULL" in sig or "BUYING" in sig
        else "#ef4444" if "BEAR" in sig or "SELLING" in sig
        else "#f59e0b"
    )

    def flow_card(label, today, fived, tend, color):
        sign = lambda v: f"+₹{v:,.0f}Cr" if v and v > 0 else f"-₹{abs(v):,.0f}Cr" if v else "N/A"
        tc   = "#22c55e" if (today or 0) > 0 else "#ef4444"
        fc   = "#22c55e" if (fived or 0) > 0 else "#ef4444"
        return f"""<div style="background:#0f172a;border:1px solid #1e293b;
                               border-radius:8px;padding:16px">
          <div style="font-size:11px;color:{color};font-weight:700;
                      letter-spacing:.08em;margin-bottom:10px">{label}</div>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
            <div><div style="font-size:10px;color:#64748b">TODAY</div>
              <div style="font-size:18px;font-weight:700;color:{tc}">{sign(today)}</div></div>
            <div><div style="font-size:10px;color:#64748b">5-DAY NET</div>
              <div style="font-size:18px;font-weight:700;color:{fc}">{sign(fived)}</div></div>
          </div>
          <div style="margin-top:8px;font-size:12px;color:{color}">{tend}</div>
        </div>"""

    fii_card = flow_card(
        "FII / FPI FLOWS", fii.get("today_cr"), fii.get("5day_cr"),
        fii.get("trend","?"), "#60a5fa"
    )
    dii_card = flow_card(
        "DII FLOWS (MF + Insurance)", dii.get("today_cr"), dii.get("5day_cr"),
        dii.get("trend","?"), "#a78bfa"
    )

    # Daily table
    daily_rows = ""
    for d in summary.get("daily_data", [])[:10]:
        fn = d.get("fii_net_cr", 0) or 0
        dn = d.get("dii_net_cr", 0) or 0
        fc = "#22c55e" if fn > 0 else "#ef4444"
        dc = "#22c55e" if dn > 0 else "#ef4444"
        daily_rows += f"""<tr>
          <td style="color:#94a3b8;font-size:12px">{d.get('date','')}</td>
          <td style="color:{fc};font-weight:600">{'+'if fn>0 else ''}₹{fn:,.0f}Cr</td>
          <td style="color:{dc};font-weight:600">{'+'if dn>0 else ''}₹{dn:,.0f}Cr</td>
          <td style="color:#f59e0b;font-size:12px">{d.get('combined_signal','')}</td>
        </tr>"""

    # Bulk deals table
    bulk_rows = ""
    for deal in bulk.get("top_deals", [])[:15]:
        bs     = deal.get("buy_sell","")
        color  = "#22c55e" if bs == "BUY" else "#ef4444"
        ct     = deal.get("client_type","")
        ct_col = "#60a5fa" if "FII" in ct else "#a78bfa" if "MF" in ct else "#94a3b8"
        bulk_rows += f"""<tr>
          <td style="font-family:monospace;font-weight:700">{deal.get('symbol','')}</td>
          <td style="color:{color};font-weight:700">{bs}</td>
          <td style="font-size:12px;color:{ct_col}">{ct}</td>
          <td style="font-size:12px;color:#64748b;max-width:200px;overflow:hidden;
                     text-overflow:ellipsis;white-space:nowrap">{deal.get('client','')[:40]}</td>
          <td style="font-family:monospace">₹{deal.get('value_cr',0):,.1f}Cr</td>
          <td style="font-size:11px;color:#64748b">{deal.get('date','')}</td>
        </tr>"""

    # AI narrative
    ai_html = ""
    if ai_narrative:
        sigs_html = ""
        for s in ai_narrative.get("actionable_signals", []):
            if isinstance(s, dict):
                sigs_html += f"""<div style="background:#052e16;border:1px solid #166534;
                  border-radius:6px;padding:10px;margin-bottom:6px">
                  <strong style="color:#22c55e">{s.get('stock','')}</strong>
                  <span style="color:#64748b;font-size:11px"> · {s.get('direction','')}</span>
                  <div style="font-size:12px;color:#86efac;margin-top:4px">
                    {s.get('rationale','')}</div></div>"""
            else:
                sigs_html += f'<div style="font-size:13px;color:#86efac;margin-bottom:6px">• {s}</div>'

        ai_html = f"""
        <div style="background:#0f172a;border:1px solid #1e293b;border-radius:10px;
                    padding:22px;margin-bottom:16px">
          <h3 style="color:#f59e0b;margin-bottom:12px">🧠 AI Flow Intelligence</h3>
          <p style="color:#94a3b8;line-height:1.7;margin-bottom:16px">
            {ai_narrative.get('flow_narrative','')}</p>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:16px">
            <div style="background:#1e293b;border-radius:6px;padding:12px">
              <div style="font-size:11px;color:#64748b;margin-bottom:6px">MARKET IMPLICATIONS</div>
              <div style="font-size:13px;color:#cbd5e1">
                {ai_narrative.get('market_implications','')[:300]}</div>
            </div>
            <div style="background:#1e293b;border-radius:6px;padding:12px">
              <div style="font-size:11px;color:#64748b;margin-bottom:6px">RISK FACTORS</div>
              <div style="font-size:13px;color:#fca5a5">
                {str(ai_narrative.get('risk_factors',''))[:300]}</div>
            </div>
          </div>
          {"<div style='margin-bottom:8px'><div style='font-size:11px;color:#16a34a;font-weight:700;margin-bottom:8px'>💡 ACTIONABLE SIGNALS</div>" + sigs_html + "</div>" if sigs_html else ""}
          <div style="background:#1e293b;border-radius:6px;padding:12px">
            <div style="font-size:11px;color:#64748b;margin-bottom:4px">OVERALL VERDICT</div>
            <div style="font-size:14px;color:#f59e0b;font-weight:600">
              {ai_narrative.get('overall_verdict','')}</div>
          </div>
        </div>"""

    # Stock analysis cards
    stock_cards = ""
    for sa in sorted(stock_analyses, key=lambda x: x.get("institutional_score",0), reverse=True):
        score = sa.get("institutional_score", 50)
        sc    = "#22c55e" if score >= 65 else "#f59e0b" if score >= 45 else "#ef4444"
        fii_pct = sa.get("fii_shareholding_pct", 0)
        fii_chg = sa.get("fii_change_1y", 0)
        pledge  = sa.get("promoter_pledge_pct", 0)
        plc     = "#ef4444" if pledge > 20 else "#f59e0b" if pledge > 10 else "#22c55e"

        sigs_html = "".join(
            f"<div style='font-size:12px;color:#86efac;margin-bottom:3px'>✓ {s}</div>"
            for s in sa.get("signals",[])[:3]
        )
        flags_html = "".join(
            f"<div style='font-size:12px;color:#fca5a5;margin-bottom:3px'>⚠ {f}</div>"
            for f in sa.get("flags",[])[:3]
        )

        top_bulk = sa.get("top_bulk_deals",[])[:2]
        bulk_html = ""
        for b in top_bulk:
            bc = "#22c55e" if b.get("buy_sell")=="BUY" else "#ef4444"
            bulk_html += f"""<div style='font-size:11px;background:#1e293b;border-radius:4px;
              padding:5px 8px;margin-top:4px'>
              <span style='color:{bc};font-weight:700'>{b.get('buy_sell','')}</span>
              <span style='color:#64748b'> by {b.get('client_type','')} —
              ₹{b.get('value_cr',0):,.1f}Cr on {b.get('date','')}</span></div>"""

        stock_cards += f"""
        <div style="background:#0f172a;border:1px solid #1e293b;border-radius:10px;
                    padding:18px;margin-bottom:10px">
          <div style="display:flex;justify-content:space-between;align-items:center;
                      margin-bottom:12px">
            <span style="font-size:18px;font-weight:800;font-family:monospace;
                         color:#f1f5f9">{sa['ticker']}</span>
            <div style="display:flex;gap:8px;align-items:center">
              <span style="font-size:11px;color:#64748b">Institutional score</span>
              <span style="font-size:20px;font-weight:800;color:{sc}">{score}</span>
            </div>
          </div>
          <div style="font-size:13px;color:{sc};font-weight:600;margin-bottom:10px">
            {sa.get('verdict','')}</div>
          <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:6px;
                      margin-bottom:12px;font-size:12px">
            <div style="background:#1e293b;padding:8px;border-radius:5px;text-align:center">
              <div style="color:#64748b;font-size:10px">FII %</div>
              <div style="color:#60a5fa;font-weight:700">{fii_pct:.1f}%</div>
            </div>
            <div style="background:#1e293b;padding:8px;border-radius:5px;text-align:center">
              <div style="color:#64748b;font-size:10px">FII CHANGE</div>
              <div style="color:{'#22c55e'if fii_chg>0 else '#ef4444'};font-weight:700">
                {fii_chg:+.1f}%</div>
            </div>
            <div style="background:#1e293b;padding:8px;border-radius:5px;text-align:center">
              <div style="color:#64748b;font-size:10px">DII %</div>
              <div style="color:#a78bfa;font-weight:700">
                {sa.get('dii_shareholding_pct',0):.1f}%</div>
            </div>
            <div style="background:#1e293b;padding:8px;border-radius:5px;text-align:center">
              <div style="color:#64748b;font-size:10px">PLEDGE</div>
              <div style="color:{plc};font-weight:700">{pledge:.1f}%</div>
            </div>
          </div>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:8px">
            <div>{sigs_html}</div>
            <div>{flags_html}</div>
          </div>
          {bulk_html}
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>HedgeFusion FII/DII Dashboard — {timestamp}</title>
<style>
  body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
       background:#050d1a;color:#e2e8f0;margin:0;padding:20px;
       -webkit-font-smoothing:antialiased}}
  .wrap{{max-width:1060px;margin:0 auto}}
  h1{{font-size:22px;font-weight:800;color:#f8fafc;margin-bottom:4px}}
  h2{{font-size:15px;font-weight:700;color:#e2e8f0;margin:28px 0 12px;
      padding-bottom:8px;border-bottom:1px solid #1e293b}}
  .meta{{font-size:12px;color:#64748b;margin-bottom:20px}}
  table{{width:100%;border-collapse:collapse;background:#0f172a;
         border-radius:8px;overflow:hidden;margin-bottom:8px}}
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
  <h1>🏦 HedgeFusion FII/DII Intelligence</h1>
  <div class="meta">{timestamp} &nbsp;·&nbsp; Source: NSE India</div>

  <div style="background:#0f172a;border:2px solid {sig_color};border-radius:10px;
              padding:16px 20px;margin-bottom:20px;display:flex;align-items:center;gap:16px">
    <div style="font-size:28px;font-weight:900;color:{sig_color}">{sig}</div>
    <div style="font-size:13px;color:#94a3b8;line-height:1.6">
      {summary.get('interpretation','')[:250]}</div>
  </div>

  <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:20px">
    {fii_card}
    {dii_card}
  </div>

  {ai_html}

  <h2>📅 Daily FII/DII Activity</h2>
  {"<table><thead><tr><th>Date</th><th>FII Net</th><th>DII Net</th><th>Signal</th></tr></thead><tbody>" + daily_rows + "</tbody></table>" if daily_rows else "<p style='color:#64748b'>Daily data unavailable during market off-hours.</p>"}

  <h2>💼 Bulk Deals — Smart Money Tracker</h2>
  {"<table><thead><tr><th>Stock</th><th>Buy/Sell</th><th>Client Type</th><th>Client Name</th><th>Value</th><th>Date</th></tr></thead><tbody>" + bulk_rows + "</tbody></table>" if bulk_rows else "<p style='color:#64748b'>Bulk deal data unavailable.</p>"}

  {"<h2>🔍 Per-Stock Institutional Analysis</h2>" + stock_cards if stock_cards else ""}

  <div class="disc">
    ⚠️ FII/DII data is sourced from NSE India APIs and may be delayed.
    Shareholding patterns are updated quarterly. Not SEBI-registered investment advice.
  </div>
</div>
</body>
</html>"""


# ── Main ──────────────────────────────────────────────────────

def run_fii_dii_dashboard(
    stock_tickers: list[str] | None = None,
    use_ai: bool = True,
) -> dict:
    from config import HOLDING_TICKERS
    tickers   = stock_tickers or HOLDING_TICKERS
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M IST")
    ts_file   = datetime.now().strftime("%Y%m%d_%H%M")

    print(f"\n{'━'*60}")
    print(f"  HEDGEFUSION FII/DII INTELLIGENCE DASHBOARD")
    print(f"  {timestamp}")
    print(f"{'━'*60}\n")

    print("Fetching market-level FII/DII flows...")
    summary = json.loads(get_fii_dii_summary())
    sig     = summary.get("market_signal_5d","?")
    fii_5d  = summary.get("fii_flows",{}).get("5day_cr")
    dii_5d  = summary.get("dii_flows",{}).get("5day_cr")
    print(f"  Market signal (5d): {sig}")
    if fii_5d is not None:
        print(f"  FII net 5d: {'+'if fii_5d>0 else ''}₹{fii_5d:,.0f}Cr")
        print(f"  DII net 5d: {'+'if (dii_5d or 0)>0 else ''}₹{(dii_5d or 0):,.0f}Cr")
    print(f"  Interpretation: {summary.get('interpretation','N/A')[:100]}...")

    print("\nFetching bulk deals (last 30 days)...")
    bulk = json.loads(get_bulk_deals(days=30))
    print(f"  Total bulk deals: {bulk.get('total_deals',0)}")
    print(f"  Buy value: ₹{bulk.get('buy_value_cr',0):,.0f}Cr | Sell: ₹{bulk.get('sell_value_cr',0):,.0f}Cr")
    print(f"  Signal: {bulk.get('aggregate_signal','?')}")

    print(f"\nAnalysing {len(tickers)} stocks for institutional patterns...")
    stock_analyses = []
    for t in tickers:
        print(f"  → {t}...", end=" ")
        try:
            sa = analyse_stock_fii_dii(t)
            stock_analyses.append(sa)
            print(f"{sa.get('verdict','')} (score: {sa.get('institutional_score',0)})")
        except Exception as e:
            logger.error("Stock FII analysis failed {}: {}", t, e)
            print(f"ERROR: {e}")

    ai_narrative = {}
    if use_ai and os.getenv("OPENAI_API_KEY","").startswith("sk-"):
        print("\nGenerating AI flow narrative...")
        ai_narrative = get_ai_fii_narrative(summary, bulk, stock_analyses)
        if ai_narrative.get("overall_verdict"):
            print(f"  AI verdict: {ai_narrative['overall_verdict'][:100]}")

    # Print terminal summary
    print(f"\n{'━'*60}")
    print(f"  INSTITUTIONAL SCORE RANKINGS")
    print(f"{'━'*60}")
    for sa in sorted(stock_analyses, key=lambda x: x.get("institutional_score",0), reverse=True):
        score = sa.get("institutional_score", 50)
        bar   = "█" * (score // 10) + "░" * (10 - score // 10)
        print(f"  {sa['ticker']:<14} {bar} {score:>3}  {sa.get('verdict','')}")
    print()

    html      = build_fii_dii_html(summary, bulk, ai_narrative, stock_analyses, timestamp)
    html_path = OUTPUT_DIR / f"fii_dii_{ts_file}.html"
    html_path.write_text(html, encoding="utf-8")
    print(f"✅ Dashboard saved: {html_path}")
    print(f"   Open in browser for full institutional flow report.\n")

    return {"summary": summary, "bulk": bulk,
            "stocks": stock_analyses, "ai": ai_narrative}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="HedgeFusion FII/DII Dashboard")
    parser.add_argument("--stock",  help="Deep-dive one stock e.g. HDFCBANK")
    parser.add_argument("--flows",  action="store_true", help="Market flows only")
    parser.add_argument("--bulk",   action="store_true", help="Bulk/block deals only")
    parser.add_argument("--no-ai",  action="store_true", help="Skip AI narrative")
    parser.add_argument("--tickers",help="Comma-separated tickers e.g. RELIANCE,TCS")
    args = parser.parse_args()

    if args.flows:
        s = json.loads(get_fii_dii_summary())
        print(json.dumps(s, indent=2, default=str))
    elif args.bulk:
        b = json.loads(get_bulk_deals())
        print(json.dumps(b, indent=2, default=str))
    elif args.stock:
        sa = analyse_stock_fii_dii(args.stock.upper())
        print(json.dumps(sa, indent=2, default=str))
    else:
        tickers = [t.strip() for t in args.tickers.split(",")] if args.tickers else None
        run_fii_dii_dashboard(
            stock_tickers=tickers,
            use_ai=not args.no_ai,
        )
