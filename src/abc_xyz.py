"""ABC, XYZ and ABC-XYZ classification (Phase 4 — not yet implemented).

ABC (value):
    annual_consumption_value = annual_demand_units x unit_cost
    Rank descending; classify by cumulative share of total value using
    ``ABCConfig`` thresholds (default A <= 80%, B <= 95%, C remainder).

XYZ (variability):
    CV = std(monthly demand) / mean(monthly demand)
    Classified with ``XYZConfig`` thresholds. Zero-demand and intermittent
    SKUs are handled explicitly rather than producing divide-by-zero CVs.

See docs/methodology.md sections 2-4.
"""
