"""Synthetic data generation for the Inventory Control Tower.

Creates a reproducible, internally consistent dataset for a fictional
wholesale distributor. Nothing here classifies, forecasts or scores SKUs —
the generator only creates the *raw operational history* that the
analytical phases later have to interpret.

How realism is achieved
-----------------------
* **Economics first, classes later.** Unit cost is log-normal per category
  and demand volume is negatively (but loosely) related to cost. Any Pareto
  / ABC structure emerges from these distributions; it is never assigned.
* **Demand patterns with overlap.** Each SKU gets an intended pattern
  (stable, seasonal, trending, declining, intermittent, highly variable,
  new, end-of-life), but parameters are drawn from wide, overlapping ranges
  and every SKU gets noise, mild seasonality and occasional large orders.
* **Operations are simulated, not painted.** A month-by-month simulation
  applies a realistic *legacy* buyer policy (reorder on a 6-month average,
  heterogeneous cover, MOQ rounding, occasional missed reviews) against
  supplier behaviour (lead-time noise, late and partial deliveries).
  Stockouts, excess and dead stock *result* from that interaction.
* **Stock flow is exact.** closing on hand = opening + receipts - shipments
  for every SKU-month; shipments never exceed demand or available stock.

The intended pattern and supplier archetype are written to a separate
"synthetic truth" folder for validation only — never into the raw tables.
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.config import DAYS_PER_MONTH, DataGenerationConfig, DirtyDataConfig, PathConfig
from src.dirty_data import inject_realistic_defects
from src.raw_data import save_tables

logger = logging.getLogger(__name__)

# ==========================================================================
# Simulation parameters
# --------------------------------------------------------------------------
# These describe the fictional *world*, not business policy, so they live
# here rather than in config.py. Each is documented in docs/assumptions.md.
# ==========================================================================


@dataclass(frozen=True)
class CategoryProfile:
    """Economic profile of a product category."""

    name: str
    sku_share: float
    median_unit_cost: float
    cost_log_sigma: float
    # Typical monthly units for a SKU priced at the category median cost.
    median_monthly_demand: float
    gross_margin_mean: float
    # Multiplier on the chance a SKU in this category is seasonal.
    seasonal_propensity: float
    peak_month: int


CATEGORIES: tuple[CategoryProfile, ...] = (
    CategoryProfile("Fasteners & Fixings", 0.20, 0.90, 1.0, 220.0, 0.45, 0.6, 5),
    CategoryProfile("Electrical", 0.15, 12.0, 1.1, 40.0, 0.35, 0.5, 10),
    CategoryProfile("Safety & PPE", 0.13, 9.0, 0.9, 60.0, 0.40, 1.6, 11),
    CategoryProfile("Hand Tools", 0.12, 18.0, 0.9, 18.0, 0.38, 0.6, 11),
    CategoryProfile("Power Tools & Accessories", 0.08, 65.0, 1.0, 7.0, 0.30, 0.8, 5),
    CategoryProfile("HVAC & Climate", 0.10, 45.0, 1.1, 10.0, 0.32, 3.0, 7),
    CategoryProfile("Plumbing", 0.10, 14.0, 1.0, 30.0, 0.36, 0.8, 1),
    CategoryProfile("Janitorial & Facility", 0.12, 7.0, 0.8, 70.0, 0.33, 0.7, 3),
)


@dataclass(frozen=True)
class SupplierArchetype:
    """Behavioural profile of a supplier type (means; suppliers vary around them)."""

    name: str
    count_share: float
    region: str | None  # None = mixed domestic / import
    quoted_lead_time_days: tuple[int, int]
    on_time_prob_mean: float
    delay_mean_days: float
    partial_prob_mean: float
    # Share of partial deliveries whose remainder is never shipped.
    short_close_prob: float
    early_days_max: int
    # Poisson mean of extra order-multiples in the MOQ.
    moq_lot_factor: float
    price_variance_sd: float


SUPPLIER_ARCHETYPES: tuple[SupplierArchetype, ...] = (
    SupplierArchetype(
        "DOMESTIC_RELIABLE", 0.36, "Domestic", (5, 21), 0.93, 4, 0.04, 0.20, 3, 0.5, 0.015
    ),
    SupplierArchetype(
        "DOMESTIC_STANDARD", 0.28, "Domestic", (10, 35), 0.85, 7, 0.09, 0.25, 4, 1.0, 0.025
    ),
    SupplierArchetype(
        "IMPORT_LONG_LEAD", 0.20, "Import", (40, 90), 0.80, 12, 0.10, 0.30, 7, 3.0, 0.040
    ),
    SupplierArchetype("UNRELIABLE", 0.16, None, (10, 60), 0.62, 15, 0.22, 0.35, 5, 1.5, 0.050),
)
# Beta concentration for supplier on-time probability: low enough that
# archetypes overlap (a "reliable" supplier can have a bad year).
ON_TIME_BETA_CONCENTRATION = 12.0
N_DEGRADING_SUPPLIERS = 3
DEGRADATION_ON_TIME_DROP = 0.12

DEMAND_PATTERN_WEIGHTS: dict[str, float] = {
    "STABLE": 0.30,
    "SEASONAL": 0.14,
    "TRENDING_UP": 0.10,
    "DECLINING": 0.10,
    "INTERMITTENT": 0.16,
    "HIGHLY_VARIABLE": 0.10,
    "NEW": 0.06,
    "END_OF_LIFE": 0.04,
}
DEMAND_PATTERNS: tuple[str, ...] = tuple(DEMAND_PATTERN_WEIGHTS)

# Demand elasticity to unit cost: cheaper items sell more, loosely.
COST_DEMAND_ELASTICITY = -0.45
# Volume dispersion across SKUs: sets how concentrated value is (80% of
# value in roughly a quarter of SKUs, typical for a distributor).
DEMAND_LOG_NOISE = 1.3
# One-off large customer orders.
SPIKE_PROB = 0.01
# B2B working-day effect: December and August are quieter.
WORKING_DAY_FACTOR = {12: 0.90, 8: 0.95}
# Rare stray orders after an item is discontinued (returns, old quotes).
STRAY_ORDER_PROB = 0.03


@dataclass(frozen=True)
class LegacyBuyerPolicy:
    """The *current* (imperfect) replenishment behaviour being simulated.

    This is deliberately not the policy the project recommends: it is a
    rule of thumb that creates the problems the analysis must find.
    """

    # Trailing average window (a common ERP default); reacts slowly to
    # trends and seasons and over-reacts to single lumpy orders.
    history_months: int = 6
    # Buyer covers lead time + review month + a flat "comfort" buffer that
    # ignores demand variability and supplier reliability (the flaw).
    safety_cover_months: tuple[float, float] = (0.1, 0.8)
    cover_median_months: float = 1.5
    cover_log_sigma: float = 0.5
    overbuyer_share: float = 0.08
    overbuyer_multiplier: float = 2.5
    review_skip_prob: float = 0.04
    initial_cover_median_months: float = 2.5
    heavy_initial_share: float = 0.08
    heavy_initial_cover_months: tuple[float, float] = (6.0, 14.0)
    new_product_forecast_log_sigma: float = 0.4
    discontinuation_known_prob: float = 0.5
    min_line_value: float = 50.0


BUYER = LegacyBuyerPolicy()
SCHEDULE_EXTRA_MONTHS = 8  # receipts beyond the horizon still count in IP


# ==========================================================================
# Calendar
# ==========================================================================
@dataclass(frozen=True)
class Calendar:
    """Monthly calendar covering burn-in + history."""

    month_starts: np.ndarray  # datetime64[D], length n_months (+ extra for scheduling)
    days_in_month: np.ndarray
    calendar_month: np.ndarray  # 1..12
    burn_in: int
    n_months: int  # n_months = burn_in + history

    @property
    def as_of(self) -> np.datetime64:
        return self.month_starts[self.n_months] - np.timedelta64(1, "D")

    @property
    def window_start(self) -> np.datetime64:
        return self.month_starts[self.burn_in]

    def month_index(self, days: np.ndarray) -> np.ndarray:
        """Index of the month containing each date (may exceed n_months)."""
        return np.searchsorted(self.month_starts, days, side="right") - 1


def build_calendar(config: DataGenerationConfig) -> Calendar:
    """Build the month grid: burn-in + history + scheduling tail."""
    total = config.burn_in_months + config.n_history_months
    end = pd.Period(config.history_end_month, freq="M")
    periods = pd.period_range(
        end=end + SCHEDULE_EXTRA_MONTHS + 1, periods=total + SCHEDULE_EXTRA_MONTHS + 1, freq="M"
    )
    starts = periods.start_time.values.astype("datetime64[D]")
    return Calendar(
        month_starts=starts,
        days_in_month=np.asarray(periods.days_in_month),
        calendar_month=np.asarray(periods.month),
        burn_in=config.burn_in_months,
        n_months=total,
    )


# ==========================================================================
# Suppliers
# ==========================================================================
def _allocate_counts(shares: list[float], total: int) -> np.ndarray:
    """Largest-remainder allocation so every type with share > 0 is present."""
    raw = np.asarray(shares) / sum(shares) * total
    counts = np.floor(raw).astype(int)
    counts[np.argsort(raw - counts)[::-1][: total - counts.sum()]] += 1
    if total >= len(shares):
        counts = np.maximum(counts, 1)
        while counts.sum() > total:
            counts[np.argmax(counts)] -= 1
    return counts


def generate_suppliers(rng: np.random.Generator, config: DataGenerationConfig) -> pd.DataFrame:
    """Create suppliers with archetype-driven but overlapping behaviour.

    Returns one row per supplier including hidden behavioural parameters
    (prefixed ``_``) that are split off into the truth table later.
    """
    n = config.n_suppliers
    counts = _allocate_counts([a.count_share for a in SUPPLIER_ARCHETYPES], n)
    arch_idx = rng.permutation(np.repeat(np.arange(len(SUPPLIER_ARCHETYPES)), counts))
    arch = [SUPPLIER_ARCHETYPES[i] for i in arch_idx]

    region = np.array([a.region if a.region else rng.choice(["Domestic", "Import"]) for a in arch])
    lo = np.array([a.quoted_lead_time_days[0] for a in arch])
    hi = np.array([a.quoted_lead_time_days[1] for a in arch])
    quoted = rng.integers(lo, hi + 1)
    # A mixed-region unreliable supplier sourcing from overseas quotes longer.
    quoted = np.where((region == "Import") & (lo < 40), quoted + 30, quoted)

    k = ON_TIME_BETA_CONCENTRATION
    on_time_mean = np.array([a.on_time_prob_mean for a in arch])
    on_time = rng.beta(on_time_mean * k, (1 - on_time_mean) * k)
    delay_mean = rng.gamma(4.0, np.array([a.delay_mean_days for a in arch]) / 4.0)
    partial_mean = np.array([a.partial_prob_mean for a in arch])
    partial = rng.beta(partial_mean * 20, (1 - partial_mean) * 20)
    price_bias = rng.normal(0.01, 0.02, n)

    degrading = np.zeros(n, dtype=bool)
    degrading[rng.choice(n, size=min(N_DEGRADING_SUPPLIERS, n), replace=False)] = True

    return pd.DataFrame(
        {
            "supplier_id": [f"SUP-{i + 1:03d}" for i in range(n)],
            "supplier_name": [f"Supplier {i + 1:03d}" for i in range(n)],
            "region": region,
            "quoted_lead_time_days": quoted.astype(int),
            "_archetype": [a.name for a in arch],
            "_on_time_prob": on_time,
            "_delay_mean_days": delay_mean,
            "_partial_prob": partial,
            "_short_close_prob": [a.short_close_prob for a in arch],
            "_early_days_max": [a.early_days_max for a in arch],
            "_moq_lot_factor": [a.moq_lot_factor for a in arch],
            "_price_bias": price_bias,
            "_price_sd": [a.price_variance_sd for a in arch],
            "_degrades_year2": degrading,
            "_size_weight": rng.lognormal(0.0, 0.9, n),
        }
    )


def _assign_supplier_categories(rng: np.random.Generator, n_suppliers: int) -> list[set[int]]:
    """Each supplier serves 1-3 categories; every category gets >= 2 suppliers."""
    n_cat = len(CATEGORIES)
    served = []
    for _ in range(n_suppliers):
        n_served = 1 + int(rng.random() < 0.5) + int(rng.random() < 0.2)
        served.append(set(rng.choice(n_cat, size=n_served, replace=False).tolist()))
    for cat in range(n_cat):
        while sum(cat in s for s in served) < min(2, n_suppliers):
            served[int(rng.integers(n_suppliers))].add(cat)
    return served


# ==========================================================================
# Products
# ==========================================================================
def _draw_patterns(
    rng: np.random.Generator, base_demand: np.ndarray, seasonal_propensity: np.ndarray
) -> np.ndarray:
    """Draw an intended demand pattern per SKU.

    Seasonality is likelier in seasonal categories and intermittency likelier
    for low-volume items, but neither is deterministic.
    """
    weights = np.tile(np.array(list(DEMAND_PATTERN_WEIGHTS.values())), (len(base_demand), 1))
    weights[:, DEMAND_PATTERNS.index("SEASONAL")] *= seasonal_propensity
    weights[:, DEMAND_PATTERNS.index("INTERMITTENT")] *= np.where(base_demand < 3, 2.5, 0.6)
    weights /= weights.sum(axis=1, keepdims=True)
    draws = rng.random(len(base_demand))[:, None]
    return np.asarray(DEMAND_PATTERNS)[(draws > weights.cumsum(axis=1)).sum(axis=1)]


def _pack_size(rng: np.random.Generator, unit_cost: np.ndarray) -> np.ndarray:
    """Order multiple (case pack) — cheap items come in bigger packs."""
    options = [
        (1.0, [50, 100, 250]),
        (5.0, [10, 20, 25, 50]),
        (25.0, [5, 6, 10, 12]),
        (100.0, [1, 2, 4, 5]),
        (np.inf, [1]),
    ]
    packs = np.empty(len(unit_cost), dtype=int)
    lower = 0.0
    for upper, choices in options:
        mask = (unit_cost >= lower) & (unit_cost < upper)
        packs[mask] = rng.choice(choices, size=mask.sum())
        lower = upper
    return packs


def generate_products(
    rng: np.random.Generator,
    config: DataGenerationConfig,
    calendar: Calendar,
    suppliers: pd.DataFrame,
) -> pd.DataFrame:
    """Create the item master plus hidden demand-pattern parameters (``_`` columns)."""
    n = config.n_skus
    cat_idx = rng.choice(len(CATEGORIES), size=n, p=[c.sku_share for c in CATEGORIES])
    cat = [CATEGORIES[i] for i in cat_idx]

    # Supplier: weighted by supplier size among those serving the category.
    served = _assign_supplier_categories(rng, len(suppliers))
    size = suppliers["_size_weight"].to_numpy()
    sup_idx = np.empty(n, dtype=int)
    for c in range(len(CATEGORIES)):
        rows = np.flatnonzero(cat_idx == c)
        eligible = np.array([i for i, s in enumerate(served) if c in s])
        p = size[eligible] / size[eligible].sum()
        sup_idx[rows] = rng.choice(eligible, size=len(rows), p=p)

    median_cost = np.array([c.median_unit_cost for c in cat])
    cost = np.exp(np.log(median_cost) + rng.normal(0, 1, n) * [c.cost_log_sigma for c in cat])
    cost = np.round(np.maximum(cost, 0.05), 2)
    margin = np.clip(rng.normal([c.gross_margin_mean for c in cat], 0.07), 0.10, 0.65)
    price = np.round(np.maximum(cost / (1 - margin), cost + 0.01), 2)

    base = (
        np.array([c.median_monthly_demand for c in cat])
        * (cost / median_cost) ** COST_DEMAND_ELASTICITY
        * rng.lognormal(0.0, DEMAND_LOG_NOISE, n)
    )
    base = np.maximum(base, 0.2)
    pattern = _draw_patterns(rng, base, np.array([c.seasonal_propensity for c in cat]))

    # Lifecycle timing.
    n_months, burn_in = calendar.n_months, calendar.burn_in
    history_start = calendar.month_starts[0]
    old_launch = np.datetime64("2015-01-01") + rng.integers(
        0, int((history_start - np.datetime64("2015-01-01")).astype(int)), n
    ).astype("timedelta64[D]")
    new_launch_idx = rng.integers(n_months - 10, n_months, n)
    new_launch = calendar.month_starts[new_launch_idx] + rng.integers(0, 28, n).astype(
        "timedelta64[D]"
    )
    is_new = pattern == "NEW"
    launch_date = np.where(is_new, new_launch, old_launch)
    launch_idx = np.where(is_new, new_launch_idx, 0)
    eol_stop_idx = np.where(
        pattern == "END_OF_LIFE", burn_in + rng.integers(4, 21, n), n_months + 99
    )

    # Supplier-driven pack and MOQ.
    moq_factor = suppliers["_moq_lot_factor"].to_numpy()[sup_idx]
    order_multiple = _pack_size(rng, cost)
    moq = order_multiple * (1 + rng.poisson(moq_factor))
    min_value_moq = np.ceil(BUYER.min_line_value / cost / order_multiple) * order_multiple
    moq = np.maximum(moq, min_value_moq).astype(int)

    quoted = suppliers["quoted_lead_time_days"].to_numpy()[sup_idx] + rng.integers(0, 8, n)

    as_of = calendar.as_of
    discontinued_known = (pattern == "END_OF_LIFE") & (
        rng.random(n) < BUYER.discontinuation_known_prob
    )
    status = np.where(
        is_new & (launch_date > as_of - np.timedelta64(182, "D")),
        "NEW",
        np.where(discontinued_known & (eol_stop_idx < n_months), "DISCONTINUED", "ACTIVE"),
    )

    cover = rng.lognormal(np.log(BUYER.cover_median_months), BUYER.cover_log_sigma, n)
    overbuyer = rng.random(n) < BUYER.overbuyer_share
    cover = np.where(overbuyer, cover * BUYER.overbuyer_multiplier, cover)

    peak = (
        np.array([c.peak_month for c in cat]) - 1 + np.round(rng.normal(0, 1, n)).astype(int)
    ) % 12 + 1

    return pd.DataFrame(
        {
            "sku_id": [f"SKU-{i + 1:05d}" for i in range(n)],
            "category": [c.name for c in cat],
            "supplier_id": suppliers["supplier_id"].to_numpy()[sup_idx],
            "unit_cost": cost,
            "unit_price": price,
            "moq": moq,
            "order_multiple": order_multiple,
            "supplier_lead_time_days": quoted.astype(int),
            "launch_date": pd.to_datetime(launch_date),
            "lifecycle_status": status,
            "_supplier_idx": sup_idx,
            "_pattern": pattern,
            "_base_demand": base,
            "_launch_idx": launch_idx,
            "_eol_stop_idx": eol_stop_idx,
            "_discontinued_known": discontinued_known,
            "_peak_month": peak,
            "_buyer_cover_months": cover,
            "_overbuyer": overbuyer,
        }
    )


# ==========================================================================
# Demand
# ==========================================================================
def _pattern_parameters(rng: np.random.Generator, pattern: np.ndarray) -> dict[str, np.ndarray]:
    """Wide, overlapping parameter ranges per pattern."""
    n = len(pattern)

    def pick(mapping: dict[str, tuple[float, float]], default: tuple[float, float]) -> np.ndarray:
        lo = np.array([mapping.get(p, default)[0] for p in pattern])
        hi = np.array([mapping.get(p, default)[1] for p in pattern])
        return rng.uniform(lo, hi, n)

    return {
        # Every SKU has some seasonality; seasonal SKUs have much more.
        "seasonal_amplitude": pick({"SEASONAL": (0.25, 0.80)}, (0.0, 0.12)),
        "monthly_trend": pick(
            {"TRENDING_UP": (0.012, 0.05), "DECLINING": (-0.07, -0.02)}, (-0.004, 0.004)
        ),
        "noise_cv": pick({"HIGHLY_VARIABLE": (0.8, 1.5), "DECLINING": (0.2, 0.45)}, (0.15, 0.45)),
        "occurrence_prob": pick({"INTERMITTENT": (0.15, 0.6)}, (1.0, 1.0)),
        "ramp_months": rng.uniform(1.0, 3.0, n),
    }


def build_expected_demand(
    products: pd.DataFrame, params: dict[str, np.ndarray], calendar: Calendar
) -> np.ndarray:
    """Expected monthly demand (n_skus x n_months) before random noise."""
    n_months, burn_in = calendar.n_months, calendar.burn_in
    t = np.arange(n_months)[None, :]
    w = t - burn_in  # months since history start (negative in burn-in)
    base = products["_base_demand"].to_numpy()[:, None]
    pattern = products["_pattern"].to_numpy()[:, None]

    level = base * np.exp(params["monthly_trend"][:, None] * w)
    # Declining items decay from their burn-in level instead of exploding backwards.
    level = np.where(
        pattern == "DECLINING", base * np.exp(params["monthly_trend"][:, None] * t), level
    )

    launch = products["_launch_idx"].to_numpy()[:, None]
    age = t - launch + 1
    ramp = 1 - np.exp(-np.maximum(age, 0) / params["ramp_months"][:, None])
    level = np.where(pattern == "NEW", base * ramp, level)
    level = np.where(t < launch, 0.0, level)

    stop = products["_eol_stop_idx"].to_numpy()[:, None]
    run_down = np.clip((stop - t) / 5.0, 0.3, 1.0)  # fades over the last 5 months
    level = np.where(pattern == "END_OF_LIFE", base * run_down, level)
    level = np.where(t >= stop, 0.0, level)

    months = calendar.calendar_month[:n_months][None, :]
    peak = products["_peak_month"].to_numpy()[:, None]
    season = 1 + params["seasonal_amplitude"][:, None] * np.cos(2 * np.pi * (months - peak) / 12)
    working_days = np.vectorize(lambda m: WORKING_DAY_FACTOR.get(int(m), 1.0))(months)
    return np.maximum(level * season * working_days, 0.0)


def sample_demand(
    rng: np.random.Generator,
    expected: np.ndarray,
    products: pd.DataFrame,
    params: dict[str, np.ndarray],
) -> np.ndarray:
    """Draw integer customer order quantities around the expected demand.

    Gamma-Poisson (negative binomial) noise gives realistic over-dispersion;
    intermittent SKUs draw an occurrence then a size.
    """
    cv = params["noise_cv"][:, None]
    shape = 1.0 / cv**2
    lam = rng.gamma(np.broadcast_to(shape, expected.shape), expected * cv**2)
    orders = rng.poisson(lam)

    p = params["occurrence_prob"][:, None]
    intermittent = (products["_pattern"].to_numpy() == "INTERMITTENT")[:, None]
    occurs = rng.random(expected.shape) < p
    size = 1 + rng.poisson(rng.gamma(3.0, expected / (p * 3.0)))
    orders = np.where(intermittent, np.where(occurs & (expected > 0), size, 0), orders)

    spike_prob = np.where(products["_pattern"].to_numpy() == "HIGHLY_VARIABLE", 0.06, SPIKE_PROB)
    spikes = (rng.random(expected.shape) < spike_prob[:, None]) & (expected > 0)
    orders = np.where(
        spikes, orders + np.round(expected * rng.uniform(2, 5, expected.shape)), orders
    )

    stop = products["_eol_stop_idx"].to_numpy()[:, None]
    t = np.arange(expected.shape[1])[None, :]
    stray = (t >= stop) & (rng.random(expected.shape) < STRAY_ORDER_PROB)
    orders = np.where(stray, rng.integers(1, 3, expected.shape), orders)

    launch = products["_launch_idx"].to_numpy()[:, None]
    orders = np.where(t < launch, 0, orders)
    return orders.astype(np.int64)


# ==========================================================================
# Operations simulation (buyer + supplier + warehouse)
# ==========================================================================
@dataclass
class _Schedule:
    """Future receipts per SKU and month (quantity and quantity x day-fraction)."""

    qty: np.ndarray
    qty_x_dayfrac: np.ndarray

    def add(self, sku: np.ndarray, month: np.ndarray, qty: np.ndarray, dayfrac: np.ndarray) -> None:
        keep = month < self.qty.shape[1]
        np.add.at(self.qty, (sku[keep], month[keep]), qty[keep])
        np.add.at(self.qty_x_dayfrac, (sku[keep], month[keep]), qty[keep] * dayfrac[keep])


def _perceived_demand(
    rng: np.random.Generator,
    t: int,
    orders: np.ndarray,
    expected: np.ndarray,
    products: pd.DataFrame,
) -> np.ndarray:
    """What the buyer *thinks* monthly demand is: trailing 6-month average.

    Before a SKU has 6 months of history the buyer relies on a planned
    figure with realistic error (new products) or on long-run knowledge.
    """
    h = BUYER.history_months
    launch = products["_launch_idx"].to_numpy()
    months_known = t - np.maximum(launch, 0)
    trailing = orders[:, max(t - h, 0) : t].mean(axis=1) if t > 0 else np.zeros(len(launch))
    noise = rng.lognormal(0.0, BUYER.new_product_forecast_log_sigma, len(launch))
    planned = products["_base_demand"].to_numpy() * noise
    fallback = np.where(products["_pattern"].to_numpy() == "NEW", planned, expected[:, t] * noise)
    return np.where(months_known >= h, trailing, fallback)


def _place_orders(
    rng: np.random.Generator,
    t: int,
    sku: np.ndarray,
    qty: np.ndarray,
    products: pd.DataFrame,
    suppliers: pd.DataFrame,
    calendar: Calendar,
) -> dict[str, np.ndarray]:
    """Create PO lines and their (future) receipt events for month ``t``."""
    n = len(sku)
    sup = products["_supplier_idx"].to_numpy()[sku]
    order_date = calendar.month_starts[t] + rng.integers(0, 5, n).astype("timedelta64[D]")
    quoted = products["supplier_lead_time_days"].to_numpy()[sku]
    promised = order_date + quoted.astype("timedelta64[D]")

    in_year_two = t - calendar.burn_in >= 12
    p_on_time = suppliers["_on_time_prob"].to_numpy()[sup] - np.where(
        suppliers["_degrades_year2"].to_numpy()[sup] & in_year_two, DEGRADATION_ON_TIME_DROP, 0.0
    )
    on_time = rng.random(n) < p_on_time
    early = rng.integers(0, suppliers["_early_days_max"].to_numpy()[sup] + 1)
    delay = 1 + np.round(rng.gamma(2.0, suppliers["_delay_mean_days"].to_numpy()[sup] / 2.0))
    offset = np.where(on_time, -early, delay).astype(int)
    first_receipt = np.maximum(promised + offset.astype("timedelta64[D]"), order_date + 1)

    partial = (rng.random(n) < suppliers["_partial_prob"].to_numpy()[sup]) & (qty > 1)
    first_qty = np.where(
        partial, np.clip(np.floor(qty * rng.uniform(0.4, 0.9, n)), 1, qty - 1), qty
    ).astype(np.int64)
    second_qty = qty - first_qty
    short_closed = partial & (rng.random(n) < suppliers["_short_close_prob"].to_numpy()[sup])
    second_receipt = first_receipt + rng.integers(7, 36, n).astype("timedelta64[D]")

    unit_price = products["unit_cost"].to_numpy()[sku] * (
        1
        + rng.normal(
            suppliers["_price_bias"].to_numpy()[sup], suppliers["_price_sd"].to_numpy()[sup]
        )
    )
    return {
        "sku": sku,
        "supplier": sup,
        "order_date": order_date,
        "promised_date": promised,
        "ordered_qty": qty.astype(np.int64),
        "unit_price": np.round(np.maximum(unit_price, 0.01), 2),
        "first_receipt": first_receipt,
        "first_qty": first_qty,
        "second_receipt": second_receipt,
        "second_qty": np.where(short_closed, 0, second_qty),
        "short_closed": short_closed,
        "remainder_qty": second_qty,
    }


def _schedule_receipts(schedule: _Schedule, po: dict[str, np.ndarray], calendar: Calendar) -> None:
    """Add a batch of PO receipt events to the receipt schedule."""
    for date_key, qty_key in (("first_receipt", "first_qty"), ("second_receipt", "second_qty")):
        dates = po[date_key]
        month = calendar.month_index(dates)
        dayfrac = (dates - calendar.month_starts[month]).astype(int) / calendar.days_in_month[month]
        mask = po[qty_key] > 0
        schedule.add(po["sku"][mask], month[mask], po[qty_key][mask], dayfrac[mask])


def simulate_operations(
    rng: np.random.Generator,
    orders: np.ndarray,
    expected: np.ndarray,
    products: pd.DataFrame,
    suppliers: pd.DataFrame,
    calendar: Calendar,
) -> dict[str, np.ndarray | list]:
    """Month-by-month stock flow: review -> receipts -> shipments.

    Within a month, demand is assumed to arrive evenly. Demand arriving
    before the (quantity-weighted) receipt day can only be served from
    opening stock; unserved demand is lost (lost-sales model).
    """
    n, n_months = orders.shape
    schedule = _Schedule(
        np.zeros((n, n_months + SCHEDULE_EXTRA_MONTHS), dtype=np.int64),
        np.zeros((n, n_months + SCHEDULE_EXTRA_MONTHS)),
    )
    launch = products["_launch_idx"].to_numpy()
    lead_months = products["supplier_lead_time_days"].to_numpy() / DAYS_PER_MONTH
    orderable_from = np.maximum(launch - np.ceil(lead_months).astype(int), 0)
    stop = products["_eol_stop_idx"].to_numpy()
    known_stop = products["_discontinued_known"].to_numpy()
    cover = products["_buyer_cover_months"].to_numpy()
    safety_cover = rng.uniform(*BUYER.safety_cover_months, n)
    moq = products["moq"].to_numpy()
    multiple = products["order_multiple"].to_numpy()

    heavy = rng.random(n) < BUYER.heavy_initial_share
    initial_cover = np.where(
        heavy,
        rng.uniform(*BUYER.heavy_initial_cover_months, n),
        rng.lognormal(np.log(BUYER.initial_cover_median_months), 0.5, n),
    )
    on_hand = np.where(launch > 0, 0, np.round(expected[:, 0] * initial_cover)).astype(np.int64)

    shipped = np.zeros((n, n_months), dtype=np.int64)
    closing = np.zeros((n, n_months), dtype=np.int64)
    allocated = np.zeros((n, n_months), dtype=np.int64)
    po_batches: list[dict[str, np.ndarray]] = []

    for t in range(n_months):
        # 1. Monthly review: buyer compares inventory position with a rule of thumb.
        perceived = _perceived_demand(rng, t, orders, expected, products)
        position = on_hand + schedule.qty[:, t:].sum(axis=1)
        reorder_level = perceived * (lead_months + 1 + safety_cover)
        order_up_to = perceived * (lead_months + 1 + safety_cover + cover)
        active = (t >= orderable_from) & ~(known_stop & (t >= stop))
        due = active & (perceived > 0) & (position < reorder_level)
        due &= rng.random(n) > BUYER.review_skip_prob
        if due.any():
            sku = np.flatnonzero(due)
            need = np.maximum(order_up_to[sku] - position[sku], moq[sku])
            qty = (np.ceil(need / multiple[sku]) * multiple[sku]).astype(np.int64)
            batch = _place_orders(rng, t, sku, qty, products, suppliers, calendar)
            _schedule_receipts(schedule, batch, calendar)
            po_batches.append(batch)

        # 2. Receipts and 3. shipments (demand before receipt day sees opening stock only).
        receipts = schedule.qty[:, t]
        dayfrac = np.divide(
            schedule.qty_x_dayfrac[:, t], receipts, out=np.ones(n), where=receipts > 0
        )
        demand = orders[:, t]
        demand_before = np.round(demand * dayfrac).astype(np.int64)
        shipped_before = np.minimum(demand_before, on_hand)
        shipped_after = np.minimum(demand - demand_before, on_hand - shipped_before + receipts)
        shipped[:, t] = shipped_before + shipped_after
        on_hand = on_hand + receipts - shipped[:, t]
        closing[:, t] = on_hand

        # Month-end allocations: orders taken but not yet picked.
        allocated[:, t] = np.minimum(on_hand, np.round(demand * rng.uniform(0.02, 0.12, n)))

    return {
        "shipped": shipped,
        "closing": closing,
        "allocated": allocated,
        "po_batches": po_batches,
        "receipts": schedule.qty[:, :n_months],
    }


# ==========================================================================
# Table assembly
# ==========================================================================
def _history_mask(products: pd.DataFrame, calendar: Calendar) -> np.ndarray:
    """SKU-months that appear in the history tables (in window and launched)."""
    t = np.arange(calendar.n_months)[None, :]
    return (t >= calendar.burn_in) & (t >= products["_launch_idx"].to_numpy()[:, None])


def _panel(
    products: pd.DataFrame,
    calendar: Calendar,
    date_col: str,
    dates: np.ndarray,
    **values: np.ndarray,
) -> pd.DataFrame:
    """Long SKU x month table restricted to the history mask."""
    mask = _history_mask(products, calendar)
    sku_i, t_i = np.nonzero(mask)
    data = {"sku_id": products["sku_id"].to_numpy()[sku_i], date_col: pd.to_datetime(dates[t_i])}
    data.update({name: arr[sku_i, t_i] for name, arr in values.items()})
    return pd.DataFrame(data)


def build_purchase_tables(
    po_batches: list[dict[str, np.ndarray]],
    products: pd.DataFrame,
    suppliers: pd.DataFrame,
    calendar: Calendar,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """PO lines (status as of the as-of date) and receipt events (<= as-of).

    Mirrors a 24-month ERP extract: lines ordered in the history window, plus
    earlier lines still in transit at its start (they deliver stock inside
    the window, so the stock balance needs them). Burn-in lines fully
    received before the window are dropped.
    """
    po = {k: np.concatenate([b[k] for b in po_batches]) for k in po_batches[0]}
    as_of = calendar.as_of
    start = calendar.window_start
    in_window = (
        (po["order_date"] >= start)
        | (po["first_receipt"] >= start)
        | ((po["second_receipt"] >= start) & (po["second_qty"] > 0))
    )
    po = {k: v[in_window] for k, v in po.items()}

    frame = pd.DataFrame(
        {
            "supplier_id": suppliers["supplier_id"].to_numpy()[po["supplier"]],
            "order_date": pd.to_datetime(po["order_date"]),
            "sku_id": products["sku_id"].to_numpy()[po["sku"]],
        }
    )
    order = np.lexsort((frame["sku_id"], frame["supplier_id"], frame["order_date"]))
    po = {k: v[order] for k, v in po.items()}
    frame = frame.iloc[order].reset_index(drop=True)
    # One PO per supplier per order date; one line per SKU.
    po_number = frame.groupby(["order_date", "supplier_id"], sort=False).ngroup() + 1
    line_no = frame.groupby(po_number).cumcount() + 1
    po_line_id = [f"PO-{p:06d}-{line:02d}" for p, line in zip(po_number, line_no, strict=True)]

    got_first = po["first_receipt"] <= as_of
    got_second = (po["second_receipt"] <= as_of) & (po["second_qty"] > 0)
    received = po["first_qty"] * got_first + po["second_qty"] * got_second
    # A short-closed remainder is only written off once the buyer gives up (30 days).
    written_off = po["short_closed"] & (po["first_receipt"] <= as_of - np.timedelta64(30, "D"))
    status = np.select(
        [received >= po["ordered_qty"], written_off, received > 0],
        ["CLOSED", "CLOSED_SHORT", "PARTIALLY_RECEIVED"],
        default="OPEN",
    )
    purchase_orders = pd.DataFrame(
        {
            "po_line_id": po_line_id,
            "po_number": [f"PO-{p:06d}" for p in po_number],
            "sku_id": frame["sku_id"],
            "supplier_id": frame["supplier_id"],
            "order_date": frame["order_date"],
            "promised_date": pd.to_datetime(po["promised_date"]),
            "ordered_qty": po["ordered_qty"],
            "unit_price": po["unit_price"],
            "status": status,
        }
    )

    receipts = []
    for date_key, qty_key, arrived in (
        ("first_receipt", "first_qty", got_first),
        ("second_receipt", "second_qty", got_second),
    ):
        mask = arrived & (po[qty_key] > 0)
        receipts.append(
            pd.DataFrame(
                {
                    "po_line_id": np.asarray(po_line_id)[mask],
                    "receipt_date": pd.to_datetime(po[date_key][mask]),
                    "received_qty": po[qty_key][mask],
                }
            )
        )
    deliveries = (
        pd.concat(receipts)
        .sort_values(["receipt_date", "po_line_id"], kind="stable")
        .reset_index(drop=True)
    )
    deliveries.insert(0, "receipt_id", [f"RCV-{i + 1:07d}" for i in range(len(deliveries))])
    return purchase_orders, deliveries


@dataclass
class SyntheticDataset:
    """Analytical tables plus the separate validation-only answer key."""

    suppliers: pd.DataFrame
    products: pd.DataFrame
    demand: pd.DataFrame
    inventory: pd.DataFrame
    purchase_orders: pd.DataFrame
    deliveries: pd.DataFrame
    sku_truth: pd.DataFrame
    supplier_truth: pd.DataFrame
    as_of: pd.Timestamp

    def raw_tables(self) -> dict[str, pd.DataFrame]:
        """Tables an analyst would receive from the ERP (no hidden truth)."""
        return {
            "suppliers": self.suppliers,
            "products": self.products,
            "demand_monthly": self.demand,
            "inventory_snapshot": self.inventory,
            "purchase_orders": self.purchase_orders,
            "supplier_deliveries": self.deliveries,
        }

    def truth_tables(self) -> dict[str, pd.DataFrame]:
        """Generator answer key — validation only, never an analytical input."""
        return {
            "sku_generation_truth": self.sku_truth,
            "supplier_generation_truth": self.supplier_truth,
        }

    def save(self, paths: PathConfig) -> None:
        """Write raw tables and truth tables to their separate folders."""
        save_tables(self.raw_tables(), paths.raw_data_dir)
        save_tables(self.truth_tables(), paths.synthetic_truth_dir)
        logger.info(
            "Saved raw tables to %s and truth to %s", paths.raw_data_dir, paths.synthetic_truth_dir
        )

    def save_dirty(
        self, paths: PathConfig, seed: int, config: DirtyDataConfig | None = None
    ) -> None:
        """Write a dirty copy (documented ERP defects) to ``raw_dirty_data_dir``.

        The clean raw folder is untouched; the defect manifest is written
        with the synthetic truth so it can never be mistaken for raw data.
        """
        dirty = inject_realistic_defects(self.raw_tables(), self.as_of, seed, config)
        save_tables(dirty.tables, paths.raw_dirty_data_dir)
        save_tables({"injected_defects": dirty.manifest}, paths.synthetic_truth_dir)
        logger.info(
            "Saved dirty copy (%d injected defects) to %s",
            len(dirty.manifest),
            paths.raw_dirty_data_dir,
        )


def _split_hidden(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Separate public columns from hidden (``_``-prefixed) generator columns."""
    hidden = [c for c in frame.columns if c.startswith("_")]
    return frame.drop(columns=hidden), frame[hidden].rename(columns=lambda c: c.lstrip("_"))


