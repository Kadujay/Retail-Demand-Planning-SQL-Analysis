"""Inventory health, days of supply, excess and dead stock (Phase 6 — not yet implemented).

    inventory_position = on_hand + open_po_qty - allocated_qty
    days_of_supply     = on_hand / average_daily_demand
    excess_units       = max(0, on_hand - target_max_units)

Status (priority order): STOCKOUT, CRITICAL, BELOW_REORDER_POINT,
DEAD_STOCK, EXCESS, HEALTHY. See docs/business_logic.md.
"""
