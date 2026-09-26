# Interview Guide

Concise, technically accurate answers to questions a hiring manager is likely
to ask. Each answer ties the method to a **business decision**. Project-specific
figures (e.g. "A items are 19% of SKUs and 80% of value") are added once the
pipeline produces results (Phase 12).

**How to use this guide:** understand each answer well enough to explain it
without reading it; the formulas are in `methodology.md` and the "why not X"
answers are in `decision_log.md`.

---

## Core questions

### 1. Why did you use ABC analysis?

Planner time and working capital are finite. ABC ranks SKUs by annual
consumption value (12-month demand × unit cost) so the small set of SKUs that
drive most inventory investment gets the tightest control, highest service
target and most frequent review. It is a prioritisation tool, not an
optimisation. The 80/95% cut-offs are configurable because the right split
depends on how concentrated the portfolio is.

### 2. Why combine ABC with XYZ?

ABC measures *value*; XYZ measures *forecastability*. Two A items can need
opposite policies: a stable AX item runs lean with tight replenishment, an
erratic AZ item needs management attention and a larger buffer. The 3×3 matrix
lets the business differentiate policy instead of applying one rule to 5,000
SKUs. Intermittent items are identified with the ADI > 1.32 rule rather than
CV alone, because CV on mostly-zero series is misleading.

### 3. How did you calculate safety stock?

`SS = Z × √(P·σ² + d²·σ_L²)` where `P` is lead time plus the one-month review
period, `σ` is the standard deviation of forecast error, `d` the forecast
demand rate and `σ_L` the supplier's lead-time standard deviation. The first
term covers demand uncertainty over the protection interval; the second
covers late or early deliveries. With a fixed lead time and continuous review
it reduces to the textbook `Z × σ × √L`.

### 4. How did you choose service levels?

By ABC class: 98% for A, 95% for B, 90% for C — protection proportional to
value, accepting more risk on C items to free working capital. Because Z rises
steeply near 100%, the last few points are the most expensive; the scenario
analysis shows that cost. In a real company targets are agreed with Sales and
Finance in S&OP. I also distinguish the *policy* measure (cycle service level)
from the *achieved* measure the customer sees (fill rate).

### 5. Why shouldn't time-series data be randomly split?

A random split trains on future months and tests on past ones — leakage. The
accuracy looks better than it will be in production. I hold out the last six
months and forecast them rolling one step ahead, using only data available at
each point, which mimics how the forecast is actually used.

### 6. How did you measure forecast accuracy?

WAPE (Σ|error| / Σactual) as the headline — volume-weighted, scale-free and
safe with zero-demand months — plus MAE and RMSE in units. I avoided MAPE
because it divides by each month's actual and breaks on zeros. I also compare
every method with a naive forecast (forecast value added) so I can show the
method is worth using.

### 7. What is forecast bias?

The systematic direction of error: `Σ(F − A) / ΣA`. Positive means
over-forecasting (builds excess), negative means under-forecasting (causes
stockouts). I monitor it with a tracking signal (cumulative error ÷ MAD);
outside ±4 the forecast is flagged. Bias usually points to a process issue,
such as optimistic sales input, rather than a statistical one.

### 8. What causes excess inventory?

Over-forecasting, MOQs or order multiples larger than need, declining or
end-of-life products, safety stock set too high, long lead times forcing
large orders, forward buying for price, and double-ordering when on-hand
rather than inventory position is used. The project attributes excess to
these causes where the data allows (bias, MOQ-driven excess, lifecycle).

### 9. How does MOQ affect inventory?

If the need is below the MOQ you must buy the MOQ; the surplus sits as extra
cycle stock. For slow movers that can be months of supply. The engine still
orders the MOQ but reports the MOQ-driven excess units and value, so buyers
can negotiate lower MOQs, consolidate or reconsider stocking the item. I also
show EOQ as a reference, which makes it visible when MOQ, not economics,
drives the lot size.

### 10. What is the difference between inventory position and on-hand inventory?

On-hand is physically in the warehouse. Inventory position = on-hand + open PO
quantity − allocated stock. Reorder decisions use inventory position;
otherwise you re-order stock that is already on the way. I assume lost sales,
so there are no backorders to subtract.

