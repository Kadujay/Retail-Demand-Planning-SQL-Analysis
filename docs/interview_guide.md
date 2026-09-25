# Interview Guide

Concise, technically accurate answers to questions a hiring manager is likely
to ask about this project. Each answer links the method to a **business
decision**. Project-specific figures (e.g. "A items are 19% of SKUs and 80% of
value") are added once the pipeline produces results (Phase 12).

---

### 1. Why did you use ABC analysis?

Planner time and working capital are finite. ABC ranks SKUs by annual
consumption value (annual demand × unit cost) so that the small set of SKUs
driving most of the inventory investment gets the tightest control, highest
service level and most frequent review. It is a prioritisation tool, not an
optimisation. I made the 80/95% cut-offs configurable because the right split
depends on how concentrated the portfolio is.

### 2. Why combine ABC with XYZ?

ABC measures *value*; XYZ (coefficient of variation) measures *forecastability*.
Two A items can need opposite policies: a stable AX item can run lean with
tight replenishment, while an erratic AZ item needs management attention and a
larger buffer. The 3×3 matrix lets the business differentiate policy instead
of applying one rule to 5,000 SKUs.

### 3. How did you calculate safety stock?

`SS = Z × √(L·σ_d² + d²·σ_L²)` — demand variability over the lead time plus
lead-time variability. Z comes from the target cycle service level. If lead
time were fixed (σ_L = 0) it reduces to the textbook `Z × σ_d × √L`. Demand
and lead time are converted to the same unit (months) first.

### 4. How did you choose service levels?

By ABC class: 98% for A, 95% for B, 90% for C. The logic is that stockouts of
high-value items cost the most, while C items have low value so accepting more
risk frees working capital. Because Z rises steeply near 100%, the last few
points of service level are the most expensive — I show this in the scenario
analysis. In a real company the targets would be agreed with Sales and Finance
in S&OP, not set by the analyst alone.

### 5. Why shouldn't time-series data be randomly split?

A random split lets the model train on future months and test on past ones —
information leakage. The accuracy looks better than it will be in production.
I train on the first 18 months and test on the last 6, forecasting forward
only with data available at each point, which mimics how the forecast is used.

### 6. How did you measure forecast accuracy?

MAE and RMSE in units, and WAPE (Σ|error| / Σactual) as the headline,
scale-free metric that is comparable across SKUs and weighted to volume. I
avoided MAPE because it divides by each period's actual and breaks on
zero-demand months. The best method per SKU is chosen by WAPE on the hold-out.

### 7. What is forecast bias?

The systematic direction of error: `Σ(F − A) / ΣA`. Positive means
over-forecasting, which builds excess inventory; negative means
under-forecasting, which causes stockouts. A forecast can have acceptable
accuracy but persistent bias, and bias usually points to a process issue
(e.g. optimistic sales input), so it is tracked separately.

### 8. What causes excess inventory?

Over-forecasting (positive bias), MOQs or order multiples larger than need,
demand decline or product end-of-life, overly high safety stock, long lead
times that force large orders, buying ahead for price, and order duplication
when on-hand rather than inventory position is used.

### 9. How does MOQ affect inventory?

If the required quantity is below MOQ, you must buy the MOQ; the difference
sits as extra cycle stock until it is consumed. For slow movers that can be
months of supply. The engine still orders the MOQ but reports the MOQ-driven
excess units and value so buyers can negotiate lower MOQs or consolidate.

### 10. What is the difference between inventory position and on-hand inventory?

On-hand is physically in the warehouse. Inventory position = on-hand + open
purchase orders − allocated (committed) stock. Reorder decisions must use
inventory position; otherwise you reorder stock that is already on the way.

### 11. How does lead-time variability affect safety stock?

It adds the `d²·σ_L²` term. When a supplier is unpredictable, you must cover
the demand that occurs during the *extra* days of a late delivery. For
high-volume items this term can dominate demand variability, so improving
supplier reliability can release more inventory than improving the forecast.

### 12. How does supplier OTIF affect inventory?

Low OTIF (late or short deliveries) means higher lead-time variability and
effective shortages, so planners compensate with more safety stock or
expediting. OTIF therefore has a direct inventory and cost impact, which is
why supplier metrics feed the safety-stock calculation.

### 13. How does inventory affect working capital?

Inventory is a current asset bought with cash. Every euro in excess or dead
stock is cash not available for the business and costs ~20–30% per year to
hold (capital, storage, insurance, obsolescence). Reducing excess improves
cash flow and return on capital; higher service levels consume it.

### 14. What happens if demand suddenly increases?

Short term: inventory position drops faster, more SKUs cross the ROP and
stockout risk rises, especially on long-lead-time items. The system
recommends more/larger orders. Policy-wise, lead-time demand and target
levels rise with the forecast. The scenario (+10% demand) quantifies the
extra orders and inventory required.

### 15. What happens if supplier lead time increases?

Both lead-time demand and safety stock rise (safety stock with √L), so
reorder points go up and orders must be placed earlier. Stock on order and
working capital increase. The +20% lead-time scenario shows the impact.

### 16. What assumptions did you make?

Approximately normal, independent demand; lead time independent of demand;
recorded sales ≈ demand except flagged stockout months; one supplier per SKU;
single warehouse; monthly buckets; cycle service level. Full list in
`docs/assumptions.md`.

### 17. What are the limitations of your model?

The normal-distribution assumption is weak for intermittent items; forecasts
are univariate (no promotions or pricing); no multi-echelon or capacity
constraints; stockout months understate true demand; synthetic data cannot
capture every real-world messiness. I chose explainable methods over maximal
accuracy on purpose.

### 18. How would you deploy this in a real company?

Connect to ERP extracts (item master, inventory, open POs, receipts, sales) via
scheduled SQL loads into PostgreSQL; run the Python pipeline nightly or weekly
(orchestrated, e.g., with Airflow or a scheduled job); publish outputs to Power
BI with a refresh schedule; validate data quality before each run and alert
on failures; and — importantly — treat recommendations as proposals that
buyers review, with overrides captured and feedback used to tune parameters.
Start with a pilot category and measure service level and inventory before and
after.

---

## Bonus: fill rate vs. cycle service level

Cycle service level = probability of no stockout in a replenishment cycle.
Fill rate = share of demand units shipped from stock. For the same safety
stock, fill rate is usually higher than CSL, because a stockout cycle often
still fills most of the demand. I used CSL for the transparent Z formula and
would add fill-rate-based sizing for customer-facing KPIs.
