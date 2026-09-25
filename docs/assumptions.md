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