### 11. How does lead-time variability affect safety stock?

It adds the `d²·σ_L²` term: when a supplier is unpredictable you must cover the
demand that occurs during the *extra* days of a late delivery. For high-volume
items this can dominate demand uncertainty, so improving supplier reliability
can release more inventory than improving the forecast.

### 12. How does supplier OTIF affect inventory?

Low OTIF means late or short deliveries: higher lead-time variability and
effective shortages. Planners compensate with more safety stock or expediting.
I measure OTIF per PO line against the original promised date, summing split
deliveries, so it can't be improved by re-promising. The supplier's lead-time
σ feeds each of its SKUs' safety stock, which puts a cost on unreliability.

### 13. How does inventory affect working capital?

Inventory is a current asset bought with cash. Excess and dead stock are cash
not available to the business and cost ~20–30% a year to hold. I report
inventory value, excess and dead-stock share, turns, DIO and GMROI, and I split
them by ABC class, supplier and category to show where the cash sits.

### 14. What happens if demand suddenly increases?

Inventory position falls faster, more SKUs cross the reorder point and
stockout risk rises — most on long-lead-time items. Reorder points, safety
stock (via `d²σ_L²`) and order quantities rise with the forecast. The +10%
demand scenario quantifies the extra orders and working capital. I'd also
check whether the increase is real (tracking signal) before chasing it.

### 15. What happens if supplier lead time increases?

The protection interval grows, so protection demand and safety stock rise,
reorder points go up and orders must be placed earlier; more stock is on order
and working capital increases. The +20% lead-time scenario shows the impact.

### 16. What assumptions did you make?

Monthly periodic review; demand = customer orders; lost sales; roughly normal,
independent forecast errors; lead time independent of demand, pooled per
supplier; one supplier per SKU; single DC; cycle service level as the policy
input. Full list in `assumptions.md`.

### 17. What are the limitations of your model?

Normal-based safety stock is weakest for intermittent items (flagged
low-confidence); forecasts are univariate; no multi-echelon, capacity or budget
constraints; residual demand censoring; synthetic data can't reproduce every
real-world messiness. I chose explainable methods over maximum accuracy on
purpose.

### 18. How would you deploy this in a real company?

Scheduled ERP extracts (item master, inventory, open POs, receipts, sales
orders) into PostgreSQL; data-quality checks that block the run on failure;
the Python pipeline run each planning cycle by a scheduler; outputs published
to Power BI with a refresh. Recommendations are *proposals* buyers approve or
override, with overrides captured to tune parameters. Start with one category
as a pilot and measure fill rate and inventory before and after.

---

## Questions an experienced interviewer will add

### 19. Your planning run is monthly — why isn't the reorder point just lead-time demand plus safety stock?

Because in a periodic-review system you can't react between reviews. An order
placed today arrives after L; the next chance to order is a month later. Stock
must cover lead time *plus* the review period. Using only lead time
under-protects by a whole month. With continuous review (R = 0) my formula
collapses to the textbook one.

### 20. Why size safety stock on forecast error rather than demand variability?

Safety stock protects against what we *don't* know. If demand is seasonal and
the forecast predicts the season, that variation isn't uncertainty.
Using raw demand σ would over-stock exactly the seasonal and trending SKUs.
New SKUs without enough error history fall back to demand σ.

### 21. Why not machine learning?

With monthly univariate history and no causal drivers (price, promotions,
weather), ML models rarely beat simple exponential methods, and they are
harder to explain to planners who must trust and override them. I let methods
compete on validation data and would add ML only when causal data exists and
it beats the baseline on hold-out.

### 22. How do EOQ and MOQ interact?

EOQ balances ordering cost against holding cost: `√(2DS / H)`. I take the
larger of EOQ and MOQ, then round up to the order multiple. When MOQ is much
larger than EOQ, the gap is inventory held for the supplier's convenience;
the output quantifies it so procurement can negotiate.

### 23. How do you handle intermittent or new items?

