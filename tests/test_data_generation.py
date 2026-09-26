"""Tests for the synthetic data generator.

Three groups:
1. Reproducibility and structure (seed, row counts, keys).
2. Internal consistency — the data obeys physical and business rules.
3. Realism — intended patterns exist, are detectable, but are not perfect.

Realism tests use broad bands rather than exact values: they fail when the
generator produces *unrealistic* data (e.g. a 60% stockout rate), which is
a signal to fix the generator, not to loosen the test.
"""

import numpy as np
import pandas as pd
import pytest

from src.config import DataGenerationConfig, PathConfig
from src.data_generation import DEMAND_PATTERNS, SUPPLIER_ARCHETYPES, generate_dataset
from tests.conftest import SMALL


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _trailing(demand: pd.DataFrame, as_of: pd.Timestamp, months: int) -> pd.DataFrame:
    return demand[demand["month_start"] > as_of - pd.DateOffset(months=months)]


def _latest_inventory(ds) -> pd.DataFrame:
    inv = ds.inventory[ds.inventory["month_end"] == ds.as_of]
    avg12 = _trailing(ds.demand, ds.as_of, 12).groupby("sku_id")["ordered_qty"].mean()
    sum6 = _trailing(ds.demand, ds.as_of, 6).groupby("sku_id")["ordered_qty"].sum()
    return inv.assign(avg12=inv["sku_id"].map(avg12), sum6=inv["sku_id"].map(sum6)).merge(
        ds.products, on="sku_id"
    )


def _line_receipts(ds) -> pd.DataFrame:
    rcv = ds.deliveries.groupby("po_line_id").agg(
        received=("received_qty", "sum"),
        n_receipts=("receipt_id", "count"),
        first_receipt=("receipt_date", "min"),
        last_receipt=("receipt_date", "max"),
    )
    return ds.purchase_orders.join(rcv, on="po_line_id")


# --------------------------------------------------------------------------
# 1. Reproducibility and structure
# --------------------------------------------------------------------------
def test_same_seed_gives_identical_output(small_dataset):
    again = generate_dataset(SMALL)
    for name, table in small_dataset.raw_tables().items():
        pd.testing.assert_frame_equal(table, again.raw_tables()[name], obj=name)
    for name, table in small_dataset.truth_tables().items():
        pd.testing.assert_frame_equal(table, again.truth_tables()[name], obj=name)


def test_different_seed_gives_different_output(small_dataset):
    other = generate_dataset(DataGenerationConfig(random_seed=7, n_skus=400, n_suppliers=20))
    assert not small_dataset.demand["ordered_qty"].equals(other.demand["ordered_qty"])
    assert not small_dataset.products["unit_cost"].equals(other.products["unit_cost"])


def test_seed_is_configurable_from_environment(monkeypatch):
    monkeypatch.setenv("SYNTHETIC_DATA_SEED", "123")
    assert DataGenerationConfig().random_seed == 123
    monkeypatch.delenv("SYNTHETIC_DATA_SEED")
    assert DataGenerationConfig().random_seed == 42


def test_expected_row_counts(full_dataset):
    ds = full_dataset
    assert len(ds.suppliers) == 50
    assert len(ds.products) == 5_000
    # Monthly rows exist from the later of history start and launch month.
    history_start = pd.Timestamp("2024-01-01")
    launch_month = ds.products["launch_date"].dt.to_period("M").dt.start_time
    first = launch_month.where(launch_month > history_start, history_start)
    expected_rows = int(
        ((ds.as_of.to_period("M") - first.dt.to_period("M")).apply(lambda x: x.n) + 1).sum()
    )
    assert len(ds.demand) == expected_rows
    assert len(ds.inventory) == expected_rows
    assert expected_rows > 5_000 * 22  # most SKUs have the full 24 months
    assert ds.demand["month_start"].nunique() == 24
    # Every received quantity belongs to a PO line; most lines have one receipt.
    assert len(ds.purchase_orders) > 20_000
    assert len(ds.purchase_orders) <= len(ds.deliveries) < 1.3 * len(ds.purchase_orders)


