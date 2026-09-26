"""PostgreSQL data layer: create the schema, load validated data, build views.

Lineage::

    data/raw/*.csv          raw ERP extract (never modified)
        |  python -m src.data_validation     -> ACCEPTED / WARNINGS / REJECTED
        v
    core.*                  validated, typed, constrained tables (1:1 with raw)
        |  sql/transformations.sql
        v
    analytics.v_*           analysis-ready views (lineage stays in SQL)
        |  sql/analysis.sql / export
        v
    outputs/sql_answers/*.csv, data/processed/*.parquet  -> KPIs & decisions

Commands (run after ``docker compose up -d``)::

    python -m src.database init         # (re)create schemas, calendar, parameters
    python -m src.database load         # validate data/raw, then load core.*
    python -m src.database load --dirty # demonstrate a rejected load (data/raw_dirty)
    python -m src.database transform    # create/refresh analytics views
    python -m src.database analyze      # run sql/analysis.sql -> outputs/sql_answers/
    python -m src.database export       # analytics views -> data/processed/*.parquet
    python -m src.database build        # all of the above for the clean data

Every load attempt is recorded in ``audit.load_run`` and
``audit.data_quality_result``. Data that fails validation is never loaded.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import pandas as pd
from sqlalchemy import Engine, create_engine, text

from src.config import DAYS_PER_MONTH, Config, get_config, get_database_url, service_level_to_z
from src.data_validation import (
    ACCEPTED_WITH_WARNINGS,
    REJECTED,
    overall_status,
    print_summary,
    run_all_checks,
)
from src.raw_data import infer_as_of, load_raw_tables

logger = logging.getLogger(__name__)

# Raw CSV -> core table. Load order respects foreign keys.
RAW_TO_CORE: dict[str, str] = {
    "suppliers": "dim_supplier",
    "products": "dim_product",
    "demand_monthly": "fact_demand",
    "inventory_snapshot": "fact_inventory",
    "purchase_orders": "fact_purchase_order",
    "supplier_deliveries": "fact_supplier_delivery",
}

# Analytical views exported to Parquet for the Python phases (4+).
PARQUET_EXPORTS: tuple[str, ...] = (
    "v_demand_monthly",
    "v_inventory_monthly",
    "v_po_line_status",
    "v_supplier_performance",
    "v_inventory_position",
)

QUERY_NAME = re.compile(r"^--\s*name:\s*(\w+)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class LoadResult:
    """Outcome of one load attempt."""

    run_id: int
    status: str
    rows_loaded: int
    report: pd.DataFrame


def get_engine(database: str | None = None) -> Engine:
    """SQLAlchemy engine from POSTGRES_* environment variables (see .env.example)."""
    return create_engine(get_database_url(database), future=True)


def run_sql_file(engine: Engine, path: Path) -> None:
    """Execute a SQL script (multiple statements) in one transaction."""
    sql = Path(path).read_text(encoding="utf-8")
    with engine.begin() as conn:
        conn.exec_driver_sql(sql)
    logger.info("Executed %s", Path(path).name)


# --------------------------------------------------------------------------
# init
# --------------------------------------------------------------------------
def planning_parameters(config: Config) -> list[tuple[str, float, str, str]]:
    """Thresholds the SQL views need, taken from config (single source of truth)."""
    return [
        ("days_per_month", DAYS_PER_MONTH, "config.DAYS_PER_MONTH", "365 / 12"),
        (
            "review_period_days",
            config.replenishment.review_period_months * DAYS_PER_MONTH,
            "ReplenishmentConfig",
            "Time between planning runs",
        ),
        (
            "trailing_demand_months",
            config.sql_screening.trailing_demand_months,
            "SqlScreeningConfig",
            "Window for average demand in SQL screens",
        ),
        (
            "screening_service_level_z",
            service_level_to_z(config.sql_screening.service_level),
            "SqlScreeningConfig",
            "Z for the uniform screening service level",
        ),
        (
            "high_cover_months",
            config.sql_screening.high_cover_months,
            "SqlScreeningConfig",
            "Months of supply above which stock is screened as excess",
        ),
        (
            "dead_stock_months",
            config.inventory_health.dead_stock_months_without_demand,
            "InventoryHealthConfig",
            "Months without demand for a dead-stock candidate",
        ),
        (
            "on_time_tolerance_days",
            config.supplier.on_time_tolerance_days,
            "SupplierConfig",
            "Grace days after the promised date for OTIF",
        ),
        (
            "in_full_tolerance",
            config.supplier.in_full_tolerance,
            "SupplierConfig",
            "Share of ordered quantity that counts as in full",
        ),
        (
            "stale_open_po_days",
            config.data_quality.stale_open_po_days,
            "DataQualityConfig",
            "Days past promise after which an open line is stale",
        ),
        (
            "otif_target",
            config.supplier.otif_target,
            "SupplierConfig",
            "Reliable segment threshold",
        ),
        ("otif_watch", config.supplier.otif_watch, "SupplierConfig", "At-risk segment threshold"),
        (
            "lead_time_cv_high",
            config.supplier.lead_time_cv_high,
            "SupplierConfig",
            "High lead-time variability threshold",
        ),
        (
            "min_lines_for_supplier_stats",
            config.supplier.min_receipts_for_stats,
            "SupplierConfig",
            "Minimum evaluable lines before ranking a supplier",
        ),
    ]


def init_database(engine: Engine, config: Config) -> None:
    """Drop and recreate schemas, seed the calendar and write planning parameters."""
    run_sql_file(engine, config.paths.sql_dir / "schema.sql")
    run_sql_file(engine, config.paths.sql_dir / "seed.sql")
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO analytics.planning_parameter (name, value, source, description) "
                "VALUES (:name, :value, :source, :description)"
            ),
            [
                {"name": n, "value": float(v), "source": s, "description": d}
                for n, v, s, d in planning_parameters(config)
            ],
        )


# --------------------------------------------------------------------------
# load
# --------------------------------------------------------------------------
def _start_run(engine: Engine, label: str, folder: Path) -> int:
    with engine.begin() as conn:
        return conn.execute(
            text(
                "INSERT INTO audit.load_run (dataset_label, source_folder, status) "
                "VALUES (:label, :folder, 'RUNNING') RETURNING run_id"
            ),
            {"label": label, "folder": str(folder)},
        ).scalar_one()


def _finish_run(engine: Engine, run_id: int, **fields: object) -> None:
    assignments = ", ".join(f"{k} = :{k}" for k in fields)
    with engine.begin() as conn:
        conn.execute(
            text(
                f"UPDATE audit.load_run SET {assignments}, finished_at = now() "
                "WHERE run_id = :run_id"
            ),
            {**fields, "run_id": run_id},
        )


def _record_quality(engine: Engine, run_id: int, report: pd.DataFrame) -> None:
    rows = report.rename(columns={"check": "check_name", "table": "table_name"}).assign(
        run_id=run_id
    )
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO audit.data_quality_result (run_id, check_name, table_name, severity, "
                "status, failed_rows, total_rows, description) VALUES (:run_id, :check_name, "
                ":table_name, :severity, :status, :failed_rows, :total_rows, :description)"
            ),
            rows[
                [
                    "run_id",
                    "check_name",
                    "table_name",
                    "severity",
                    "status",
                    "failed_rows",
                    "total_rows",
                    "description",
                ]
            ].to_dict("records"),
        )


def _copy_tables(engine: Engine, tables: dict[str, pd.DataFrame]) -> int:
    """Replace all core data in ONE transaction using PostgreSQL COPY."""
    total = 0
    raw = engine.raw_connection()
    try:
        with raw.cursor() as cur:
            cur.execute(
                "TRUNCATE " + ", ".join(f"core.{t}" for t in reversed(RAW_TO_CORE.values()))
            )
            for raw_name, core_name in RAW_TO_CORE.items():
                df = tables[raw_name]
                cols = ", ".join(df.columns)
                # Stream the frame as CSV: empty unquoted fields become NULL.
                payload = df.to_csv(index=False, header=False, date_format="%Y-%m-%d")
                with cur.copy(
                    f"COPY core.{core_name} ({cols}) FROM STDIN WITH (FORMAT csv)"
                ) as copy:
                    copy.write(payload.encode("utf-8"))
                total += len(df)
                logger.info("Loaded %s rows into core.%s", f"{len(df):,}", core_name)
        raw.commit()
    except Exception:
        raw.rollback()
        raise
    finally:
        raw.close()
    return total


def load_validated_data(engine: Engine, folder: Path, label: str = "clean") -> LoadResult:
    """Validate the CSV extract in ``folder`` and load it into core.* if accepted.

    Rejected data is never loaded: the audit tables record why, and the
    previously loaded (valid) data stays in place.
    """
    run_id = _start_run(engine, label, folder)
    tables = load_raw_tables(folder)
    as_of = infer_as_of(tables)
    report = run_all_checks(tables, as_of)
    _record_quality(engine, run_id, report)
    status = overall_status(report)
    counts = {
        "as_of_date": as_of.date(),
        "failed_checks": int((report["status"] == "FAIL").sum()),
        "warning_checks": int((report["status"] == "WARN").sum()),
    }
    if status == REJECTED:
        _finish_run(
            engine,
            run_id,
            status=REJECTED,
            rows_loaded=0,
            message="Validation failed; nothing loaded",
            **counts,
        )
        logger.error("Load %s REJECTED: %d failed checks", run_id, counts["failed_checks"])
        return LoadResult(run_id, REJECTED, 0, report)
    try:
        rows = _copy_tables(engine, tables)
    except Exception as exc:  # database constraint = second line of defence
        _finish_run(
            engine,
            run_id,
            status="FAILED",
            rows_loaded=0,
            message=f"Database rejected load: {exc}"[:1000],
            **counts,
        )
        raise
    message = "Loaded with warnings" if status == ACCEPTED_WITH_WARNINGS else "Loaded"
    _finish_run(engine, run_id, status=status, rows_loaded=rows, message=message, **counts)
    logger.info("Load %s %s: %s rows", run_id, status, f"{rows:,}")
    return LoadResult(run_id, status, rows, report)


# --------------------------------------------------------------------------
# transform / analyze / export
# --------------------------------------------------------------------------
def build_transformations(engine: Engine, config: Config) -> None:
    """Create or refresh the analytics views."""
    run_sql_file(engine, config.paths.sql_dir / "transformations.sql")


def split_named_queries(sql: str) -> dict[str, str]:
    """Split a file of ``-- name: x`` blocks into {name: query}."""
    parts = QUERY_NAME.split(sql)
    return {name: body.strip() for name, body in zip(parts[1::2], parts[2::2], strict=True)}


def _clean_types(df: pd.DataFrame) -> pd.DataFrame:
    """Decimal columns (PostgreSQL numeric) -> float for pandas/Parquet."""
    for col in df.columns:
        if df[col].map(lambda v: isinstance(v, Decimal)).any():
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")
    return df


def run_analysis(engine: Engine, config: Config) -> dict[str, pd.DataFrame]:
    """Run every named query in sql/analysis.sql and save each answer as CSV."""
    queries = split_named_queries((config.paths.sql_dir / "analysis.sql").read_text())
    out_dir = config.paths.sql_answers_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    results = {}
    with engine.connect() as conn:
        for name, sql in queries.items():
            df = _clean_types(pd.read_sql(text(sql), conn))
            df.to_csv(out_dir / f"{name}.csv", index=False)
            results[name] = df
            logger.info("%s: %d rows", name, len(df))
    return results


def export_parquet(engine: Engine, config: Config) -> dict[str, Path]:
    """Export the analysis-ready views to Parquet (typed, compressed)."""
    out_dir = Path(config.paths.processed_data_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = {}
    with engine.connect() as conn:
        for view in PARQUET_EXPORTS:
            df = _clean_types(pd.read_sql(text(f"SELECT * FROM analytics.{view}"), conn))
            path = out_dir / f"{view.removeprefix('v_')}.parquet"
            df.to_parquet(path, index=False)
            written[view] = path
            logger.info("Exported analytics.%s (%s rows) -> %s", view, f"{len(df):,}", path.name)
    return written


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Inventory Control Tower database layer")
    parser.add_argument(
        "command", choices=["init", "load", "transform", "analyze", "export", "build"]
    )
    parser.add_argument(
        "--dirty", action="store_true", help="load data/raw_dirty (to demo rejection)"
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    config = get_config()
    engine = get_engine()
    if args.command in ("init", "build"):
        init_database(engine, config)
    if args.command in ("load", "build"):
        folder = config.paths.raw_dirty_data_dir if args.dirty else config.paths.raw_data_dir
        result = load_validated_data(engine, folder, "dirty" if args.dirty else "clean")
        print_summary(result.report)
        print(f"Load run {result.run_id}: {result.status} ({result.rows_loaded:,} rows loaded)")
        if result.status == REJECTED:
            sys.exit(1)
    if args.command in ("transform", "build"):
        build_transformations(engine, config)
    if args.command in ("analyze", "build"):
        run_analysis(engine, config)
    if args.command in ("export", "build"):
        export_parquet(engine, config)


if __name__ == "__main__":
    main()
