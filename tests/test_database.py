"""PostgreSQL layer: schema, constraints, loading gate, transformations, answers.

Views are cross-checked against independent pandas calculations on the same
raw extract, so a SQL bug cannot hide behind a plausible-looking number.
"""

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from src.config import DAYS_PER_MONTH
from src.database import (
    RAW_TO_CORE,
    calendar_range,
    export_parquet,
    load_validated_data,
    planning_parameters,
    run_analysis,
    split_named_queries,
)
from src.raw_data import load_raw_tables, save_tables

pytestmark = pytest.mark.database


def q(engine, sql: str, **params) -> pd.DataFrame:
    with engine.connect() as conn:
        return pd.read_sql(text(sql), conn, params=params)


@pytest.fixture(scope="module")
def raw(db_config):
    return load_raw_tables(db_config.paths.raw_data_dir)


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------
def test_expected_tables_and_views_exist(db_engine):
    objs = q(
        db_engine,
        """
        SELECT table_schema, table_name, table_type FROM information_schema.tables
        WHERE table_schema IN ('core', 'analytics', 'audit')""",
    )
    tables = set(objs.loc[objs.table_type == "BASE TABLE", "table_name"])
    views = set(objs.loc[objs.table_type == "VIEW", "table_name"])
    assert {"dim_date", *RAW_TO_CORE.values()} <= tables
    assert {"planning_parameter", "load_run", "data_quality_result"} <= tables
    assert {
        "v_parameters",
        "v_as_of",
        "v_demand_monthly",
        "v_receipts_monthly",
        "v_inventory_monthly",
        "v_po_line_status",
        "v_supplier_performance",
        "v_inventory_position",
    } <= views


@pytest.mark.parametrize(
    ("table", "expected_fk_targets"),
    [
        ("dim_product", {"dim_supplier"}),
        ("fact_demand", {"dim_product", "dim_date"}),
        ("fact_inventory", {"dim_product", "dim_date"}),
        ("fact_purchase_order", {"dim_product", "dim_supplier", "dim_date"}),
        ("fact_supplier_delivery", {"fact_purchase_order", "dim_date"}),
    ],
)
def test_foreign_keys_are_declared(db_engine, table, expected_fk_targets):
    fks = q(
        db_engine,
        """
        SELECT ccu.table_name AS target
        FROM information_schema.table_constraints tc
        JOIN information_schema.constraint_column_usage ccu
          ON tc.constraint_name = ccu.constraint_name AND tc.table_schema = ccu.table_schema
        WHERE tc.table_schema = 'core' AND tc.constraint_type = 'FOREIGN KEY'
          AND tc.table_name = :t""",
        t=table,
    )
    assert set(fks["target"]) == expected_fk_targets


@pytest.mark.parametrize(
    ("table", "pk"),
    [
        ("fact_demand", ["month_start", "sku_id"]),
        ("fact_inventory", ["month_end", "sku_id"]),
        ("fact_purchase_order", ["po_line_id"]),
        ("fact_supplier_delivery", ["receipt_id"]),
        ("dim_product", ["sku_id"]),
    ],
)
def test_primary_keys_match_documented_grain(db_engine, table, pk):
    cols = q(
        db_engine,
        """
        SELECT kcu.column_name FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu USING (constraint_schema, constraint_name)
        WHERE tc.table_schema = 'core' AND tc.table_name = :t
          AND tc.constraint_type = 'PRIMARY KEY'""",
        t=table,
    )
    assert sorted(cols["column_name"]) == pk


@pytest.mark.parametrize(
    ("table", "column", "data_type"),
    [
        ("dim_product", "unit_cost", "numeric"),
        ("dim_product", "launch_date", "date"),
        ("fact_demand", "ordered_qty", "integer"),
        ("fact_demand", "month_start", "date"),
        ("fact_purchase_order", "unit_price", "numeric"),
        ("fact_supplier_delivery", "receipt_date", "date"),
    ],
)
def test_column_data_types(db_engine, table, column, data_type):
    got = q(
        db_engine,
        """
        SELECT data_type FROM information_schema.columns
        WHERE table_schema = 'core' AND table_name = :t AND column_name = :c""",
        t=table,
        c=column,
    )
    assert got["data_type"].iloc[0] == data_type


def test_core_stores_no_derived_classifications(db_engine):
    cols = q(
        db_engine, "SELECT column_name FROM information_schema.columns WHERE table_schema='core'"
    )
    derived = [
        c for c in cols["column_name"] if any(k in c for k in ("abc", "xyz", "health", "segment"))
    ]
    assert derived == []


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------
def test_row_counts_match_raw_extract(db_engine, raw):
    for raw_name, core_name in RAW_TO_CORE.items():
        n = q(db_engine, f"SELECT count(*) AS n FROM core.{core_name}")["n"].iloc[0]
        assert n == len(raw[raw_name]), core_name


