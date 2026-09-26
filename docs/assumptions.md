# Assumptions

Every model is a simplification. This file lists each assumption, why it is
reasonable here, and what would change in a real deployment. Defaults live in
[`src/config.py`](../src/config.py); rejected alternatives are in
[`decision_log.md`](decision_log.md).

## Business and data

| # | Assumption | Rationale | Real-world consideration |
|---|---|---|---|
| D1 | All data is **synthetic**, fixed seed (42) | No confidential data; reproducible | Real data needs cleansing and master-data governance |
| D2 | Monthly buckets | Standard tactical planning grain for distributors | Fast movers may justify weekly planning |
| D3 | Demand = customer **ordered** quantity; shipments recorded separately | Avoids forecasting on stockout-censored shipments; gives a measured fill rate | Customers who never order a known-unavailable item are still invisible (residual censoring) |
| D4 | Unfilled demand is **lost**, not backordered | Typical for MRO/industrial distribution with alternative sources | Backorder businesses subtract backorders from inventory position |
| D5 | One primary supplier per SKU | Common for distributors; clean lead-time mapping | Multi-sourcing needs allocation logic |
| D6 | Standard unit cost constant over the horizon; price variance captured on POs (PPV) | Standard-cost convention | Refresh standards periodically |
| D7 | Single distribution centre | Scope control | Multi-echelon needs network optimisation |
| D8 | Monthly planning run (review period 1 month) | Matches the stated process | Configurable; 0 = continuous review |
| D9 | Allocated quantity known at snapshot | Available in any ERP | – |

## Synthetic data (Phase 2)

How the generator (`src/data_generation.py`) builds a realistic world. These are
assumptions about the *fictional company*, not analytical choices; all are
documented so results can be interpreted honestly.

### Scope and time

| # | Assumption | Value / rule | Why |
|---|---|---|---|
| G1 | History window | Jan 2024 – Dec 2025 (24 months); as-of 2025-12-31 | Two full seasonal cycles |
| G2 | Burn-in | 12 months simulated before the window, then discarded | Without it, starting stock distorts the first months (a 20% inventory drop in early 2024 was observed with 6 months) |
| G3 | Reproducibility | NumPy `default_rng(seed)`, seed 42 by default; override with `SYNTHETIC_DATA_SEED` or `--seed` | Same seed → identical tables (tested) |
| G4 | PO extract | Lines ordered in the window, plus earlier lines still in transit at its start | Mirrors a 24-month ERP extract; keeps the stock balance exact |

### Economics (no classes are assigned)

| # | Assumption | Value / rule | Why |
|---|---|---|---|
| E1 | 8 categories with different cost levels, margins, volumes and seasonality | e.g. fasteners ≈ $0.90 median cost / high volume; power tools ≈ $65 / low volume | Real assortments are heterogeneous |
| E2 | Unit cost | Log-normal per category (σ ≈ 0.8–1.1) | Costs are right-skewed |
| E3 | Volume vs. cost | `volume ∝ cost^−0.45 × lognormal(σ = 1.3)` | Cheap items sell more, loosely. Calibrated so ~24% of SKUs hold 80% of value; ABC is **discovered** in Phase 4, never assigned |
| E4 | Selling price | cost ÷ (1 − gross margin), margin ~ N(category mean, 0.07), clipped 10–65% | Always above cost |
| E5 | Pack size / MOQ | Order multiple by cost band (cheap → big packs); MOQ = multiple × (1 + Poisson(supplier factor)), and at least $50 per line | MOQs come from supplier terms, not from demand, which is why slow movers end up with MOQs covering many months |

### Demand

