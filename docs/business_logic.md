# Business Logic

`methodology.md` defines *how* each metric is calculated. This document
explains **the business context, who uses each output, which decision each
metric drives, and the trade-offs involved**.

## 1. The business

A **fictional** wholesale distributor of industrial and maintenance supplies
(no real company is represented; all data is synthetic):

| Attribute | Value |
|---|---|
| Active SKUs | 5,000 across categories such as fasteners, electrical, safety/PPE, tools, HVAC, janitorial |
| Suppliers | 50, mixed domestic (short, reliable lead times) and import (long, variable lead times) |
| Planning cadence | Monthly replenishment run (periodic review) |
| Customer promise | Ship from stock; unfilled order lines are usually lost to competitors |
| Network | Single central distribution centre |

### Situation the analysis responds to

Management sees **both** symptoms at once — the classic sign of undifferentiated
inventory policy:

- Customers complain about stockouts on important items.
- Finance complains that inventory (working capital) keeps growing.
- Buyers use one rule of thumb for all SKUs and order to MOQ by habit.
- Some suppliers are frequently late or short, and nobody quantifies the cost.

### The question

> **What should we order, when should we order it, and which inventory
> problems should management address first?**

## 2. Scope: what "control tower" means here

"Control tower" is used in the **planning-analytics** sense: one consistent
view of inventory risk, its causes (forecast, supplier, policy) and the
recommended actions, refreshed each planning cycle.

It is **not** a real-time logistics visibility platform (no shipment tracking,
IoT or event streams). Saying this explicitly avoids over-claiming.

## 3. Who uses the outputs

| Stakeholder | Question they ask | Output |
|---|---|---|
| Buyer / supply planner | What do I order this cycle, how much, and why? | `replenishment_recommendations.csv`, Power BI page 2 |
| Demand planner | Which forecasts are inaccurate or biased? | `forecast_results.csv`, page 3 |
| Procurement / category manager | Which suppliers hurt service or inflate stock? | `supplier_performance.csv`, page 4 |
| Supply chain manager / S&OP | What is the risk and cost of changing service levels or lead times? | Scenario KPIs, page 1 |
| Finance (CFO / controller) | How much cash is tied up in excess and dead stock? | `executive_kpis.csv`, page 1 |

## 4. Decisions supported

| Question | Answered by | Output |
|---|---|---|
| **What** to order | (R, s, S) policy with EOQ/MOQ/multiple lot sizing | Recommended quantity and value |
| **When** to order | Inventory position vs. reorder point; projected reorder date for SKUs not yet due | Reason code, `projected_reorder_date` |
| **What first** | Health status × ABC × **value at risk** | Ranked action list |
| **Why** it happened | Forecast bias, supplier OTIF / lead-time variability, MOQ-driven excess | Root-cause columns and supplier segment |

## 5. Metric → decision map

| Metric | Why it matters | Decision it drives |
|---|---|---|
| ABC class | Concentrates control where the money is | Review frequency, service-level target, planner ownership |
| XYZ class | Forecastability | Planning approach; how much to trust the forecast |
| ABC-XYZ segment | Value × risk | Differentiated policy (methodology §4) |
| WAPE / FVA | Size of forecast error; value of the method | Where to invest in forecasting effort |
| Bias / tracking signal | Systematic direction of error | Correct over/under-forecasting before it becomes excess/stockouts |
| Safety stock | Buffer for uncertainty | Inventory investment for the chosen service level |
| Reorder point | Trigger level | When to order |
| Inventory position | True available supply | Prevents double-ordering stock already on the way |
| Days of supply | Coverage in time units | Comparable across SKUs; spots shortage and excess |
| Stockout probability / revenue at risk | Likelihood and cost of running out | Expedite, reprioritise |
| Excess value (on hand / on order) | Capital above need | Stop ordering, push out / cancel POs, rebalance |
| Dead-stock value | Capital at write-off risk | Liquidate, return to vendor, write down, delist |
| OTIF, fill rate | Supplier reliability | Supplier reviews, sourcing strategy |
| Lead-time variability | Supply uncertainty | Directly raises safety stock (methodology §9) |
| EOQ vs. MOQ | Ordering vs. holding cost | Lot size; MOQ negotiation |
| Turns, DIO, GMROI | Inventory productivity | Where inventory earns its keep, where it doesn't |
| Carrying cost | Annual cost of holding stock | Quantifies excess and service-level decisions in money |

## 6. Inventory health status — rationale

Statuses are assigned in **priority order**, so every SKU has exactly one,
most-urgent status (an out-of-stock SKU is never labelled "excess").

| Status | Business meaning | Typical action |
|---|---|---|
| STOCKOUT | No stock while demand exists — sales being lost now | Expedite, alternative source, customer communication |
| CRITICAL | Below safety stock — buffer consumed | Expedite open POs; order immediately |
| BELOW_REORDER_POINT | Normal trigger reached | Standard replenishment order |
| DEAD_STOCK | Stock with no demand in 6 months (and not seasonal off-season) | Liquidate, return to vendor, write down, stop replenishment |
| EXCESS | Above policy maximum + tolerance | Stop ordering, push out / cancel POs |
| HEALTHY | Within policy | No action |