def test_calendar_is_continuous_and_follows_config(db_engine, db_config):
    days = q(db_engine, "SELECT min(date_key) lo, max(date_key) hi, count(*) n FROM core.dim_date")
    assert days["n"].iloc[0] == (days["hi"].iloc[0] - days["lo"].iloc[0]).days + 1
    assert (days["lo"].iloc[0], days["hi"].iloc[0]) == calendar_range(db_config)


def test_foreign_key_rejects_orphan_rows(db_engine):
    with pytest.raises(IntegrityError), db_engine.begin() as conn:
        conn.execute(
            text("INSERT INTO core.fact_demand VALUES ('SKU-99999', DATE '2024-01-01', 1, 1)")
        )


@pytest.mark.parametrize(
    "sql",
    [
        # shipped > ordered
        "INSERT INTO core.fact_demand VALUES ('SKU-00001', DATE '2023-01-01', 5, 9)",
        # not the first day of a month
        "INSERT INTO core.fact_demand VALUES ('SKU-00001', DATE '2023-01-15', 5, 1)",
        "UPDATE core.fact_inventory SET on_hand_qty = -1 WHERE sku_id = 'SKU-00001'",
        "UPDATE core.dim_product SET unit_cost = 0 WHERE sku_id = 'SKU-00001'",
    ],
)
def test_check_constraints_reject_impossible_values(db_engine, sql):
    with pytest.raises(IntegrityError), db_engine.begin() as conn:
        conn.execute(text(sql))


def test_accepted_load_is_audited(db_engine):
    run = q(
        db_engine,
        "SELECT * FROM audit.load_run WHERE dataset_label = 'clean' ORDER BY run_id LIMIT 1",
    )
    assert run["status"].iloc[0] == "ACCEPTED"
    assert run["rows_loaded"].iloc[0] > 300_000
    n_checks = q(
        db_engine,
        "SELECT count(*) n FROM audit.data_quality_result WHERE run_id = :r",
        r=int(run["run_id"].iloc[0]),
    )["n"].iloc[0]
    assert n_checks > 40


def test_parameters_come_from_config(db_engine, db_config):
    stored = q(db_engine, "SELECT name, value FROM analytics.planning_parameter").set_index("name")
    for name, value, _, _ in planning_parameters(db_config):
        assert float(stored.loc[name, "value"]) == pytest.approx(float(value))


# --------------------------------------------------------------------------
# Transformations vs. independent pandas calculations
# --------------------------------------------------------------------------
def test_stock_flow_reconciles_in_sql(db_engine):
    diff = q(
        db_engine,
        """SELECT count(*) FILTER (WHERE balance_difference <> 0) AS bad,
                                  count(*) FILTER (WHERE balance_difference = 0) AS ok
                           FROM analytics.v_inventory_monthly""",
    )
    assert diff["bad"].iloc[0] == 0 and diff["ok"].iloc[0] > 100_000


def test_monthly_demand_and_rolling_average_match_pandas(db_engine, raw):
    sql = q(
        db_engine,
        """SELECT sku_id, month_start, demand_qty, shipped_qty, lost_qty,
                                 rolling_3m_avg_demand, rolling_12m_demand
                          FROM analytics.v_demand_monthly""",
    )
    sql["month_start"] = pd.to_datetime(sql["month_start"])
    d = raw["demand_monthly"].sort_values(["sku_id", "month_start"])
    d["r3"] = d.groupby("sku_id")["ordered_qty"].transform(
        lambda s: s.rolling(3, min_periods=1).mean()
    )
    d["r12"] = d.groupby("sku_id")["ordered_qty"].transform(
        lambda s: s.rolling(12, min_periods=1).sum()
    )
    m = d.merge(sql, on=["sku_id", "month_start"])
    assert len(m) == len(d)
    assert (m["demand_qty"] == m["ordered_qty"]).all()
    assert (m["lost_qty"] == m["ordered_qty"] - m["shipped_qty_x"]).all()
    np.testing.assert_allclose(m["rolling_3m_avg_demand"].astype(float), m["r3"])
    np.testing.assert_allclose(m["rolling_12m_demand"].astype(float), m["r12"])


