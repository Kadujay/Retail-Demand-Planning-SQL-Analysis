"""ABC, XYZ and ABC-XYZ classification (Phase 4 — not yet implemented).

ABC (value), trailing ``ABCConfig.window_months``:
    annual_consumption_value = annual_demand_units x unit_cost
    Rank descending; classify by cumulative share of total value using
    ``ABCConfig`` thresholds (default A <= 80%, B <= 95%, C remainder).

XYZ (variability), same window:
    ADI = periods / non-zero periods   -> ADI > 1.32 => intermittent => Z
    CV  = std(monthly demand) / mean(monthly demand)
    Zero-demand SKUs -> NO_DEMAND; young SKUs -> NEW (flagged).

See docs/methodology.md sections 2-4.
"""
