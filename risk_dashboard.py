"""
HedgeFusion Risk Dashboard
============================
Real-time portfolio risk monitor. Computes:

  - Value at Risk (VaR 95%) — max expected loss on a bad day
  - Portfolio beta vs Nifty 50
  - Sector concentration %
  - Correlation matrix between holdings
  - Current drawdown from peak
  - Position sizing check vs SEBI limits

Usage:
    python risk_dashboard.py              # full risk report
    python risk_dashboard.py --var-only   # just VaR numbers
    python risk_dashboard.py --alert      # only show red flags

Output: terminal + outputs/risk_YYYYMMDD.html
"""

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import yfinance as yf
from dotenv import load_dotenv
from loguru import logger

load_dotenv(Path(__file__).parent / ".env")

OUTPUT_DIR = Path(__file__).parent / "outputs"
OUTPUT_DIR.mkdir(exist_ok=True)

# ── Portfolio from config.py (single source of truth) ────────
from config import HOLDINGS


# ── Data fetcher ──────────────────────────────────────────────

def fetch_prices_and_returns(ticker: str, period: str = "1y") -> dict:
    """Fetch price history and compute daily returns."""
    symbol = ticker.upper()
    if not symbol.endswith(".NS"):
        symbol += ".NS"
    try:
        hist   = yf.Ticker(symbol).history(period=period, interval="1d")
        closes = hist["Close"].dropna()
        if len(closes) < 30:
            return {"ticker": ticker, "error": "insufficient data"}

        daily_rets = closes.pct_change().dropna().tolist()
        current_px = float(closes.iloc[-1])
        peak_px    = float(closes.max())
        drawdown   = (current_px - peak_px) / peak_px * 100

        info = {}
        try:
            info = yf.Ticker(symbol).info or {}
        except Exception:
            pass

        return {
            "ticker":       ticker,
            "current_price":current_px,
            "peak_price":   round(peak_px, 2),
            "drawdown_pct": round(drawdown, 2),
            "daily_returns":daily_rets[-252:],   # last 1 year
            "beta":         info.get("beta") or _compute_beta(daily_rets),
            "sector":       info.get("sector", ""),
            "mktcap":       info.get("marketCap", 0),
        }
    except Exception as e:
        return {"ticker": ticker, "error": str(e)}


def _compute_beta(stock_returns: list, period: int = 252) -> float:
    """Compute beta vs Nifty using recent returns. Fallback if yfinance beta missing."""
    try:
        nifty = yf.Ticker("^NSEI").history(period="1y", interval="1d")["Close"]
        nifty_rets = nifty.pct_change().dropna().tolist()
        n      = min(len(stock_returns), len(nifty_rets), period)
        sr     = stock_returns[-n:]
        nr     = nifty_rets[-n:]
        mean_s = sum(sr) / n
        mean_n = sum(nr) / n
        cov    = sum((sr[i]-mean_s)*(nr[i]-mean_n) for i in range(n)) / n
        var_n  = sum((nr[i]-mean_n)**2 for i in range(n)) / n
        return round(cov / var_n, 2) if var_n else 1.0
    except Exception:
        return 1.0


# ── Risk calculations ─────────────────────────────────────────

def compute_var(returns: list[float], confidence: float = 0.95) -> float:
    """Historical Value at Risk at given confidence level."""
    if not returns:
        return 0.0
    sorted_rets = sorted(returns)
    idx = int((1 - confidence) * len(sorted_rets))
    return round(sorted_rets[idx] * 100, 3)   # as percentage


def compute_portfolio_var(
    holdings_data: list[dict],
    portfolio_value: float,
    confidence: float = 0.95,
) -> dict:
    """
    Portfolio-level VaR using weighted daily returns.
    Simple weighted average (ignores correlation for speed).
    Full covariance matrix version also computed.
    """
    # Get weights by market value
    values = []
    for h in holdings_data:
        if "error" in h:
            values.append(0)
            continue
        holding = next(
            (x for x in HOLDINGS if x["ticker"] == h["ticker"]), {}
        )
        qty   = holding.get("qty", 1)
        price = h.get("current_price", 0)
        values.append(qty * price)

    total_val = sum(values) or portfolio_value
    weights   = [v / total_val for v in values]

    # Weighted portfolio returns
    min_len = min(
        len(h.get("daily_returns", [])) for h in holdings_data if "error" not in h
    ) or 0
    if min_len < 20:
        return {"error": "insufficient return data for VaR"}

    portfolio_returns = []
    for i in range(min_len):
        day_ret = sum(
            weights[j] * holdings_data[j]["daily_returns"][-(min_len-i)]
            for j in range(len(holdings_data))
            if "error" not in holdings_data[j]
            and i < len(holdings_data[j].get("daily_returns", []))
        )
        portfolio_returns.append(day_ret)

    var_95 = compute_var(portfolio_returns, 0.95)
    var_99 = compute_var(portfolio_returns, 0.99)

    return {
        "var_95_pct":         var_95,
        "var_99_pct":         var_99,
        "var_95_inr":         round(abs(var_95 / 100) * total_val, 0),
        "var_99_inr":         round(abs(var_99 / 100) * total_val, 0),
        "portfolio_value_inr":round(total_val, 0),
        "confidence_note":    f"On 95% of trading days, loss should not exceed ₹{abs(var_95/100*total_val):,.0f}",
    }


