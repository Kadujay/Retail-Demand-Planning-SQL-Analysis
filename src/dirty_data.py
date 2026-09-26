"""Optional realistic-noise mode: inject a few documented ERP data defects.

The default dataset is clean. ``python -m src.data_generation --dirty``
writes a *separate* copy to ``data/raw_dirty/`` with a small, controlled
number of the defects real ERP extracts contain. Every defect:

* has a business explanation (below and in docs/assumptions.md);
* is reproducible from the seed;
* is listed in a manifest (the dirty-data answer key) stored with the
  synthetic truth, not with the raw data;
* is caught by a named check in ``src/data_validation.py`` — which the
  tests verify defect by defect.

The analytical pipeline never reads the dirty copy unless explicitly asked.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.config import DirtyDataConfig

# Independent random stream: injecting defects must never change the clean data.
DIRTY_STREAM_ID = 1_000


@dataclass(frozen=True)
class DefectType:
    """A documented kind of ERP data defect."""

    name: str
    expected_check: str
    severity: str
    business_explanation: str


DEFECT_TYPES: dict[str, DefectType] = {
    d.name: d
    for d in (
        DefectType(
            "DUPLICATE_RECEIPT",
            "duplicate_receipt",
            "ERROR",
            "A goods receipt posted twice (double scan at the dock or a re-post after a "
            "system timeout). Overstates stock and supplier fill rate.",
        ),
        DefectType(
            "STALE_OPEN_PO",
            "stale_open_po",
            "WARNING",
            "A PO line the supplier never shipped and the buyer never cancelled. It sits in "
            "inventory position as phantom supply, so the planner under-orders.",
        ),
        DefectType(
            "UNIT_OF_MEASURE_ERROR",
            "receipt_qty_implausible",
            "ERROR",
            "Receipt keyed in units while the system expected cases (or vice versa), so the "
            "quantity is multiplied by the case pack. Inflates stock by an order of magnitude.",
        ),
        DefectType(
            "BACKDATED_RECEIPT",
            "receipt_before_order",
            "ERROR",
            "Receipt posted with a date earlier than the PO (wrong date keyed or back-posted "
            "to close a period). Breaks lead-time and OTIF measurement.",
        ),
        DefectType(
            "MISSING_OPTIONAL_FIELD",
            "missing_optional_fields",
            "WARNING",
            "Master data created in a hurry or migrated from a legacy system without optional "
            "attributes (launch date, supplier region). Limits lifecycle and sourcing analysis.",
        ),
        DefectType(
            "INVALID_SUPPLIER_CODE",
            "missing_supplier",
            "ERROR",
            "PO keyed with a supplier code that does not exist (typo). Spend and delivery "
            "performance cannot be attributed.",
        ),
        DefectType(
            "WRONG_SUPPLIER_REFERENCE",
            "po_supplier_mismatch",
            "WARNING",
            "PO keyed against a real but wrong supplier. Spend and OTIF are credited to the "
            "wrong supplier; plausible in multi-sourcing, so a warning rather than an error.",
        ),
    )
}


@dataclass
class DirtyResult:
    """Dirty copies of the raw tables plus the manifest of injected defects."""

    tables: dict[str, pd.DataFrame]
    manifest: pd.DataFrame


class _Injector:
    """Applies defects to copies of the tables and records each one."""

    def __init__(self, tables: Mapping[str, pd.DataFrame], rng: np.random.Generator) -> None:
        self.tables = {name: t.copy() for name, t in tables.items()}
        self.rng = rng
        self.records: list[dict[str, object]] = []
        # Reserve disjoint samples so one defect never masks another.
        self._used_receipts: set[str] = set()
        self._used_lines: set[str] = set()

    def record(
        self, defect: str, table: str, key: str, column: str, before: object, after: object
    ) -> None:
        d = DEFECT_TYPES[defect]
        self.records.append(
            {
                "defect_type": defect,
                "table": table,
                "record_key": key,
                "column": column,
                "original_value": before,
                "injected_value": after,
                "expected_check": d.expected_check,
                "severity": d.severity,
                "business_explanation": d.business_explanation,
            }
        )

    # -- sampling helpers -------------------------------------------------
    def _single_receipt_lines(self) -> pd.DataFrame:
        """Receipts that are the only receipt of their PO line (unambiguous targets)."""
        rcv = self.tables["supplier_deliveries"]
        counts = rcv["po_line_id"].map(rcv["po_line_id"].value_counts())
        return rcv[counts == 1]

    def _sample_receipts(self, n: int, candidates: pd.DataFrame) -> pd.DataFrame:
        free = candidates[
            ~candidates["receipt_id"].isin(self._used_receipts)
            & ~candidates["po_line_id"].isin(self._used_lines)
        ]
        chosen = free.iloc[np.sort(self.rng.choice(len(free), size=n, replace=False))]
        self._used_receipts.update(chosen["receipt_id"])
        self._used_lines.update(chosen["po_line_id"])
        return chosen

    def _sample_po_lines(self, n: int) -> pd.DataFrame:
        po = self.tables["purchase_orders"]
        received_lines = set(self.tables["supplier_deliveries"]["po_line_id"])
        free = po[~po["po_line_id"].isin(self._used_lines) & po["po_line_id"].isin(received_lines)]
        chosen = free.iloc[np.sort(self.rng.choice(len(free), size=n, replace=False))]
        self._used_lines.update(chosen["po_line_id"])
        return chosen

    # -- defects ----------------------------------------------------------
    def duplicate_receipts(self, n: int) -> None:
        rcv = self.tables["supplier_deliveries"]
        chosen = self._sample_receipts(n, self._single_receipt_lines())
        next_id = int(rcv["receipt_id"].str[4:].astype(int).max()) + 1
        copies = chosen.copy()
        copies["receipt_id"] = [f"RCV-{next_id + i:07d}" for i in range(n)]
        self.tables["supplier_deliveries"] = pd.concat([rcv, copies], ignore_index=True)
        for original, copy in zip(chosen["receipt_id"], copies["receipt_id"], strict=True):
            self.record(
                "DUPLICATE_RECEIPT", "supplier_deliveries", copy, "receipt_id", original, copy
            )

    def stale_open_pos(self, n: int, as_of: pd.Timestamp) -> None:
        po = self.tables["purchase_orders"]
        products = self.tables["products"]
        picks = products.iloc[self.rng.choice(len(products), size=n, replace=False)]
        age_days = self.rng.integers(200, 400, n)
        order_date = as_of - pd.to_timedelta(age_days, unit="D")
        next_po = int(po["po_number"].str[3:].astype(int).max()) + 1
        new = pd.DataFrame(
            {
                "po_line_id": [f"PO-{next_po + i:06d}-01" for i in range(n)],
                "po_number": [f"PO-{next_po + i:06d}" for i in range(n)],
                "sku_id": picks["sku_id"].to_numpy(),
                "supplier_id": picks["supplier_id"].to_numpy(),
                "order_date": order_date,
                "promised_date": order_date
                + pd.to_timedelta(picks["supplier_lead_time_days"].to_numpy(), unit="D"),
                "ordered_qty": picks["moq"].to_numpy(),
                "unit_price": picks["unit_cost"].to_numpy(),
                "status": "OPEN",
            }
        )
        self.tables["purchase_orders"] = pd.concat([po, new], ignore_index=True)
        for key, days in zip(new["po_line_id"], age_days, strict=True):
            self.record(
                "STALE_OPEN_PO",
                "purchase_orders",
                key,
                "status",
                None,
                f"OPEN, ordered {days} days ago",
            )

    def unit_of_measure_errors(self, n: int) -> None:
        rcv = self.tables["supplier_deliveries"]
        packs = (
            self.tables["purchase_orders"]
            .merge(self.tables["products"][["sku_id", "order_multiple"]], on="sku_id")
            .set_index("po_line_id")["order_multiple"]
        )
        candidates = self._single_receipt_lines()
        candidates = candidates[candidates["po_line_id"].map(packs) >= 10]
        chosen = self._sample_receipts(n, candidates)
        for idx, row in chosen.iterrows():
            wrong = int(row["received_qty"] * packs[row["po_line_id"]])
            rcv.loc[idx, "received_qty"] = wrong
            self.record(
                "UNIT_OF_MEASURE_ERROR",
                "supplier_deliveries",
                row["receipt_id"],
                "received_qty",
                int(row["received_qty"]),
                wrong,
            )

    def backdated_receipts(self, n: int) -> None:
        rcv = self.tables["supplier_deliveries"]
        order_dates = self.tables["purchase_orders"].set_index("po_line_id")["order_date"]
        chosen = self._sample_receipts(n, self._single_receipt_lines())
        for idx, row in chosen.iterrows():
            wrong = order_dates[row["po_line_id"]] - pd.Timedelta(
                days=int(self.rng.integers(1, 21))
            )
            rcv.loc[idx, "receipt_date"] = wrong
            self.record(
                "BACKDATED_RECEIPT",
                "supplier_deliveries",
                row["receipt_id"],
                "receipt_date",
                row["receipt_date"].date(),
                wrong.date(),
            )

    def missing_optional_fields(self, n: int) -> None:
        n_products = n - n // 2
        for table, column, key_col, count in (
            ("products", "launch_date", "sku_id", n_products),
            ("suppliers", "region", "supplier_id", n // 2),
        ):
            df = self.tables[table]
            idx = df.index[np.sort(self.rng.choice(len(df), size=count, replace=False))]
            for i in idx:
                before = df.loc[i, column]
                self.record(
                    "MISSING_OPTIONAL_FIELD",
                    table,
                    df.loc[i, key_col],
                    column,
                    before.date() if hasattr(before, "date") else before,
                    None,
                )
            df.loc[idx, column] = pd.NaT if column == "launch_date" else None

    def incorrect_supplier_references(self, n: int) -> None:
        po = self.tables["purchase_orders"]
        suppliers = self.tables["suppliers"]["supplier_id"].to_numpy()
        chosen = self._sample_po_lines(n)
        for i, (idx, row) in enumerate(chosen.iterrows()):
            if i < n // 2:  # typo: letter O instead of zero -> code does not exist
                wrong, defect = row["supplier_id"].replace("0", "O", 1), "INVALID_SUPPLIER_CODE"
            else:  # real supplier, but not the one supplying this SKU
                others = suppliers[suppliers != row["supplier_id"]]
                wrong, defect = str(self.rng.choice(others)), "WRONG_SUPPLIER_REFERENCE"
            po.loc[idx, "supplier_id"] = wrong
            self.record(
                defect,
                "purchase_orders",
                row["po_line_id"],
                "supplier_id",
                row["supplier_id"],
                wrong,
            )


def inject_realistic_defects(
    tables: Mapping[str, pd.DataFrame],
    as_of: pd.Timestamp,
    seed: int,
    config: DirtyDataConfig | None = None,
) -> DirtyResult:
    """Return dirty copies of ``tables`` and a manifest of every injected defect.

    The input tables are not modified.
    """
    config = config or DirtyDataConfig()
    injector = _Injector(tables, np.random.default_rng([seed, DIRTY_STREAM_ID]))
    injector.duplicate_receipts(config.duplicate_receipts)
    injector.unit_of_measure_errors(config.unit_of_measure_errors)
    injector.backdated_receipts(config.backdated_receipts)
    injector.incorrect_supplier_references(config.incorrect_supplier_references)
    injector.stale_open_pos(config.stale_open_pos, as_of)
    injector.missing_optional_fields(config.missing_optional_fields)
    return DirtyResult(tables=injector.tables, manifest=pd.DataFrame(injector.records))
