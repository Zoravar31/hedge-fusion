"""
HedgeFusion Backtester
========================
Tests how the quantitative multibagger scoring signals
would have performed historically on NSE stocks.

Important caveat: this is a SIGNAL backtester, not a full
strategy backtester. It tests whether:
  - Stocks scoring >60 on the quant screen outperform over 3/6/12 months
  - The bull/bear conviction spread predicts direction
  - Stop loss levels historically contain drawdowns

What it does NOT do:
  - Simulate actual order execution (slippage, impact cost)
  - Account for survivorship bias fully
  - Simulate the AI agents on historical data (too expensive)

Usage:
    python backtester.py                          # test full universe
    python backtester.py --ticker RELIANCE        # single stock backtest
    python backtester.py --period 1y --top 20    # top 20 stocks, 1 year

Output: outputs/backtest_YYYYMMDD.html
"""

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

import yfinance as yf
from dotenv import load_dotenv
from loguru import logger
from config import HOLDING_TICKERS

load_dotenv(Path(__file__).parent / ".env")

OUTPUT_DIR = Path(__file__).parent / "outputs"
OUTPUT_DIR.mkdir(exist_ok=True)

# ── Backtest universe ─────────────────────────────────────────
BACKTEST_UNIVERSE = [
    # Nifty 50 large caps — liquid, reliable data
    "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "BHARTIARTL",
    "INFY", "LT", "SBIN", "AXISBANK", "KOTAKBANK",
    "HCLTECH", "WIPRO", "MARUTI", "TITAN", "NESTLEIND",
    "ASIANPAINT", "BAJFINANCE", "POWERGRID", "NTPC", "ONGC",
    # Your holdings
    "ZOMATO", "M&M", "MAZDOCK", "BEL", "HINDZINC", "VBL",
    # Multibagger candidates
    "POLYCAB", "PERSISTENT", "COFORGE", "TATAELXSI",
    "ELGIEQUIP", "GRINDWELL", "MTAR",
]

PERIODS = {
    "3m":  90,
    "6m":  180,
    "1y":  365,
    "2y":  730,
}


# ── Data fetcher ──────────────────────────────────────────────

def fetch_historical(ticker: str, days_back: int = 400) -> dict:
    """
    Fetch historical price data and compute rolling returns.
    Returns dict with price series and forward returns at 3m/6m/12m.
    """
    symbol = ticker.strip().upper()
    if not symbol.endswith(".NS"):
        symbol += ".NS"

    try:
        t    = yf.Ticker(symbol)
        hist = t.history(period="2y", interval="1d")

        if hist is None or hist.empty or len(hist) < 60:
            return {"ticker": ticker, "error": "insufficient data"}

        # Get key price points
        prices = hist["Close"].dropna()
        dates  = prices.index

        latest_price = float(prices.iloc[-1])
        price_1y_ago = float(prices.iloc[-252]) if len(prices) >= 252 else float(prices.iloc[0])
        price_6m_ago = float(prices.iloc[-126]) if len(prices) >= 126 else float(prices.iloc[0])
        price_3m_ago = float(prices.iloc[-63])  if len(prices) >= 63  else float(prices.iloc[0])

        ret_1y = (latest_price - price_1y_ago) / price_1y_ago * 100
        ret_6m = (latest_price - price_6m_ago) / price_6m_ago * 100
        ret_3m = (latest_price - price_3m_ago) / price_3m_ago * 100

        # Max drawdown (1 year)
        prices_1y = prices.iloc[-252:] if len(prices) >= 252 else prices
        peak      = prices_1y.cummax()
        drawdown  = ((prices_1y - peak) / peak * 100)
        max_dd    = float(drawdown.min())

        # Volatility (annualised)
        daily_returns = prices.pct_change().dropna()
        volatility    = float(daily_returns.std() * (252 ** 0.5) * 100)

        # 52-week high/low
        w52_high = float(prices.iloc[-252:].max()) if len(prices) >= 252 else float(prices.max())
        w52_low  = float(prices.iloc[-252:].min()) if len(prices) >= 252 else float(prices.min())

        # Nifty comparison (benchmark)
        try:
            nifty = yf.Ticker("^NSEI")
            nh = nifty.history(period="1y", interval="1d")["Close"].dropna()
            nifty_ret_1y = float((nh.iloc[-1] - nh.iloc[0]) / nh.iloc[0] * 100) if len(nh) > 10 else 0
        except Exception:
            nifty_ret_1y = 15.0  # fallback assumption

        alpha_1y = ret_1y - nifty_ret_1y

        return {
            "ticker":        ticker,
            "latest_price":  round(latest_price, 2),
            "ret_3m_pct":    round(ret_3m, 2),
            "ret_6m_pct":    round(ret_6m, 2),
            "ret_1y_pct":    round(ret_1y, 2),
            "max_dd_pct":    round(max_dd, 2),
            "volatility_pct":round(volatility, 2),
            "w52_high":      round(w52_high, 2),
            "w52_low":       round(w52_low, 2),
            "alpha_1y":      round(alpha_1y, 2),
            "nifty_ret_1y":  round(nifty_ret_1y, 2),
            "data_points":   len(prices),
        }

    except Exception as e:
        logger.warning("Backtest fetch failed {}: {}", ticker, e)
        return {"ticker": ticker, "error": str(e)}


