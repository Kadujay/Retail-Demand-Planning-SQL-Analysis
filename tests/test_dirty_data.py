"""Optional dirty-data mode: every injected defect is documented and detected."""

import pandas as pd
import pytest

from src.config import PathConfig
from src.data_validation import overall_status, run_all_checks
from src.dirty_data import DEFECT_TYPES, inject_realistic_defects


@pytest.fixture(scope="module")
def dirty(full_dataset):
    return inject_realistic_defects(full_dataset.raw_tables(), full_dataset.as_of, seed=42)


@pytest.fixture(scope="module")
def reports(full_dataset, dirty):
    clean = run_all_checks(full_dataset.raw_tables(), full_dataset.as_of)
    return clean, run_all_checks(dirty.tables, full_dataset.as_of)


def _failed(report, check, table):
    rows = report[(report["check"] == check) & (report["table"] == table)]
    return int(rows["failed_rows"].sum())


def test_every_defect_type_is_injected_and_documented(dirty):
    assert set(dirty.manifest["defect_type"]) == set(DEFECT_TYPES)
    assert dirty.manifest["business_explanation"].str.len().min() > 40
    assert len(dirty.manifest) == 25  # small and controlled, not random corruption


def test_each_defect_is_caught_by_its_check(dirty, reports):
    """Injected count == rows flagged by the named check, which is 0 on clean data."""
    clean, dirty_report = reports
    expected = dirty.manifest.groupby(["expected_check", "table"]).size()
    for (check, table), count in expected.items():
        report_table = "purchase_orders" if check == "receipt_qty_implausible" else table
        assert _failed(clean, check, report_table) == 0, check
        assert _failed(dirty_report, check, report_table) == count, check


def test_verdicts_clean_accepted_dirty_rejected(reports):
    clean, dirty_report = reports
    assert overall_status(clean) == "ACCEPTED"
    assert overall_status(dirty_report) == "REJECTED"


def test_dirty_mode_is_reproducible_and_seed_dependent(full_dataset, dirty):
    again = inject_realistic_defects(full_dataset.raw_tables(), full_dataset.as_of, seed=42)
    pd.testing.assert_frame_equal(dirty.manifest, again.manifest)
    other = inject_realistic_defects(full_dataset.raw_tables(), full_dataset.as_of, seed=7)
    assert not dirty.manifest["record_key"].equals(other.manifest["record_key"])


def test_injection_never_modifies_the_clean_tables(full_dataset):
    before = {k: v.copy() for k, v in full_dataset.raw_tables().items()}
    inject_realistic_defects(full_dataset.raw_tables(), full_dataset.as_of, seed=42)
    for name, table in full_dataset.raw_tables().items():
        pd.testing.assert_frame_equal(table, before[name])


def test_clean_data_is_the_default_input():
    paths = PathConfig()
    assert paths.raw_data_dir.name == "raw"
    assert paths.raw_dirty_data_dir != paths.raw_data_dir


def test_dirty_copy_and_manifest_are_saved_separately(small_dataset, tmp_path):
    paths = PathConfig(
        raw_data_dir=tmp_path / "raw",
        processed_data_dir=tmp_path / "p",
        output_dir=tmp_path / "o",
        synthetic_truth_dir=tmp_path / "truth",
        raw_dirty_data_dir=tmp_path / "raw_dirty",
    )
    small_dataset.save_dirty(paths, seed=42)
    assert (tmp_path / "raw_dirty" / "supplier_deliveries.csv").exists()
    assert (tmp_path / "truth" / "injected_defects.csv").exists()
    assert not (tmp_path / "raw_dirty" / "injected_defects.csv").exists()
    assert not (tmp_path / "raw").exists()
