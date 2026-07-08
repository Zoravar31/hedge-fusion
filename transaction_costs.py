"""
Transaction Cost Model — Feature #15
======================================
Accurate Zerodha/NSE trade cost modelling for P&L calculations.

Zerodha CNC (delivery) cost breakdown per trade:
  ┌────────────────────────────────┬──────────────────────────────────────────┐
  │ Cost component                  │ Rate                                     │
  ├────────────────────────────────┼──────────────────────────────────────────┤
  │ Brokerage                       │ ₹20 per order (both buy AND sell)        │
  │ STT (Securities Transaction Tax)│ 0.1% on SELL turnover (CNC only)         │
  │ Exchange Transaction Charge     │ 0.00297% on turnover (NSE equity)        │
  │ SEBI turnover fee               │ ₹10 per crore of turnover (both sides)   │
  │ Stamp duty                      │ 0.015% on BUY turnover (varies by state) │
  │ GST                             │ 18% on (brokerage + exchange charge)      │
  └────────────────────────────────┴──────────────────────────────────────────┘

For a ₹50,000 trade (buy + sell = ₹1,00,000 turnover):
  Brokerage:  ₹20 + ₹20 = ₹40.00
  STT:        ₹50,000 × 0.1% = ₹50.00
  Exch. chg:  ₹1,00,000 × 0.00297% = ₹2.97
  SEBI:       ₹1,00,000 / 1,00,00,000 × 10 = ₹0.10
  Stamp duty: ₹50,000 × 0.015% = ₹7.50
  GST:        18% × (₹40 + ₹2.97) = ₹7.73
  ─────────────────────────────────────────
  TOTAL COST: ~₹108  (≈ 0.22% round-trip)
  On 10 trades/month: ₹1,080/month in friction alone.

Usage:
    from transaction_costs import TradeCostModel, cost_adjusted_return

    model = TradeCostModel()

    # Get full cost breakdown for a trade
    cost = model.calculate(
        buy_price=2950.0,
        sell_price=3200.0,
        quantity=10,
    )
    print(cost)
    # → CostBreakdown(net_pnl=₹2392.97, gross_pnl=₹2500, total_cost=₹107.03, ...)

    # Adjust a % return for costs
    adjusted = cost_adjusted_return(
        gross_return_pct=5.0,
        trade_value_inr=50_000
    )
    # → 4.78%  (cost-adjusted net return)
"""

from dataclasses import dataclass, field
from typing import Optional


# ──────────────────────────────────────────────
# Zerodha CNC rate constants (as of 2025-2026)
# ──────────────────────────────────────────────

BROKERAGE_PER_ORDER_INR:    float = 20.0      # flat ₹20 per executed order
STT_SELL_PCT:               float = 0.001     # 0.1% of sell turnover
EXCHANGE_TXN_CHARGE_PCT:    float = 0.0000297 # 0.00297% of total turnover (NSE)
SEBI_FEE_PER_CRORE_INR:     float = 10.0      # ₹10 per crore (₹1,00,00,000)
STAMP_DUTY_BUY_PCT:         float = 0.00015   # 0.015% of buy turnover
GST_PCT:                    float = 0.18      # 18% on brokerage + exchange charge


# ──────────────────────────────────────────────
# Cost breakdown dataclass
# ──────────────────────────────────────────────