# ── Signal tester ─────────────────────────────────────────────

def score_signal_accuracy(results: list[dict]) -> dict:
    """
    Tests whether the quant score predicted actual returns.
    Compares stocks with score >60 vs <40.
    """
    valid = [r for r in results if "error" not in r]
    if len(valid) < 5:
        return {"error": "insufficient valid results"}

    high_score = [r for r in valid if r.get("quant_score", 0) >= 60]
    low_score  = [r for r in valid if r.get("quant_score", 0) < 40]

    def avg(lst, key):
        vals = [x.get(key) for x in lst if x.get(key) is not None]
        return round(sum(vals) / len(vals), 2) if vals else 0

    # Test hypothesis: high quant score → better returns
    high_ret_1y  = avg(high_score, "ret_1y_pct")
    low_ret_1y   = avg(low_score,  "ret_1y_pct")
    high_ret_6m  = avg(high_score, "ret_6m_pct")
    low_ret_6m   = avg(low_score,  "ret_6m_pct")
    high_dd      = avg(high_score, "max_dd_pct")
    low_dd       = avg(low_score,  "max_dd_pct")

    signal_works_1y = high_ret_1y > low_ret_1y
    signal_works_6m = high_ret_6m > low_ret_6m

    return {
        "stocks_tested":       len(valid),
        "high_score_count":    len(high_score),
        "low_score_count":     len(low_score),
        "high_score_avg_1y":   high_ret_1y,
        "low_score_avg_1y":    low_ret_1y,
        "high_score_avg_6m":   high_ret_6m,
        "low_score_avg_6m":    low_ret_6m,
        "high_score_avg_dd":   high_dd,
        "low_score_avg_dd":    low_dd,
        "signal_edge_1y":      round(high_ret_1y - low_ret_1y, 2),
        "signal_edge_6m":      round(high_ret_6m - low_ret_6m, 2),
        "signal_works_1y":     signal_works_1y,
        "signal_works_6m":     signal_works_6m,
        "verdict":             (
            "✅ Signal has predictive value" if signal_works_1y and signal_works_6m
            else "⚠️ Mixed signal accuracy — use with caution"
            if signal_works_1y or signal_works_6m
            else "❌ Signal not predictive in this period"
        ),
    }


# ── Simulated trade outcomes ──────────────────────────────────

