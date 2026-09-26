"""End-to-end pipeline orchestration (built up across Phases 2-11).

Planned stages, in dependency order:
generate -> validate -> classify (ABC/XYZ) -> forecast (+ error sigma) ->
supplier analytics (lead-time sigma) -> safety stock / ROP ->
inventory health -> replenishment -> working capital / KPIs ->
scenarios -> export.

Implemented so far: generate, validate.

Run with ``python -m src.pipeline [--seed N]``.
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import replace
from pathlib import Path

from src.config import Config, get_config
from src.data_generation import generate_dataset
from src.data_validation import has_blocking_errors, run_all_checks

logger = logging.getLogger(__name__)

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
DATA_QUALITY_REPORT = "data_quality_report.csv"


class DataQualityError(RuntimeError):
    """Raised when blocking data-quality errors are found."""


def run(config: Config) -> None:
    """Run all implemented stages."""
    dataset = generate_dataset(config.data)
    dataset.save(config.paths)

    report = run_all_checks(dataset.raw_tables(), dataset.as_of)
    output_dir = Path(config.paths.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    report.to_csv(output_dir / DATA_QUALITY_REPORT, index=False)
    logger.info("Data-quality report written to %s", output_dir / DATA_QUALITY_REPORT)
    if has_blocking_errors(report):
        raise DataQualityError("Blocking data-quality errors; see the data-quality report")


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Inventory Control Tower pipeline")
    parser.add_argument("--seed", type=int, default=None, help="synthetic data seed (default 42)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)

    config = get_config()
    if args.seed is not None:
        config = replace(config, data=replace(config.data, random_seed=args.seed))
    logger.info("Running pipeline (seed=%s, SKUs=%s)", config.data.random_seed, config.data.n_skus)
    run(config)


if __name__ == "__main__":
    main()