def test_primary_keys_are_unique(full_dataset):
    ds = full_dataset
    assert ds.suppliers["supplier_id"].is_unique
    assert ds.products["sku_id"].is_unique
    assert not ds.demand.duplicated(["sku_id", "month_start"]).any()
    assert not ds.inventory.duplicated(["sku_id", "month_end"]).any()
    assert ds.purchase_orders["po_line_id"].is_unique
    assert ds.deliveries["receipt_id"].is_unique


def test_referential_integrity(full_dataset):
    ds = full_dataset
    skus, suppliers = set(ds.products["sku_id"]), set(ds.suppliers["supplier_id"])
    assert set(ds.products["supplier_id"]) <= suppliers
    assert set(ds.demand["sku_id"]) <= skus
    assert set(ds.inventory["sku_id"]) <= skus
    assert set(ds.purchase_orders["sku_id"]) <= skus
    assert set(ds.purchase_orders["supplier_id"]) <= suppliers
    assert set(ds.deliveries["po_line_id"]) <= set(ds.purchase_orders["po_line_id"])
    # A PO line is always raised on the SKU's own supplier.
    merged = ds.purchase_orders.merge(ds.products[["sku_id", "supplier_id"]], on="sku_id")
    assert (merged["supplier_id_x"] == merged["supplier_id_y"]).all()


# --------------------------------------------------------------------------
# 2. Internal consistency
# --------------------------------------------------------------------------
def test_inventory_balance_reconciles_exactly(full_dataset):
    """closing = previous closing + receipts - shipments, for every SKU-month."""
    ds = full_dataset
    receipts = ds.deliveries.merge(ds.purchase_orders[["po_line_id", "sku_id"]], on="po_line_id")
    receipts["month_start"] = receipts["receipt_date"].dt.to_period("M").dt.start_time
    monthly_receipts = receipts.groupby(["sku_id", "month_start"])["received_qty"].sum()

    flow = ds.inventory.assign(
        month_start=ds.inventory["month_end"].dt.to_period("M").dt.start_time
    )
    flow = flow.merge(ds.demand, on=["sku_id", "month_start"]).sort_values(
        ["sku_id", "month_start"]
    )
    flow["receipts"] = (
        flow.set_index(["sku_id", "month_start"]).index.map(monthly_receipts).fillna(0)
    )
    flow["prev_closing"] = flow.groupby("sku_id")["on_hand_qty"].shift()
    check = flow.dropna(subset=["prev_closing"])
    expected = check["prev_closing"] + check["receipts"] - check["shipped_qty"]
    assert (expected == check["on_hand_qty"]).all()


def test_shipments_never_exceed_demand_or_stock(full_dataset):
    ds = full_dataset
    assert (ds.demand["shipped_qty"] <= ds.demand["ordered_qty"]).all()
    assert (ds.inventory["on_hand_qty"] >= 0).all()
    assert (ds.inventory["allocated_qty"] <= ds.inventory["on_hand_qty"]).all()


def test_po_status_matches_receipts(full_dataset):
    lines = _line_receipts(full_dataset)
    received = lines["received"].fillna(0)
    assert (received <= lines["ordered_qty"]).all()
    assert (
        received[lines["status"] == "CLOSED"]
        == lines.loc[lines["status"] == "CLOSED", "ordered_qty"]
    ).all()
    assert (received[lines["status"] == "OPEN"] == 0).all()
    partial = lines["status"].isin(["PARTIALLY_RECEIVED", "CLOSED_SHORT"])
    assert ((received[partial] > 0) & (received[partial] < lines.loc[partial, "ordered_qty"])).all()


def test_open_pos_are_recent(full_dataset):
    """Open lines at the as-of date should be recent orders, not stale junk."""
    po = full_dataset.purchase_orders
    open_lines = po[po["status"].isin(["OPEN", "PARTIALLY_RECEIVED"])]
    assert len(open_lines) > 0
    assert (open_lines["order_date"] > full_dataset.as_of - pd.DateOffset(months=6)).all()