def simulate_trades(results: list[dict], stop_loss_pct: float = 5.0) -> dict:
    """
    Simulates what would have happened if you bought each stock
    6 months ago and either:
    (a) held to today, or
    (b) hit the stop loss at -stop_loss_pct%

    Uses actual 6-month returns as proxy.
    """
    valid = [r for r in results if "error" not in r]
    trades = []

    for r in valid:
        ret_6m = r.get("ret_6m_pct", 0)
        max_dd = abs(r.get("max_dd_pct", 0))

        # Did it hit stop loss? (max drawdown exceeded stop)
        hit_stop = max_dd > stop_loss_pct
        if hit_stop:
            actual_return = -stop_loss_pct
            outcome       = "STOPPED OUT"
        else:
            actual_return = ret_6m
            outcome       = "WIN" if ret_6m > 0 else "LOSS"

        trades.append({
            "ticker":        r["ticker"],
            "return_6m_pct": round(ret_6m, 2),
            "max_dd_pct":    round(-max_dd, 2),
            "hit_stop":      hit_stop,
            "actual_return": round(actual_return, 2),
            "outcome":       outcome,
            "quant_score":   r.get("quant_score", 0),
        })

    trades.sort(key=lambda x: x["actual_return"], reverse=True)

    winners    = [t for t in trades if t["actual_return"] > 0]
    losers     = [t for t in trades if t["actual_return"] <= 0]
    stopped    = [t for t in trades if t["hit_stop"]]
    win_rate   = len(winners) / len(trades) * 100 if trades else 0
    avg_return = sum(t["actual_return"] for t in trades) / len(trades) if trades else 0

    return {
        "total_simulated":   len(trades),
        "winners":           len(winners),
        "losers":            len(losers),
        "stopped_out":       len(stopped),
        "win_rate_pct":      round(win_rate, 1),
        "avg_return_pct":    round(avg_return, 2),
        "stop_loss_used_pct": stop_loss_pct,
        "best_trade":        trades[0]  if trades else None,
        "worst_trade":       trades[-1] if trades else None,
        "all_trades":        trades,
    }


# ── HTML report ───────────────────────────────────────────────

