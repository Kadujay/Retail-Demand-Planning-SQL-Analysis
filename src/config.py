"""Central configuration for the Supply Chain Inventory Control Tower.

Every business threshold used anywhere in the pipeline lives here, so that:

* nothing in the analytical modules is a "magic number";
* a planner can change policy (e.g. ABC cut-offs, service levels) without
  touching calculation code;
* the scenario engine can create modified copies of the configuration
  (``dataclasses.replace``) and re-run the same logic.

The defaults are common textbook / practitioner starting points. They are
*policy choices*, not laws of nature — see ``docs/assumptions.md`` for the
rationale behind each default.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from dotenv import load_dotenv
from scipy.stats import norm
from sqlalchemy.engine import URL

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------
# Resolved relative to the repository root so the pipeline works from any
# working directory. Each can be overridden with an environment variable
# (see .env.example) — no hard-coded absolute paths.
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent


def _path_from_env(env_var: str, default: Path) -> Path:
    """Return a path from an environment variable, or the default."""
    value = os.getenv(env_var)
    return Path(value).expanduser().resolve() if value else default


@dataclass(frozen=True)
class PathConfig:
    """File-system locations for data and outputs."""

    raw_data_dir: Path = field(
        default_factory=lambda: _path_from_env("RAW_DATA_DIR", PROJECT_ROOT / "data" / "raw")
    )
    processed_data_dir: Path = field(
        default_factory=lambda: _path_from_env(
            "PROCESSED_DATA_DIR", PROJECT_ROOT / "data" / "processed"
        )
    )
    output_dir: Path = field(
        default_factory=lambda: _path_from_env("OUTPUT_DIR", PROJECT_ROOT / "outputs")
    )
    # Generator "answer key" (intended patterns / supplier archetypes).
    # Kept apart from raw data so analytics can never use it as a feature.
    synthetic_truth_dir: Path = field(
        default_factory=lambda: _path_from_env(
            "SYNTHETIC_TRUTH_DIR", PROJECT_ROOT / "data" / "synthetic_truth"
        )
    )
    # Optional "dirty" copy of the raw data (realistic ERP defects injected).
    # Never read by the analytical pipeline unless explicitly requested.
    raw_dirty_data_dir: Path = field(
        default_factory=lambda: _path_from_env(
            "RAW_DIRTY_DATA_DIR", PROJECT_ROOT / "data" / "raw_dirty"
        )
    )
    sql_dir: Path = PROJECT_ROOT / "sql"

    @property
    def sql_answers_dir(self) -> Path:
        """Small CSV answers to the SQL business questions (committed)."""
        return self.output_dir / "sql_answers"


# --------------------------------------------------------------------------
# Synthetic data generation
# --------------------------------------------------------------------------
DEFAULT_RANDOM_SEED: int = 42


def _seed_from_env() -> int:
    """Seed from SYNTHETIC_DATA_SEED if set, else the default (42)."""
    value = os.getenv("SYNTHETIC_DATA_SEED")
    return int(value) if value else DEFAULT_RANDOM_SEED


@dataclass(frozen=True)
class DataGenerationConfig:
    """Size and reproducibility settings for the synthetic dataset.

    The same seed always produces byte-identical tables. Override the seed
    with ``SYNTHETIC_DATA_SEED`` or ``python -m src.pipeline --seed N``.
    """

    random_seed: int = field(default_factory=_seed_from_env)
    n_skus: int = 5_000
    n_suppliers: int = 50
    n_history_months: int = 24
    # Last month of history; the "as-of" date is its last day.
    history_end_month: str = "2025-12"
    # Months simulated before the history window and then discarded, so the
    # first reported month already has realistic stock and open POs.
    burn_in_months: int = 12

    def __post_init__(self) -> None:
        if self.n_skus < 1 or self.n_suppliers < 1:
            raise ValueError("n_skus and n_suppliers must be positive")
        if self.n_history_months < 13:
            raise ValueError("n_history_months must be >= 13 (seasonality needs > 1 year)")
        if self.burn_in_months < 0:
            raise ValueError("burn_in_months must be >= 0")


# --------------------------------------------------------------------------
# Optional dirty-data mode
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class DirtyDataConfig:
    """How many realistic ERP defects to inject in ``--dirty`` mode.

    Deliberately small: the point is to prove each defect is detected, not
    to corrupt the dataset. Each defect type is documented in
    docs/assumptions.md and src/dirty_data.py.
    """

    duplicate_receipts: int = 5
    stale_open_pos: int = 4
    unit_of_measure_errors: int = 3
    backdated_receipts: int = 3
    missing_optional_fields: int = 6
    incorrect_supplier_references: int = 4


# --------------------------------------------------------------------------
# Data-quality rules
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class DataQualityConfig:
    """Thresholds used by the validation framework (src/data_validation.py)."""

    # Longest credible lead time; beyond a year is almost certainly a
    # data-entry error, not a slow supplier.
    max_credible_lead_time_days: int = 365
    # Receiving slightly more than ordered happens (overage); more than 5%
    # is suspicious.
    over_receipt_tolerance: float = 0.05
    # Receiving 3x the ordered quantity is not an overage: it is almost
    # always a unit-of-measure error (cases keyed as units or vice versa).
    implausible_receipt_multiple: float = 3.0
    # An open PO line this many days past its promised date is "stale":
    # it inflates inventory position with supply that will likely never come.
    stale_open_po_days: int = 90


# --------------------------------------------------------------------------
# SQL screening layer
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class SqlScreeningConfig:
    """Parameters for the first-pass SQL screens (sql/transformations.sql).

    The SQL layer screens with a simple, uniform rule so problems can be
    listed before the full Python planning logic exists. Phase 7 replaces
    the screening reorder point with the class-based, forecast-error policy.
    """

    service_level: float = 0.95
    trailing_demand_months: int = 6
    high_cover_months: float = 6.0

    def __post_init__(self) -> None:
        if not 0 < self.service_level < 1:
            raise ValueError("service_level must be in (0, 1)")


# --------------------------------------------------------------------------
# ABC classification (value / Pareto)
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class ABCConfig:
    """Cumulative-share cut-offs for ABC classification.

    SKUs are ranked by annual consumption value (annual demand x unit cost).
    A SKU is class A while the cumulative share *before* it is below
    ``a_threshold``; B up to ``b_threshold``; C thereafter.

    80/95 is a common default, but companies legitimately use 70/90 or
    75/95 depending on how concentrated their value is and how much planner
    capacity exists for A items — hence configurable.
    """

    a_threshold: float = 0.80
    b_threshold: float = 0.95
    # Trailing window used for annual consumption value. 12 months reflects
    # the current value mix and removes seasonality from the annual total.
    window_months: int = 12

    def __post_init__(self) -> None:
        if not 0 < self.a_threshold < self.b_threshold < 1:
            raise ValueError("ABC thresholds must satisfy 0 < A < B < 1")


# --------------------------------------------------------------------------
# XYZ classification (demand variability)
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class XYZConfig:
    """Coefficient-of-variation cut-offs for XYZ classification.

    CV = standard deviation / mean of monthly demand.
    X: CV <= x_threshold, Y: x_threshold < CV <= y_threshold, Z: CV > y_threshold.

    ``adi_intermittent_threshold``: average demand interval (periods per
    non-zero demand). ADI > 1.32 is the Syntetos-Boylan-Croston cut-off for
    intermittent demand; such SKUs are classed Z because CV of mostly-zero
    series is not a reliable forecastability measure.
    ``min_history_months``: SKUs younger than this (new products) are
    flagged NEW rather than classified on insufficient history.
    """

    x_threshold: float = 0.50
    y_threshold: float = 1.00
    adi_intermittent_threshold: float = 1.32
    min_history_months: int = 6
    # Same trailing window as ABC so both dimensions describe the same period.
    window_months: int = 12

    def __post_init__(self) -> None:
        if not 0 < self.x_threshold < self.y_threshold:
            raise ValueError("XYZ thresholds must satisfy 0 < X < Y")
        if self.adi_intermittent_threshold < 1:
            raise ValueError("ADI threshold must be >= 1 (ADI is never below 1)")


# --------------------------------------------------------------------------
# Forecasting
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class ForecastConfig:
    """Forecast method parameters and validation design."""

    # Time-based hold-out: the last N months are used for validation only.
    test_months: int = 6
    moving_average_window: int = 3
    # Weights for weighted moving average, oldest -> newest; must sum to 1.
    wma_weights: tuple[float, ...] = (0.2, 0.3, 0.5)
    ses_alpha: float = 0.3
    # Holt's linear trend: level and trend smoothing. Fixed (not optimised)
    # so the method stays explainable and stable on short histories.
    holt_alpha: float = 0.3
    holt_beta: float = 0.1
    season_length: int = 12
    # Seasonal naive is only a candidate when two full seasonal cycles exist,
    # so a repeating yearly pattern can actually be observed.
    seasonal_naive_min_history_months: int = 24
    # Planning horizon for forward-looking forecasts (months).
    horizon_months: int = 6
    # Tracking signal = cumulative error / MAD. |TS| beyond this flags a
    # persistently biased forecast (4 is the common textbook control limit).
    tracking_signal_limit: float = 4.0

    def __post_init__(self) -> None:
        if abs(sum(self.wma_weights) - 1.0) > 1e-9:
            raise ValueError("wma_weights must sum to 1")
        for name in ("ses_alpha", "holt_alpha", "holt_beta"):
            if not 0 < getattr(self, name) <= 1:
                raise ValueError(f"{name} must be in (0, 1]")


# --------------------------------------------------------------------------
# Service levels and safety stock
# --------------------------------------------------------------------------
# Cycle service level (probability of no stockout during a replenishment
# cycle) by ABC class. Higher protection is reserved for the items with the
# largest financial impact; C items accept more risk to save working capital.
DEFAULT_SERVICE_LEVEL_BY_ABC: Mapping[str, float] = MappingProxyType(
    {"A": 0.98, "B": 0.95, "C": 0.90}
)

# Days per month used to convert monthly demand into daily demand.
# 30.4 = 365 / 12; a planning convention, documented in docs/assumptions.md.
DAYS_PER_MONTH: float = 365.0 / 12.0


def service_level_to_z(service_level: float) -> float:
    """Map a cycle service level to a one-sided standard-normal Z-score.

    Z is the inverse of the standard normal CDF, e.g.
    90% -> 1.2816, 95% -> 1.6449, 98% -> 2.0537.

    Args:
        service_level: Target probability of no stockout per cycle, in (0, 1).

    Returns:
        The Z-score used as the safety-stock multiplier.

    Raises:
        ValueError: If ``service_level`` is not strictly between 0 and 1.
    """
    if not 0 < service_level < 1:
        raise ValueError(f"service_level must be in (0, 1), got {service_level}")
    return float(norm.ppf(service_level))


SigmaSource = Literal["forecast_error", "demand"]


@dataclass(frozen=True)
class SafetyStockConfig:
    """Safety-stock policy settings."""

    service_level_by_abc: Mapping[str, float] = field(
        default_factory=lambda: DEFAULT_SERVICE_LEVEL_BY_ABC
    )
    # If True, use the combined demand + lead-time variability formula
    # instead of the demand-only formula (see docs/methodology.md).
    include_lead_time_variability: bool = True
    # Which uncertainty safety stock protects against:
    # "forecast_error" (default) = RMSE of one-step-ahead forecast errors, so
    # predictable movements (trend, seasonality) are not double-counted as
    # risk; "demand" = std of raw monthly demand (textbook simplification).
    sigma_source: SigmaSource = "forecast_error"
    # Minimum forecast-error observations before sigma_source="forecast_error"
    # is trusted; otherwise fall back to demand std.
    min_error_observations: int = 6

    def __post_init__(self) -> None:
        if self.sigma_source not in ("forecast_error", "demand"):
            raise ValueError("sigma_source must be 'forecast_error' or 'demand'")
        for abc_class, level in self.service_level_by_abc.items():
            if not 0 < level < 1:
                raise ValueError(f"service level for {abc_class} must be in (0, 1)")


# --------------------------------------------------------------------------
# Inventory health classification
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class InventoryHealthConfig:
    """Thresholds for inventory health status.

    Rules are evaluated in priority order:
    STOCKOUT -> CRITICAL -> BELOW_REORDER_POINT -> DEAD_STOCK -> EXCESS ->
    HEALTHY. See docs/business_logic.md for the full rules.

    Excess is measured against the SAME policy maximum the replenishment
    engine orders up to (max stock level), so the health report and the
    order recommendations can never contradict each other.
    """

    # Tolerance above the policy maximum before stock is called excess,
    # expressed in days of forward demand. Absorbs normal forecast noise so
    # that a SKU one unit above max is not flagged.
    excess_tolerance_days: float = 30.0
    # No demand in this many trailing months -> dead stock candidate.
    dead_stock_months_without_demand: int = 6


# --------------------------------------------------------------------------
# Supplier performance
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class SupplierConfig:
    """Definitions and segmentation thresholds for supplier analytics."""

    # On time = full quantity received no later than the ORIGINAL promised
    # date + this tolerance. Re-promised dates are ignored so lateness cannot
    # be hidden by moving the target. Early receipts count as on time.
    on_time_tolerance_days: int = 0
    # In full = cumulative qty received by the on-time cut-off >= this share
    # of ordered qty (split deliveries are summed per PO line).
    in_full_tolerance: float = 1.0
    # Lead-time statistics are pooled at supplier level (SKU-level samples
    # are too small); suppliers with fewer receipts are flagged low-confidence.
    min_receipts_for_stats: int = 10
    # Segmentation thresholds (documented in docs/business_logic.md).
    otif_target: float = 0.95
    otif_watch: float = 0.85
    lead_time_cv_high: float = 0.30


# --------------------------------------------------------------------------
# Replenishment
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class ReplenishmentConfig:
    """Periodic-review (R, s, S) policy settings.

    The planning run happens every ``review_period_months``. Between runs
    nobody can react, so the protection interval is lead time + review
    period, and the reorder point / safety stock must cover both.
    Setting the review period to 0 reduces the policy to continuous review
    (ROP = lead-time demand + safety stock).
    """

    review_period_months: float = 1.0
    # Administrative cost of raising, receiving and paying one purchase
    # order. Used only for the EOQ lot-size reference.
    ordering_cost_per_order: float = 75.0

    def __post_init__(self) -> None:
        if self.review_period_months < 0:
            raise ValueError("review_period_months must be >= 0")
        if self.ordering_cost_per_order < 0:
            raise ValueError("ordering_cost_per_order must be >= 0")


# --------------------------------------------------------------------------
# Working capital
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class WorkingCapitalConfig:
    """Financial assumptions for inventory carrying cost."""

    # Annual carrying cost as a share of inventory value (capital cost,
    # storage, insurance, obsolescence). 20-30% is a typical range.
    # Also the holding-cost rate in the EOQ formula.
    annual_carrying_cost_rate: float = 0.25

    def __post_init__(self) -> None:
        if not 0 < self.annual_carrying_cost_rate < 1:
            raise ValueError("annual_carrying_cost_rate must be in (0, 1)")


# --------------------------------------------------------------------------
# Top-level configuration
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Config:
    """Aggregate configuration passed through the pipeline."""

    paths: PathConfig = field(default_factory=PathConfig)
    data: DataGenerationConfig = field(default_factory=DataGenerationConfig)
    abc: ABCConfig = field(default_factory=ABCConfig)
    xyz: XYZConfig = field(default_factory=XYZConfig)
    forecast: ForecastConfig = field(default_factory=ForecastConfig)
    safety_stock: SafetyStockConfig = field(default_factory=SafetyStockConfig)
    inventory_health: InventoryHealthConfig = field(default_factory=InventoryHealthConfig)
    supplier: SupplierConfig = field(default_factory=SupplierConfig)
    replenishment: ReplenishmentConfig = field(default_factory=ReplenishmentConfig)
    working_capital: WorkingCapitalConfig = field(default_factory=WorkingCapitalConfig)
    dirty_data: DirtyDataConfig = field(default_factory=DirtyDataConfig)
    data_quality: DataQualityConfig = field(default_factory=DataQualityConfig)
    sql_screening: SqlScreeningConfig = field(default_factory=SqlScreeningConfig)


def get_config() -> Config:
    """Return the default project configuration."""
    return Config()


def get_database_url(database: str | None = None) -> str:
    """Build the PostgreSQL connection URL from environment variables.

    Credentials are never hard-coded; copy ``.env.example`` to ``.env``.
    Variables already set in the environment take precedence over ``.env``.

    Args:
        database: Override the database name (tests use a separate database).
    """
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    url = URL.create(
        "postgresql+psycopg",
        username=os.getenv("POSTGRES_USER", "ict"),
        password=os.getenv("POSTGRES_PASSWORD") or None,
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        database=database or os.getenv("POSTGRES_DB", "inventory_control_tower"),
    )
    return url.render_as_string(hide_password=False)