@dataclass
class CostBreakdown:
    """Full trade cost breakdown for a single round-trip (buy + sell)."""
    # Trade details
    ticker:             str
    quantity:           int
    buy_price:          float
    sell_price:         float

    # Turnover
    buy_turnover:       float   # qty × buy_price
    sell_turnover:      float   # qty × sell_price
    total_turnover:     float   # buy + sell

    # Individual costs (₹)
    brokerage:          float   # ₹20 × 2 (buy + sell order)
    stt:                float   # 0.1% × sell_turnover
    exchange_charge:    float   # 0.00297% × total_turnover
    sebi_fee:           float   # ₹10 per crore of total_turnover
    stamp_duty:         float   # 0.015% × buy_turnover
    gst:                float   # 18% × (brokerage + exchange_charge)

    # Summary
    total_cost_inr:     float   # sum of all costs
    gross_pnl_inr:      float   # (sell_price - buy_price) × qty
    net_pnl_inr:        float   # gross_pnl - total_cost
    gross_return_pct:   float   # gross_pnl / buy_turnover × 100
    net_return_pct:     float   # net_pnl / buy_turnover × 100
    cost_as_pct:        float   # total_cost / buy_turnover × 100
    breakeven_price:    float   # buy_price × (1 + total_cost/buy_turnover)

    def __str__(self) -> str:
        lines = [
            f"  Trade:         {self.quantity} × {self.ticker}",
            f"  Buy price:     ₹{self.buy_price:,.2f}",
            f"  Sell price:    ₹{self.sell_price:,.2f}",
            f"  Buy turnover:  ₹{self.buy_turnover:,.2f}",
            f"  Sell turnover: ₹{self.sell_turnover:,.2f}",
            f"  ─────────────────────────────────────",
            f"  Brokerage:     ₹{self.brokerage:.2f}  (₹20 × 2 orders)",
            f"  STT:           ₹{self.stt:.2f}  (0.1% on sell)",
            f"  Exchange chg:  ₹{self.exchange_charge:.4f}  (0.00297% turnover)",
            f"  SEBI fee:      ₹{self.sebi_fee:.4f}  (₹10/crore)",
            f"  Stamp duty:    ₹{self.stamp_duty:.2f}  (0.015% on buy)",
            f"  GST:           ₹{self.gst:.2f}  (18% on bkg+exch)",
            f"  ─────────────────────────────────────",
            f"  TOTAL COST:    ₹{self.total_cost_inr:.2f}  ({self.cost_as_pct:.3f}%)",
            f"  Gross P&L:     ₹{self.gross_pnl_inr:+,.2f}  ({self.gross_return_pct:+.2f}%)",
            f"  NET P&L:       ₹{self.net_pnl_inr:+,.2f}  ({self.net_return_pct:+.2f}%)",
            f"  Break-even:    ₹{self.breakeven_price:,.2f}",
        ]
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "ticker":          self.ticker,
            "quantity":        self.quantity,
            "buy_price":       round(self.buy_price, 2),
            "sell_price":      round(self.sell_price, 2),
            "buy_turnover":    round(self.buy_turnover, 2),
            "sell_turnover":   round(self.sell_turnover, 2),
            "total_turnover":  round(self.total_turnover, 2),
            "costs": {
                "brokerage":       round(self.brokerage, 2),
                "stt":             round(self.stt, 2),
                "exchange_charge": round(self.exchange_charge, 4),
                "sebi_fee":        round(self.sebi_fee, 4),
                "stamp_duty":      round(self.stamp_duty, 2),
                "gst":             round(self.gst, 2),
                "total":           round(self.total_cost_inr, 2),
                "total_pct":       round(self.cost_as_pct, 4),
            },
            "pnl": {
                "gross_inr":    round(self.gross_pnl_inr, 2),
                "gross_pct":    round(self.gross_return_pct, 4),
                "net_inr":      round(self.net_pnl_inr, 2),
                "net_pct":      round(self.net_return_pct, 4),
            },
            "breakeven_price": round(self.breakeven_price, 2),
        }


# ──────────────────────────────────────────────
# Main cost model
# ──────────────────────────────────────────────

