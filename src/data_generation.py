"""Synthetic data generation (Phase 2 — not yet implemented).

Generates a reproducible synthetic dataset for a wholesale distributor:
5,000 SKUs, 50 suppliers, 24 months of monthly demand, purchase orders,
supplier deliveries and an inventory snapshot.

Demand archetypes (stable, seasonal, trending, intermittent, highly
variable, new, declining) and supplier archetypes (reliable, slow,
unreliable) are generated deliberately so that downstream analytics have
realistic patterns to find. All data is synthetic; no real company data
is used.
"""
