"""
HedgeFusion Pipeline
=====================
The complete 9-agent pipeline for autonomous Indian equity trading.

Flow:
  [Analyst team — 4 agents in parallel]
    Fundamentals, Technical, Sentiment, News

  [Research team — 3 agents in sequence]
    Bull researcher ← analyst reports
    Bear researcher ← analyst reports
    Research manager ← bull + bear reports → verdict (PASS/BLOCK)

  [Execution team — 3 agents in sequence]
    Trader         ← research verdict → order parameters
    Risk manager   ← trader order    → validated params
    Portfolio mgr  ← risk validation → APPROVE / VETO

  [Execution]
    place_nse_order() → paper CSV log or Zerodha Kite

Each agent is a call to run_agent() with its specific prompt and tools.
The pipeline state is a dict that accumulates outputs at each stage.
"""

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

from loguru import logger

from agents.prompts import (
    FUNDAMENTALS_PROMPT,
    TECHNICAL_PROMPT,
    SENTIMENT_PROMPT,
    NEWS_PROMPT,
    BULL_RESEARCHER_PROMPT,
    BEAR_RESEARCHER_PROMPT,
    RESEARCH_MANAGER_PROMPT,
    TRADER_PROMPT,
    RISK_MANAGER_PROMPT,
    PORTFOLIO_MANAGER_PROMPT,
)
from agents.runner import run_agent, parse_json_response
from tools.india_data import (
    DATA_TOOL_DEFINITIONS,
    DATA_TOOL_MAP,
    get_nse_quote,
    get_macro_india_context,
    get_nifty_pcr,
)
from tools.fii_dii import (
    FII_DII_TOOL_DEFINITIONS,
    FII_DII_TOOL_MAP,
    get_fii_dii_summary,
    get_stock_shareholding,
    get_bulk_deals,
)
from tools.kite_execution import (
    EXECUTION_TOOL_DEFINITIONS,
    EXECUTION_TOOL_MAP,
    place_nse_order,
    get_paper_portfolio,
)
from position_sizer import POSITION_SIZER_TOOL_DEFINITIONS, POSITION_SIZER_TOOL_MAP

OUTPUT_DIR = Path(__file__).parent / "outputs"
OUTPUT_DIR.mkdir(exist_ok=True)

ALL_TOOLS      = DATA_TOOL_DEFINITIONS + FII_DII_TOOL_DEFINITIONS + EXECUTION_TOOL_DEFINITIONS
ALL_TOOL_MAP   = {**DATA_TOOL_MAP, **FII_DII_TOOL_MAP, **EXECUTION_TOOL_MAP}
DATA_ONLY_MAP  = {**DATA_TOOL_MAP, **FII_DII_TOOL_MAP}


# ──────────────────────────────────────────────
# Individual agent wrappers
# ──────────────────────────────────────────────

def run_fundamentals_analyst(ticker: str) -> dict:
    raw = run_agent(
        agent_name=f"Fundamentals [{ticker}]",
        system_prompt=FUNDAMENTALS_PROMPT,
        user_message=f"Analyse the fundamentals of {ticker} listed on NSE. Fetch all available financial data.",
        tools=DATA_TOOL_DEFINITIONS,
        tool_map=DATA_ONLY_MAP,
    )
    return parse_json_response(raw) or {"raw": raw, "score": 0.5}


def run_technical_analyst(ticker: str) -> dict:
    raw = run_agent(
        agent_name=f"Technical [{ticker}]",
        system_prompt=TECHNICAL_PROMPT,
        user_message=f"Perform full technical analysis on {ticker} (NSE). Fetch 6-month daily OHLCV data.",
        tools=DATA_TOOL_DEFINITIONS,
        tool_map=DATA_ONLY_MAP,
    )
    return parse_json_response(raw) or {"raw": raw, "technical_score": 0.5}


def run_sentiment_analyst(ticker: str) -> dict:
    # Pre-fetch FII/DII summary to enrich the agent's context
    try:
        fii_summary = get_fii_dii_summary()
        shareholding = get_stock_shareholding(ticker)
        bulk = get_bulk_deals(ticker, days=30)
    except Exception:
        fii_summary = shareholding = bulk = "{}"
    raw = run_agent(
        agent_name=f"Sentiment [{ticker}]",
        system_prompt=SENTIMENT_PROMPT,
        user_message=(
            f"Assess sentiment for {ticker} on NSE.\n\n"
            f"MARKET-LEVEL FII/DII FLOWS (pre-fetched):\n{fii_summary[:1200]}\n\n"
            f"STOCK-SPECIFIC SHAREHOLDING PATTERN:\n{shareholding[:800]}\n\n"
            f"RECENT BULK DEALS FOR {ticker}:\n{bulk[:600]}\n\n"
            f"Now fetch additional news and sentiment data using your tools."
        ),
        tools=DATA_TOOL_DEFINITIONS + FII_DII_TOOL_DEFINITIONS,
        tool_map=DATA_ONLY_MAP,
    )
    return parse_json_response(raw) or {"raw": raw, "overall_score": 0.5}


