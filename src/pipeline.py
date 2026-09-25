"""End-to-end pipeline orchestration (built up across Phases 2-11).

Planned stages, in dependency order:
generate -> validate -> classify (ABC/XYZ) -> forecast (+ error sigma) ->
supplier analytics (lead-time sigma) -> safety stock / ROP ->
inventory health -> replenishment -> working capital / KPIs ->
scenarios -> export.

Run with ``python -m src.pipeline``.
"""

from __future__ import annotations

import logging

from src.config import get_config

logger = logging.getLogger(__name__)

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def main() -> None:
    """Entry point. Stages are wired in as each phase is implemented."""
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    config = get_config()
    logger.info(
        "Configuration loaded (seed=%s, SKUs=%s)",
        config.data.random_seed,
        config.data.n_skus,
    )
    logger.info("No pipeline stages implemented yet (Phase 1: scaffolding only).")


if __name__ == "__main__":
    main()
