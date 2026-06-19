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

from tools.kite_execution import (
    place_nse_order,
    get_paper_portfolio,
    get_live_portfolio,
    EXECUTION_TOOL_DEFINITIONS,
    EXECUTION_TOOL_MAP,
)

ALL_TOOL_DEFINITIONS = (
    DATA_TOOL_DEFINITIONS +
    FII_DII_TOOL_DEFINITIONS +
    EXECUTION_TOOL_DEFINITIONS
)

ALL_TOOL_MAP = {
    **DATA_TOOL_MAP,
    **FII_DII_TOOL_MAP,
    **EXECUTION_TOOL_MAP,
}

__all__ = [
    "get_nse_quote", "get_nse_history", "get_nse_fundamentals",
    "get_india_news", "get_macro_india_context", "get_bulk_block_deals",
    "get_fii_dii_daily", "get_fii_dii_summary", "get_bulk_deals",
    "get_block_deals", "get_stock_shareholding",
    "place_nse_order", "get_paper_portfolio", "get_live_portfolio",
    "DATA_TOOL_DEFINITIONS", "DATA_TOOL_MAP",
    "FII_DII_TOOL_DEFINITIONS", "FII_DII_TOOL_MAP",
    "EXECUTION_TOOL_DEFINITIONS", "EXECUTION_TOOL_MAP",
    "ALL_TOOL_DEFINITIONS", "ALL_TOOL_MAP",
]
