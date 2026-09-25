"""Replenishment engine (Phase 8 — not yet implemented).

Order-up-to logic with MOQ and order-multiple rounding:
    If inventory_position < ROP:
        raw_qty   = target_level - inventory_position
        order_qty = round_up_to_multiple(max(raw_qty, MOQ), order_multiple)
    Else: no order.

Every recommendation carries a reason code: STOCKOUT_RISK, BELOW_ROP,
SAFETY_STOCK_RISK, EXCESS_ALREADY_PRESENT or NO_ORDER_REQUIRED.
"""
