"""Read and write the raw operational tables (the CSV "ERP extract").

The raw CSV files are the system of record for this project: they are never
modified after generation. Validation, database loading and the notebooks
all read them through this module so every consumer parses types the same
way.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pandas as pd

# File name (without .csv) -> date columns to parse.
RAW_TABLES: dict[str, tuple[str, ...]] = {
    "suppliers": (),
    "products": ("launch_date",),
    "demand_monthly": ("month_start",),
    "inventory_snapshot": ("month_end",),
    "purchase_orders": ("order_date", "promised_date"),
    "supplier_deliveries": ("receipt_date",),
}


def load_raw_tables(folder: Path) -> dict[str, pd.DataFrame]:
    """Load all raw tables from ``folder``.

    Raises:
        FileNotFoundError: If a table is missing (run data generation first).
    """
    tables = {}
    for name, date_cols in RAW_TABLES.items():
        path = Path(folder) / f"{name}.csv"
        if not path.exists():
            raise FileNotFoundError(
                f"{path} not found. Generate data first: python -m src.data_generation"
            )
        tables[name] = pd.read_csv(path, parse_dates=list(date_cols))
    return tables


def save_tables(tables: Mapping[str, pd.DataFrame], folder: Path) -> None:
    """Write tables as CSV (ISO dates) into ``folder``."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    for name, table in tables.items():
        table.to_csv(folder / f"{name}.csv", index=False, date_format="%Y-%m-%d")


def infer_as_of(tables: Mapping[str, pd.DataFrame]) -> pd.Timestamp:
    """The as-of date of an extract = the latest inventory snapshot."""
    return pd.Timestamp(tables["inventory_snapshot"]["month_end"].max())
