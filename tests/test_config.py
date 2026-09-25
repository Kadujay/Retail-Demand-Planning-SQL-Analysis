"""Tests for the configuration layer (Phase 1).

The configuration is the contract for every business threshold, so its
validation rules and the service-level -> Z mapping are tested first.
"""

import importlib

import pytest

from src.config import (
    ABCConfig,
    ForecastConfig,
    XYZConfig,
    get_config,
    get_database_url,
    service_level_to_z,
)


@pytest.mark.parametrize(
    ("service_level", "expected_z"),
    [(0.90, 1.2816), (0.95, 1.6449), (0.98, 2.0537)],
)
def test_service_level_to_z_matches_standard_normal_table(service_level, expected_z):
    assert service_level_to_z(service_level) == pytest.approx(expected_z, abs=1e-4)


def test_higher_service_level_requires_higher_z():
    # Core trade-off: more protection -> larger safety-stock multiplier.
    assert service_level_to_z(0.90) < service_level_to_z(0.95) < service_level_to_z(0.98)


@pytest.mark.parametrize("invalid", [0.0, 1.0, -0.1, 1.5])
def test_service_level_to_z_rejects_invalid_levels(invalid):
    with pytest.raises(ValueError):
        service_level_to_z(invalid)


def test_abc_thresholds_must_be_ordered():
    with pytest.raises(ValueError):
        ABCConfig(a_threshold=0.95, b_threshold=0.80)


def test_xyz_thresholds_must_be_ordered():
    with pytest.raises(ValueError):
        XYZConfig(x_threshold=1.0, y_threshold=0.5)


def test_wma_weights_must_sum_to_one():
    with pytest.raises(ValueError):
        ForecastConfig(wma_weights=(0.5, 0.6))


def test_default_config_has_class_differentiated_service_levels():
    levels = get_config().safety_stock.service_level_by_abc
    assert levels["A"] > levels["B"] > levels["C"]


def test_default_paths_are_inside_project():
    paths = get_config().paths
    assert paths.output_dir.name == "outputs"
    assert paths.raw_data_dir.parts[-2:] == ("data", "raw")


def test_database_url_reads_environment(monkeypatch):
    monkeypatch.setenv("POSTGRES_HOST", "db.example")
    monkeypatch.setenv("POSTGRES_DB", "ict")
    url = get_database_url()
    assert url.startswith("postgresql+psycopg://")
    assert "@db.example:" in url and url.endswith("/ict")


@pytest.mark.parametrize(
    "module",
    [
        "src.pipeline",
        "src.data_generation",
        "src.data_validation",
        "src.abc_xyz",
        "src.forecasting",
        "src.forecast_accuracy",
        "src.safety_stock",
        "src.inventory_health",
        "src.supplier_analysis",
        "src.replenishment",
        "src.working_capital",
    ],
)
def test_all_modules_import(module):
    importlib.import_module(module)
