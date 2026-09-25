# Business Logic

`methodology.md` defines *how* each metric is calculated. This document
explains **why each metric matters, which decision it drives, and the
trade-offs involved** — the questions a supply-chain manager actually asks.

## The decision the system supports

> **What should we order, when should we order it, and which inventory
> problems should management address first?**

| Question | Answered by | Output |
|---|---|---|
| What should we order? | Replenishment engine (ROP, order-up-to, MOQ, multiples) | `replenishment_recommendations.csv` |
| When should we order it? | Inventory position vs. reorder point; projected inventory at lead-time end | Reason codes `STOCKOUT_RISK`, `BELOW_ROP`, `SAFETY_STOCK_RISK` |
| What should management address first? | Inventory health × ABC class × value at risk; supplier segmentation | `inventory_health.csv`, `supplier_performance.csv`, `executive_kpis.csv` |

## Metric → decision map

| Metric | Why it matters | Decision it drives |
|---|---|---|
| ABC class | Concentrates control where money is | Review frequency, service-level target, planner ownership |
| XYZ class | Indicates forecastability | How much to trust the forecast; buffer size; planning method |
| ABC-XYZ segment | Combines value and risk | Differentiated inventory policy (see methodology §4) |
| Forecast WAPE | Size of forecast error | Safety stock sizing; where to invest in better forecasting |
| Forecast bias | Direction of error | Correct systematic over/under-forecasting before it becomes excess/stockouts |
| Safety stock | Buffer against uncertainty | Inventory investment required for the chosen service level |
| Reorder point | Trigger level | *When* to place an order |
| Inventory position | True available supply | Prevents double-ordering stock already on the way |
| Days of supply | Coverage in time units | Comparable across SKUs; spots both shortage and excess |
| Excess value | Capital above need | Stop ordering, cancel/push out POs, promotions, returns to vendor |
| Dead-stock value | Capital at write-off risk | Liquidation, write-down, delisting decisions |
| OTIF | Supplier reliability | Supplier reviews, sourcing strategy, safety-stock inputs |
| Lead-time variability | Uncertainty of supply | Directly increases safety stock (methodology §9) |
| Fill rate | Share of ordered quantity delivered | Partial deliveries cause hidden shortages |
| Carrying cost | Annual cost of holding inventory | Quantifies the cost of excess and of higher service levels |

## Inventory health status — rationale

Status is assigned in **priority order** so that every SKU has exactly one,
most-urgent status (e.g. an out-of-stock SKU is never labelled "excess").

| Status | Business meaning | Typical action |
|---|---|---|
| STOCKOUT | No stock while demand exists — lost sales / backorders now | Expedite, alternative supplier, customer communication |
| CRITICAL | Below safety stock — buffer consumed, stockout likely before replenishment | Expedite open POs, place order immediately |
| BELOW_REORDER_POINT | Normal trigger reached | Place standard replenishment order |
| DEAD_STOCK | Stock with no recent demand | Liquidate, return to vendor, write down, stop replenishment |
| EXCESS | More than target coverage | Stop ordering, push out / cancel POs, rebalance |
| HEALTHY | Within policy | No action |

**Threshold choices (configurable in `InventoryHealthConfig`):**

- `excess_days_of_supply = 120`: stock beyond safety stock + ~4 months of
  forward demand. For a distributor with typical 2–8-week lead times and
  monthly review, ~4 months is well above what the replenishment cycle
  needs, so a flag here indicates a real problem rather than normal cycle
  stock. Businesses with long lead times (imports) would raise it.
- `dead_stock_months_without_demand = 6`: no sales for two quarters. Short
  enough to act before obsolescence, long enough not to flag normal
  slow-movers. Caveat: a seasonal item with a short season can legitimately
  go 6 months without sales off-season. Phase 6 checks this interaction
  (e.g. by also requiring no demand in the upcoming months of last year)
  before an item is labelled dead stock.

## Supplier segmentation — rationale

Thresholds in `SupplierConfig`:

| Segment | Rule | Rationale |
|---|---|---|
| Reliable | OTIF ≥ 95% and lead-time CV ≤ 0.30 | 95% OTIF is a common contractual target; CV ≤ 0.30 means lead time rarely deviates by more than ~a third |
| Watch | OTIF 85–95%, or lead-time CV > 0.30 | Performance acceptable but costing safety stock; monitor and discuss |
| At Risk | OTIF < 85% | Frequent failures; driving stockouts or excess safety stock; escalation / dual sourcing |

Segments are always reported next to **spend**, because the business
priority is "high-spend and at-risk", not "at-risk" alone.

## Key trade-offs made visible

| Lever | Effect | Where it is shown |
|---|---|---|
| Higher service level | More safety stock → higher inventory investment → lower stockout risk | Scenario: 90/95/98% service level |
| Lower inventory | Lower working capital and carrying cost → higher stockout risk | Scenario comparison; health status mix |
| Higher MOQ | Fewer orders → higher average inventory (MOQ-driven excess) | `moq_excess_units` in replenishment output |
| Longer lead time | Higher lead-time demand and safety stock → more inventory, more exposure | Scenario: +20% lead time |
| Poor supplier OTIF / variable lead time | Higher σ_L → higher safety stock | Supplier → SKU safety-stock link |
| Forecast bias | Positive bias → excess; negative bias → stockouts | Forecast results vs. inventory health |

## Prioritisation logic for management

Problems are ranked by **financial exposure**, not count of SKUs:

1. Stockout / critical **A** items (revenue and customer risk).
2. High-value excess and dead stock (cash release opportunity).
3. High-spend suppliers in *At Risk* segment (systemic cause of 1).
4. Biased forecasts on A/B items (systemic cause of 1 and 2).

The exact ranking view is implemented in Phases 6–9 and documented in the
Power BI guide.
