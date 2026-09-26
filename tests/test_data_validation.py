"""Tests for data-quality checks.

The generator produces clean data, so each test injects one realistic
defect into a copy and asserts that exactly the right check catches it.
"""

import pandas as pd
import pytest

from src.data_validation import has_blocking_errors, run_all_checks


def _tables(ds) -> dict[str, pd.DataFrame]:
    return {name: table.copy() for name, table in ds.raw_tables().items()}


def _failed(report: pd.DataFrame, check: str, table: str | None = None) -> int:
    rows = report[report["check"] == check]
    if table:
        rows = rows[rows["table"] == table]
    return int(rows["failed_rows"].sum())


def test_clean_generated_data_has_no_blocking_errors(small_dataset):
    report = run_all_checks(small_dataset.raw_tables(), small_dataset.as_of)
    assert not has_blocking_errors(report)
    assert set(report["severity"]) <= {"ERROR", "WARNING", "INFO"}


def test_zero_demand_is_reported_as_info_not_error(small_dataset):
    report = run_all_checks(small_dataset.raw_tables(), small_dataset.as_of)
    row = report[report["check"] == "zero_demand_12m"].iloc[0]
    assert row["severity"] == "INFO"
    assert row["status"] in {"PASS", "INFO"}


def _inject_duplicate(t):
    t["demand_monthly"] = pd.concat([t["demand_monthly"], t["demand_monthly"].head(3)])


def _inject_missing_sku(t):
    t["purchase_orders"].loc[t["purchase_orders"].index[:2], "sku_id"] = "SKU-99999"


def _inject_missing_supplier(t):
    t["products"].loc[t["products"].index[:1], "supplier_id"] = "SUP-999"


def _inject_negative_inventory(t):
    t["inventory_snapshot"].loc[t["inventory_snapshot"].index[:4], "on_hand_qty"] = -5


def _inject_receipt_before_order(t):
    t["supplier_deliveries"].loc[t["supplier_deliveries"].index[:1], "receipt_date"] = pd.Timestamp(
        "2000-01-01"
    )


def _inject_negative_price(t):
    t["products"].loc[t["products"].index[:2], "unit_price"] = -1.0


def _inject_missing_cost(t):
    t["products"].loc[t["products"].index[:3], "unit_cost"] = float("nan")


def _inject_impossible_lead_time(t):
    t["suppliers"].loc[t["suppliers"].index[:1], "quoted_lead_time_days"] = 900


def _inject_overshipment(t):
    idx = t["demand_monthly"].index[:2]
    t["demand_monthly"].loc[idx, "shipped_qty"] = t["demand_monthly"].loc[idx, "ordered_qty"] + 10


@pytest.mark.parametrize(
    ("inject", "check", "table", "expected"),
    [
        (_inject_duplicate, "duplicate_records", "demand_monthly", 3),
        (_inject_missing_sku, "missing_sku", "purchase_orders", 2),
        (_inject_missing_supplier, "missing_supplier", "products", 1),
        (_inject_negative_inventory, "negative_inventory", "inventory_snapshot", 4),
        (_inject_receipt_before_order, "receipt_before_order", "supplier_deliveries", 1),
        (_inject_negative_price, "non_positive_price", "products", 2),
        (_inject_missing_cost, "missing_unit_cost", "products", 3),
        (_inject_impossible_lead_time, "impossible_quoted_lead_time", None, 1),
        (_inject_overshipment, "shipped_exceeds_ordered", "demand_monthly", 2),
    ],
)
def test_injected_defect_is_caught(small_dataset, inject, check, table, expected):
    tables = _tables(small_dataset)
    inject(tables)
    report = run_all_checks(tables, small_dataset.as_of)
    assert _failed(report, check, table) == expected
    assert has_blocking_errors(report)


def test_price_below_cost_is_a_warning_not_an_error(small_dataset):
    tables = _tables(small_dataset)
    p = tables["products"]
    p.loc[p.index[:1], "unit_price"] = p.loc[p.index[:1], "unit_cost"] * 0.5
    report = run_all_checks(tables, small_dataset.as_of)
    assert _failed(report, "price_below_cost") == 1
    assert not has_blocking_errors(report)