def run_news_analyst(ticker: str) -> dict:
    macro = get_macro_india_context()
    # Pre-fetch Nifty PCR — key contrarian signal for the News agent
    try:
        pcr_data = get_nifty_pcr()
        pcr_obj  = json.loads(pcr_data)
        pcr_line = (
            f"NIFTY PCR: {pcr_obj.get('pcr', 'N/A')} — {pcr_obj.get('signal', '')}\n"
            f"  Interpretation: {pcr_obj.get('interpretation', '')}"
        )
    except Exception:
        pcr_line = "NIFTY PCR: unavailable"

    raw = run_agent(
        agent_name=f"News [{ticker}]",
        system_prompt=NEWS_PROMPT,
        user_message=(
            f"Analyse news and macro impact on {ticker} (NSE).\n"
            f"Current India macro context:\n{macro[:1500]}\n\n"
            f"MARKET SENTIMENT — NIFTY OPTIONS PCR (live):\n{pcr_line}\n\n"
            f"Fetch company-specific news for {ticker}. "
            f"Factor the PCR into your macro_context and news_score — "
            f"a PCR > 1.3 is a contrarian buy signal; PCR < 0.8 suggests complacency."
        ),
        tools=DATA_TOOL_DEFINITIONS,
        tool_map=DATA_ONLY_MAP,
    )
    return parse_json_response(raw) or {"raw": raw, "news_score": 0.5}


def run_bull_researcher(ticker: str, analyst_reports: dict) -> dict:
    context = _format_analyst_context(ticker, analyst_reports)
    raw = run_agent(
        agent_name=f"Bull Researcher [{ticker}]",
        system_prompt=BULL_RESEARCHER_PROMPT,
        user_message=f"Build the strongest bull case for {ticker}.\n\n{context}",
        tools=DATA_TOOL_DEFINITIONS,
        tool_map=DATA_ONLY_MAP,
    )
    return parse_json_response(raw) or {"raw": raw, "conviction": 0.5}


def run_bear_researcher(ticker: str, analyst_reports: dict) -> dict:
    context = _format_analyst_context(ticker, analyst_reports)
    raw = run_agent(
        agent_name=f"Bear Researcher [{ticker}]",
        system_prompt=BEAR_RESEARCHER_PROMPT,
        user_message=f"Build the strongest bear case for {ticker}.\n\n{context}",
        tools=DATA_TOOL_DEFINITIONS,
        tool_map=DATA_ONLY_MAP,
    )
    return parse_json_response(raw) or {"raw": raw, "conviction": 0.5}


def run_research_manager(ticker: str, analyst_reports: dict, bull: dict, bear: dict) -> dict:
    from agent_memory import summarize_memory
    prior_memory = summarize_memory(ticker, last_n=3)
    context = (
        f"TICKER: {ticker}\n\n"
        f"ANALYST REPORTS:\n{_format_analyst_context(ticker, analyst_reports)}\n\n"
        f"BULL RESEARCHER:\n{json.dumps(bull, default=str)[:1500]}\n\n"
        f"BEAR RESEARCHER:\n{json.dumps(bear, default=str)[:1500]}"
        + (f"\n\n{prior_memory}" if prior_memory else "")
    )
    raw = run_agent(
        agent_name=f"Research Manager [{ticker}]",
        system_prompt=RESEARCH_MANAGER_PROMPT,
        user_message=context,
    )
    return parse_json_response(raw) or {"raw": raw, "decision": "BLOCK", "recommendation": "HOLD"}


def run_trader(ticker: str, research_verdict: dict, portfolio_size_inr: float) -> dict:
    context = (
        f"TICKER: {ticker}\n"
        f"PORTFOLIO SIZE: ₹{portfolio_size_inr:,.0f}\n\n"
        f"RESEARCH VERDICT:\n{json.dumps(research_verdict, default=str)[:2000]}\n\n"
        f"Use calculate_position_size to determine the correct share quantity "
        f"based on your entry price and stop loss — do not guess a round number."
    )
    raw = run_agent(
        agent_name=f"Trader [{ticker}]",
        system_prompt=TRADER_PROMPT,
        user_message=context,
        tools=DATA_TOOL_DEFINITIONS + POSITION_SIZER_TOOL_DEFINITIONS,
        tool_map={**DATA_ONLY_MAP, **POSITION_SIZER_TOOL_MAP},
    )
    return parse_json_response(raw) or {"execute": False, "rationale": "parse error"}