def generate_dataset(config: DataGenerationConfig | None = None) -> SyntheticDataset:
    """Generate the full synthetic dataset deterministically from ``config.random_seed``."""
    config = config or DataGenerationConfig()
    rng = np.random.default_rng(config.random_seed)
    calendar = build_calendar(config)
    logger.info(
        "Generating %s SKUs / %s suppliers (seed=%s)",
        config.n_skus,
        config.n_suppliers,
        config.random_seed,
    )

    suppliers_full = generate_suppliers(rng, config)
    products_full = generate_products(rng, config, calendar, suppliers_full)
    params = _pattern_parameters(rng, products_full["_pattern"].to_numpy())
    expected = build_expected_demand(products_full, params, calendar)
    orders = sample_demand(rng, expected, products_full, params)
    sim = simulate_operations(rng, orders, expected, products_full, suppliers_full, calendar)

    month_starts = calendar.month_starts[: calendar.n_months]
    month_ends = calendar.month_starts[1 : calendar.n_months + 1] - np.timedelta64(1, "D")
    demand = _panel(
        products_full,
        calendar,
        "month_start",
        month_starts,
        ordered_qty=orders,
        shipped_qty=sim["shipped"],
    )
    inventory = _panel(
        products_full,
        calendar,
        "month_end",
        month_ends,
        on_hand_qty=sim["closing"],
        allocated_qty=sim["allocated"],
    )
    purchase_orders, deliveries = build_purchase_tables(
        sim["po_batches"], products_full, suppliers_full, calendar
    )

    suppliers, supplier_hidden = _split_hidden(suppliers_full)
    products, product_hidden = _split_hidden(products_full)
    sku_truth = pd.concat(
        [
            products[["sku_id"]],
            product_hidden[
                ["pattern", "base_demand", "peak_month", "buyer_cover_months", "overbuyer"]
            ].rename(columns={"pattern": "intended_pattern", "base_demand": "base_monthly_demand"}),
            pd.DataFrame(params)[
                ["seasonal_amplitude", "monthly_trend", "noise_cv", "occurrence_prob"]
            ],
        ],
        axis=1,
    )
    stop = product_hidden["eol_stop_idx"].to_numpy()
    sku_truth["eol_stop_month"] = pd.to_datetime(
        np.where(
            stop < calendar.n_months,
            calendar.month_starts[np.minimum(stop, calendar.n_months)],
            np.datetime64("NaT"),
        )
    )
    supplier_truth = pd.concat([suppliers[["supplier_id"]], supplier_hidden], axis=1).drop(
        columns=["size_weight"]
    )

    return SyntheticDataset(
        suppliers=suppliers,
        products=products,
        demand=demand,
        inventory=inventory,
        purchase_orders=purchase_orders,
        deliveries=deliveries,
        sku_truth=sku_truth,
        supplier_truth=supplier_truth,
        as_of=pd.Timestamp(calendar.as_of),
    )


def main() -> None:
    """CLI: ``python -m src.data_generation [--seed N] [--dirty]``."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, default=None, help="random seed (default 42)")
    parser.add_argument(
        "--dirty",
        action="store_true",
        help="also write a copy with documented ERP defects to data/raw_dirty/",
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    config = (
        DataGenerationConfig() if args.seed is None else DataGenerationConfig(random_seed=args.seed)
    )
    dataset = generate_dataset(config)
    paths = PathConfig()
    dataset.save(paths)
    if args.dirty:
        dataset.save_dirty(paths, config.random_seed)


if __name__ == "__main__":
    main()