def _pandas_po_lines(raw, as_of: pd.Timestamp) -> pd.DataFrame:
    """Independent OTIF / open-quantity calculation per methodology section 12."""
    po, rcv = raw["purchase_orders"], raw["supplier_deliveries"]
    r = rcv.merge(po[["po_line_id", "promised_date"]], on="po_line_id")
    received = r.groupby("po_line_id")["received_qty"].sum()
    by_cutoff = (
        r[r["receipt_date"] <= r["promised_date"]].groupby("po_line_id")["received_qty"].sum()
    )
    out = po.set_index("po_line_id")
    out["received"] = received.reindex(out.index).fillna(0)
    out["by_cutoff"] = by_cutoff.reindex(out.index).fillna(0)
    is_open = out["status"].isin(["OPEN", "PARTIALLY_RECEIVED"])
    out["open_qty"] = np.where(is_open, (out["ordered_qty"] - out["received"]).clip(lower=0), 0)
    evaluable = out["status"].isin(["CLOSED", "CLOSED_SHORT"]) | (out["promised_date"] < as_of)
    out["otif"] = np.where(evaluable, out["by_cutoff"] >= out["ordered_qty"], np.nan)
    return out


def test_po_line_open_qty_and_otif_match_pandas(db_engine, raw):
    expected = _pandas_po_lines(raw, raw["inventory_snapshot"]["month_end"].max())
    sql = q(
        db_engine, "SELECT po_line_id, open_qty, otif FROM analytics.v_po_line_status"
    ).set_index("po_line_id")
    m = expected.join(sql, rsuffix="_sql")
    assert (m["open_qty"] == m["open_qty_sql"]).all()
    evaluable = m["otif"].notna()
    assert (
        m.loc[evaluable, "otif"].astype(bool) == m.loc[evaluable, "otif_sql"].astype(bool)
    ).all()
    assert m.loc[~evaluable, "otif_sql"].isna().all()


def test_supplier_spend_and_otif_match_pandas(db_engine, raw):
    lines = _pandas_po_lines(raw, raw["inventory_snapshot"]["month_end"].max())
    lines["spend"] = lines["received"] * lines["unit_price"]
    expected = lines.groupby("supplier_id").agg(spend=("spend", "sum"), otif=("otif", "mean"))
    sql = q(
        db_engine, "SELECT supplier_id, spend, otif_pct FROM analytics.v_supplier_performance"
    ).set_index("supplier_id")
    m = expected.join(sql, rsuffix="_sql")
    np.testing.assert_allclose(m["spend_sql"].astype(float), m["spend"], rtol=1e-9)
    np.testing.assert_allclose(m["otif_pct"].astype(float), m["otif"], rtol=1e-9)


def test_inventory_position_definitions(db_engine, raw):
    v = q(db_engine, "SELECT * FROM analytics.v_inventory_position")
    assert len(v) == len(raw["products"])
    assert (
        v["inventory_position"] == v["on_hand_qty"] + v["open_po_qty"] - v["allocated_qty"]
    ).all()
    has_demand = v["avg_monthly_demand"] > 0
    np.testing.assert_allclose(
        v.loc[has_demand, "days_of_supply"].astype(float),
        (
            v.loc[has_demand, "on_hand_qty"]
            / (v.loc[has_demand, "avg_monthly_demand"].astype(float) / DAYS_PER_MONTH)
        ),
    )
    # Dead-stock candidate = stock on hand and no demand in the last 6 months.
    d = raw["demand_monthly"]
    as_of = raw["inventory_snapshot"]["month_end"].max()
    last6 = (
        d[d["month_start"] > as_of - pd.DateOffset(months=6)].groupby("sku_id")["ordered_qty"].sum()
    )
    on_hand = (
        raw["inventory_snapshot"].query("month_end == @as_of").set_index("sku_id")["on_hand_qty"]
    )
    expected_dead = set(
        on_hand[(on_hand > 0) & (last6.reindex(on_hand.index).fillna(0) == 0)].index
    )
    assert set(v.loc[v["dead_stock_candidate"], "sku_id"]) == expected_dead


# --------------------------------------------------------------------------
# Analytical answers
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def answers(db_engine, db_config):
    return run_analysis(db_engine, db_config)


def test_all_fifteen_questions_are_answered(answers, db_config):
    queries = split_named_queries((db_config.paths.sql_dir / "analysis.sql").read_text())
    assert len(queries) == 15
    for name, df in answers.items():
        assert len(df) > 0, name
        assert (db_config.paths.sql_answers_dir / f"{name}.csv").exists()


def test_answer_shares_are_consistent(answers):
    pareto = answers["q09_supplier_spend_pareto"]
    assert pareto["cumulative_share_pct"].iloc[-1] == pytest.approx(100, abs=0.1)
    assert pareto["cumulative_share_pct"].is_monotonic_increasing
    deciles = answers["q14_inventory_value_concentration"]
    assert deciles["cumulative_pct_of_value"].iloc[-1] == pytest.approx(100, abs=0.1)
    assert deciles["pct_of_value"].is_monotonic_decreasing