def run_risk_manager(ticker: str, trader_order: dict, portfolio_size_inr: float) -> dict:
    context = (
        f"TICKER: {ticker}\n"
        f"PORTFOLIO SIZE: ₹{portfolio_size_inr:,.0f}\n\n"
        f"PROPOSED ORDER:\n{json.dumps(trader_order, default=str)[:1500]}"
    )
    raw = run_agent(
        agent_name=f"Risk Manager [{ticker}]",
        system_prompt=RISK_MANAGER_PROMPT,
        user_message=context,
        tools=DATA_TOOL_DEFINITIONS,
        tool_map=DATA_ONLY_MAP,
    )
    return parse_json_response(raw) or {"approve": False, "risk_comments": "parse error"}


def run_portfolio_manager(
    ticker: str,
    research_verdict: dict,
    trader_order: dict,
    risk_validation: dict,
) -> dict:
    context = (
        f"TICKER: {ticker}\n\n"
        f"RESEARCH VERDICT:\n{json.dumps(research_verdict, default=str)[:1000]}\n\n"
        f"TRADER ORDER:\n{json.dumps(trader_order, default=str)[:800]}\n\n"
        f"RISK VALIDATION:\n{json.dumps(risk_validation, default=str)[:800]}"
    )
    raw = run_agent(
        agent_name=f"Portfolio Manager [{ticker}]",
        system_prompt=PORTFOLIO_MANAGER_PROMPT,
        user_message=context,
    )
    return parse_json_response(raw) or {"decision": "VETO", "pm_note": "parse error"}


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def _format_analyst_context(ticker: str, reports: dict) -> str:
    lines = [f"Analyst reports for {ticker}:"]
    for name, report in reports.items():
        summary = report.get("summary") or str(report)[:300]
        score_key = next((k for k in report if "score" in k.lower()), None)
        score = report.get(score_key, "N/A") if score_key else "N/A"
        lines.append(f"\n[{name.upper()}] score={score}\n{summary}")
    return "\n".join(lines)


def _execute_approved_order(pm_decision: dict, allow_execution: bool) -> Optional[dict]:
    """Actually call place_nse_order if PM approved and execution is enabled."""
    if pm_decision.get("decision") != "APPROVE":
        return None
    if not allow_execution:
        logger.info("Execution disabled — order approved but not placed.")
        return None

    final_order = pm_decision.get("final_order")
    if not final_order or not isinstance(final_order, dict):
        logger.warning("PM approved but no final_order in response.")
        return None

    result = place_nse_order(
        symbol=final_order.get("symbol", ""),
        transaction_type=final_order.get("transaction_type", "BUY"),
        quantity=int(final_order.get("quantity", 0)),
        order_type=final_order.get("order_type", "MARKET"),
        price=final_order.get("price"),
        stop_loss=final_order.get("stop_loss"),
        take_profit=final_order.get("take_profit") or final_order.get("take_profit"),
    )
    return json.loads(result)


# ──────────────────────────────────────────────
# Main pipeline
# ──────────────────────────────────────────────