Intermittent (ADI > 1.32) items are classed Z, their normal-based safety stock
is flagged low-confidence, and CZ items are candidates for order-on-demand or
delisting. New items with under six months of history are flagged NEW and
need planner input; statistical methods aren't trusted on three data points.

### 24. You call it a control tower — isn't a control tower real-time?

In logistics, often yes. Here it means a planning control tower: one
consistent view of inventory risk, its causes and recommended actions,
refreshed every planning cycle. I don't claim real-time execution visibility.

### 25. If you had one day with the output, what would you do first?

Sort by money at stake: revenue at risk on A-item stockouts first (expedite),
then the largest excess and dead-stock values and open POs that can be pushed
out (cash release), then high-spend At-Risk suppliers and A/B items with
biased forecasts, because those are the causes that keep regenerating the
first two lists.

### 26. How do you know your numbers are right?

Unit tests on every formula with hand-calculated cases and edge cases (zero
demand, zero lead time, MOQ above need); data-quality checks before every
run; reconciliation checks (inventory value by category sums to the total;
Python and SQL give the same ABC split); and sense checks (A items ~20% of
SKUs; higher service level always raises safety stock).

---

## Data and SQL questions

### 27. What is the grain of your fact tables?

`fact_demand`: one SKU per month. `fact_inventory`: one SKU per month end.
`fact_purchase_order`: one PO line. `fact_supplier_delivery`: one goods
receipt, so a partial delivery is several rows for one PO line. The grain is
enforced by the primary keys and tested. Getting grain wrong is how totals get
double-counted.

### 28. Why keep demand and shipments separate?

Because stockouts cap shipments. If I forecast on shipments, last year's
stockouts become next year's lower forecast, and the stockout repeats. Demand
(customer orders) is the signal; the gap is lost sales.

### 29. How do you stop bad data from reaching the analysis?

Validation runs before loading: 47 checks, each ERROR, WARNING or INFO. Any
ERROR means REJECTED: nothing is loaded, the reasons go to an audit table, and
the command exits with an error. Warnings load but are recorded. Database
constraints (keys, CHECKs) are the second line of defence, and the load is one
transaction, so it is all or nothing. I refuse bad data rather than silently
fixing it, because fixes belong in the source system.

### 30. Explain a window function you used.

The 3-month rolling average: `AVG(ordered_qty) OVER (PARTITION BY sku_id ORDER
BY month_start ROWS BETWEEN 2 PRECEDING AND CURRENT ROW)`. For each SKU,
order its months and average each month with the two before it; `PARTITION
BY` restarts per SKU. It works because every SKU has a row for every month
since launch. Others: `LAG` for month-over-month change, a running `SUM` for
the supplier-spend Pareto, `PERCENT_RANK` within category for unusual
inventory.

### 31. How did you calculate OTIF in SQL, and what did you watch out for?

Per PO line, sum the quantity received by the original promised date; OTIF if
that covers the ordered quantity. Two traps: split deliveries (sum them, don't
look at one receipt) and open lines already overdue (count them as failures,
otherwise a supplier looks better the later it is).

### 32. A stockout happened although a PO was open. Whose fault?

If the supplier promised delivery by the end of that month and missed it, it
was the supplier. Otherwise the PO was placed too late or too small, which is a
planning issue. In this data it is roughly half and half, which is why I
wouldn't let either team blame the other.

### 33. Why views instead of tables for the analytics layer?

About 300k rows: views are fast enough, can never go stale, and keep the
logic readable in one SQL file. If data grew, I'd turn the heavy ones into
materialized views without changing the consumers.

### 34. Why is the SQL reorder point different from the Python one?

The SQL version is a transparent screen: one 95% service level and demand σ.
It lists candidates quickly. The Python policy (Phase 7) uses class-based
service levels, forecast-error σ and lead-time variability. I kept the
screen simple on purpose rather than maintaining two copies of the full policy.

## Fill rate vs. cycle service level

Cycle service level = probability of no stockout in a replenishment cycle.
Fill rate = share of demand units shipped from stock. For the same safety
stock, fill rate is usually higher because a stockout cycle often still fills
most of the demand. CSL drives the policy (transparent Z formula); fill rate
is the achieved KPI reported to the business.