**Threshold choices** (`InventoryHealthConfig`):

- **Excess = above the policy maximum `S` + 30 days of demand.** Tying excess to
  the same maximum the replenishment engine orders up to means the health view
  and order recommendations can never disagree. The 30-day tolerance absorbs
  normal forecast noise; without it, a SKU one unit above max would be flagged.
  A business preferring a simple rule of thumb (e.g. "> 6 months of supply")
  can swap the definition — the trade-off is logged in the decision log.
- **Dead stock = no demand for 6 months.** Two quarters is short enough to act
  before obsolescence and long enough not to flag normal slow movers. Seasonal
  items are protected by also checking last year's demand in the upcoming
  months.

## 7. Supplier segmentation — rationale

| Segment | Rule | Rationale |
|---|---|---|
| Reliable | OTIF ≥ 95% and lead-time CV ≤ 0.30 | 95% OTIF is a common contractual target; CV ≤ 0.30 means lead time rarely strays by more than about a third |
| Watch | OTIF 85–95%, or lead-time CV > 0.30 | Acceptable delivery, but variability is costing safety stock |
| At Risk | OTIF < 85% | Frequent failures; escalation, corrective action plan or dual sourcing |

Segments are always reported next to **spend**: the priority is
"high-spend *and* at-risk", not "at-risk" alone.

## 8. Key trade-offs made visible

| Lever | Effect | Where it is shown |
|---|---|---|
| Higher service level | More safety stock → more working capital → fewer stockouts (non-linear) | Scenario: 90 / 95 / 98% |
| Lower inventory | Lower working capital and carrying cost → higher stockout risk | Scenario comparison, health mix |
| Higher MOQ | Fewer orders → higher average inventory | `moq_excess_units` / value |
| Larger lots vs. frequent orders | Holding cost vs. ordering cost | EOQ reference vs. actual lot |
| Longer lead time | More protection demand and safety stock → more inventory, earlier orders | Scenario: +20% lead time |
| Poor OTIF / variable lead time | Higher σ_L → higher safety stock | Supplier → SKU safety-stock link |
| Forecast bias | Positive → excess; negative → stockouts | Forecast results vs. health status |
| Longer review period | Larger protection interval → more safety stock | Config `review_period_months` |

## 9. Prioritisation logic for management

Problems are ranked by **money at stake**, not SKU counts:

1. **Revenue at risk** on STOCKOUT / CRITICAL items (A items first).
2. **Cash release**: high-value dead and excess stock; open POs that can be
   pushed out (`excess_on_order`).
3. **Systemic supplier causes**: high-spend *At Risk* suppliers.
4. **Systemic forecast causes**: A/B items with tracking signal outside ±4.

## 10. Business rules the data layer preserves (Phase 3)

The database and SQL keep the operational distinctions that make supply-chain
analysis trustworthy:

| Principle | How the data layer preserves it |
|---|---|
| Demand and shipments are different | `ordered_qty` and `shipped_qty` are separate columns; lost sales = the difference |
| Stockouts make shipments understate demand | Rolling demand and forecasts use `ordered_qty`, never shipments |
| Open POs affect inventory position | `inventory_position = on hand + open PO − allocated`; open qty derived from receipts |
| Partial deliveries affect receipts | One row per receipt; OTIF sums receipts up to the original promise |
| Supplier type affects lead time and reliability | Measured, not assumed: lead-time mean / P90 / CV and OTIF per supplier |
| Product lifecycle affects demand | Dead-stock candidates derived from demand, not from the (stale) lifecycle flag |
| MOQ can create excess | q11 flags open POs placed at MOQ that push cover above the limit |
| Stale open POs inflate supply | Flagged in validation, **excluded from inventory position**, and reported separately (`stale_open_po_qty`) so they get chased or cancelled |

### What the SQL layer tells management (seed 42)

- **Service:** 90.5% unit fill rate; stockouts in 10% of SKU-months, costing
  ≈ $19M of revenue over two years.
- **Root cause is shared:** where a PO was already open during a stockout,
  about half were supplier misses and half were POs placed too late or too
  small (q12). Supplier management and planning parameters both need work.
- **Working capital:** ≈ $17M of stock; 63% of it sits in the top 10% of SKUs;
  ≈ $2.9M is above 6 months of supply and ≈ $1M has had no demand for 6 months.
- **Suppliers:** strict line-level OTIF is 79%. Several of the largest suppliers
  by spend are *At Risk* or *Watch* (q15), and their SKUs account for many of
  the stockout months.

## 11. What success would look like

If the company adopted the recommendations, it would track (baseline values
are filled in from the synthetic results in Phase 12):

| KPI | Direction |
|---|---|
| Fill rate on A items | ↑ towards target |
| Excess + dead-stock share of inventory value | ↓ |
| Inventory turns / DIO | ↑ turns / ↓ DIO |
| Forecast bias on A/B items | → 0 |
| Spend with *At Risk* suppliers | ↓ |

The project does not claim that these improvements would be realised — it
provides the analysis a planning team would use to pursue them.