def test_po_extract_matches_history_window(full_dataset):
    """Lines ordered before the window are only included if still in transit at its start."""
    lines = _line_receipts(full_dataset)
    window_start = pd.Timestamp("2024-01-01")
    before = lines[lines["order_date"] < window_start]
    assert len(before) > 0  # in-transit stock at the start of the window exists
    assert (before["last_receipt"] >= window_start).all()
    assert before["order_date"].min() > window_start - pd.DateOffset(months=8)


def test_supplier_type_drives_delivery_performance(full_dataset):
    """Unreliable suppliers deliver on time less often; imports take longer."""
    ds = full_dataset
    lines = _line_receipts(ds).dropna(subset=["last_receipt"])
    lines = lines[lines["status"] == "CLOSED"].merge(
        ds.supplier_truth[["supplier_id", "archetype"]]
    )
    lines["on_time"] = lines["last_receipt"] <= lines["promised_date"]
    lines["lead_time"] = (lines["last_receipt"] - lines["order_date"]).dt.days
    by_type = lines.groupby("archetype").agg(
        on_time=("on_time", "mean"), lead_time=("lead_time", "mean")
    )
    assert by_type.loc["DOMESTIC_RELIABLE", "on_time"] > by_type.loc["UNRELIABLE", "on_time"] + 0.1
    assert (
        by_type.loc["IMPORT_LONG_LEAD", "lead_time"]
        > 2 * by_type.loc["DOMESTIC_RELIABLE", "lead_time"]
    )


def test_supplier_types_overlap(full_dataset):
    """Archetypes must not be perfectly separated: realistic data has overlap."""
    truth = full_dataset.supplier_truth
    best_unreliable = truth.loc[truth["archetype"] == "UNRELIABLE", "on_time_prob"].max()
    worst_reliable = truth.loc[truth["archetype"] == "DOMESTIC_RELIABLE", "on_time_prob"].min()
    standard = truth.loc[truth["archetype"] == "DOMESTIC_STANDARD", "on_time_prob"]
    assert standard.max() > worst_reliable  # standard and reliable overlap
    assert best_unreliable > standard.min()  # unreliable and standard overlap


def test_lifecycle_shapes_demand(full_dataset):
    ds = full_dataset
    truth = ds.sku_truth.set_index("sku_id")
    d = ds.demand.join(truth[["intended_pattern", "eol_stop_month"]], on="sku_id")

    after_stop = d[
        (d["intended_pattern"] == "END_OF_LIFE") & (d["month_start"] >= d["eol_stop_month"])
    ]
    assert after_stop["ordered_qty"].mean() < 0.2  # only rare stray orders

    new = d[d["intended_pattern"] == "NEW"]
    assert new.groupby("sku_id")["month_start"].count().max() <= 10

    yearly = (
        d.assign(year=d["month_start"].dt.year)
        .pivot_table(index="sku_id", columns="year", values="ordered_qty", aggfunc="sum")
        .join(truth["intended_pattern"])
    )
    ratio = (yearly[2025] + 1) / (yearly[2024] + 1)
    assert ratio[yearly["intended_pattern"] == "DECLINING"].median() < 0.8
    assert ratio[yearly["intended_pattern"] == "TRENDING_UP"].median() > 1.2


# --------------------------------------------------------------------------
# 3. Realism: intended phenomena exist, at plausible levels
# --------------------------------------------------------------------------
def test_all_demand_patterns_exist(full_dataset):
    counts = full_dataset.sku_truth["intended_pattern"].value_counts()
    assert set(counts.index) == set(DEMAND_PATTERNS)
    assert (counts >= 0.02 * 5_000).all()


def test_all_supplier_types_exist(full_dataset):
    types = set(full_dataset.supplier_truth["archetype"])
    assert types == {a.name for a in SUPPLIER_ARCHETYPES}


def test_stockouts_exist_at_realistic_level(full_dataset):
    d = full_dataset.demand
    short_share = (d["shipped_qty"] < d["ordered_qty"]).mean()
    fill_rate = d["shipped_qty"].sum() / d["ordered_qty"].sum()
    assert 0.03 < short_share < 0.20
    assert 0.85 < fill_rate < 0.98
    inv = full_dataset.inventory
    assert (inv["on_hand_qty"] == 0).sum() > 100