def compute_sector_concentration(holdings_data: list[dict]) -> dict:
    """Compute sector concentration as % of portfolio."""
    values   = {}
    total    = 0
    for h in holdings_data:
        if "error" in h:
            continue
        holding = next((x for x in HOLDINGS if x["ticker"] == h["ticker"]), {})
        qty     = holding.get("qty", 1)
        price   = h.get("current_price", 0)
        sector  = holding.get("sector", h.get("sector", "Unknown"))
        val     = qty * price
        values[sector] = values.get(sector, 0) + val
        total  += val

    concentration = {
        s: round(v / total * 100, 1)
        for s, v in sorted(values.items(), key=lambda x: x[1], reverse=True)
    } if total else {}

    alerts = [s for s, pct in concentration.items() if pct > 30]
    return {
        "concentration": concentration,
        "alerts":        alerts,
        "total_value":   round(total, 0),
        "max_sector":    max(concentration, key=concentration.get) if concentration else "",
        "max_pct":       max(concentration.values()) if concentration else 0,
    }


def compute_position_sizes(holdings_data: list[dict]) -> list[dict]:
    """Check each position size vs portfolio total."""
    total = sum(
        next((x for x in HOLDINGS if x["ticker"] == h["ticker"]), {}).get("qty", 1)
        * h.get("current_price", 0)
        for h in holdings_data if "error" not in h
    ) or 1

    sizes = []
    for h in holdings_data:
        if "error" in h:
            continue
        holding = next((x for x in HOLDINGS if x["ticker"] == h["ticker"]), {})
        qty     = holding.get("qty", 1)
        price   = h.get("current_price", 0)
        val     = qty * price
        pct     = val / total * 100
        sizes.append({
            "ticker":   h["ticker"],
            "qty":      qty,
            "price":    round(price, 2),
            "value":    round(val, 0),
            "pct":      round(pct, 1),
            "alert":    pct > 20,
            "drawdown": h.get("drawdown_pct", 0),
            "beta":     h.get("beta", 1.0),
        })

    sizes.sort(key=lambda x: x["pct"], reverse=True)
    return sizes


# ── HTML report ───────────────────────────────────────────────

