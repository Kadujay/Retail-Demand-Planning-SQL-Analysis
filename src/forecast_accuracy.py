"""Forecast accuracy, bias and tracking signal (Phase 5 — not yet implemented).

    MAE  = mean(|A - F|)
    RMSE = sqrt(mean((A - F)^2))
    WAPE = sum(|A - F|) / sum(A)          (NaN when sum(A) = 0)
    Bias = sum(F - A) / sum(A)            (positive = over-forecasting)
    Tracking signal = sum(F - A) / MAE    (|TS| > 4 => biased forecast)
    FVA  = WAPE(naive) - WAPE(method)     (positive = method adds value)

MAPE is deliberately not used (breaks on zero-demand periods).
See docs/methodology.md section 6.
"""