def test_excess_inventory_exists_but_is_not_the_norm(full_dataset):
    inv = _latest_inventory(full_dataset)
    active = inv[inv["avg12"] > 0]
    months_of_supply = active["on_hand_qty"] / active["avg12"]
    share = (months_of_supply > 6).mean()
    assert 0.05 < share < 0.40
    assert months_of_supply.median() < 6


def test_dead_stock_exists(full_dataset):
    inv = _latest_inventory(full_dataset)
    dead = inv[(inv["on_hand_qty"] > 0) & (inv["sum6"] == 0)]
    assert len(dead) > 0.01 * 5_000
    value_share = (dead["on_hand_qty"] * dead["unit_cost"]).sum() / (
        inv["on_hand_qty"] * inv["unit_cost"]
    ).sum()
    assert 0.01 < value_share < 0.20
    # Lifecycle is one cause of dead stock, but not the only one.
    patterns = full_dataset.sku_truth.set_index("sku_id").loc[dead["sku_id"], "intended_pattern"]
    assert "END_OF_LIFE" in set(patterns)
    assert patterns.nunique() > 1


def test_partial_deliveries_exist(full_dataset):
    lines = _line_receipts(full_dataset)
    partial = (lines["n_receipts"] > 1) | (lines["status"] == "CLOSED_SHORT")
    assert 0.02 < partial.mean() < 0.25


def test_delayed_deliveries_exist(full_dataset):
    lines = _line_receipts(full_dataset).dropna(subset=["first_receipt"])
    late = (lines["first_receipt"] > lines["promised_date"]).mean()
    assert 0.05 < late < 0.50


def test_high_moq_slow_movers_exist(full_dataset):
    inv = _latest_inventory(full_dataset)
    slow = inv[(inv["avg12"] > 0) & (inv["avg12"] <= 5)]
    high_moq = slow[slow["moq"] >= 6 * slow["avg12"]]
    assert len(high_moq) > 0.01 * 5_000


def test_seasonality_is_detectable(full_dataset):
    """Seasonal SKUs repeat their yearly profile far more often than noise would.

    "Detectable" is defined against a null distribution: the year-over-year
    profile correlation of STABLE SKUs (no intended seasonality). A seasonal
    SKU is detected if it beats the stable 90th percentile, which by
    construction only 10% of stable SKUs do. Detection is deliberately not
    100%: low-amplitude or low-volume seasonal items are noisy, as in reality.
    """
    ds = full_dataset
    d = ds.demand[ds.demand.groupby("sku_id")["month_start"].transform("count") == 24]
    d = d.assign(year=d["month_start"].dt.year, month=d["month_start"].dt.month)
    profile = d.pivot_table(index="sku_id", columns=["year", "month"], values="ordered_qty")
    y1, y2 = profile[2024].to_numpy(), profile[2025].to_numpy()
    varying = (y1.std(axis=1) > 0) & (y2.std(axis=1) > 0)
    y1, y2 = y1[varying], y2[varying]
    y1c, y2c = y1 - y1.mean(axis=1, keepdims=True), y2 - y2.mean(axis=1, keepdims=True)
    corr = pd.Series(
        (y1c * y2c).sum(axis=1) / np.sqrt((y1c**2).sum(axis=1) * (y2c**2).sum(axis=1)),
        index=profile.index[varying],
    )
    truth = ds.sku_truth.set_index("sku_id").loc[corr.index]
    null_90 = corr[truth["intended_pattern"] == "STABLE"].quantile(0.90)
    seasonal = corr[truth["intended_pattern"] == "SEASONAL"]
    assert (seasonal > null_90).mean() > 0.45  # >4.5x the 10% false-positive rate

    volume = d.groupby("sku_id")["ordered_qty"].mean().loc[seasonal.index]
    high, low = seasonal[volume >= 20], seasonal[volume < 20]
    assert (high > null_90).mean() > (low > null_90).mean()


