"""Safety stock, protection-interval demand and reorder point (Phase 7 — not yet implemented).

Periodic review every R months, supplier lead time L (months):
    P   = L + R                                   protection interval
    SS  = Z x sqrt(P x sigma^2 + d^2 x sigma_L^2) sigma = forecast-error RMSE
                                                  (fallback: demand std)
    s   = d x P + SS                              reorder point
With R = 0 and sigma_L = 0 this reduces to the textbook
    SS = Z x sigma x sqrt(L),  ROP = d x L + SS.

Z = norm.ppf(cycle service level); sigma_L comes from supplier analytics.
See docs/methodology.md sections 7-10.
"""
