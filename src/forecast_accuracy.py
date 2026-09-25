"""Forecast accuracy and bias metrics (Phase 5 — not yet implemented).

    MAE  = mean(|A - F|)
    RMSE = sqrt(mean((A - F)^2))
    WAPE = sum(|A - F|) / sum(A)          (undefined when sum(A) = 0)
    Bias = sum(F - A) / sum(A)            (positive = over-forecasting)

Zero-actual periods are handled safely (no division by zero; MAPE is
deliberately not used). See docs/methodology.md section 6.
"""
