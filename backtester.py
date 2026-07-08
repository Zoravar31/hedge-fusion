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
from typing import Dict, List, Optional

import yfinance as yf
from dotenv import load_dotenv
from loguru import logger

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

def simulate_trades(results: list[dict], stop_loss_pct: float = 5.0,
                    avg_trade_value_inr: float = 50_000) -> dict:
    """
    Simulates what would have happened if you bought each stock
    6 months ago and either:
    (a) held to today, or
    (b) hit the stop loss at -stop_loss_pct%

    Uses actual 6-month returns as proxy.
    Now includes real transaction cost modelling (Feature #15).
    """
    from transaction_costs import TradeCostModel, cost_adjusted_return
    cost_model = TradeCostModel()

    valid = [r for r in results if "error" not in r]
    trades = []

    for r in valid:
        ret_6m = r.get("ret_6m_pct", 0)
        max_dd = abs(r.get("max_dd_pct", 0))

        # Did it hit stop loss? (max drawdown exceeded stop)
        hit_stop = max_dd > stop_loss_pct
        if hit_stop:
            gross_return = -stop_loss_pct
            outcome       = "STOPPED OUT"
        else:
            gross_return = ret_6m
            outcome       = "WIN" if ret_6m > 0 else "LOSS"

        # Apply transaction costs
        net_return = cost_adjusted_return(gross_return, avg_trade_value_inr, r["ticker"])
        cost_drag  = round(gross_return - net_return, 4)
        net_outcome = "WIN" if net_return > 0 else "LOSS"

        trades.append({
            "ticker":           r["ticker"],
            "return_6m_pct":    round(ret_6m, 2),
            "max_dd_pct":       round(-max_dd, 2),
            "hit_stop":         hit_stop,
            "gross_return":     round(gross_return, 2),
            "cost_drag_pct":    round(cost_drag, 4),
            "actual_return":    round(net_return, 2),   # net of costs
            "outcome":          net_outcome,
            "quant_score":      r.get("quant_score", 0),
        })

    trades.sort(key=lambda x: x["actual_return"], reverse=True)

    winners    = [t for t in trades if t["actual_return"] > 0]
    losers     = [t for t in trades if t["actual_return"] <= 0]
    stopped    = [t for t in trades if t["hit_stop"]]
    win_rate   = len(winners) / len(trades) * 100 if trades else 0
    avg_return = sum(t["actual_return"] for t in trades) / len(trades) if trades else 0
    avg_cost   = sum(t["cost_drag_pct"]  for t in trades) / len(trades) if trades else 0

    # Monthly cost estimate (10 trades)
    cost_monthly_est = cost_model.monthly_cost_estimate(10, avg_trade_value_inr)

    return {
        "total_simulated":    len(trades),
        "winners":            len(winners),
        "losers":             len(losers),
        "stopped_out":        len(stopped),
        "win_rate_pct":       round(win_rate, 1),
        "avg_return_pct":     round(avg_return, 2),     # net of costs
        "avg_cost_drag_pct":  round(avg_cost, 4),
        "stop_loss_used_pct": stop_loss_pct,
        "avg_trade_value_inr":avg_trade_value_inr,
        "monthly_cost_est":   cost_monthly_est,
        "best_trade":         trades[0]  if trades else None,
        "worst_trade":        trades[-1] if trades else None,
        "all_trades":         trades,
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
    universe  = tickers or BACKTEST_UNIVERSE
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


# ─────────────────────────────────────────────────────────────
# Feature #14 — Walk-Forward Backtesting
# ─────────────────────────────────────────────────────────────
#
# Splits historical data into rolling train/test windows.
# Train:  2022-01-01 → 2023-12-31  (2 years)
# Test:   2024-01-01 → 2024-12-31  (1 year, out-of-sample)
#
# For each window:
#   1. Compute signal weights on TRAIN data
#      (which signals correlate with forward returns in that period)
#   2. Apply those weights to TEST data, measure actual P&L
#
# This tells you: does the quant signal generalise, or did it
# just overfit 2022–2024?
#
# Walk-forward prevents look-ahead bias by never letting the
# test window see the training data.
# ─────────────────────────────────────────────────────────────

import pandas as pd


def _fetch_full_history(ticker: str, years: int = 4) -> Optional["pd.DataFrame"]:
    """
    Fetch full OHLCV history for walk-forward analysis.
    Returns a pandas DataFrame indexed by date, or None on failure.
    """
    symbol = ticker.strip().upper()
    if not symbol.endswith(".NS"):
        symbol += ".NS"
    try:
        t    = yf.Ticker(symbol)
        hist = t.history(period=f"{years}y", interval="1d")
        if hist is None or hist.empty or len(hist) < 252:
            return None
        hist.index = hist.index.tz_localize(None)
        return hist[["Open", "High", "Low", "Close", "Volume"]].copy()
    except Exception as e:
        logger.warning("_fetch_full_history {} failed: {}", ticker, e)
        return None


def _compute_quant_signals(df: "pd.DataFrame") -> "pd.DataFrame":
    """
    Compute a set of rule-based quant signals on a price DataFrame.
    Returns the same DataFrame with signal columns added.

    Signals (all normalised to 0–1 range so they can be averaged):
      sig_momentum  : 3-month price return percentile vs 12-month window
      sig_trend     : price above 200-day SMA (1 if yes, 0 if no)
      sig_rsi       : RSI-14 in oversold zone (<40) = 1, overbought (>70) = 0
      sig_vol       : low 20-day realised vol (inverse — calmer = higher score)
      sig_breakout  : within 5% of 52-week high = 1 (momentum breakout)
    """
    close = df["Close"]

    # 1. Momentum: 3M return vs 12M rolling
    ret_3m = close.pct_change(63)
    ret_12m = close.pct_change(252)
    df["sig_momentum"] = (ret_3m - ret_12m.rolling(252).mean()) / (ret_12m.rolling(252).std() + 1e-9)
    df["sig_momentum"] = df["sig_momentum"].clip(-3, 3).add(3).div(6)  # → 0–1

    # 2. Trend: price > 200-DMA
    sma200 = close.rolling(200).mean()
    df["sig_trend"] = (close > sma200).astype(float)

    # 3. RSI-14
    delta   = close.diff()
    gain    = delta.clip(lower=0).rolling(14).mean()
    loss    = (-delta.clip(upper=0)).rolling(14).mean()
    rs      = gain / (loss + 1e-9)
    rsi     = 100 - (100 / (1 + rs))
    # Score: RSI 0–40 maps to 1.0–0.5, RSI 40–70 maps to 0.5–0.0, RSI 70–100 → 0
    df["sig_rsi"] = ((70 - rsi.clip(0, 70)) / 70).clip(0, 1)

    # 4. Low volatility (calmer = better entry)
    vol20 = close.pct_change().rolling(20).std() * (252 ** 0.5)
    df["sig_vol"] = (1 - vol20.clip(0, 0.8) / 0.8).clip(0, 1)

    # 5. Breakout: within 5% of 52-week high
    hi52 = close.rolling(252).max()
    df["sig_breakout"] = ((close / (hi52 + 1e-9)) >= 0.95).astype(float)

    return df


def _compute_signal_weights(train_df: "pd.DataFrame") -> Dict[str, float]:
    """
    Compute per-signal correlation with 3-month forward returns on the
    TRAINING window. Returns {signal_name: weight} normalised to sum=1.

    Signals with negative correlation get weight 0 (we don't invert them).
    """
    signal_cols = ["sig_momentum", "sig_trend", "sig_rsi", "sig_vol", "sig_breakout"]
    df = train_df.copy()

    # Forward 3-month return (target variable)
    df["fwd_ret_3m"] = df["Close"].pct_change(63).shift(-63)
    df = df.dropna(subset=["fwd_ret_3m"] + signal_cols)

    if len(df) < 50:
        # Not enough data — equal weights
        n = len(signal_cols)
        return {s: 1.0 / n for s in signal_cols}

    weights = {}
    for sig in signal_cols:
        corr = df[sig].corr(df["fwd_ret_3m"])
        weights[sig] = max(corr, 0.0)  # negative correlation → 0

    total = sum(weights.values())
    if total < 1e-9:
        # All signals negative — equal weights as fallback
        n = len(signal_cols)
        return {s: 1.0 / n for s in signal_cols}

    return {s: w / total for s, w in weights.items()}


def _apply_weights_to_test(
    test_df: "pd.DataFrame",
    weights: Dict[str, float],
    stop_loss_pct: float = 5.0,
    avg_trade_value_inr: float = 50_000,
) -> dict:
    """
    Apply learned weights to TEST window.
    Simulates: buy when composite score > 0.6, hold 3M or stop.
    Returns trade-level P&L stats, net of transaction costs.
    """
    from transaction_costs import cost_adjusted_return

    signal_cols = list(weights.keys())
    df = test_df.copy()

    # Composite score
    df["score"] = sum(df[sig] * weights[sig] for sig in signal_cols)

    # Entry points: score > 0.6 on a given day (weekly resample to avoid over-trading)
    entries = df["score"].resample("W").max()
    entry_dates = entries[entries > 0.6].index

    trades = []
    prices = df["Close"]

    for entry_date in entry_dates:
        try:
            idx = prices.index.get_indexer([entry_date], method="nearest")[0]
        except Exception:
            continue
        if idx < 0 or idx >= len(prices) - 1:
            continue

        entry_price = float(prices.iloc[idx])
        if entry_price <= 0:
            continue

        # Hold for 63 trading days (≈3 months) or stop-loss
        exit_idx    = min(idx + 63, len(prices) - 1)
        stop_price  = entry_price * (1 - stop_loss_pct / 100)
        actual_exit = None
        exit_reason = "HELD"

        for j in range(idx + 1, exit_idx + 1):
            p = float(prices.iloc[j])
            if p <= stop_price:
                actual_exit = p
                exit_reason = "STOP"
                break

        if actual_exit is None:
            actual_exit = float(prices.iloc[exit_idx])

        gross_pct = (actual_exit - entry_price) / entry_price * 100
        net_pct   = cost_adjusted_return(gross_pct, avg_trade_value_inr)

        trades.append({
            "entry_date":     str(prices.index[idx].date()),
            "entry_price":    round(entry_price, 2),
            "exit_price":     round(actual_exit, 2),
            "exit_reason":    exit_reason,
            "gross_return_pct": round(gross_pct, 2),
            "cost_drag_pct":  round(gross_pct - net_pct, 4),
            "return_pct":     round(net_pct, 2),       # net of costs
            "outcome":        "WIN" if net_pct > 0 else "LOSS",
        })

    if not trades:
        return {"trades": [], "win_rate": 0, "avg_return": 0, "total_trades": 0}

    wins     = [t for t in trades if t["return_pct"] > 0]
    avg_ret  = sum(t["return_pct"] for t in trades) / len(trades)

    return {
        "trades":       trades,
        "total_trades": len(trades),
        "wins":         len(wins),
        "losses":       len(trades) - len(wins),
        "win_rate":     round(len(wins) / len(trades) * 100, 1),
        "avg_return":   round(avg_ret, 2),
        "best":         max(t["return_pct"] for t in trades),
        "worst":        min(t["return_pct"] for t in trades),
    }


def run_walkforward_backtest(
    tickers:        Optional[List[str]] = None,
    train_start:    str = "2022-01-01",
    train_end:      str = "2023-12-31",
    test_start:     str = "2024-01-01",
    test_end:       str = "2025-12-31",
    stop_loss_pct:  float = 5.0,
    workers:        int = 6,
) -> dict:
    """
    Walk-forward backtest across the BACKTEST_UNIVERSE (or a custom list).

    Default windows:
      TRAIN: 2022–2023   (2 years — learn signal weights)
      TEST:  2024–2025   (1 year — out-of-sample)

    For each ticker:
      1. Compute quant signals (momentum, trend, RSI, vol, breakout)
      2. Train: correlate each signal with actual 3M forward returns
      3. Test:  use learned weights to score daily; buy on score > 0.6
      4. Simulate trades with stop-loss; record P&L

    Aggregates across all tickers → win rate, avg return, signal importance.
    Saves results to outputs/walkforward_YYYYMMDD.json + .html
    """
    universe  = tickers or BACKTEST_UNIVERSE
    ts_file   = datetime.now().strftime("%Y%m%d_%H%M")
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M IST")

    print(f"\n{'━'*60}")
    print(f"  HEDGEFUSION WALK-FORWARD BACKTESTER")
    print(f"  Train: {train_start} → {train_end}")
    print(f"  Test:  {test_start} → {test_end}")
    print(f"  Universe: {len(universe)} stocks | SL: {stop_loss_pct}%")
    print(f"{'━'*60}\n")

    all_results = []

    def process_ticker(ticker):
        df = _fetch_full_history(ticker, years=5)
        if df is None:
            return {"ticker": ticker, "error": "insufficient data"}

        df = _compute_quant_signals(df)

        # Slice train / test windows
        train_df = df.loc[train_start:train_end]
        test_df  = df.loc[test_start:test_end]

        if len(train_df) < 100:
            return {"ticker": ticker, "error": f"train window too short ({len(train_df)} rows)"}
        if len(test_df) < 50:
            return {"ticker": ticker, "error": f"test window too short ({len(test_df)} rows)"}

        weights  = _compute_signal_weights(train_df)
        test_out = _apply_weights_to_test(test_df, weights, stop_loss_pct)

        # Buy-and-hold benchmark for the test period
        bh_ret = 0.0
        try:
            bh_ret = float(
                (test_df["Close"].iloc[-1] - test_df["Close"].iloc[0])
                / test_df["Close"].iloc[0] * 100
            )
        except Exception:
            pass

        return {
            "ticker":           ticker,
            "signal_weights":   {k: round(v, 4) for k, v in weights.items()},
            "train_rows":       len(train_df),
            "test_rows":        len(test_df),
            "bh_return_pct":    round(bh_ret, 2),
            **test_out,
        }

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(process_ticker, t): t for t in universe}
        done = 0
        for future in as_completed(futs):
            t = futs[future]
            try:
                r = future.result()
                all_results.append(r)
            except Exception as e:
                all_results.append({"ticker": t, "error": str(e)})
            done += 1
            if done % 5 == 0 or done == len(universe):
                print(f"  [{done}/{len(universe)}] processed...", end="\r")

    print(f"\n  ✓ Walk-forward complete for {len(all_results)} stocks")

    # Aggregate stats
    valid   = [r for r in all_results if "error" not in r and r.get("total_trades", 0) > 0]
    overall_win_rate  = (
        sum(r["win_rate"] for r in valid) / len(valid) if valid else 0
    )
    overall_avg_ret   = (
        sum(r["avg_return"] for r in valid) / len(valid) if valid else 0
    )
    overall_bh        = (
        sum(r["bh_return_pct"] for r in valid) / len(valid) if valid else 0
    )
    # Average signal importance across all tickers
    avg_weights: Dict[str, float] = {}
    for r in valid:
        for sig, w in (r.get("signal_weights") or {}).items():
            avg_weights[sig] = avg_weights.get(sig, 0) + w
    if valid:
        avg_weights = {k: round(v / len(valid), 4) for k, v in avg_weights.items()}

    summary = {
        "train_window": f"{train_start} → {train_end}",
        "test_window":  f"{test_start} → {test_end}",
        "stop_loss_pct": stop_loss_pct,
        "stocks_tested": len(universe),
        "stocks_valid":  len(valid),
        "overall_win_rate_pct": round(overall_win_rate, 1),
        "overall_avg_return_pct": round(overall_avg_ret, 2),
        "buy_hold_avg_pct": round(overall_bh, 2),
        "alpha_vs_bh": round(overall_avg_ret - overall_bh, 2),
        "avg_signal_weights": avg_weights,
        "verdict": (
            "✅ Walk-forward signal generalises (alpha > 0)"
            if overall_avg_ret > overall_bh
            else "⚠️ Signal underperforms buy-and-hold in test window"
        ),
    }

    print(f"\n  Win rate:    {overall_win_rate:.1f}%")
    print(f"  Avg return:  {overall_avg_ret:+.2f}%")
    print(f"  Buy & hold:  {overall_bh:+.2f}%")
    print(f"  Alpha:       {summary['alpha_vs_bh']:+.2f}%")
    print(f"\n  {summary['verdict']}")

    # Build minimal HTML report
    html = _build_walkforward_html(all_results, summary, timestamp)
    html_path = OUTPUT_DIR / f"walkforward_{ts_file}.html"
    json_path = OUTPUT_DIR / f"walkforward_{ts_file}.json"
    html_path.write_text(html, encoding="utf-8")
    json_path.write_text(
        json.dumps({"summary": summary, "results": all_results}, default=str, indent=2),
        encoding="utf-8",
    )
    print(f"\n✅ Walk-forward report: {html_path}")
    return {"summary": summary, "results": all_results}


