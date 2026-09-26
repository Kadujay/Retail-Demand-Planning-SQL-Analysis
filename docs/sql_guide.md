# SQL Guide: What Each Query Does and How to Explain It

A walkthrough of [`sql/transformations.sql`](../sql/transformations.sql) and
[`sql/analysis.sql`](../sql/analysis.sql), written to be explained in an
interview. Numbers are from the default dataset (seed 42, as of 2025-12-31);
the saved answers are in [`outputs/sql_answers/`](../outputs/sql_answers).

## Headline results

| Finding | Value | Source |
|---|---|---|
| Unit fill rate (shipped ÷ ordered) | 90.5% | `v_demand_monthly` |
| SKU-months with a stockout | 10.2% (11,642) | `v_demand_monthly` |
| Revenue lost to stockouts (24 months) | ≈ $19.2M | `v_demand_monthly` |
| Inventory value at cost | ≈ $17.3M (+$2.2M on open POs) | `v_inventory_position` |
| Top 10% of stocked SKUs' share of inventory value | 63% | q14 |
| Screening excess (> 6 months of supply) | 778 SKUs, ≈ $2.9M | q07 |
| Dead-stock candidates (no demand in 6 months) | 238 SKUs, ≈ $0.97M | q08 |
| Supplier OTIF (evaluable lines, strict) | 79.1% | `v_po_line_status` |
| Stockout months where a PO was already open | 3,818 (33% of stockout months) | q12 |
| … of which the supplier missed a promise inside the month | 2,059 (≈ $2.9M lost revenue) | q12 |
| … of which the PO was placed too late or too small | 1,759 (≈ $2.6M lost revenue) | q12 |
| SKUs below the screening reorder point | 2,329 of 4,761 active SKUs | q03 |

The last line needs context: the screen applies a uniform 95% service level
with demand σ, while the current (legacy) buying rule ignores variability. So
half the SKUs sit below a statistically sized reorder point, which is
consistent with the 90% fill rate. The screen also overstates σ for seasonal
items (their predictable swings count as noise). Phase 7 replaces it with the
forecast-error, class-based policy.

## Transformations (views)

### `v_demand_monthly`: demand vs. shipments
- **Question:** per SKU and month, what did customers order, what did we ship,
  and what was lost?
- **Key logic:** `lost_qty = ordered − shipped`; `stockout_month = shipped < ordered`.
- **Window functions:**
  - `AVG(ordered_qty) OVER (PARTITION BY sku_id ORDER BY month_start ROWS BETWEEN 2 PRECEDING AND CURRENT ROW)`
    is the 3-month rolling average. **How to explain:** "for each SKU, sort its
    months and average this month with the two before it. `PARTITION BY` restarts
    the calculation for every SKU."
  - `LAG(ordered_qty)` gives last month's value, so month-over-month change is one subtraction.
  - `SUM(...) OVER (PARTITION BY sku_id, year ORDER BY month_start)` is a
    year-to-date running total that resets each January.
- **Why ROWS works here:** each SKU has a row for every month since launch
  (zero months included), so "2 preceding rows" = "2 preceding months". If the
  panel had gaps, I would use a calendar join first.

### `v_inventory_monthly`: stock, value, and a reconciliation
- **Question:** what was in stock at each month end, and is the data sound?
- `opening_qty = LAG(on_hand_qty)`; `balance_difference = opening + received − shipped − closing`.
- **How to explain:** "This is the stock equation. If it is not zero, receipts,
  shipments or snapshots are wrong. It is 0 for all 109,424 rows that have an
  opening balance, so every downstream number is traceable."

### `v_po_line_status`: open quantity, lead time, OTIF per line
- **Receipts CTE:** aggregates receipts per line; `SUM(received_qty) FILTER (WHERE receipt_date <= promised + tolerance)`
  is conditional aggregation: quantity received *in time*.
- **OTIF:** `received_by_cutoff >= ordered_qty × in_full_tolerance`. Split
  deliveries count only if the full quantity arrived by the original promise.
- **Evaluable lines:** closed lines, **plus open lines already past their
  promise**. **How to explain:** "if I only scored closed lines, a supplier who
  is months late on open orders would look better than one who delivered late
  but delivered."
- **Open quantity:** derived as ordered − received on open lines, so it cannot
  disagree with receipts.

### `v_supplier_performance`: the scorecard
- `PERCENTILE_CONT(0.5)` and `(0.9) WITHIN GROUP (ORDER BY actual_lead_time_days)`:
  the median and P90 lead time. **How to explain:** "P90 is the lead time I plan
  for if I want to be covered 9 times out of 10. The mean hides the bad tail."
