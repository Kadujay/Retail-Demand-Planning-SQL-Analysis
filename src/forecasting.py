"""Statistical forecasting (Phase 5 — not yet implemented).

Candidate methods (transparent, explainable baselines):
    * Moving average (MA)
    * Weighted moving average (WMA) / simple exponential smoothing (SES)
    * Seasonal naive (same month last year) where seasonality exists

The best method per SKU is selected on a time-based hold-out (never a
random split). See docs/methodology.md section 5.
"""