def run_pipeline(
    ticker: str,
    portfolio_size_inr: float = 500_000,
    allow_execution: bool = True,
    parallel_analysts: bool = True,
) -> dict:
    """
    Run the complete 9-agent HedgeFusion pipeline for one NSE stock.

    Parameters
    ----------
    ticker             : NSE ticker e.g. RELIANCE, HDFCBANK.
    portfolio_size_inr : Total portfolio value in ₹. Used for position sizing.
    allow_execution    : If True, PM-approved orders are executed (paper or live).
    parallel_analysts  : If True, all 4 analysts run concurrently (faster, costs same).

    Returns
    -------
    dict with all agent outputs, PM decision, and execution result.
    """
    ticker = ticker.strip().upper()
    start_time = datetime.now()
    paper_mode = os.getenv("KITE_PAPER_TRADE", "true").lower() in ("true", "1", "yes")

    logger.info("")
    logger.info("━" * 60)
    logger.info("  HedgeFusion Pipeline: {}", ticker)
    logger.info("  Portfolio: ₹{:,.0f} | Mode: {}", portfolio_size_inr,
                "PAPER" if paper_mode else "LIVE 🔴")
    logger.info("━" * 60)

    state: dict = {
        "ticker": ticker,
        "portfolio_size_inr": portfolio_size_inr,
        "paper_mode": paper_mode,
        "started_at": start_time.isoformat(),
        "analysts": {},
        "bull": {},
        "bear": {},
        "research_verdict": {},
        "trader_order": {},
        "risk_validation": {},
        "pm_decision": {},
        "execution_result": None,
    }

    # ── STAGE 1: Analyst team ─────────────────────────────────
    logger.info("")
    logger.info("STAGE 1/4 — Analyst team (4 agents)")

    analyst_fns = {
        "fundamentals": lambda: run_fundamentals_analyst(ticker),
        "technical":    lambda: run_technical_analyst(ticker),
        "sentiment":    lambda: run_sentiment_analyst(ticker),
        "news":         lambda: run_news_analyst(ticker),
    }

    if parallel_analysts:
        with ThreadPoolExecutor(max_workers=4) as ex:
            futures = {ex.submit(fn): name for name, fn in analyst_fns.items()}
            for future in as_completed(futures):
                name = futures[future]
                try:
                    state["analysts"][name] = future.result()
                    logger.info("  ✓ {} analyst done", name)
                except Exception as e:
                    logger.error("  ✗ {} analyst failed: {}", name, e)
                    state["analysts"][name] = {"error": str(e)}
    else:
        for name, fn in analyst_fns.items():
            state["analysts"][name] = fn()
            logger.info("  ✓ {} analyst done", name)

    # ── STAGE 2: Research team ────────────────────────────────
    logger.info("")
    logger.info("STAGE 2/4 — Research team (Bull vs Bear debate)")

    state["bull"] = run_bull_researcher(ticker, state["analysts"])
    logger.info("  ✓ Bull researcher done | conviction: {}",
                state["bull"].get("conviction", "?"))

    state["bear"] = run_bear_researcher(ticker, state["analysts"])
    logger.info("  ✓ Bear researcher done | conviction: {}",
                state["bear"].get("conviction", "?"))

    state["research_verdict"] = run_research_manager(
        ticker, state["analysts"], state["bull"], state["bear"]
    )
    verdict = state["research_verdict"]
    logger.info("  ✓ Research Manager: {} | {} | confidence: {}",
                verdict.get("recommendation", "?"),
                verdict.get("decision", "?"),
                verdict.get("confidence", "?"))

    # ── STAGE 3: Execution team ───────────────────────────────
    logger.info("")
    logger.info("STAGE 3/4 — Execution team (Trader → Risk → PM)")

    state["trader_order"] = run_trader(ticker, state["research_verdict"], portfolio_size_inr)
    logger.info("  ✓ Trader order: {} × {} | execute={}",
                state["trader_order"].get("transaction_type", "?"),
                state["trader_order"].get("quantity", "?"),
                state["trader_order"].get("execute", False))

    state["risk_validation"] = run_risk_manager(ticker, state["trader_order"], portfolio_size_inr)
    logger.info("  ✓ Risk Manager: approve={} | rating={}",
                state["risk_validation"].get("approve", "?"),
                state["risk_validation"].get("overall_risk_rating", "?"))

    state["pm_decision"] = run_portfolio_manager(
        ticker,
        state["research_verdict"],
        state["trader_order"],
        state["risk_validation"],
    )
    pm = state["pm_decision"]
    logger.info("  ✓ Portfolio Manager: {} — {}",
                pm.get("decision", "?"),
                pm.get("pm_note", "")[:80])

    # ── STAGE 4: Execute ──────────────────────────────────────
    logger.info("")
    logger.info("STAGE 4/4 — Execute")

    if pm.get("decision") == "APPROVE" and allow_execution:
        state["execution_result"] = _execute_approved_order(pm, allow_execution)
        if state["execution_result"]:
            logger.info("  ✅ Order placed: {}", state["execution_result"].get("order_id"))
        else:
            logger.info("  ⚠ PM approved but no order params found")
    elif pm.get("decision") == "VETO":
        logger.info("  🚫 VETO — order blocked")
        state["execution_result"] = {"status": "VETOED", "reason": pm.get("pm_note", "")}
    else:
        logger.info("  ⏸ Execution disabled or order not approved")
        state["execution_result"] = {"status": "NOT_EXECUTED"}

    # ── Wrap up ───────────────────────────────────────────────
    elapsed = (datetime.now() - start_time).total_seconds()
    state["elapsed_seconds"] = round(elapsed, 1)
    state["completed_at"] = datetime.now().isoformat()

    logger.info("")
    logger.info("━" * 60)
    logger.info("  Pipeline complete: {} in {:.0f}s", ticker, elapsed)
    logger.info("  Final: {} | {}",
                pm.get("decision", "?"),
                state["execution_result"].get("status", "?") if state["execution_result"] else "?")
    logger.info("━" * 60)

    # Save state to outputs/
    out_path = OUTPUT_DIR / f"{ticker}_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    out_path.write_text(json.dumps(state, default=str, indent=2), encoding="utf-8")
    logger.info("  Saved: {}", out_path.name)

    # Record this verdict into per-ticker agent memory for future runs
    try:
        from agent_memory import record_verdict
        record_verdict(ticker, state)
    except Exception as e:
        logger.warning("agent_memory recording failed: {}", e)

    return state
