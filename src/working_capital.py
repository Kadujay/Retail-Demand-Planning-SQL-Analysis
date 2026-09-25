"""Working capital, inventory productivity and executive KPIs (Phase 9 — not yet implemented).

    inventory_value      = sum(on_hand x unit_cost)
    carrying_cost        = inventory_value x annual_carrying_cost_rate
    inventory_turns      = annual COGS / inventory_value
    DIO                  = 365 / inventory_turns
    GMROI                = annual gross margin / inventory_value
    excess / dead share  = excess or dead value / inventory_value

Values split by ABC class, supplier and category. See methodology section 14.
"""