- `spend / SUM(spend) OVER ()`: share of total without a second query.
- `lead_time_cv = std / mean` makes variability comparable between a 10-day and
  an 80-day supplier.
- **Purchase price variance:** `Σ (paid − standard cost) × qty`.

### `v_inventory_position`: today's position per SKU
- `inventory_position = on_hand + open_po − allocated`. It decides reorders;
  on-hand alone would re-order stock already on the way. Open lines more than
  90 days past their promise are **excluded** (reported as `stale_open_po_qty`):
  phantom supply must not suppress a needed reorder.
- `days_of_supply = on_hand ÷ (avg monthly demand ÷ 30.42)`, `NULL` when demand is 0.
- Trailing statistics use `AVG(...) FILTER (WHERE month_start > as_of − N months)`:
  one pass over the table computes several windows (6-month average, 6-month
  dead-stock window, 12-month totals).
- **Screening ROP:** `d × P + z × σ × √P` with `P = (lead time + review) / 30.42`.
  It is a first-pass screen, clearly labelled.

## Analytical queries

| # | Question | Technique worth explaining |
|---|---|---|
| q01 | Highest inventory value SKUs | `RANK() OVER (PARTITION BY category ...)`, share via `SUM() OVER ()` |
| q02 | Lowest days of supply | `CASE` comparing cover with next supply; overdue POs flagged, not counted as cover |
| q03 | Below (screening) reorder point | Position vs. ROP; shortfall valued at cost; `COUNT(*) OVER ()` for the total |
| q04 | Longest lead times | Mean vs. median vs. P90; slippage vs. promise |
| q05 | Highest OTIF | `DENSE_RANK`; minimum-history filter |
| q06 | Most variable lead times | CV; `CASE` against configured limit |
| q07 | Excess by category | `GROUP BY` + `COUNT(*) FILTER`; `SUM(SUM(x)) OVER ()` = share of grand total |
| q08 | No demand for 6 months | Derived flag; `AGE()` for months since last demand; ACTIVE status exposes stale master data |
| q09 | Spend Pareto | Running `SUM() OVER (ORDER BY spend DESC ROWS UNBOUNDED PRECEDING)` |
| q10 | Unusually high cover | `PERCENT_RANK() OVER (PARTITION BY category)` + category median via `PERCENTILE_CONT` |
| q11 | Open POs that create excess | `LEAST/GREATEST` to attribute only the excess part of each line; MOQ-driven flag |
| q12 | Stockouts despite open POs | Range join to lines open at month start; `DISTINCT ON`; root cause via `CASE`; window totals by cause |
| q13 | Inventory value trend | `LAG` for MoM, 3-month moving `AVG`, running `MAX` (peak) |
| q14 | Value concentration | `NTILE(10)` deciles; cumulative share over deciles |
| q15 | High spend and poor delivery | `NTILE(4)` spend quartile; segmentation `CASE` from parameters |

### Five window-function examples to be ready to explain

1. **Rolling average (v_demand_monthly):**
   `AVG(x) OVER (PARTITION BY sku ORDER BY month ROWS BETWEEN 2 PRECEDING AND CURRENT ROW)`.
   Smooths noise before judging a trend.
2. **Month-over-month change (q13):** `value − LAG(value) OVER (ORDER BY month)`.
   Compares each row with the previous one without a self-join.
3. **Cumulative share (q09, q14):** `SUM(share) OVER (ORDER BY spend DESC ROWS UNBOUNDED PRECEDING)`.
   Builds a Pareto curve in one pass.
4. **Ranking within a group (q01, q10):** `RANK() / PERCENT_RANK() OVER (PARTITION BY category ...)`.
   "Top within its category" is fairer than "top overall" when categories differ.
5. **Share of total (q01, q07):** `x / SUM(x) OVER ()`. An empty `OVER ()` is the
   whole result set; with `GROUP BY`, `SUM(SUM(x)) OVER ()` sums the group totals.

### q12 in detail: whose fault was the stockout?

For every month where a SKU shipped less than was ordered, find a PO line
that was already open at the start of that month (ordered before, not yet
received). Keep the earliest-promised one (`DISTINCT ON`). Then:

- **SUPPLIER_LATE**: the supplier promised delivery by the end of that month
  and missed the promise. That's a supplier-management issue.
- **ORDERED_TOO_LATE_OR_TOO_LITTLE**: the promise was after the month, or it
  was kept. The PO existed but could not cover demand in time. That's a
  planning-parameter issue.

Result: about half the stockouts with an open PO are supplier misses and half
are planning misses. Neither team can fix stockouts alone.