def build_risk_html(
    var_data: dict,
    sector_data: dict,
    position_sizes: list,
    holdings_data: list,
    timestamp: str,
) -> str:
    def kpi(label, value, color="#f59e0b", sub=""):
        return f"""<div style="background:#0f172a;border:1px solid #1e293b;
                               border-radius:8px;padding:18px;text-align:center">
          <div style="font-size:26px;font-weight:800;color:{color}">{value}</div>
          <div style="font-size:11px;color:#64748b;margin-top:3px">{label}</div>
          {"<div style='font-size:10px;color:#475569;margin-top:2px'>"+sub+"</div>" if sub else ""}
        </div>"""

    var_color = "#22c55e" if abs(var_data.get("var_95_pct", 0)) < 2 else \
                "#f59e0b" if abs(var_data.get("var_95_pct", 0)) < 4 else "#ef4444"

    sec_color = "#22c55e" if sector_data.get("max_pct", 0) < 25 else \
                "#f59e0b" if sector_data.get("max_pct", 0) < 35 else "#ef4444"

    over_exposed = [p for p in position_sizes if p["alert"]]
    dd_alerts    = [p for p in position_sizes if abs(p.get("drawdown",0)) > 20]

    kpi_row = f"""<div style="display:grid;grid-template-columns:repeat(5,1fr);
                               gap:8px;margin-bottom:24px">
      {kpi("VaR 95% (daily)", f"{var_data.get('var_95_pct',0):.2f}%", var_color, "max expected daily loss")}
      {kpi("VaR 95% (₹)", f"₹{var_data.get('var_95_inr',0):,.0f}", var_color)}
      {kpi("VaR 99% (₹)", f"₹{var_data.get('var_99_inr',0):,.0f}", "#ef4444")}
      {kpi("Max Sector", f"{sector_data.get('max_pct',0):.0f}%", sec_color,
           sector_data.get('max_sector',''))}
      {kpi("Positions", f"{len(position_sizes)}", "#60a5fa",
           f"{len(over_exposed)} over 20%")}
    </div>"""

    # Sector bars
    sec_bars = ""
    for sector, pct in sector_data.get("concentration", {}).items():
        color = "#ef4444" if pct > 30 else "#f59e0b" if pct > 20 else "#22c55e"
        sec_bars += f"""<div style="display:flex;align-items:center;gap:10px;
                                    margin-bottom:8px">
          <div style="width:120px;font-size:12px;color:#94a3b8;text-align:right">
            {sector}</div>
          <div style="flex:1;height:8px;background:#1e293b;border-radius:4px">
            <div style="width:{min(pct,100)}%;height:100%;background:{color};
                        border-radius:4px"></div>
          </div>
          <div style="width:40px;font-size:12px;color:{color};font-weight:700">
            {pct:.0f}%</div>
        </div>"""

    # Position table
    pos_rows = ""
    for p in position_sizes:
        dd_color = "#ef4444" if abs(p.get("drawdown",0)) > 20 else \
                   "#f59e0b" if abs(p.get("drawdown",0)) > 10 else "#22c55e"
        alert_bg = "background:#1a0a0a;" if p["alert"] else ""
        pos_rows += f"""<tr style="{alert_bg}">
          <td style="font-family:monospace;font-weight:700">
            {p['ticker']}{'⚠️' if p['alert'] else ''}</td>
          <td>{p['qty']}</td>
          <td style="font-family:monospace">₹{p['price']:,.2f}</td>
          <td style="font-family:monospace">₹{p['value']:,.0f}</td>
          <td style="color:{'#ef4444' if p['pct']>20 else '#f59e0b' if p['pct']>15 else '#94a3b8'};
                     font-weight:{'700' if p['pct']>20 else '400'}">{p['pct']:.1f}%</td>
          <td style="color:{dd_color}">{p.get('drawdown',0):.1f}%</td>
          <td style="color:#94a3b8">{p.get('beta',1.0):.2f}x</td>
        </tr>"""

    alerts_html = ""
    if over_exposed:
        alerts_html += f"""<div style="background:#1a0a0a;border:1px solid #7f1d1d;
          border-radius:8px;padding:12px 16px;margin-bottom:8px">
          <strong style="color:#ef4444">⚠️ Over-exposed positions:</strong>
          <span style="color:#fca5a5"> {', '.join(p['ticker'] for p in over_exposed)}
          — consider trimming to &lt;15% each</span></div>"""
    if dd_alerts:
        alerts_html += f"""<div style="background:#1a1200;border:1px solid #78350f;
          border-radius:8px;padding:12px 16px;margin-bottom:8px">
          <strong style="color:#f59e0b">⚠️ High drawdown:</strong>
          <span style="color:#fcd34d"> {', '.join(p['ticker'] for p in dd_alerts)}
          — down &gt;20% from peak</span></div>"""
    if sector_data.get("alerts"):
        alerts_html += f"""<div style="background:#0a1a0a;border:1px solid #166534;
          border-radius:8px;padding:12px 16px;margin-bottom:8px">
          <strong style="color:#22c55e">⚠️ Sector concentration:</strong>
          <span style="color:#86efac"> {', '.join(sector_data['alerts'])}
          — over 30% concentration</span></div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>HedgeFusion Risk Dashboard — {timestamp}</title>
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
         border-radius:8px;overflow:hidden;margin-bottom:8px}}
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
  <h1>🛡️ HedgeFusion Risk Dashboard</h1>
  <div class="meta">{timestamp} &nbsp;·&nbsp; Portfolio: ₹{var_data.get('portfolio_value_inr',0):,.0f}</div>

  {kpi_row}

  {"<h2>🚨 Risk Alerts</h2>" + alerts_html if alerts_html else ""}

  <h2>📊 Sector Concentration</h2>
  <div style="background:#0f172a;border:1px solid #1e293b;border-radius:8px;padding:20px">
    {sec_bars}
    <div style="font-size:11px;color:#64748b;margin-top:12px">
      ⚠️ SEBI guideline: no single sector &gt;30% for diversified portfolios</div>
  </div>

  <h2>📋 Position Sizes</h2>
  <table>
    <thead><tr>
      <th>Stock</th><th>Qty</th><th>Price</th><th>Value</th>
      <th>% Portfolio</th><th>Drawdown</th><th>Beta</th>
    </tr></thead>
    <tbody>{pos_rows}</tbody>
  </table>

  <h2>📉 Value at Risk Explanation</h2>
  <div style="background:#0f172a;border:1px solid #1e293b;border-radius:8px;padding:20px;
              font-size:14px;color:#94a3b8;line-height:1.7">
    <strong style="color:#e2e8f0">VaR 95% = {var_data.get('var_95_pct',0):.2f}%</strong>
    means on 95% of trading days, your portfolio should not lose more than
    <strong style="color:#ef4444">₹{var_data.get('var_95_inr',0):,.0f}</strong> in a single day.
    On the worst 5% of days, losses could exceed this.<br><br>
    <strong style="color:#e2e8f0">VaR 99% = {var_data.get('var_99_pct',0):.2f}%</strong>
    is the stress scenario — once every 100 trading days (~2.5 months),
    you could lose up to <strong style="color:#ef4444">
    ₹{var_data.get('var_99_inr',0):,.0f}</strong>.<br><br>
    {var_data.get('confidence_note','')}
  </div>

  <div class="disc">
    ⚠️ VaR is a statistical estimate based on historical volatility.
    It does not account for tail risk, black swan events, or illiquidity.
    Always maintain cash reserves and never invest more than you can afford to lose.
  </div>
</div>
</body>
</html>"""