def test_answer_rows_satisfy_their_question(answers):
    assert answers["q02_lowest_days_of_supply"]["days_of_supply"].is_monotonic_increasing
    q03 = answers["q03_below_screening_reorder_point"]
    assert (q03["inventory_position"] < q03["screening_reorder_point"]).all()
    q08 = answers["q08_no_demand_six_months"]
    assert (q08["months_since_last_demand"] >= 6).all() and (q08["on_hand_qty"] > 0).all()
    q12 = answers["q12_stockouts_despite_open_pos"]
    assert (q12["shipped_qty"] < q12["demand_qty"]).all()
    assert set(q12["root_cause"]) <= {"SUPPLIER_LATE", "ORDERED_TOO_LATE_OR_TOO_LITTLE"}
    q13 = answers["q13_inventory_value_trend"]
    assert len(q13) == 24


def test_parquet_export_is_typed(db_engine, db_config):
    written = export_parquet(db_engine, db_config)
    demand = pd.read_parquet(written["v_demand_monthly"])
    assert len(demand) > 100_000
    assert demand["fill_rate"].dtype == "float64"
    assert pd.api.types.is_bool_dtype(demand["stockout_month"])


# --------------------------------------------------------------------------
# Validation gate (run last: these tests re-load data)
# --------------------------------------------------------------------------
def _core_fingerprint(engine) -> pd.DataFrame:
    return q(
        engine,
        """SELECT (SELECT sum(ordered_qty) FROM core.fact_demand) AS demand,
                               (SELECT sum(on_hand_qty) FROM core.fact_inventory) AS stock,
                               (SELECT sum(received_qty)
                                  FROM core.fact_supplier_delivery) AS receipts,
                               (SELECT count(*) FROM core.fact_purchase_order) AS po_lines""",
    )


def test_dirty_data_is_rejected_and_core_is_unchanged(db_engine, db_config):
    before = _core_fingerprint(db_engine)
    result = load_validated_data(db_engine, db_config.paths.raw_dirty_data_dir, "dirty")
    assert result.status == "REJECTED" and result.rows_loaded == 0
    pd.testing.assert_frame_equal(before, _core_fingerprint(db_engine))
    audit = q(
        db_engine,
        "SELECT status, failed_checks FROM audit.load_run WHERE run_id = :r",
        r=result.run_id,
    )
    assert audit["status"].iloc[0] == "REJECTED" and audit["failed_checks"].iloc[0] >= 5


def test_warning_only_data_loads_then_clean_reload_is_reproducible(
    db_engine, db_config, raw, tmp_path
):
    """Warnings do not block a load; reloading the same seed restores identical data."""
    original = _core_fingerprint(db_engine)
    tables = {k: v.copy() for k, v in raw.items()}
    tables["suppliers"].loc[0, "region"] = None  # missing optional field = warning
    product = tables["products"].iloc[0]
    stale_line = {  # never shipped, never cancelled = warning, not error
        "po_line_id": "PO-999999-01",
        "po_number": "PO-999999",
        "sku_id": product["sku_id"],
        "supplier_id": product["supplier_id"],
        "order_date": pd.Timestamp("2025-01-06"),
        "promised_date": pd.Timestamp("2025-02-10"),
        "ordered_qty": 777,
        "unit_price": product["unit_cost"],
        "status": "OPEN",
    }
    tables["purchase_orders"] = pd.concat(
        [tables["purchase_orders"], pd.DataFrame([stale_line])], ignore_index=True
    )
    save_tables(tables, tmp_path)
    result = load_validated_data(db_engine, tmp_path, "clean-with-warning")
    assert result.status == "ACCEPTED_WITH_WARNINGS"
    assert result.rows_loaded > 300_000
    region = q(db_engine, "SELECT region FROM core.dim_supplier WHERE supplier_id = 'SUP-001'")
    assert region["region"].isna().all()
    # Phantom supply is reported but NOT counted in inventory position.
    pos = q(
        db_engine,
        "SELECT stale_open_po_qty, open_po_qty, on_hand_qty, allocated_qty, "
        "inventory_position FROM analytics.v_inventory_position WHERE sku_id = :s",
        s=product["sku_id"],
    ).iloc[0]
    assert pos["stale_open_po_qty"] == 777
    assert (
        pos["inventory_position"] == pos["on_hand_qty"] + pos["open_po_qty"] - pos["allocated_qty"]
    )

    reload = load_validated_data(db_engine, db_config.paths.raw_data_dir, "clean")
    assert reload.status == "ACCEPTED"
    pd.testing.assert_frame_equal(original, _core_fingerprint(db_engine))
