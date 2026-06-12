# 🇮🇳 HedgeFusion

> 9-agent autonomous AI trading system for Indian equity markets (NSE/BSE)  
> Powered by OpenAI GPT-4o + Zerodha Kite Connect

[![Python](https://img.shields.io/badge/Python-3.10+-blue?style=flat-square&logo=python)](https://python.org)
[![NSE](https://img.shields.io/badge/Exchange-NSE%20India-orange?style=flat-square)](https://nseindia.com)
[![Zerodha](https://img.shields.io/badge/Broker-Zerodha%20Kite-387ed1?style=flat-square)](https://kite.zerodha.com)
[![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)](LICENSE)

HedgeFusion combines the analyst depth of [TradingAgents](https://github.com/TauricResearch/TradingAgents) with the Zerodha execution layer of [AutoHedge](https://github.com/The-Swarm-Corporation/AutoHedge), rewritten from scratch for Indian markets with NSE-specific data feeds, SEBI-aware prompts, and a paper-first safety model.

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                   ANALYST TEAM                      │
│  Fundamentals │ Technical │ Sentiment │ News        │
│  (run in parallel — fastest path)                   │
└──────────────────────┬──────────────────────────────┘
                       ↓
┌─────────────────────────────────────────────────────┐
│                  RESEARCH TEAM                      │
│  Bull Researcher ←→ Bear Researcher (debate)        │
│            ↓ Research Manager (verdict)             │
└──────────────────────┬──────────────────────────────┘
                       ↓
┌─────────────────────────────────────────────────────┐
│                 EXECUTION TEAM                      │
│  Trader → Risk Manager → Portfolio Manager (gate)   │
└──────────────────────┬──────────────────────────────┘
                       ↓
                 place_nse_order()
              [Paper CSV log | Zerodha Kite]
```

**Key design principles:**
- 4 analysts run in **parallel** — saves time, same cost
- Bull vs Bear **debate** catches one-sided calls
- Portfolio Manager **approval gate** — hard veto on bad risk/reward
- **Paper mode by default** — zero real money until you flip the switch

---

## Quick Start

```bash
# 1. Clone
git clone https://github.com/Zoravar31/hedge-fusion.git
cd hedge-fusion

# 2. Virtual environment
python -m venv venv
venv\Scripts\activate          # Windows
source venv/bin/activate       # Mac/Linux

# 3. Install
pip install -r requirements.txt

# 4. Configure
copy .env.example .env         # Windows
cp .env.example .env           # Mac/Linux
# Edit .env → add OPENAI_API_KEY

# 5. Run
python main.py RELIANCE        # Single stock, analysis only
python main.py RELIANCE --execute   # Single stock + paper trade
python main.py --portfolio     # All your holdings
```

---

## Usage

### Single stock
```bash
python main.py HDFCBANK                  # Analysis only
python main.py HDFCBANK --execute        # Analysis + execute if approved
```

### Full portfolio
```bash
python main.py --portfolio               # All holdings, no execution
python main.py --portfolio --execute     # All holdings + execute approved
```

### Automated daily schedule
```bash
python scheduler.py     # Runs at SCHEDULE_TIME every trading day
```

### Python API
```python
from pipeline import run_pipeline

state = run_pipeline(
    ticker="ICICIBANK",
    portfolio_size_inr=500_000,
    allow_execution=True,    # paper or live depending on .env
)

print(state["pm_decision"]["decision"])    # APPROVE or VETO
print(state["research_verdict"]["recommendation"])   # BUY / SELL / HOLD
print(state["execution_result"])           # order details
```

---

## Agent Roles

| Agent | Role | Key output |
|---|---|---|
| **Fundamentals** | P/E, balance sheet, DCF, promoter flags | `score`, `fair_value_inr` |
| **Technical** | RSI, MACD, 200 DMA, Supertrend, support/resistance | `technical_score`, `key_levels` |
| **Sentiment** | FII/DII, news tone, social media, bulk deals | `overall_score`, `red_flags` |
| **News** | Macro India, RBI/SEBI, sector events, catalysts | `news_score`, `upcoming_catalysts` |
| **Bull Researcher** | Strongest bull case with conviction | `conviction`, `target_price` |
| **Bear Researcher** | Strongest bear case with conviction | `conviction`, `downside_scenario` |
| **Research Manager** | Adjudicates debate, issues PASS/BLOCK | `recommendation`, `risk_reward` |
| **Trader** | Generates Kite order parameters | `quantity`, `stop_loss`, `take_profit` |
| **Risk Manager** | Validates position size, R:R, concentration | `approve`, `recommended_quantity` |
| **Portfolio Manager** | Final APPROVE or VETO gate | `decision`, `pm_note` |

---

## Live Trading Setup

> ⚠️ **Paper trade for at least 4 weeks before going live.**

1. Create Kite app at [developers.kite.trade](https://developers.kite.trade/) — ₹2,000/year
2. Add `KITE_API_KEY` and `KITE_API_SECRET` to `.env`
3. Every morning before trading: `python tools/kite_login.py`
4. Set `KITE_PAPER_TRADE=false` and `MODEL_NAME=gpt-4o` in `.env`
5. Start with one stock and monitor every order manually for the first week

---

## Cost Reference

| Model | Cost per stock | Best for |
|---|---|---|
| `gpt-4o-mini` | ~₹2–5 | Paper testing, daily portfolio scans |
| `gpt-4o` | ~₹20–50 | Live trade decisions |

---

## Project Structure

```
hedge_fusion/
├── main.py               ← Entry point (CLI + interactive)
├── pipeline.py           ← 9-agent orchestration
├── scheduler.py          ← Autonomous daily runner
├── agents/
│   ├── prompts.py        ← System prompts for all 9 agents
│   └── runner.py         ← OpenAI function-calling engine
├── tools/
│   ├── india_data.py     ← NSE data + Indian news RSS feeds
│   ├── kite_execution.py ← Paper + live Zerodha order execution
│   └── kite_login.py     ← Daily access token generator
├── data/cache/           ← Auto-created API response cache
├── logs/                 ← Paper trade CSV + run logs
└── outputs/              ← Per-run JSON state files
```

---

## Disclaimer

HedgeFusion is for **educational and research purposes only**. It is not SEBI-registered investment advice. Paper trade thoroughly before risking real capital. AI analysis is probabilistic, not deterministic — always apply your own judgment.

---

## Credits

Built by [@Zoravar31](https://github.com/Zoravar31).  
Architecturally inspired by:
- [TradingAgents](https://github.com/TauricResearch/TradingAgents) by TauricResearch (Apache-2.0)
- [AutoHedge](https://github.com/The-Swarm-Corporation/AutoHedge) by The Swarm Corporation (MIT)

This is an original implementation — no source code was copied from either project.
