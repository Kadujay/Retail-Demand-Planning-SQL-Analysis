# Assumptions

Every model is a simplification. This file lists the assumptions made, why
each is reasonable for this project, and what would change in a real
deployment. Defaults are in [`src/config.py`](../src/config.py).

## Data

| # | Assumption | Rationale | Real-world consideration |
|---|---|---|---|
| D1 | All data is **synthetic**, generated with a fixed seed (42) | No confidential data; fully reproducible | Real data needs cleansing, master-data governance |
| D2 | Monthly demand buckets | Standard tactical planning grain for distributors | Fast movers may need weekly planning |
| D3 | Recorded sales ≈ demand, except flagged stockout months | Keeps the model simple; distortion is flagged | Impute lost sales from stockout days / lost orders |
| D4 | One primary supplier per SKU | Typical for a distributor; simplifies lead-time mapping | Multi-sourcing requires allocation logic |
| D5 | Unit cost is constant over the history (PPV captured separately on POs) | Standard-cost convention | Real costs change; use current standard cost |
| D6 | Single warehouse (no multi-echelon) | Scope control | Multi-location needs network inventory optimisation |

## Statistical

| # | Assumption | Rationale | Limitation |
|---|---|---|---|
| S1 | Demand per period ~ Normal and independent between periods | Required for the closed-form safety-stock formula; reasonable for X/Y items with adequate volume | Poor for intermittent / low-volume (Z) items; Poisson or empirical methods are better |
| S2 | Lead time independent of demand | Standard assumption in the combined formula | Suppliers may slow down in peak season |
| S3 | Sample standard deviation (`ddof=1`) | Unbiased estimate from limited history | 24 months is a short sample for σ |
| S4 | Last 12 months used for ABC | Reflects current value mix | Declining/new items may be misclassified |
| S5 | Forecast error ≈ demand variability for safety stock | Simpler and transparent | Using forecast-error σ (RMSE) is more precise — a planned refinement |

## Policy defaults

| Parameter | Default | Why |
|---|---|---|
| ABC thresholds | 80% / 95% | Widely used Pareto convention |
| XYZ thresholds | CV 0.5 / 1.0 | Common practitioner starting points |
| Service level A/B/C | 98% / 95% / 90% | Differentiate protection by value; typical distributor targets |
| Service level type | Cycle service level | Transparent Z-based formula |
| Days per month | 365/12 ≈ 30.42 | Consistent calendar conversion |
| Forecast hold-out | Last 6 months | Covers half a seasonal cycle while leaving 18 months to train |
| Review period | 1 month | Monthly planning cycle |
| Excess threshold | SS + 120 days of supply | See `business_logic.md` |
| Dead stock | 6 months without demand | See `business_logic.md` |
| OTIF tolerance | 0 days late, 100% quantity | Strict definition; configurable |
| Carrying cost rate | 25% p.a. | Middle of the typical 20–30% range |

## Out of scope (by design)

- Price elasticity, promotions and causal forecasting
- Multi-echelon inventory and transfers between locations
- Capacity, budget or warehouse-space constraints on ordering
- Supplier quantity discounts / economic order quantity optimisation
- Shelf life and expiry

These are listed as future improvements in the README.
