"""Supplier performance analytics (Phase 7 — not yet implemented).

Spend, SKU count, average lead time, lead-time variability (std, CV),
OTIF %, average delay, fill rate and purchase price variance.

OTIF: a delivery line counts only if it is BOTH on time (received <=
promised date + tolerance) AND in full (received qty >= ordered qty x
tolerance). See docs/methodology.md section 12.
"""