def _build_walkforward_html(results: list, summary: dict, timestamp: str) -> str:
    valid  = [r for r in results if "error" not in r and r.get("total_trades", 0) > 0]
    errors = [r for r in results if "error" in r]
    valid.sort(key=lambda x: x.get("avg_return", -999), reverse=True)

    # Signal weight bar
    sig_labels = {
        "sig_momentum": "Momentum",
        "sig_trend":    "Trend (200D)",
        "sig_rsi":      "RSI Oversold",
        "sig_vol":      "Low Volatility",
        "sig_breakout": "52W Breakout",
    }
    weight_bars = ""
    for sig, label in sig_labels.items():
        w   = summary.get("avg_signal_weights", {}).get(sig, 0)
        pct = int(w * 100)
        col = "#22c55e" if w > 0.25 else "#f59e0b" if w > 0.15 else "#64748b"
        weight_bars += f"""
        <div style="margin-bottom:10px">
          <div style="display:flex;justify-content:space-between;font-size:12px;margin-bottom:3px">
            <span style="color:#cbd5e1">{label}</span>
            <span style="color:{col};font-weight:700">{pct}%</span>
          </div>
          <div style="height:6px;background:#1e293b;border-radius:3px">
            <div style="width:{pct}%;height:100%;background:{col};border-radius:3px"></div>
          </div>
        </div>"""

    rows = ""
    for r in valid:
        wr_col  = "#22c55e" if r.get("win_rate", 0) >= 55 else "#f59e0b" if r.get("win_rate", 0) >= 45 else "#ef4444"
        ar_col  = "#22c55e" if r.get("avg_return", 0) >= 0 else "#ef4444"
        bh_col  = "#22c55e" if r.get("bh_return_pct", 0) >= 0 else "#ef4444"
        alp     = r.get("avg_return", 0) - r.get("bh_return_pct", 0)
        alp_col = "#22c55e" if alp > 0 else "#ef4444"
        rows += f"""<tr>
          <td style="font-family:monospace;font-weight:700">{r['ticker']}</td>
          <td style="color:#64748b">{r.get('total_trades',0)}</td>
          <td style="color:{wr_col};font-weight:600">{r.get('win_rate',0):.0f}%</td>
          <td style="color:{ar_col};font-weight:600">{r.get('avg_return',0):+.1f}%</td>
          <td style="color:{bh_col}">{r.get('bh_return_pct',0):+.1f}%</td>
          <td style="color:{alp_col};font-weight:600">{alp:+.1f}%</td>
          <td style="color:#22c55e">{r.get('best',0):+.1f}%</td>
          <td style="color:#ef4444">{r.get('worst',0):+.1f}%</td>
        </tr>"""

    alpha_col = "#22c55e" if summary.get("alpha_vs_bh", 0) > 0 else "#ef4444"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><title>Walk-Forward Backtester — {timestamp}</title>
