# 🇮🇳 HedgeFusion

> 9-agent autonomous AI trading system for Indian equity markets (NSE/BSE)  
> Powered by OpenAI GPT-4o · Zerodha Kite Connect · Live NSE Data · FII/DII Intelligence

[![Python](https://img.shields.io/badge/Python-3.10+-blue?style=flat-square&logo=python)](https://python.org)
[![NSE](https://img.shields.io/badge/Exchange-NSE%20India-orange?style=flat-square)](https://nseindia.com)
[![Zerodha](https://img.shields.io/badge/Broker-Zerodha%20Kite-387ed1?style=flat-square)](https://kite.zerodha.com)
[![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)](LICENSE)

HedgeFusion is a complete autonomous trading research system built specifically for Indian equity markets. It combines a 9-agent AI pipeline with live NSE data, FII/DII institutional flow tracking, multibagger screening, risk management, and Zerodha order execution — all starting in paper mode so no real money is ever at risk until you're ready.

Architecturally inspired by [TradingAgents](https://github.com/TauricResearch/TradingAgents) and [AutoHedge](https://github.com/The-Swarm-Corporation/AutoHedge). Original implementation — no source code copied from either project.

---

## Architecture

```
                    ┌─────────────────────────────────────────┐
                    │           ANALYST TEAM (parallel)       │
                    │  Fundamentals │ Technical │ Sentiment   │
                    │              │ News                     │
                    │  + FII/DII flows + Shareholding pattern │
                    │  + Bulk/Block deals + NSE RSS feeds     │
                    └─────────────────┬───────────────────────┘
                                      ↓
                    ┌─────────────────────────────────────────┐
                    │         RESEARCH TEAM (debate)          │
                    │  Bull Researcher ←→ Bear Researcher     │
                    │         ↓ Research Manager              │
                    │      BUY/SELL/HOLD + PASS/BLOCK         │
                    └─────────────────┬───────────────────────┘
                                      ↓
                    ┌─────────────────────────────────────────┐
                    │          EXECUTION TEAM (gate)          │
                    │  Trader → Risk Manager → Portfolio Mgr  │
                    │          APPROVE or VETO                │
                    └─────────────────┬───────────────────────┘
                                      ↓
                               place_nse_order()
                         [Paper CSV log | Zerodha Kite]
```

**Design principles:**
- 4 analysts run in **parallel** → faster, same cost
- Bull vs Bear **debate** → catches one-sided calls
- Portfolio Manager **hard veto** → R:R < 1:2 never executes
- **FII/DII data** wired into every sentiment call
- **Paper mode by default** → zero real money until you flip the switch

---

## Quick Start

```bash
# 1. Clone
git clone https://github.com/Zoravar31/hedge-fusion.git
cd hedge-fusion

# 2. Environment (use Anaconda Prompt on Windows)
conda create -n hedge_fusion python=3.11 -y
conda activate hedge_fusion
pip install -r requirements.txt

# 3. Setup (checks everything, creates .env)
python setup.py

# 4. Add your OpenAI key to .env
# OPENAI_API_KEY=sk-proj-xxxxxxxx

# 5. Run
python hf.py                    # interactive menu
python hf.py run RELIANCE       # single stock
python hf.py portfolio          # all your holdings
```

---

## All Commands

```bash
# Single stock
python hf.py run RELIANCE             # analysis only
python hf.py run RELIANCE --execute   # analysis + paper trade

# Portfolio
python hf.py portfolio                # all holdings, no execution
python hf.py portfolio --execute      # all holdings + execute approved

# Multibagger screener
python hf.py screen                   # scan 80+ NSE stocks
python hf.py screen --sector DEFENCE  # one sector
python hf.py screen --ticker TITAN    # score one stock

# FII/DII Intelligence
python hf.py fii                      # full institutional flow dashboard
python hf.py fii --stock HDFCBANK     # one stock deep-dive
python hf.py fii --flows              # market flows only

# Watchlist
python hf.py watchlist                # scan for BUY ZONEs
python hf.py watchlist --add TITAN 3200
python hf.py watchlist --show

# Analysis tools
python hf.py risk                     # VaR, concentration, drawdown
python hf.py sector                   # sector rotation tracker
python hf.py earnings                 # upcoming results calendar
python hf.py backtest                 # backtest signals on history

# Tracking
python hf.py journal                  # trade journal and P&L
python hf.py status                   # system health check
python hf.py config                   # show current config

# Automation
python hf.py scheduler               # start daily 9:30 AM IST scheduler
python hf.py alerts --test           # test Telegram/email/WhatsApp
```

---

## All 24 Files

| File | Purpose |
|---|---|
| `hf.py` | Unified CLI — single entry point for everything |
| `config.py` | Single source of truth: holdings, watchlist, thresholds |
| `pipeline.py` | 9-agent orchestration engine |
| `main.py` | Alternative CLI with argparse |
| `scheduler.py` | Autonomous daily runner (9:30 AM IST) |
| `setup.py` | One-command installer and health checker |
| `portfolio_runner.py` | Full portfolio parallel pipeline |
| `multibagger_screener.py` | 80-stock NSE universe screener |
| `fii_dii_dashboard.py` | FII/DII institutional flow dashboard |
| `backtester.py` | Historical signal accuracy tester |
| `risk_dashboard.py` | VaR, concentration, drawdown monitor |
| `sector_rotation.py` | NSE sector momentum tracker |
| `earnings_calendar.py` | Upcoming results + pre-earnings briefs |
| `trade_journal.py` | Paper trade P&L and agent accuracy |
| `watchlist.py` | BUY ZONE scanner for target stocks |
| `alert_system.py` | Telegram / Email / WhatsApp alerts |
| `agents/prompts.py` | All 9 agent system prompts (India-tuned) |
| `agents/runner.py` | OpenAI function-calling engine |
| `tools/india_data.py` | NSE quotes, history, news RSS feeds |
| `tools/fii_dii.py` | FII/DII flows, bulk/block deals, shareholding |
| `tools/kite_execution.py` | Paper + live Zerodha order execution |
| `tools/kite_login.py` | Daily Kite access token generator |
| `index.html` | Landing page (GitHub Pages) |
| `README.md` | This file |

---

## The 9 Agents

| # | Agent | Job | Key Output |
|---|---|---|---|
| 1 | **Fundamentals** | P/E, ROE, DCF, promoter flags, SEBI history | `fair_value_inr`, `score` |
| 2 | **Technical** | 200 DMA, RSI, MACD, Supertrend, support/resistance | `technical_score`, `key_levels` |
| 3 | **Sentiment** | FII/DII flows, bulk deals, shareholding, news tone | `overall_score`, `red_flags` |
| 4 | **News** | RBI/SEBI macro, sector policy, INR/USD, catalysts | `news_score`, `upcoming_catalysts` |
| 5 | **Bull Researcher** | Strongest buy case with India alpha | `conviction`, `target_price` |
| 6 | **Bear Researcher** | Strongest bear case with downside scenario | `conviction`, `downside_scenario` |
| 7 | **Research Manager** | Adjudicates debate → PASS or BLOCK | `recommendation`, `risk_reward` |
| 8 | **Trader** | Kite order parameters: qty, price, SL, CNC/MIS | `quantity`, `stop_loss` |
| 9 | **Risk Manager** | Validates size, R:R ≥ 1:2, sector limits | `approve`, `recommended_quantity` |
| +PM | **Portfolio Manager** | Final APPROVE or VETO gate | `decision`, `pm_note` |

---

## FII/DII Intelligence

The Sentiment Agent is enriched with live institutional flow data before every analysis:

```
get_fii_dii_summary()      → market-level: FII net, DII net, signal
get_stock_shareholding()   → quarterly: FII %, DII %, promoter pledge trend
get_bulk_deals()           → stock-specific: who is buying/selling in bulk
get_block_deals()          → negotiated institutional trades
```

Interpretation framework used by the Sentiment Agent:

| Signal | Meaning |
|---|---|
| FII net buy + DII net buy | STRONGLY BULLISH — both institutions buying |
| FII selling + DII absorbing | Cautious — DII providing floor |
| Both selling | BEARISH — rare double-exit, reduce exposure |
| FII bulk BUY on stock | Accumulation — institutional conviction signal |
| Promoter pledge >20% | RED FLAG — avoid regardless of other signals |
| FII shareholding rising QoQ | Increasing foreign confidence in stock |

---

## Live Trading Setup

> ⚠️ **Paper trade for at least 4 weeks before going live.**

```bash
# Step 1: Get Kite Connect credentials
# Go to https://developers.kite.trade/ → Create app (₹2,000/year)
# Add KITE_API_KEY and KITE_API_SECRET to .env

# Step 2: Every morning (generates fresh access token)
python tools/kite_login.py

# Step 3: Switch to live mode
# In .env: KITE_PAPER_TRADE=false
# In .env: MODEL_NAME=gpt-4o  (use the best model for real decisions)

# Step 4: Start with one stock manually
python hf.py run HDFCBANK --execute

# Step 5: After a week of successful live trades, automate
python hf.py scheduler
```

---

## Graduation Path: Paper → Live

| Phase | Timeline | Action |
|---|---|---|
| **Paper testing** | Weeks 1–4 | `python hf.py portfolio` daily. Track: does AI thesis match reality? |
| **Validate signals** | Week 4 | Check paper trade log. Win rate >55%? Avg R:R >1:2? |
| **Live (manual)** | Weeks 5–8 | `KITE_PAPER_TRADE=false`. Review every order before market opens. |
| **Live (automated)** | Month 3+ | `python hf.py scheduler`. Runs at 9:30 AM IST every trading day. |

---

## Cost Reference

| Task | gpt-4o-mini | gpt-4o |
|---|---|---|
| Single stock pipeline | ₹3–8 | ₹30–50 |
| Full portfolio (10 stocks) | ₹50–80 | ₹300–500 |
| Multibagger screen (80 stocks, AI) | ₹20–40 | ₹150–300 |
| Watchlist scan (10 stocks) | ₹5–10 | ₹40–80 |
| FII/DII dashboard | ₹3–8 | ₹20–40 |

Use `gpt-4o-mini` for paper testing. Switch to `gpt-4o` for real money decisions.

---

## Configuration

All settings in one place: `config.py`

```python
# Your holdings — update from Zerodha Console
HOLDINGS = [
    {"ticker": "ICICIBANK",  "qty": 10, "avg_buy_price": 1245, "sector": "Banking"},
    # ... add any NSE stock
]

# Stocks you're watching
WATCHLIST = [
    {"ticker": "TITAN", "entry_target": 3200, "reason": "Premium consumption moat"},
    # ...
]

# Risk limits
MAX_POSITION_PCT   = 15.0   # no single stock >15%
MAX_SECTOR_PCT     = 30.0   # no single sector >30%
MIN_RISK_REWARD    = 2.0    # PM blocks R:R < 2.0
```

---

## Disclaimer

HedgeFusion is for **educational and research purposes only**. It is not SEBI-registered investment advice. Paper trade thoroughly before using real capital. AI analysis is probabilistic — always apply your own judgment. Past paper performance does not guarantee live results.

---

---


## Credits

Built by [@Zoravar31](https://github.com/Zoravar31)

Architecturally inspired by:
- [TradingAgents](https://github.com/TauricResearch/TradingAgents) — Apache-2.0
- [AutoHedge](https://github.com/The-Swarm-Corporation/AutoHedge) — MIT

Original implementation. No source code copied from either project.
