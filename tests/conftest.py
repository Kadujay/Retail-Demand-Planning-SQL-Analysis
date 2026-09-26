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