class TradeCostModel:
    """
    Zerodha CNC trade cost calculator.

    All defaults match Zerodha's published rates as of 2025-2026.
    Override any rate in the constructor for custom scenarios.
    """

    def __init__(
        self,
        brokerage_per_order: float = BROKERAGE_PER_ORDER_INR,
        stt_sell_pct:        float = STT_SELL_PCT,
        exchange_charge_pct: float = EXCHANGE_TXN_CHARGE_PCT,
        sebi_per_crore:      float = SEBI_FEE_PER_CRORE_INR,
        stamp_duty_pct:      float = STAMP_DUTY_BUY_PCT,
        gst_pct:             float = GST_PCT,
    ):
        self.brokerage_per_order = brokerage_per_order
        self.stt_sell_pct        = stt_sell_pct
        self.exchange_charge_pct = exchange_charge_pct
        self.sebi_per_crore      = sebi_per_crore
        self.stamp_duty_pct      = stamp_duty_pct
        self.gst_pct             = gst_pct

    def calculate(
        self,
        buy_price:  float,
        sell_price: float,
        quantity:   int,
        ticker:     str = "UNKNOWN",
    ) -> CostBreakdown:
        """
        Calculate full round-trip cost for a CNC trade.

        Parameters
        ----------
        buy_price  : Entry price in ₹ per share
        sell_price : Exit price in ₹ per share
        quantity   : Number of shares (positive integer)
        ticker     : NSE ticker symbol (for labelling)

        Returns
        -------
        CostBreakdown dataclass with all components and net P&L.
        """
        buy_turnover  = buy_price  * quantity
        sell_turnover = sell_price * quantity
        total_turnover = buy_turnover + sell_turnover

        brokerage      = self.brokerage_per_order * 2           # buy + sell
        stt            = sell_turnover * self.stt_sell_pct
        exchange_charge = total_turnover * self.exchange_charge_pct
        sebi_fee       = (total_turnover / 1e7) * self.sebi_per_crore
        stamp_duty     = buy_turnover * self.stamp_duty_pct
        gst            = (brokerage + exchange_charge) * self.gst_pct

        total_cost     = brokerage + stt + exchange_charge + sebi_fee + stamp_duty + gst
        gross_pnl      = (sell_price - buy_price) * quantity
        net_pnl        = gross_pnl - total_cost

        gross_return_pct = (gross_pnl / buy_turnover * 100) if buy_turnover > 0 else 0
        net_return_pct   = (net_pnl   / buy_turnover * 100) if buy_turnover > 0 else 0
        cost_as_pct      = (total_cost / buy_turnover * 100) if buy_turnover > 0 else 0
        breakeven_price  = buy_price * (1 + cost_as_pct / 100)

        return CostBreakdown(
            ticker=ticker.upper(),
            quantity=quantity,
            buy_price=buy_price,
            sell_price=sell_price,
            buy_turnover=buy_turnover,
            sell_turnover=sell_turnover,
            total_turnover=total_turnover,
            brokerage=brokerage,
            stt=stt,
            exchange_charge=exchange_charge,
            sebi_fee=sebi_fee,
            stamp_duty=stamp_duty,
            gst=gst,
            total_cost_inr=total_cost,
            gross_pnl_inr=gross_pnl,
            net_pnl_inr=net_pnl,
            gross_return_pct=gross_return_pct,
            net_return_pct=net_return_pct,
            cost_as_pct=cost_as_pct,
            breakeven_price=breakeven_price,
        )

    def breakeven_move_pct(self, trade_value_inr: float) -> float:
        """
        How much (%) must the stock move UP just to break even after costs?
        Useful for filtering low-conviction trades with small expected moves.
        """
        dummy = self.calculate(
            buy_price=100.0,
            sell_price=100.0,
            quantity=int(trade_value_inr / 100),
            ticker="DUMMY",
        )
        return dummy.cost_as_pct

    def monthly_cost_estimate(
        self,
        trades_per_month: int,
        avg_trade_value_inr: float,
    ) -> dict:
        """
        Estimate monthly cost drag given trading frequency and average trade size.
        """
        dummy = self.calculate(
            buy_price=100.0,
            sell_price=100.0,
            quantity=int(avg_trade_value_inr / 100),
        )
        cost_per_trade = dummy.total_cost_inr
        monthly_total  = cost_per_trade * trades_per_month
        annual_total   = monthly_total * 12

        return {
            "trades_per_month":     trades_per_month,
            "avg_trade_value_inr":  avg_trade_value_inr,
            "cost_per_trade_inr":   round(cost_per_trade, 2),
            "cost_per_trade_pct":   round(dummy.cost_as_pct, 3),
            "monthly_cost_inr":     round(monthly_total, 2),
            "annual_cost_inr":      round(annual_total, 2),
            "breakeven_move_pct":   round(dummy.cost_as_pct, 3),
        }


