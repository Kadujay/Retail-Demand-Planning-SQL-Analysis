"""Shared fixtures: generating the dataset once per test session keeps the suite fast."""

import pytest

from src.config import DataGenerationConfig
from src.data_generation import SyntheticDataset, generate_dataset

SMALL = DataGenerationConfig(random_seed=42, n_skus=400, n_suppliers=20)


@pytest.fixture(scope="session")
def full_dataset() -> SyntheticDataset:
    """The default dataset (5,000 SKUs, 50 suppliers, seed 42)."""
    return generate_dataset(DataGenerationConfig(random_seed=42))


@pytest.fixture(scope="session")
def small_dataset() -> SyntheticDataset:
    """A small dataset for tests that mutate or regenerate data."""
    return generate_dataset(SMALL)


# --------------------------------------------------------------------------
# PostgreSQL fixtures
# --------------------------------------------------------------------------
# Database tests use a separate "<POSTGRES_DB>_test" database. Locally they are
# skipped when PostgreSQL is not running; in CI (ICT_REQUIRE_DB=1) an
# unreachable database fails the run instead of silently skipping.
import os  # noqa: E402
from dataclasses import replace  # noqa: E402

from sqlalchemy import create_engine, text  # noqa: E402

from src.config import Config, PathConfig, get_database_url  # noqa: E402


def _test_db_name() -> str:
    return os.getenv("POSTGRES_DB", "inventory_control_tower") + "_test"


@pytest.fixture(scope="session")
def db_config(tmp_path_factory, full_dataset) -> Config:
    """Config pointing at temporary folders holding the seed-42 raw extract."""
    root = tmp_path_factory.mktemp("db_run")
    paths = PathConfig(
        raw_data_dir=root / "raw",
        processed_data_dir=root / "processed",
        output_dir=root / "outputs",
        synthetic_truth_dir=root / "truth",
        raw_dirty_data_dir=root / "raw_dirty",
    )
    full_dataset.save(paths)
    full_dataset.save_dirty(paths, seed=42)
    return replace(Config(), paths=paths)


@pytest.fixture(scope="session")
def db_engine(db_config):
    """A freshly initialised and loaded test database."""
    from src.database import build_transformations, init_database, load_validated_data

    admin = create_engine(get_database_url("postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            name = _test_db_name()
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            conn.execute(text(f'CREATE DATABASE "{name}"'))
    except Exception as exc:  # pragma: no cover - depends on environment
        if os.getenv("ICT_REQUIRE_DB") == "1":
            raise
        pytest.skip(f"PostgreSQL not available ({type(exc).__name__}); run docker compose up -d")
    finally:
        admin.dispose()

    engine = create_engine(get_database_url(_test_db_name()))
    init_database(engine, db_config)
    result = load_validated_data(engine, db_config.paths.raw_data_dir, "clean")
    assert result.status == "ACCEPTED"
    build_transformations(engine, db_config)
    yield engine
    engine.dispose()