def test_value_concentration_emerges_without_being_assigned(full_dataset):
    """A realistic Pareto shape should emerge from the economics (not be assigned).

    Distributors typically see ~15-30% of SKUs making up 80% of consumption
    value; a much flatter curve would make ABC analysis meaningless.
    """
    ds = full_dataset
    usage = _trailing(ds.demand, ds.as_of, 12).groupby("sku_id")["ordered_qty"].sum()
    value = (usage * ds.products.set_index("sku_id")["unit_cost"]).dropna()
    value = value.sort_values(ascending=False)
    cumulative = value.cumsum() / value.sum()
    share_of_skus_for_80pct = (cumulative < 0.80).mean()
    assert 0.12 < share_of_skus_for_80pct < 0.32
    top20_share = value.iloc[: int(len(value) * 0.2)].sum() / value.sum()
    assert 0.65 < top20_share < 0.92


def test_no_impossible_dates(full_dataset):
    ds = full_dataset
    lines = _line_receipts(ds)
    assert (lines["promised_date"] >= lines["order_date"]).all()
    received = lines.dropna(subset=["first_receipt"])
    assert (received["first_receipt"] > received["order_date"]).all()
    assert ds.deliveries["receipt_date"].max() <= ds.as_of
    assert ds.purchase_orders["order_date"].max() <= ds.as_of
    assert ds.products["launch_date"].max() <= ds.as_of
    assert ds.demand["month_start"].between("2024-01-01", ds.as_of).all()


def test_no_negative_prices_or_costs(full_dataset):
    p = full_dataset.products
    assert (p["unit_cost"] > 0).all()
    assert (p["unit_price"] > p["unit_cost"]).all()
    assert (full_dataset.purchase_orders["unit_price"] > 0).all()
    assert p[["unit_cost", "unit_price"]].notna().all().all()


def test_no_impossible_lead_times(full_dataset):
    ds = full_dataset
    assert ds.suppliers["quoted_lead_time_days"].between(1, 365).all()
    assert ds.products["supplier_lead_time_days"].between(1, 365).all()
    lines = _line_receipts(ds).dropna(subset=["last_receipt"])
    actual = (lines["last_receipt"] - lines["order_date"]).dt.days
    assert actual.between(1, 365).all()


def test_moq_respects_order_multiple(full_dataset):
    p = full_dataset.products
    assert (p["moq"] % p["order_multiple"] == 0).all()
    po = full_dataset.purchase_orders.merge(p[["sku_id", "moq", "order_multiple"]], on="sku_id")
    assert (po["ordered_qty"] >= po["moq"]).all()
    assert (po["ordered_qty"] % po["order_multiple"] == 0).all()


# --------------------------------------------------------------------------
# Answer-key separation
# --------------------------------------------------------------------------
@pytest.mark.parametrize("forbidden", ["pattern", "archetype", "abc", "xyz", "truth", "overbuyer"])
def test_raw_tables_contain_no_hidden_or_derived_columns(full_dataset, forbidden):
    for name, table in full_dataset.raw_tables().items():
        for column in table.columns:
            assert forbidden not in column.lower(), f"{name}.{column}"
            assert not column.startswith("_"), f"{name}.{column}"


def test_save_writes_raw_and_truth_to_separate_folders(small_dataset, tmp_path):
    paths = PathConfig(
        raw_data_dir=tmp_path / "raw",
        processed_data_dir=tmp_path / "processed",
        output_dir=tmp_path / "out",
        synthetic_truth_dir=tmp_path / "truth",
    )
    small_dataset.save(paths)
    raw_files = {f.stem for f in (tmp_path / "raw").glob("*.csv")}
    truth_files = {f.stem for f in (tmp_path / "truth").glob("*.csv")}
    assert raw_files == set(small_dataset.raw_tables())
    assert truth_files == {"sku_generation_truth", "supplier_generation_truth"}
    reloaded = pd.read_csv(tmp_path / "raw" / "demand_monthly.csv")
    assert len(reloaded) == len(small_dataset.demand)
    assert np.issubdtype(reloaded["ordered_qty"].dtype, np.integer)
