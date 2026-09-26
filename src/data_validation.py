"""Data-quality checks run before any analysis.

Each check answers one question an analyst must ask of an ERP extract
("are there negative stock balances?", "does every PO line point to a real
SKU?") and returns the number of failing rows with a severity:

* ERROR   — the data is wrong; analysis on it would be misleading. The
            pipeline stops.
* WARNING — suspicious but possible in real operations (e.g. price below
            cost on a clearance item); reported, analysis continues.
* INFO    — expected business condition worth knowing (e.g. SKUs with no
            demand in 12 months — dead-stock candidates, not bad data).

The report is written to ``outputs/data_quality_report.csv``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass

import pandas as pd

logger = logging.getLogger(__name__)

Tables = Mapping[str, pd.DataFrame]

# Longest credible supplier lead time (days). Beyond a year an order is
# almost certainly a data-entry error, not a slow supplier.
MAX_CREDIBLE_LEAD_TIME_DAYS = 365
# Receiving more than this share above the ordered quantity is suspicious.
OVER_RECEIPT_TOLERANCE = 0.05

PRIMARY_KEYS: dict[str, list[str]] = {
    "suppliers": ["supplier_id"],
    "products": ["sku_id"],
    "demand_monthly": ["sku_id", "month_start"],
    "inventory_snapshot": ["sku_id", "month_end"],
    "purchase_orders": ["po_line_id"],
    "supplier_deliveries": ["receipt_id"],
}

REQUIRED_COLUMNS: dict[str, list[str]] = {
    "suppliers": ["supplier_id", "quoted_lead_time_days"],
    "products": ["sku_id", "category", "supplier_id", "unit_cost", "unit_price", "moq"],
    "demand_monthly": ["sku_id", "month_start", "ordered_qty", "shipped_qty"],
    "inventory_snapshot": ["sku_id", "month_end", "on_hand_qty", "allocated_qty"],
    "purchase_orders": [
        "po_line_id",
        "sku_id",
        "supplier_id",
        "order_date",
        "promised_date",
        "ordered_qty",
    ],
    "supplier_deliveries": ["receipt_id", "po_line_id", "receipt_date", "received_qty"],
}


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


def _count(mask: pd.Series) -> int:
    return int(mask.fillna(False).sum())


# --------------------------------------------------------------------------
# Structural checks
# --------------------------------------------------------------------------
def check_duplicates(tables: Tables) -> list[CheckResult]:
    """Duplicate primary keys double-count demand, stock or spend."""
    results = []
    for name, keys in PRIMARY_KEYS.items():
        df = tables[name]
        results.append(
            CheckResult(
                "duplicate_records",
                name,
                "ERROR",
                _count(df.duplicated(keys, keep="first")),
                len(df),
                f"Rows sharing primary key {keys}",
            )
        )
    return results


def check_missing_values(tables: Tables) -> list[CheckResult]:
    """Missing keys, costs or quantities make rows unusable."""
    results = []
    for name, cols in REQUIRED_COLUMNS.items():
        df = tables[name]
        results.append(
            CheckResult(
                "missing_values",
                name,
                "ERROR",
                _count(df[cols].isna().any(axis=1)),
                len(df),
                f"Null in required columns {cols}",
            )
        )
    products = tables["products"]
    results.append(
        CheckResult(
            "missing_unit_cost",
            "products",
            "ERROR",
            _count(products["unit_cost"].isna()),
            len(products),
            "Unit cost missing: inventory cannot be valued",
        )
    )
    return results


def check_referential_integrity(tables: Tables) -> list[CheckResult]:
    """Every fact row must point to an existing SKU / supplier / PO line."""
    skus = set(tables["products"]["sku_id"])
    suppliers = set(tables["suppliers"]["supplier_id"])
    po_lines = set(tables["purchase_orders"]["po_line_id"])
    specs = [
        ("missing_sku", "demand_monthly", "sku_id", skus),
        ("missing_sku", "inventory_snapshot", "sku_id", skus),
        ("missing_sku", "purchase_orders", "sku_id", skus),
        ("missing_supplier", "products", "supplier_id", suppliers),
        ("missing_supplier", "purchase_orders", "supplier_id", suppliers),
        ("missing_po_line", "supplier_deliveries", "po_line_id", po_lines),
    ]
    return [
        CheckResult(
            check,
            table,
            "ERROR",
            _count(~tables[table][col].isin(valid)),
            len(tables[table]),
            f"{table}.{col} not found in master data",
        )
        for check, table, col, valid in specs
    ]


# --------------------------------------------------------------------------
# Business-rule checks
# --------------------------------------------------------------------------
def check_prices(tables: Tables) -> list[CheckResult]:
    """Costs and prices must be positive; price below cost is suspicious."""
    p = tables["products"]
    po = tables["purchase_orders"]
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


def check_quantities(tables: Tables) -> list[CheckResult]:
    """Quantities must be non-negative and physically consistent."""
    d = tables["demand_monthly"]
    inv = tables["inventory_snapshot"]
    po = tables["purchase_orders"]
    rcv = tables["supplier_deliveries"]
    p = tables["products"]
    received = rcv.groupby("po_line_id")["received_qty"].sum()
    over = po["po_line_id"].map(received).fillna(0) > po["ordered_qty"] * (
        1 + OVER_RECEIPT_TOLERANCE
    )
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
            f"Received > ordered by more than {OVER_RECEIPT_TOLERANCE:.0%}",
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


def check_dates(tables: Tables, as_of: pd.Timestamp) -> list[CheckResult]:
    """Dates must be in order and not in the future."""
    po = tables["purchase_orders"]
    rcv = tables["supplier_deliveries"].merge(
        po[["po_line_id", "order_date"]], on="po_line_id", how="left"
    )
    d = tables["demand_monthly"]
    inv = tables["inventory_snapshot"]
    p = tables["products"]
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
            "Goods received before they were ordered",
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
            _count(
                d["month_start"]
                < d["sku_id"]
                .map(p.set_index("sku_id")["launch_date"])
                .dt.to_period("M")
                .dt.start_time
            ),
            len(d),
            "Demand recorded before the product existed",
        ),
    ]


def check_lead_times(tables: Tables) -> list[CheckResult]:
    """Quoted and actual lead times must be credible."""
    s = tables["suppliers"]
    p = tables["products"]
    po = tables["purchase_orders"]
    rcv = tables["supplier_deliveries"].merge(
        po[["po_line_id", "order_date"]], on="po_line_id", how="left"
    )
    actual = (rcv["receipt_date"] - rcv["order_date"]).dt.days
    quoted = pd.concat([s["quoted_lead_time_days"], p["supplier_lead_time_days"]])
    return [
        CheckResult(
            "impossible_quoted_lead_time",
            "suppliers/products",
            "ERROR",
            _count((quoted <= 0) | (quoted > MAX_CREDIBLE_LEAD_TIME_DAYS)),
            len(quoted),
            f"Quoted lead time <= 0 or > {MAX_CREDIBLE_LEAD_TIME_DAYS} days",
        ),
        CheckResult(
            "impossible_actual_lead_time",
            "supplier_deliveries",
            "ERROR",
            _count((actual < 0) | (actual > MAX_CREDIBLE_LEAD_TIME_DAYS)),
            len(actual),
            f"Actual lead time < 0 or > {MAX_CREDIBLE_LEAD_TIME_DAYS} days",
        ),
    ]


def check_zero_demand(tables: Tables, as_of: pd.Timestamp) -> list[CheckResult]:
    """SKUs with no orders in 12 months: a business finding, not bad data."""
    d = tables["demand_monthly"]
    recent = d[d["month_start"] > as_of - pd.DateOffset(months=12)]
    totals = recent.groupby("sku_id")["ordered_qty"].sum()
    return [
        CheckResult(
            "zero_demand_12m",
            "demand_monthly",
            "INFO",
            _count(totals == 0),
            tables["products"]["sku_id"].nunique(),
            "SKUs with no customer orders in the last 12 months (dead-stock candidates)",
        ),
    ]


CHECKS: tuple[Callable[..., list[CheckResult]], ...] = (
    check_duplicates,
    check_missing_values,
    check_referential_integrity,
    check_prices,
    check_quantities,
    check_lead_times,
)
DATED_CHECKS: tuple[Callable[..., list[CheckResult]], ...] = (check_dates, check_zero_demand)


def run_all_checks(tables: Tables, as_of: pd.Timestamp) -> pd.DataFrame:
    """Run every check and return the data-quality report."""
    results: list[CheckResult] = []
    for check in CHECKS:
        results.extend(check(tables))
    for check in DATED_CHECKS:
        results.extend(check(tables, as_of))
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
    failures = report[(report["status"] == "FAIL") & (report["severity"] == "ERROR")]
    logger.info("Data quality: %d checks, %d blocking errors", len(report), len(failures))
    return report


def has_blocking_errors(report: pd.DataFrame) -> bool:
    """True if any ERROR-severity check failed."""
    return bool(((report["severity"] == "ERROR") & (report["status"] == "FAIL")).any())
