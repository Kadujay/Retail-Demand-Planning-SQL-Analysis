"""Replenishment engine (Phase 8 — not yet implemented).

Periodic-review (R, s, S) policy with MOQ and order multiples:
    Q   = max(EOQ, MOQ) rounded up to order multiple   lot size
    EOQ = sqrt(2 x annual_demand x ordering_cost / (unit_cost x carrying_rate))
    S   = s + Q                                        policy maximum
    If inventory_position <= s:
        order_qty = ceil_to_multiple(max(S - IP, MOQ), order_multiple)
    Else: no order; projected reorder date = today + (IP - s) / daily demand.

Every recommendation carries a reason code: STOCKOUT_RISK,
SAFETY_STOCK_RISK, BELOW_ROP, EXCESS_ALREADY_PRESENT or NO_ORDER_REQUIRED.
See docs/methodology.md section 13.
"""
