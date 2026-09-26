"""Data-quality checks run before any data is loaded or analysed.

Each check answers one question an analyst must ask of an ERP extract
("are there negative stock balances?", "does every PO line point to a real
supplier?") and returns the number of failing rows with a severity:

* ERROR   — the data is wrong; analysis on it would be misleading. Loading
            into PostgreSQL is refused.
* WARNING — suspicious but possible in real operations (e.g. price below
            cost on a clearance item, a stale open PO). Data is loaded and
            the warning is recorded.
* INFO    — expected business condition worth knowing (e.g. SKUs with no
            demand in 12 months — dead-stock candidates, not bad data).

The overall verdict is ACCEPTED, ACCEPTED_WITH_WARNINGS or REJECTED.

Usage::

    python -m src.data_validation            # validate data/raw
    python -m src.data_validation --dirty    # validate data/raw_dirty

The exit code is 1 when the data is REJECTED, so scripts cannot ignore it.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from src.config import DataQualityConfig, PathConfig
from src.raw_data import infer_as_of, load_raw_tables

logger = logging.getLogger(__name__)

Tables = Mapping[str, pd.DataFrame]

ACCEPTED = "ACCEPTED"
ACCEPTED_WITH_WARNINGS = "ACCEPTED_WITH_WARNINGS"
REJECTED = "REJECTED"

PRIMARY_KEYS: dict[str, list[str]] = {
    "suppliers": ["supplier_id"],
    "products": ["sku_id"],
    "demand_monthly": ["sku_id", "month_start"],
    "inventory_snapshot": ["sku_id", "month_end"],
    "purchase_orders": ["po_line_id"],
    "supplier_deliveries": ["receipt_id"],
}

# Required = the row is unusable without it (ERROR if missing).
REQUIRED_COLUMNS: dict[str, list[str]] = {
    "suppliers": ["supplier_id", "supplier_name", "quoted_lead_time_days"],
    "products": [
        "sku_id",
        "category",
        "supplier_id",
        "unit_cost",
        "unit_price",
        "moq",
        "order_multiple",
        "supplier_lead_time_days",
    ],
    "demand_monthly": ["sku_id", "month_start", "ordered_qty", "shipped_qty"],
    "inventory_snapshot": ["sku_id", "month_end", "on_hand_qty", "allocated_qty"],
    "purchase_orders": [
        "po_line_id",
        "po_number",
        "sku_id",
        "supplier_id",
        "order_date",
        "promised_date",
        "ordered_qty",
        "unit_price",
        "status",
    ],
    "supplier_deliveries": ["receipt_id", "po_line_id", "receipt_date", "received_qty"],
}

# Optional = useful for analysis but the row still works without it (WARNING).
OPTIONAL_COLUMNS: dict[str, list[str]] = {
    "suppliers": ["region"],
    "products": ["launch_date", "lifecycle_status"],
}

PO_STATUSES = {"OPEN", "PARTIALLY_RECEIVED", "CLOSED", "CLOSED_SHORT"}


@dataclass(frozen=True)
class CheckResult:
    """Outcome of one data-quality check."""

    check: str
    table: str
    severity: str
    failed_rows: int
    total_rows: int
    description: str

    @property
    def status(self) -> str:
        """PASS, or FAIL / WARN / INFO depending on severity."""
        if self.failed_rows == 0:
            return "PASS"
        return {"ERROR": "FAIL", "WARNING": "WARN"}.get(self.severity, "INFO")


@dataclass(frozen=True)
class Context:
    """Everything a check needs besides the tables."""

    as_of: pd.Timestamp
    rules: DataQualityConfig


def _count(mask: pd.Series) -> int:
    return int(mask.fillna(False).sum())


def _receipts_with_po(t: Tables) -> pd.DataFrame:
    """Receipts joined to their PO line (order date, ordered qty)."""
    po = t["purchase_orders"][["po_line_id", "order_date", "ordered_qty"]]
    return t["supplier_deliveries"].merge(po, on="po_line_id", how="left")


# --------------------------------------------------------------------------
# Structural checks
# --------------------------------------------------------------------------
def check_duplicates(t: Tables, ctx: Context) -> list[CheckResult]:
    """Duplicate primary keys double-count demand, stock or spend."""
    return [
        CheckResult(
            "duplicate_records",
            name,
            "ERROR",
            _count(t[name].duplicated(keys)),
            len(t[name]),
            f"Rows sharing primary key {keys}",
        )
        for name, keys in PRIMARY_KEYS.items()
    ]


def check_missing_values(t: Tables, ctx: Context) -> list[CheckResult]:
    """Missing required fields make rows unusable; missing optional ones limit analysis."""
    results = [
        CheckResult(
            "missing_values",
            name,
            "ERROR",
            _count(t[name][cols].isna().any(axis=1)),
            len(t[name]),
            f"Null in required columns {cols}",
        )
        for name, cols in REQUIRED_COLUMNS.items()
    ]
    results.append(
        CheckResult(
            "missing_unit_cost",
            "products",
            "ERROR",
            _count(t["products"]["unit_cost"].isna()),
            len(t["products"]),
            "Unit cost missing: inventory cannot be valued",
        )
    )
    results += [
        CheckResult(
            "missing_optional_fields",
            name,
            "WARNING",
            _count(t[name][cols].isna().any(axis=1)),
            len(t[name]),
            f"Null in optional columns {cols} (analysis limited, row still usable)",
        )
        for name, cols in OPTIONAL_COLUMNS.items()
    ]
    return results


def check_referential_integrity(t: Tables, ctx: Context) -> list[CheckResult]:
    """Every fact row must point to an existing SKU / supplier / PO line."""
    skus = set(t["products"]["sku_id"])
    suppliers = set(t["suppliers"]["supplier_id"])
    po_lines = set(t["purchase_orders"]["po_line_id"])
    specs = [
        ("missing_sku", "demand_monthly", "sku_id", skus),
        ("missing_sku", "inventory_snapshot", "sku_id", skus),
        ("missing_sku", "purchase_orders", "sku_id", skus),
        ("missing_supplier", "products", "supplier_id", suppliers),
        ("missing_supplier", "purchase_orders", "supplier_id", suppliers),
        ("missing_po_line", "supplier_deliveries", "po_line_id", po_lines),
    ]
    results = [
        CheckResult(
            check,
            table,
            "ERROR",
            _count(~t[table][col].isin(valid)),
            len(t[table]),
            f"{table}.{col} not found in master data",
        )
        for check, table, col, valid in specs
    ]
    # A real supplier, but not the one the item master says supplies this SKU.
    po = t["purchase_orders"]
    primary = po["sku_id"].map(t["products"].set_index("sku_id")["supplier_id"])
    mismatch = po["supplier_id"].isin(suppliers) & primary.notna() & (po["supplier_id"] != primary)
    results.append(
        CheckResult(
            "po_supplier_mismatch",
            "purchase_orders",
            "WARNING",
            _count(mismatch),
            len(po),
            "PO supplier differs from the SKU's supplier in the item master",
        )
    )
    return results


# --------------------------------------------------------------------------
# Business-rule checks
# --------------------------------------------------------------------------
def check_prices(t: Tables, ctx: Context) -> list[CheckResult]:
    """Costs and prices must be positive; price below cost is suspicious."""
    p, po = t["products"], t["purchase_orders"]
    return [
        CheckResult(
            "non_positive_cost",
            "products",
            "ERROR",
            _count(p["unit_cost"] <= 0),
            len(p),
            "Unit cost <= 0",
        ),
        CheckResult(
            "non_positive_price",
            "products",
            "ERROR",
            _count(p["unit_price"] <= 0),
            len(p),
            "Selling price <= 0",
        ),
        CheckResult(
            "price_below_cost",
            "products",
            "WARNING",
            _count(p["unit_price"] < p["unit_cost"]),
            len(p),
            "Selling price below unit cost (negative margin)",
        ),
        CheckResult(
            "non_positive_po_price",
            "purchase_orders",
            "ERROR",
            _count(po["unit_price"] <= 0),
            len(po),
            "PO unit price <= 0",
        ),
    ]


def check_quantities(t: Tables, ctx: Context) -> list[CheckResult]:
    """Quantities must be non-negative and physically consistent."""
    d, inv, po, rcv, p = (
        t[k]
        for k in (
            "demand_monthly",
            "inventory_snapshot",
            "purchase_orders",
            "supplier_deliveries",
            "products",
        )
    )
    received = po["po_line_id"].map(rcv.groupby("po_line_id")["received_qty"].sum()).fillna(0)
    rules = ctx.rules
    over = (received > po["ordered_qty"] * (1 + rules.over_receipt_tolerance)) & (
        received <= po["ordered_qty"] * rules.implausible_receipt_multiple
    )
    implausible = received > po["ordered_qty"] * rules.implausible_receipt_multiple
    return [
        CheckResult(
            "negative_inventory",
            "inventory_snapshot",
            "ERROR",
            _count((inv["on_hand_qty"] < 0) | (inv["allocated_qty"] < 0)),
            len(inv),
            "Negative on-hand or allocated quantity",
        ),
        CheckResult(
            "allocated_exceeds_on_hand",
            "inventory_snapshot",
            "WARNING",
            _count(inv["allocated_qty"] > inv["on_hand_qty"]),
            len(inv),
            "Allocated more than physically on hand",
        ),
        CheckResult(
            "negative_demand",
            "demand_monthly",
            "ERROR",
            _count((d["ordered_qty"] < 0) | (d["shipped_qty"] < 0)),
            len(d),
            "Negative ordered or shipped quantity",
        ),
        CheckResult(
            "shipped_exceeds_ordered",
            "demand_monthly",
            "ERROR",
            _count(d["shipped_qty"] > d["ordered_qty"]),
            len(d),
            "Shipped more than customers ordered",
        ),
        CheckResult(
            "non_positive_po_qty",
            "purchase_orders",
            "ERROR",
            _count(po["ordered_qty"] <= 0),
            len(po),
            "PO line quantity <= 0",
        ),
        CheckResult(
            "invalid_po_status",
            "purchase_orders",
            "ERROR",
            _count(~po["status"].isin(PO_STATUSES)),
            len(po),
            f"PO status not in {sorted(PO_STATUSES)}",
        ),
        CheckResult(
            "non_positive_receipt_qty",
            "supplier_deliveries",
            "ERROR",
            _count(rcv["received_qty"] <= 0),
            len(rcv),
            "Receipt quantity <= 0",
        ),
        CheckResult(
            "over_receipt",
            "purchase_orders",
            "WARNING",
            _count(over),
            len(po),
            f"Received more than {rules.over_receipt_tolerance:.0%} above ordered",
        ),
        CheckResult(
            "receipt_qty_implausible",
            "purchase_orders",
            "ERROR",
            _count(implausible),
            len(po),
            f"Received > {rules.implausible_receipt_multiple:g}x ordered "
            "(likely unit-of-measure error)",
        ),
        CheckResult(
            "invalid_moq",
            "products",
            "ERROR",
            _count((p["moq"] < 1) | (p["order_multiple"] < 1)),
            len(p),
            "MOQ or order multiple < 1",
        ),
        CheckResult(
            "moq_not_multiple",
            "products",
            "WARNING",
            _count(p["moq"] % p["order_multiple"] != 0),
            len(p),
            "MOQ not a whole number of order multiples",
        ),
    ]


def check_duplicate_receipts(t: Tables, ctx: Context) -> list[CheckResult]:
    """Same PO line, same day, same quantity twice = a receipt posted twice."""
    rcv = t["supplier_deliveries"]
    dupes = rcv.duplicated(["po_line_id", "receipt_date", "received_qty"])
    return [
        CheckResult(
            "duplicate_receipt",
            "supplier_deliveries",
            "ERROR",
            _count(dupes),
            len(rcv),
            "Identical receipt (line, date, qty) posted more than once",
        )
    ]


def check_dates(t: Tables, ctx: Context) -> list[CheckResult]:
    """Dates must be in order and not in the future."""
    po, d, inv, p = (
        t[k] for k in ("purchase_orders", "demand_monthly", "inventory_snapshot", "products")
    )
    rcv = _receipts_with_po(t)
    as_of = ctx.as_of
    launch_month = d["sku_id"].map(p.set_index("sku_id")["launch_date"]).dt.to_period("M")
    return [
        CheckResult(
            "promised_before_order",
            "purchase_orders",
            "ERROR",
            _count(po["promised_date"] < po["order_date"]),
            len(po),
            "Promised date earlier than order date",
        ),
        CheckResult(
            "receipt_before_order",
            "supplier_deliveries",
            "ERROR",
            _count(rcv["receipt_date"] < rcv["order_date"]),
            len(rcv),
            "Goods received before they were ordered (backdated or mis-keyed)",
        ),
        CheckResult(
            "future_dated_records",
            "all",
            "ERROR",
            _count(po["order_date"] > as_of)
            + _count(rcv["receipt_date"] > as_of)
            + _count(d["month_start"] > as_of)
            + _count(inv["month_end"] > as_of),
            len(po) + len(rcv) + len(d) + len(inv),
            f"Dated after as-of {as_of.date()}",
        ),
        CheckResult(
            "launch_after_as_of",
            "products",
            "ERROR",
            _count(p["launch_date"] > as_of),
            len(p),
            "Product launched after as-of date",
        ),
        CheckResult(
            "demand_before_launch",
            "demand_monthly",
            "ERROR",
            _count(d["month_start"].dt.to_period("M") < launch_month),
            len(d),
            "Demand recorded before the product existed",
        ),
    ]


def check_lead_times(t: Tables, ctx: Context) -> list[CheckResult]:
    """Quoted and actual lead times must be credible."""
    limit = ctx.rules.max_credible_lead_time_days
    rcv = _receipts_with_po(t)
    actual = (rcv["receipt_date"] - rcv["order_date"]).dt.days
    quoted = pd.concat(
        [t["suppliers"]["quoted_lead_time_days"], t["products"]["supplier_lead_time_days"]]
    )
    return [
        CheckResult(
            "impossible_quoted_lead_time",
            "suppliers/products",
            "ERROR",
            _count((quoted <= 0) | (quoted > limit)),
            len(quoted),
            f"Quoted lead time <= 0 or > {limit} days",
        ),
        CheckResult(
            "impossible_actual_lead_time",
            "supplier_deliveries",
            "ERROR",
            _count((actual < 0) | (actual > limit)),
            len(actual),
            f"Actual lead time < 0 or > {limit} days",
        ),
    ]


def check_open_orders(t: Tables, ctx: Context) -> list[CheckResult]:
    """Open lines long past their promised date are probably never coming."""
    po = t["purchase_orders"]
    cutoff = ctx.as_of - pd.Timedelta(days=ctx.rules.stale_open_po_days)
    stale = po["status"].isin(["OPEN", "PARTIALLY_RECEIVED"]) & (po["promised_date"] < cutoff)
    return [
        CheckResult(
            "stale_open_po",
            "purchase_orders",
            "WARNING",
            _count(stale),
            len(po),
            f"Open PO line more than {ctx.rules.stale_open_po_days} days past its "
            "promised date (phantom supply in inventory position)",
        )
    ]


def check_zero_demand(t: Tables, ctx: Context) -> list[CheckResult]:
    """SKUs with no orders in 12 months: a business finding, not bad data."""
    d = t["demand_monthly"]
    recent = d[d["month_start"] > ctx.as_of - pd.DateOffset(months=12)]
    totals = recent.groupby("sku_id")["ordered_qty"].sum()
    return [
        CheckResult(
            "zero_demand_12m",
            "demand_monthly",
            "INFO",
            _count(totals == 0),
            t["products"]["sku_id"].nunique(),
            "SKUs with no customer orders in the last 12 months (dead-stock candidates)",
        )
    ]


CHECKS: tuple[Callable[[Tables, Context], list[CheckResult]], ...] = (
    check_duplicates,
    check_missing_values,
    check_referential_integrity,
    check_prices,
    check_quantities,
    check_duplicate_receipts,
    check_dates,
    check_lead_times,
    check_open_orders,
    check_zero_demand,
)


def run_all_checks(
    tables: Tables, as_of: pd.Timestamp, rules: DataQualityConfig | None = None
) -> pd.DataFrame:
    """Run every check and return the data-quality report (one row per check)."""
    ctx = Context(as_of=pd.Timestamp(as_of), rules=rules or DataQualityConfig())
    results = [r for check in CHECKS for r in check(tables, ctx)]
    report = pd.DataFrame(
        [
            {
                "check": r.check,
                "table": r.table,
                "severity": r.severity,
                "status": r.status,
                "failed_rows": r.failed_rows,
                "total_rows": r.total_rows,
                "description": r.description,
            }
            for r in results
        ]
    )
    logger.info("Data quality: %d checks -> %s", len(report), overall_status(report))
    return report


def has_blocking_errors(report: pd.DataFrame) -> bool:
    """True if any ERROR-severity check failed."""
    return bool((report["status"] == "FAIL").any())


def overall_status(report: pd.DataFrame) -> str:
    """ACCEPTED, ACCEPTED_WITH_WARNINGS or REJECTED."""
    if has_blocking_errors(report):
        return REJECTED
    if (report["status"] == "WARN").any():
        return ACCEPTED_WITH_WARNINGS
    return ACCEPTED


def report_filename(dirty: bool) -> str:
    return "data_quality_report_dirty.csv" if dirty else "data_quality_report.csv"


def validate_folder(folder: Path, output_dir: Path, dirty: bool = False) -> pd.DataFrame:
    """Validate the raw CSVs in ``folder`` and write the report to ``output_dir``."""
    tables = load_raw_tables(folder)
    report = run_all_checks(tables, infer_as_of(tables))
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    report.to_csv(Path(output_dir) / report_filename(dirty), index=False)
    return report


def print_summary(report: pd.DataFrame) -> None:
    """Human-readable summary of non-passing checks and the verdict."""
    issues = report[report["status"] != "PASS"]
    print(
        f"\n{len(report)} checks run: "
        f"{(report['status'] == 'FAIL').sum()} failed, "
        f"{(report['status'] == 'WARN').sum()} warnings, "
        f"{(report['status'] == 'INFO').sum()} info"
    )
    if not issues.empty:
        print(
            issues[["status", "check", "table", "failed_rows", "description"]].to_string(
                index=False
            )
        )
    print(f"\nVerdict: {overall_status(report)}")


def main() -> None:
    """CLI: validate raw (or dirty) CSVs; exit code 1 if rejected."""
    parser = argparse.ArgumentParser(description="Validate the raw ERP extract")
    parser.add_argument("--dirty", action="store_true", help="validate data/raw_dirty instead")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    paths = PathConfig()
    folder = paths.raw_dirty_data_dir if args.dirty else paths.raw_data_dir
    report = validate_folder(folder, paths.output_dir, dirty=args.dirty)
    print_summary(report)
    print(f"Report: {Path(paths.output_dir) / report_filename(args.dirty)}")
    sys.exit(1 if has_blocking_errors(report) else 0)


if __name__ == "__main__":
    main()