| # | Assumption | Value / rule | Why |
|---|---|---|---|
| M1 | Intended patterns | Stable 30%, intermittent 16%, seasonal 14%, trending 10%, declining 10%, highly variable 10%, new 6%, end-of-life 4% (before modulation) | Typical distributor mix |
| M2 | Pattern modulation | Seasonal likelier in seasonal categories (HVAC ×3); intermittent likelier for low-volume SKUs | Patterns correlate with the business, not random |
| M3 | Overlap | Every SKU has mild seasonality (0–12%) and noise (CV 0.15–0.45); seasonal amplitude 25–80%; trend 1.2–5%/month | Real data is not cleanly separable: e.g. 30% of seasonal SKUs look "stable" on CV |
| M4 | Noise model | Gamma-Poisson (negative binomial); intermittent = occurrence (15–60%) × size | Over-dispersed counts, zeros for slow movers |
| M5 | Large one-off orders | 1% of SKU-months (6% for highly variable SKUs), 2–5× expected | Project orders |
| M6 | Working-day effect | December −10%, August −5% | B2B calendar |
| M7 | Lifecycle | New SKUs launch in the last 10 months with a 1–3-month ramp-up; end-of-life SKUs fade over 5 months and stop, with rare stray orders (3%) | Lifecycle drives dead stock and forecasting difficulty |
| M8 | Demand = customer orders; shipments = min(demand, available stock) | See D3 above | Stockouts show up as shipped < ordered |

### Suppliers

| # | Assumption | Value / rule | Why |
|---|---|---|---|
| S-1 | Archetypes | Domestic reliable (18), domestic standard (14), import long-lead (10), unreliable (8) | Mix of supply-base quality |
| S-2 | Behaviour varies within archetype | On-time probability ~ Beta (concentration 12) around 93 / 85 / 80 / 62%; delay, partial-delivery rate and price variance also drawn per supplier | Archetypes overlap: the best unreliable supplier beats the worst standard one |
| S-3 | Lead times | Quoted: 5–21 d (reliable), 10–35 d (standard), 40–90 d (import), 10–60 d (unreliable; +30 d if overseas); SKU quote = supplier quote + 0–7 d | Import vs. domestic |
| S-4 | Delivery | On time → arrives 0–7 days early; late → 1 + Gamma(2, delay/2) days late; partial → 40–90% first, remainder 7–35 days later or never (short-closed) | Realistic lateness and split deliveries |
| S-5 | Deterioration | 3 suppliers lose 12 points of on-time probability in year 2 | Gives trend analysis something real to find |
| S-6 | PO price | Standard cost × (1 + N(supplier bias ≈ 1%, supplier sd 1.5–5%)) | Purchase price variance exists |

### Operations (the "as-is" process being simulated)

| # | Assumption | Value / rule | Why |
|---|---|---|---|
| O1 | Legacy buyer rule | Monthly review in the first week; perceived demand = trailing 6-month average of orders; reorder when position < demand × (lead time + 1 + 0.1–0.8 months); order up to that + 0.5–6 months of cover; round up to MOQ and multiple | A common rule of thumb that ignores variability and supplier reliability. It creates the problems the analysis must find; it is **not** the recommended policy |
| O2 | Buyer heterogeneity | Cover varies by SKU (log-normal, median 1.5 months); 8% "over-buyers" hold 2.5× cover; 4% of reviews skipped | Different buyers, different habits |
| O3 | New products | Ordered one lead time before launch using a planned forecast with ±40% error | Launch forecasts are uncertain |
| O4 | Discontinuation | Only 50% of end-of-life SKUs are flagged DISCONTINUED in the item master; the rest keep being replenished until their average decays | Stale master data leaves dead stock |
| O5 | Opening stock | 2.5 months median cover; 8% of SKUs start with 6–14 months | Legacy over-stock |
| O6 | Intra-month timing | Demand arrives evenly; demand before the (quantity-weighted) receipt day can only use opening stock; unmet demand is lost | Stockouts occur even in months with receipts |
| O7 | Allocations | 2–12% of the month's orders are allocated but unshipped at month end (never above on hand) | Committed stock exists |
| O8 | Buyer's view of open POs | The buyer counts the expected quantity on open POs (including remainders later short-closed) | Simplification; in reality buyers learn about short shipments late |

