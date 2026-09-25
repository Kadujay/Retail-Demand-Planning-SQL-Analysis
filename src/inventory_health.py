"""Inventory health, days of supply, excess and dead stock (Phase 7 — not yet implemented).

    inventory_position = on_hand + open_po_qty - allocated_qty   (lost-sales model)
    days_of_supply     = on_hand / forecast daily demand
    policy_max (S)     = s + Q          (same max the replenishment engine uses)
    excess_on_hand     = max(0, on_hand - (S + tolerance_days x daily demand))
    stockout_prob      = 1 - Phi((IP - d x P) / sigma_P),  P = L + R

Status (priority order): STOCKOUT, CRITICAL, BELOW_REORDER_POINT,
DEAD_STOCK, EXCESS, HEALTHY. See docs/methodology.md section 11.
"""