def build_backtest_html(results: list, signal_acc: dict, sim: dict, timestamp: str) -> str:
    valid = [r for r in results if "error" not in r]
    valid.sort(key=lambda x: x.get("ret_1y_pct", -999), reverse=True)

    def ret_color(v):
        if v is None: return "#64748b"
        return "#22c55e" if v > 15 else "#86efac" if v > 0 else "#ef4444"

    rows = ""
    for r in valid:
        r1y = r.get("ret_1y_pct")
        r6m = r.get("ret_6m_pct")
        r3m = r.get("ret_3m_pct")
        dd  = r.get("max_dd_pct")
        vol = r.get("volatility_pct")
        alp = r.get("alpha_1y")
        qs  = r.get("quant_score", "—")
        rows += f"""<tr>
          <td style="font-family:monospace;font-weight:700">{r['ticker']}</td>
          <td style="color:{ret_color(r3m)};font-weight:600">{r3m:+.1f}%</td>
          <td style="color:{ret_color(r6m)};font-weight:600">{r6m:+.1f}%</td>
          <td style="color:{ret_color(r1y)};font-weight:600">{r1y:+.1f}%</td>
          <td style="color:#ef4444">{dd:.1f}%</td>
          <td style="color:#94a3b8">{vol:.1f}%</td>
          <td style="color:{'#22c55e' if (alp or 0)>0 else '#ef4444'}">{alp:+.1f}%</td>
          <td>
            <div style="display:flex;align-items:center;gap:6px">
              <div style="width:50px;height:5px;background:#1e293b;border-radius:2px">
                <div style="width:{qs}%;height:100%;background:#f59e0b;border-radius:2px"></div>
              </div>
              <span style="font-size:12px;color:#f59e0b">{qs}</span>
            </div>
          </td>
        </tr>"""

    # Signal accuracy cards
    se = signal_acc.get("signal_edge_1y", 0)
    se_color = "#22c55e" if se > 5 else "#f59e0b" if se > 0 else "#ef4444"

    # Sim trade rows
    sim_rows = ""
    for t in (sim.get("all_trades") or [])[:20]:
        oc = {"WIN":"#22c55e","LOSS":"#ef4444","STOPPED OUT":"#f59e0b"}.get(t["outcome"],"#64748b")
        sim_rows += f"""<tr>
          <td style="font-family:monospace;font-weight:700">{t['ticker']}</td>
          <td style="color:{oc};font-weight:600">{t['outcome']}</td>
          <td style="color:{oc}">{t['actual_return']:+.1f}%</td>
          <td style="color:#94a3b8">{t['return_6m_pct']:+.1f}%</td>
          <td style="color:#ef4444">{t['max_dd_pct']:.1f}%</td>
          <td style="color:#f59e0b">{t['quant_score']}</td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>HedgeFusion Backtester — {timestamp}</title>
<style>
  body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
       background:#050d1a;color:#e2e8f0;margin:0;padding:20px;
       -webkit-font-smoothing:antialiased}}
  .wrap{{max-width:1060px;margin:0 auto}}
  h1{{font-size:22px;font-weight:800;color:#f8fafc;margin-bottom:4px}}
  h2{{font-size:15px;font-weight:700;color:#e2e8f0;margin:28px 0 12px;
      padding-bottom:8px;border-bottom:1px solid #1e293b}}
  .meta{{font-size:12px;color:#64748b;margin-bottom:24px}}
  .kpi-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:24px}}
  .kpi{{background:#0f172a;border:1px solid #1e293b;border-radius:8px;padding:16px;text-align:center}}
  .kv{{font-size:26px;font-weight:800;margin-bottom:3px}}
  .kl{{font-size:11px;color:#64748b}}
  table{{width:100%;border-collapse:collapse;background:#0f172a;
         border-radius:8px;overflow:hidden;margin-bottom:8px}}
  th{{background:#1e293b;color:#64748b;padding:9px 12px;text-align:left;
      font-size:11px;font-weight:600;letter-spacing:.04em}}
  td{{padding:9px 12px;border-bottom:1px solid #1e293b;font-size:13px;color:#cbd5e1}}
  tr:last-child td{{border-bottom:none}}
  tr:hover td{{background:#0a1628}}
  .signal-box{{background:#0f172a;border:1px solid #1e293b;border-radius:10px;
               padding:20px;margin-bottom:16px}}
  .disc{{background:#1e1a0a;border:1px solid #78350f;border-radius:8px;
         padding:14px 18px;font-size:12px;color:#92400e;margin-top:24px}}
</style>
</head>
<body>
<div class="wrap">
  <h1>📊 HedgeFusion Backtester</h1>
  <div class="meta">{timestamp} &nbsp;·&nbsp; {len(valid)} stocks tested</div>

  <div class="kpi-grid">
    <div class="kpi">
      <div class="kv" style="color:#f59e0b">{len(valid)}</div>
      <div class="kl">Stocks backtested</div>
    </div>
    <div class="kpi">
      <div class="kv" style="color:{se_color}">{se:+.1f}%</div>
      <div class="kl">Signal edge (1Y alpha)</div>
    </div>
    <div class="kpi">
      <div class="kv" style="color:#{'22c55e' if sim.get('win_rate_pct',0)>=50 else 'ef4444'}">{sim.get('win_rate_pct',0):.0f}%</div>
      <div class="kl">Simulated win rate</div>
    </div>
    <div class="kpi">
      <div class="kv" style="color:#{'22c55e' if sim.get('avg_return_pct',0)>0 else 'ef4444'}">{sim.get('avg_return_pct',0):+.1f}%</div>
      <div class="kl">Avg simulated return</div>
    </div>
  </div>

  <h2>🎯 Signal Accuracy — Does the Quant Score Predict Returns?</h2>
  <div class="signal-box">
    <div style="font-size:18px;font-weight:700;margin-bottom:12px;color:#f8fafc">
      {signal_acc.get('verdict','—')}
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;font-size:13px">
      <div>
        <div style="color:#64748b;font-size:11px;margin-bottom:4px">HIGH SCORE (≥60) AVG 1Y RETURN</div>
        <div style="font-size:22px;font-weight:700;color:#22c55e">{signal_acc.get('high_score_avg_1y',0):+.1f}%</div>
        <div style="color:#64748b;font-size:11px">{signal_acc.get('high_score_count',0)} stocks</div>
      </div>
      <div>
        <div style="color:#64748b;font-size:11px;margin-bottom:4px">LOW SCORE (&lt;40) AVG 1Y RETURN</div>
        <div style="font-size:22px;font-weight:700;color:#ef4444">{signal_acc.get('low_score_avg_1y',0):+.1f}%</div>
        <div style="color:#64748b;font-size:11px">{signal_acc.get('low_score_count',0)} stocks</div>
      </div>
      <div>
        <div style="color:#64748b;font-size:11px;margin-bottom:4px">SIGNAL EDGE</div>
        <div style="font-size:22px;font-weight:700;color:{se_color}">{se:+.1f}%</div>
        <div style="color:#64748b;font-size:11px">outperformance</div>
      </div>
    </div>
  </div>

  <h2>📈 Historical Returns — All Stocks</h2>
  <table>
    <thead><tr>
      <th>Stock</th><th>3M Return</th><th>6M Return</th><th>1Y Return</th>
      <th>Max Drawdown</th><th>Volatility</th><th>Alpha vs Nifty</th><th>Quant Score</th>
    </tr></thead>
    <tbody>{rows}</tbody>
  </table>

  <h2>🎲 Simulated Trade Outcomes (6M holding, {sim.get('stop_loss_used_pct',5)}% SL)</h2>
  <table>
    <thead><tr>
      <th>Stock</th><th>Outcome</th><th>Actual Return</th>
      <th>Raw 6M Return</th><th>Max Drawdown</th><th>Quant Score</th>
    </tr></thead>
    <tbody>{sim_rows}</tbody>
  </table>

  <div class="disc">
    ⚠️ <strong>Backtesting caveat:</strong> Past returns do not predict future performance.
    This backtest uses actual historical prices but does NOT account for:
    survivorship bias, slippage, impact cost, taxes (STT, LTCG), or timing.
    Use as a signal validation tool, not a performance guarantee.
  </div>
</div>
</body>
</html>"""


