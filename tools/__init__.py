"""
HedgeFusion Tools Package
==========================
Exports all data tools and execution tools for use by agents.
"""

from tools.india_data import (
    get_nse_quote,
    get_nse_history,
    get_nse_fundamentals,
    get_india_news,
    get_macro_india_context,
    get_bulk_block_deals,
    get_nifty_pcr,
    DATA_TOOL_DEFINITIONS,
    DATA_TOOL_MAP,
)

from tools.fii_dii import (
    get_fii_dii_daily,
    get_fii_dii_summary,
    get_bulk_deals,
    get_block_deals,
    get_stock_shareholding,
    FII_DII_TOOL_DEFINITIONS,
    FII_DII_TOOL_MAP,
)

# analyse_stock_fii_dii lives in fii_dii_dashboard (not tools.fii_dii).
# NOTE: fii_dii_dashboard.py itself imports from tools.fii_dii, so importing
# it eagerly here at module load time creates a circular import
# (tools/__init__ -> fii_dii_dashboard -> tools.fii_dii -> tools/__init__).
# Fix: expose it lazily via PEP 562 module __getattr__ instead of a top-level
# import. This means `from tools import analyse_stock_fii_dii` still works,
# but the actual import only happens the first time it's accessed — by then
# fii_dii_dashboard.py has finished loading.
def __getattr__(name):
    if name == "analyse_stock_fii_dii":
        from fii_dii_dashboard import analyse_stock_fii_dii
        return analyse_stock_fii_dii
    raise AttributeError(f"module 'tools' has no attribute {name!r}")

from tools.kite_execution import (
    place_nse_order,
    get_paper_portfolio,
    get_live_portfolio,
    EXECUTION_TOOL_DEFINITIONS,
    EXECUTION_TOOL_MAP,
)

from tools.kite_ticker import (
    get_live_price,
    get_live_prices,
    TickerSession,
    TICKER_TOOL_DEFINITIONS,
    TICKER_TOOL_MAP,
)

ALL_TOOL_DEFINITIONS = (
    DATA_TOOL_DEFINITIONS +
    FII_DII_TOOL_DEFINITIONS +
    EXECUTION_TOOL_DEFINITIONS +
    TICKER_TOOL_DEFINITIONS
)

ALL_TOOL_MAP = {
    **DATA_TOOL_MAP,
    **FII_DII_TOOL_MAP,
    **EXECUTION_TOOL_MAP,
    **TICKER_TOOL_MAP,
}

__all__ = [
    "get_nse_quote", "get_nse_history", "get_nse_fundamentals",
    "get_india_news", "get_macro_india_context", "get_bulk_block_deals",
    "get_nifty_pcr",
    "get_fii_dii_daily", "get_fii_dii_summary", "get_bulk_deals",
    "get_block_deals", "get_stock_shareholding",
    "place_nse_order", "get_paper_portfolio", "get_live_portfolio",
    "get_live_price", "get_live_prices", "TickerSession",
    "DATA_TOOL_DEFINITIONS", "DATA_TOOL_MAP",
    "FII_DII_TOOL_DEFINITIONS", "FII_DII_TOOL_MAP",
    "EXECUTION_TOOL_DEFINITIONS", "EXECUTION_TOOL_MAP",
    "TICKER_TOOL_DEFINITIONS", "TICKER_TOOL_MAP",
    "ALL_TOOL_DEFINITIONS", "ALL_TOOL_MAP",
    "analyse_stock_fii_dii",
]
