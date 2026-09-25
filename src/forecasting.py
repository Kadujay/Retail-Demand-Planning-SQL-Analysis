"""Statistical forecasting (Phase 5 — not yet implemented).

Candidate methods (transparent, explainable):
    * Naive (last month)           — baseline for forecast value added
    * Moving average (MA)          — stable demand
    * Simple exponential smoothing — stable, recency-weighted
    * Holt's linear trend          — trending / declining demand
    * Seasonal naive (t - 12)      — seasonal demand, >= 24 months history

The best method per SKU is selected by hold-out WAPE on a time-based split
(never a random split). New / intermittent SKUs are flagged for planner
review. See docs/methodology.md section 5.
"""