<style>
  body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
       background:#050d1a;color:#e2e8f0;margin:0;padding:20px;-webkit-font-smoothing:antialiased}}
  .wrap{{max-width:1060px;margin:0 auto}}
  h1{{font-size:22px;font-weight:800;color:#f8fafc;margin-bottom:4px}}
  h2{{font-size:15px;font-weight:700;color:#e2e8f0;margin:28px 0 12px;
      padding-bottom:8px;border-bottom:1px solid #1e293b}}
  .meta{{font-size:12px;color:#64748b;margin-bottom:24px}}
  .kpi-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:24px}}
  .kpi{{background:#0f172a;border:1px solid #1e293b;border-radius:8px;padding:16px;text-align:center}}
  .kv{{font-size:26px;font-weight:800;margin-bottom:3px}}
  .kl{{font-size:11px;color:#64748b}}
  .two-col{{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:24px}}
  .card{{background:#0f172a;border:1px solid #1e293b;border-radius:8px;padding:18px}}
  .card h3{{font-size:13px;font-weight:700;color:#94a3b8;margin:0 0 14px 0;
            text-transform:uppercase;letter-spacing:.04em}}
  table{{width:100%;border-collapse:collapse;background:#0f172a;
         border-radius:8px;overflow:hidden;margin-bottom:8px}}
  th{{background:#1e293b;color:#64748b;padding:9px 12px;text-align:left;
      font-size:11px;font-weight:600;letter-spacing:.04em}}
  td{{padding:9px 12px;border-bottom:1px solid #1e293b;font-size:13px;color:#cbd5e1}}
  tr:last-child td{{border-bottom:none}}
  tr:hover td{{background:#0a1628}}
  .verdict{{font-size:18px;font-weight:700;margin-bottom:8px;color:#f8fafc}}
</style>
</head>
<body><div class="wrap">
  <h1>🔄 Walk-Forward Backtester</h1>
  <div class="meta">
    {timestamp} &nbsp;·&nbsp;
    Train: {summary['train_window']} &nbsp;·&nbsp;
    Test: {summary['test_window']} &nbsp;·&nbsp;
    SL: {summary['stop_loss_pct']}%
  </div>

  <div class="kpi-grid">
    <div class="kpi">
      <div class="kv" style="color:#f59e0b">{summary['stocks_valid']}</div>
      <div class="kl">Stocks with signals</div>
    </div>
    <div class="kpi">
      <div class="kv" style="color:{'#22c55e' if summary['overall_win_rate_pct']>=55 else '#ef4444'}">{summary['overall_win_rate_pct']:.0f}%</div>
      <div class="kl">Out-of-sample win rate</div>
    </div>
    <div class="kpi">
      <div class="kv" style="color:{'#22c55e' if summary['overall_avg_return_pct']>=0 else '#ef4444'}">{summary['overall_avg_return_pct']:+.1f}%</div>
      <div class="kl">Avg trade return (test)</div>
    </div>
    <div class="kpi">
      <div class="kv" style="color:{alpha_col}">{summary['alpha_vs_bh']:+.1f}%</div>
      <div class="kl">Alpha vs buy-and-hold</div>
    </div>
  </div>

  <div class="two-col">
    <div class="card">
      <h3>Signal importance (avg across universe)</h3>
      {weight_bars}
      <div style="font-size:11px;color:#475569;margin-top:8px">
        Weights learned on {summary['train_window']} training data.
        Higher = signal correlated more with 3M forward returns.
      </div>
    </div>
    <div class="card">
      <h3>Verdict</h3>
      <div class="verdict">{summary['verdict']}</div>
      <div style="font-size:13px;color:#64748b;line-height:1.6">
        <b>Avg trade return (test):</b> {summary['overall_avg_return_pct']:+.1f}%<br>
        <b>Buy-and-hold benchmark:</b> {summary['buy_hold_avg_pct']:+.1f}%<br>
        <b>Alpha vs buy-and-hold:</b> <span style="color:{alpha_col}">{summary['alpha_vs_bh']:+.1f}%</span><br>
        <b>Stocks with positive alpha:</b> {sum(1 for r in valid if r.get('avg_return',0) > r.get('bh_return_pct',0))} / {len(valid)}<br>
      </div>
    </div>
  </div>

  <h2>📈 Per-Stock Results (out-of-sample test window)</h2>
  <table>
    <thead><tr>
      <th>Stock</th><th>Trades</th><th>Win Rate</th>
      <th>Avg Return</th><th>Buy &amp; Hold</th><th>Alpha</th>
      <th>Best Trade</th><th>Worst Trade</th>
    </tr></thead>
    <tbody>{rows}</tbody>
  </table>

  <div style="background:#1e1a0a;border:1px solid #78350f;border-radius:8px;
              padding:14px 18px;font-size:12px;color:#92400e;margin-top:24px">
    ⚠️ Walk-forward results are out-of-sample (test window never seen during training).
    However, they still carry survivorship bias (only liquid stocks) and do not account
    for slippage, STT, brokerage costs, or market impact. See walkforward_*.json for raw trades.
  </div>
</div></body></html>"""