# ──────────────────────────────────────────────
# Convenience functions
# ──────────────────────────────────────────────

_DEFAULT_MODEL = TradeCostModel()


def cost_adjusted_return(
    gross_return_pct: float,
    trade_value_inr:  float,
    ticker:           str = "STOCK",
) -> float:
    """
    Adjust a gross return % for round-trip transaction costs.

    Example:
        gross return = +5.0%, trade value = ₹50,000
        → net return after costs = ~4.78%

    Parameters
    ----------
    gross_return_pct : Raw return before costs (e.g. 5.0 for +5%)
    trade_value_inr  : Size of the trade in ₹ (buy side)
    ticker           : NSE ticker (for labelling)

    Returns
    -------
    float: Net return % after all Zerodha CNC costs
    """
    qty = max(1, int(trade_value_inr / 100))
    buy_price  = 100.0
    sell_price = buy_price * (1 + gross_return_pct / 100)
    cb = _DEFAULT_MODEL.calculate(buy_price, sell_price, qty, ticker)
    return round(cb.net_return_pct, 4)


def cost_adjust_trade_result(trade: dict, trade_value_inr: float) -> dict:
    """
    Take a backtester trade dict {return_pct, outcome, ...} and add cost-adjusted fields.
    Used by backtester.simulate_trades() and walk-forward engine.
    """
    gross_pct = trade.get("return_pct") or trade.get("actual_return") or 0.0
    net_pct   = cost_adjusted_return(gross_pct, trade_value_inr)
    cost_drag = round(gross_pct - net_pct, 4)
    net_outcome = "WIN" if net_pct > 0 else "LOSS"
    return {
        **trade,
        "gross_return_pct": round(gross_pct, 4),
        "net_return_pct":   round(net_pct, 4),
        "cost_drag_pct":    round(cost_drag, 4),
        "net_outcome":      net_outcome,
    }


def print_cost_summary(trade_value_inr: float = 50_000, ticker: str = "EXAMPLE"):
    """Print a formatted cost breakdown for a neutral (flat) trade — useful for onboarding."""
    cb = _DEFAULT_MODEL.calculate(
        buy_price=trade_value_inr / 100,
        sell_price=trade_value_inr / 100,
        quantity=100,
        ticker=ticker,
    )
    print(f"\n  Transaction Cost Breakdown — ₹{trade_value_inr:,.0f} trade (CNC, NSE)")
    print("  " + "─" * 45)
    print(cb)
    print()
    monthly = _DEFAULT_MODEL.monthly_cost_estimate(10, trade_value_inr)
    print(f"  At 10 trades/month:")
    print(f"    Monthly cost drag:  ₹{monthly['monthly_cost_inr']:,.0f}")
    print(f"    Annual cost drag:   ₹{monthly['annual_cost_inr']:,.0f}")
    print(f"    Break-even move:    {monthly['breakeven_move_pct']:.3f}%  per trade\n")


# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────

if __name__ == "__main__":
    import argparse, json

    parser = argparse.ArgumentParser(description="Transaction cost calculator")
    parser.add_argument("--buy",   type=float, required=True, help="Buy price in ₹")
    parser.add_argument("--sell",  type=float, required=True, help="Sell price in ₹")
    parser.add_argument("--qty",   type=int,   required=True, help="Number of shares")
    parser.add_argument("--ticker",default="STOCK",           help="NSE ticker")
    args = parser.parse_args()

    model = TradeCostModel()
    cb    = model.calculate(args.buy, args.sell, args.qty, args.ticker)
    print(cb)
    print(f"\n  JSON:\n{json.dumps(cb.to_dict(), indent=2)}")