### Resulting dataset (seed 42) — not targets, outcomes

Unit fill rate ≈ 90%; ~10% of SKU-months short; ~24% of SKUs hold 80% of value;
inventory ≈ $17M at cost; turns ≈ 3.6 (DIO ≈ 100 days); ~24% of inventory value
above 6 months of supply; ~6% of value in SKUs with no demand for 6 months;
~15% of PO lines first received after the promised date; ~5% split receipts.
These fall inside published ranges for industrial/MRO distributors and are
checked by realism tests with deliberately broad bands.

### What the synthetic data does **not** contain

- **Dirty data by default.** The default extract is clean so analysis stays
  interpretable. An optional dirty copy (below) shows how the pipeline deals
  with realistic ERP defects.
- Customer-level detail, prices that change over time, returns, multi-site stock.

## Optional dirty-data mode (Phase 3)

`python -m src.data_generation --dirty` writes a copy of the extract to
`data/raw_dirty/` with **25 documented defects**. The clean extract in
`data/raw/` is untouched and remains the default for all analysis. Defects
use an independent random stream derived from the seed, so they are
reproducible and never change the clean data. The manifest (which row, which
column, before/after, which check should catch it) is
`data/synthetic_truth/injected_defects.csv`.

| Defect | Count | Business explanation | Caught by (severity) | Side-effects also flagged |
|---|---|---|---|---|
| Duplicate receipt | 5 | Receipt posted twice (double scan, re-post after timeout); overstates stock and fill rate | `duplicate_receipt` (ERROR) | `over_receipt` (WARNING) |
| Stale open PO | 4 | Line never shipped and never cancelled; phantom supply in inventory position | `stale_open_po` (WARNING) | — |
| Unit-of-measure error | 3 | Receipt keyed in units instead of cases (× case pack) | `receipt_qty_implausible` (ERROR) | — |
| Backdated receipt | 3 | Receipt dated before the PO (wrong date / back-posting) | `receipt_before_order` (ERROR) | `impossible_actual_lead_time` (ERROR) |
| Missing optional field | 6 | Launch date / supplier region missing after hurried set-up or migration | `missing_optional_fields` (WARNING) | — |
| Invalid supplier code | 2 | Typo in supplier code (`SUP-O12`); spend cannot be attributed | `missing_supplier` (ERROR) | — |
| Wrong supplier reference | 2 | Real but wrong supplier keyed on the PO; OTIF credited to the wrong supplier | `po_supplier_mismatch` (WARNING) | — |

The dirty extract is **REJECTED** (5 failed checks), so nothing is loaded.
Warning-only data (e.g. just a missing region) is loaded as
**ACCEPTED_WITH_WARNINGS**. Tests verify that every injected defect is caught
by its named check with exactly the injected count, and that each of those
checks reports zero on the clean data.

## Database and SQL layer (Phase 3)