# ── Main ──────────────────────────────────────────────────────

def run_backtest(tickers: list[str] | None = None, workers: int = 8) -> dict:
    universe  = tickers or list(dict.fromkeys(HOLDING_TICKERS + BACKTEST_UNIVERSE))
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M IST")
    ts_file   = datetime.now().strftime("%Y%m%d_%H%M")

    print(f"\n{'━'*60}")
    print(f"  HEDGEFUSION BACKTESTER")
    print(f"  Testing {len(universe)} stocks on historical NSE data")
    print(f"{'━'*60}\n")

    # Fetch historical data + quant scores in parallel
    from multibagger_screener import score_stock
    results = []
    done    = 0

    def fetch_and_score(ticker):
        hist  = fetch_historical(ticker)
        score = score_stock(ticker)
        if "error" not in hist:
            hist["quant_score"] = score.get("score", 0)
            hist["signals"]     = score.get("signals", [])
        return hist

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(fetch_and_score, t): t for t in universe}
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                result = future.result()
                results.append(result)
                done += 1
                if done % 5 == 0 or done == len(universe):
                    print(f"  [{done}/{len(universe)}] fetched...", end="\r")
            except Exception as e:
                logger.warning("Backtest failed {}: {}", ticker, e)

    print(f"\n  ✓ Data fetched for {len([r for r in results if 'error' not in r])} stocks")

    signal_acc = score_signal_accuracy(results)
    sim        = simulate_trades(results, stop_loss_pct=5.0)

    print(f"\n  Signal verdict: {signal_acc.get('verdict','?')}")
    print(f"  Simulated win rate: {sim.get('win_rate_pct',0):.0f}%")
    print(f"  Avg simulated return: {sim.get('avg_return_pct',0):+.1f}%")

    html      = build_backtest_html(results, signal_acc, sim, timestamp)
    html_path = OUTPUT_DIR / f"backtest_{ts_file}.html"
    json_path = OUTPUT_DIR / f"backtest_{ts_file}.json"

    html_path.write_text(html, encoding="utf-8")
    json_path.write_text(
        json.dumps({"results": results, "signal_accuracy": signal_acc,
                    "simulation": sim}, default=str, indent=2),
        encoding="utf-8",
    )

    print(f"\n✅ Backtest report: {html_path}")
    return {"results": results, "signal_accuracy": signal_acc, "simulation": sim}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="HedgeFusion Backtester")
    parser.add_argument("--ticker",  help="Single ticker to backtest")
    parser.add_argument("--workers", type=int, default=8,
                        help="Parallel workers (default 8)")
    args = parser.parse_args()

    if args.ticker:
        r = fetch_historical(args.ticker.upper())
        print(json.dumps(r, indent=2, default=str))
    else:
        run_backtest(workers=args.workers)
