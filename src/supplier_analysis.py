"""Supplier performance analytics (Phase 6 — not yet implemented).

Spend, SKU count, average lead time, lead-time variability (std, CV),
OTIF %, fill rate, average delay and purchase price variance.

OTIF (per PO line): cumulative quantity received by the ORIGINAL promised
date (+ tolerance) >= ordered quantity (x tolerance). Early receipts count
as on time. Supplier-level lead-time std feeds safety stock (sigma_L).
See docs/methodology.md section 12.
"""
