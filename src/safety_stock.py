"""Safety stock, lead-time demand and reorder point (Phase 6 — not yet implemented).

Demand-only safety stock:
    SS = Z x sigma_d x sqrt(L)
Demand + lead-time variability:
    SS = Z x sqrt(L x sigma_d^2 + d^2 x sigma_L^2)
Reorder point:
    ROP = d x L + SS

where d / sigma_d are mean / std of demand per period, L / sigma_L are mean /
std of lead time in the same period unit, and Z = norm.ppf(service level).
See docs/methodology.md sections 8-10.
"""