| # | Assumption | Why | Consequence |
|---|---|---|---|
| Q1 | "Today" = the latest month-end snapshot (2025-12-31), never `now()` | Results must be reproducible whenever queries run | Rerunning next year gives the same answers |
| Q2 | SKU-month panels are dense from launch (zero months included) | Makes `ROWS BETWEEN n PRECEDING` equal to n calendar months | Guaranteed and tested in Phase 2 |
| Q3 | Supplier spend = received quantity × PO price over the whole extract | Spend is what was actually bought | Open POs are shown separately as `open_po_value` |
| Q4 | OTIF evaluates closed lines **and** open lines past their promise | Otherwise overdue open orders would flatter late suppliers | Open lines not yet due are excluded |
| Q5 | Actual lead time only on complete lines (last receipt − order date) | Lead time is not known until the line is complete | Partial lines still open are excluded |
| Q6 | Trailing demand = last 6 months (`SqlScreeningConfig`) | Recent behaviour for screening | New SKUs use the months they have |
| Q7 | Screening ROP = `d × P + z × σ × √P`, one 95% service level for all SKUs, σ of raw monthly demand | A transparent first-pass screen before Phase 7 | Overstates σ for seasonal items; flags ~49% of active SKUs; Phase 7 replaces it |
| Q8 | Screening excess = stock above 6 months of trailing demand | Simple, explainable screen | Phase 7 measures excess against the policy maximum |
| Q9 | A stockout month = any month with shipped < ordered | Even a partial shortfall lost sales | Counts partial and full stockouts alike |
| Q10 | A stockout is SUPPLIER_LATE only if the supplier promised delivery by the end of that month and missed it | Fair attribution of root cause | Other stockouts with open POs are planning misses |
| Q11 | Validated data is loaded as-is (no silent corrections) | Fixes belong in the source system | Rejected data leaves the previous load in place |
| Q12 | Open PO lines > 90 days past promise are not counted in inventory position | A planner would not rely on them | Shown as `stale_open_po_qty`; a real reorder is not suppressed by phantom supply |

## Statistical

| # | Assumption | Rationale | Limitation |
|---|---|---|---|
| S1 | Forecast errors ~ Normal, independent month to month | Needed for closed-form safety stock; reasonable for X/Y items with volume | Weak for intermittent/low-volume (Z) items — their SS is flagged low-confidence; Poisson/empirical methods are better |
| S2 | Lead time independent of demand | Standard in the combined formula | Suppliers may slow down in peak season |
| S3 | Safety stock σ = RMSE of one-step-ahead forecast errors (fallback demand std) | Protects against what the forecast gets wrong, not predictable patterns | Monthly one-step error understates multi-month error correlation |
| S4 | Lead-time σ pooled per supplier | SKU-level receipt samples too small | Masks SKU-specific differences (e.g. special items) |
| S5 | Sample std (`ddof=1`) | Unbiased with short histories | 24 months is a short sample |
| S6 | 12-month window for ABC and XYZ | Current mix; one of each season | New/declining items still move between classes |
| S7 | Forecast parameters fixed, not fitted | Explainable; errors are out-of-sample | Some accuracy left on the table |
| S8 | EOQ as reference lot size | Classic cost balance, flat cost curve | Assumes steady demand and known ordering cost |

## Policy defaults

| Parameter | Default | Why |
|---|---|---|
| ABC thresholds | 80% / 95% | Widely used Pareto convention |
| XYZ thresholds | CV 0.5 / 1.0 | Common practitioner starting points |
| Intermittency | ADI > 1.32 | Syntetos–Boylan–Croston classification |
| Service level A/B/C | 98% / 95% / 90% (CSL) | Protection proportional to value; typical distributor targets |
| Forecast hold-out | Last 6 months, rolling one-step | Half a seasonal cycle; 18 months remain for history |
| Tracking signal limit | ±4 | Common textbook control limit |
| Excess | Above policy max + 30 days of demand | Consistent with replenishment policy; tolerance for noise |
| Dead stock | 6 months without demand (seasonality-checked) | See `business_logic.md` |
| OTIF | Original promised date, 0 days tolerance, 100% quantity, early = on time | Strict, non-gameable definition |
| Ordering cost | $75 per PO | Plausible admin cost of raising/receiving/paying a PO; used only for EOQ |
| Carrying cost | 25% p.a. | Middle of the typical 20–30% range |
| Days per month | 365/12 | Consistent conversion |

## Out of scope (by design)

- Price elasticity, promotions and causal forecasting
- Multi-echelon inventory and inter-site transfers
- Capacity, budget, warehouse-space or supplier-level order-value constraints
- Quantity discounts and landed-cost optimisation
- Shelf life and expiry
- Real-time execution visibility (see "control tower" scope in `business_logic.md`)