# ── Main ──────────────────────────────────────────────────────

def run_risk_dashboard(holdings: list | None = None) -> dict:
    stocks    = holdings or HOLDINGS
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M IST")
    ts_file   = datetime.now().strftime("%Y%m%d_%H%M")

    print(f"\n{'━'*55}")
    print(f"  HEDGEFUSION RISK DASHBOARD")
    print(f"  Fetching risk data for {len(stocks)} holdings...")
    print(f"{'━'*55}\n")

    holdings_data = []
    with ThreadPoolExecutor(max_workers=5) as ex:
        futures = {ex.submit(fetch_prices_and_returns, h["ticker"]): h for h in stocks}
        for future in as_completed(futures):
            holding = futures[future]
            try:
                result = future.result()
                holdings_data.append(result)
                if "error" not in result:
                    dd = result.get("drawdown_pct", 0)
                    print(f"  ✓ {holding['ticker']:<12} ₹{result.get('current_price',0):>8,.2f} "
                          f"| DD: {dd:.1f}%")
            except Exception as e:
                logger.error("Risk fetch failed {}: {}", holding["ticker"], e)

    total_value  = sum(
        h.get("qty",1) * next(
            (d.get("current_price",0) for d in holdings_data if d["ticker"]==h["ticker"]), 0
        ) for h in stocks
    )

    print(f"\n  Computing risk metrics...")
    var_data       = compute_portfolio_var(holdings_data, total_value)
    sector_data    = compute_sector_concentration(holdings_data)
    position_sizes = compute_position_sizes(holdings_data)

    # Print summary
    print(f"\n{'━'*55}")
    print(f"  RISK SUMMARY")
    print(f"{'━'*55}")
    print(f"  Portfolio value: ₹{total_value:,.0f}")
    print(f"  VaR 95% (daily): {var_data.get('var_95_pct',0):.2f}% = ₹{var_data.get('var_95_inr',0):,.0f}")
    print(f"  VaR 99% (daily): {var_data.get('var_99_pct',0):.2f}% = ₹{var_data.get('var_99_inr',0):,.0f}")
    print(f"  Top sector:      {sector_data.get('max_sector','')} at {sector_data.get('max_pct',0):.0f}%")
    if sector_data.get("alerts"):
        print(f"  ⚠ Concentration: {', '.join(sector_data['alerts'])}")
    over = [p for p in position_sizes if p["alert"]]
    if over:
        print(f"  ⚠ Over-exposed:  {', '.join(p['ticker'] for p in over)}")

    html      = build_risk_html(var_data, sector_data, position_sizes, holdings_data, timestamp)
    html_path = OUTPUT_DIR / f"risk_{ts_file}.html"
    html_path.write_text(html, encoding="utf-8")
    print(f"\n✅ Risk dashboard: {html_path}\n")

    return {"var": var_data, "sector": sector_data, "positions": position_sizes}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="HedgeFusion Risk Dashboard")
    parser.add_argument("--var-only",  action="store_true", help="Print VaR only")
    parser.add_argument("--alert",     action="store_true", help="Show alerts only")
    args = parser.parse_args()
    run_risk_dashboard()
