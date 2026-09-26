-- =============================================================================
-- analysis.sql — Business questions answered in SQL
-- =============================================================================
-- Each query starts with "-- name: <id>" (used by `python -m src.database
-- analyze` to run it and save the answer to outputs/sql_answers/<id>.csv),
-- followed by the business question, why it matters, and the SQL technique.
--
-- All queries read the analytics views (sql/transformations.sql), never the
-- raw CSVs, and use the extract's as-of date, never now().
-- Screens labelled "screening" use simple uniform rules; the class-based
-- planning policy arrives in Phase 7.
-- =============================================================================


-- name: q01_top_inventory_value
-- Question: Which SKUs tie up the most inventory value?
-- Why: Working capital is concentrated; the top items are where a reduction
--      releases the most cash, and where counting accuracy matters most.
-- Technique: window SUM for share of total; RANK within category (PARTITION BY).
SELECT
    sku_id,
    category,
    supplier_id,
    on_hand_qty,
    ROUND(inventory_value, 2)                                              AS inventory_value,
    ROUND(100 * inventory_value / SUM(inventory_value) OVER (), 2)         AS pct_of_total_value,
    RANK() OVER (PARTITION BY category ORDER BY inventory_value DESC)      AS rank_in_category,
    ROUND(months_of_supply, 1)                                             AS months_of_supply
FROM analytics.v_inventory_position
ORDER BY inventory_value DESC
LIMIT 20;


-- name: q02_lowest_days_of_supply
-- Question: Which active SKUs will run out first?
-- Why: Days of supply is the planner's early-warning signal. A SKU whose stock
--      runs out before the next supply can arrive needs expediting now.
-- Technique: CASE to compare coverage with the next supply date (open PO
--            promise, or a new order's lead time if nothing is open). An open
--            PO already past its promised date is flagged, not counted as cover.
SELECT
    sku_id,
    category,
    supplier_id,
    on_hand_qty,
    open_po_qty,
    ROUND(avg_daily_demand, 2)                                  AS avg_daily_demand,
    ROUND(days_of_supply, 1)                                    AS days_of_supply,
    next_promised_date,
    COALESCE(next_promised_date - as_of_date, supplier_lead_time_days) AS days_until_next_supply,
    CASE
        WHEN next_promised_date < as_of_date
            THEN 'OPEN PO OVERDUE - CHASE SUPPLIER'
        WHEN days_of_supply < COALESCE(next_promised_date - as_of_date, supplier_lead_time_days)
            THEN 'RUNS OUT BEFORE NEXT SUPPLY'
        ELSE 'COVERED UNTIL NEXT SUPPLY'
    END                                                         AS outlook,
    ROUND(avg_monthly_demand * unit_price, 2)                   AS monthly_revenue_at_stake
FROM analytics.v_inventory_position
WHERE avg_monthly_demand > 0
ORDER BY days_of_supply ASC, avg_monthly_demand * unit_price DESC
LIMIT 25;


-- name: q03_below_screening_reorder_point
-- Question: Which SKUs are below their (screening) reorder point?
-- Why: Inventory position below the reorder point means an order is due now;
--      waiting risks a stockout within the lead time + review period.
-- Technique: filter on the derived flag; shortfall valued at cost to rank by
--            money at stake. Uses inventory POSITION (incl. open POs), not on
--            hand, so stock already on order is not double-ordered.
SELECT
    sku_id,
    category,
    supplier_id,
    on_hand_qty,
    open_po_qty,
    allocated_qty,
    inventory_position,
    ROUND(screening_reorder_point, 1)                                   AS screening_reorder_point,
    ROUND(screening_reorder_point - inventory_position, 1)              AS shortfall_qty,
    ROUND((screening_reorder_point - inventory_position) * unit_cost, 2) AS shortfall_value,
    COUNT(*) OVER ()                                                    AS skus_below_rop
FROM analytics.v_inventory_position
WHERE below_screening_rop
ORDER BY (screening_reorder_point - inventory_position) * unit_cost DESC
LIMIT 25;


-- name: q04_longest_supplier_lead_times
-- Question: Which suppliers have the longest actual lead times, and do they
--           take longer than they quote?
-- Why: Long lead times need more inventory (more demand to cover while
--      waiting) and earlier ordering; slippage vs. quote means planners are
--      using optimistic parameters.
-- Technique: pre-aggregated scorecard; PERCENTILE_CONT (in the view) for the
--            median and P90, which resist outliers better than the mean.
SELECT
    supplier_id,
    region,
    po_lines,
    quoted_lead_time_days,
    ROUND(avg_lead_time_days, 1)                                AS avg_lead_time_days,
    ROUND(median_lead_time_days::numeric, 1)                    AS median_lead_time_days,
    ROUND(p90_lead_time_days::numeric, 1)                       AS p90_lead_time_days,
    ROUND(avg_lead_time_days - avg_promised_lead_time_days, 1)  AS avg_slippage_vs_promise_days
FROM analytics.v_supplier_performance
WHERE enough_history
ORDER BY avg_lead_time_days DESC
LIMIT 15;


-- name: q05_highest_otif_suppliers
-- Question: Which suppliers deliver on time and in full most reliably?
-- Why: Reliable suppliers need less safety stock and are candidates for more
--      volume or collaborative programmes (e.g. VMI).
-- Technique: DENSE_RANK (ties share a rank); minimum-history filter so a
--            supplier with 3 perfect lines does not top the list.
SELECT
    DENSE_RANK() OVER (ORDER BY ROUND(otif_pct, 3) DESC)        AS otif_rank,
    supplier_id,
    region,
    otif_lines,
    ROUND(100 * otif_pct, 1)                                    AS otif_pct,
    ROUND(100 * fill_rate, 1)                                   AS fill_rate_pct,
    ROUND(avg_days_late, 1)                                     AS avg_days_late,
    ROUND(spend, 0)                                             AS spend
FROM analytics.v_supplier_performance
WHERE enough_history
ORDER BY otif_pct DESC, spend DESC
LIMIT 15;


-- name: q06_lead_time_variability
-- Question: Which suppliers have the most unpredictable lead times?
-- Why: Lead-time variability (not just length) drives safety stock through
--      the d^2 x sigma_L^2 term. Unpredictable suppliers cost inventory.
-- Technique: coefficient of variation (std / mean) to compare suppliers with
--            different lead-time levels; RANK; CASE against configured limit.
SELECT
    RANK() OVER (ORDER BY lead_time_cv DESC)                    AS variability_rank,
    supplier_id,
    region,
    po_lines,
    ROUND(avg_lead_time_days, 1)                                AS avg_lead_time_days,
    ROUND(lead_time_std_days, 1)                                AS lead_time_std_days,
    ROUND(lead_time_cv, 2)                                      AS lead_time_cv,
    CASE WHEN lead_time_cv > prm.lead_time_cv_high THEN 'HIGH' ELSE 'NORMAL' END
                                                                AS variability_flag
FROM analytics.v_supplier_performance
CROSS JOIN analytics.v_parameters AS prm
WHERE enough_history
ORDER BY lead_time_cv DESC
LIMIT 15;


-- name: q07_excess_by_category
-- Question: Which product categories hold the most excess (screening) inventory?
-- Why: Excess is cash on the shelf that costs ~25% a year to hold. Category
--      totals point category managers to where reductions are possible.
-- Technique: GROUP BY with conditional aggregation (COUNT FILTER); window
--            SUM for share of all excess; RANK.
-- Screening rule: stock above `high_cover_months` of trailing demand.
SELECT
    RANK() OVER (ORDER BY SUM(high_cover_excess_value) DESC)    AS excess_rank,
    category,
    COUNT(*)                                                    AS skus,
    COUNT(*) FILTER (WHERE high_cover)                          AS skus_with_excess,
    ROUND(SUM(inventory_value), 0)                              AS inventory_value,
    ROUND(SUM(high_cover_excess_value), 0)                      AS excess_value,
    ROUND(100 * SUM(high_cover_excess_value) / NULLIF(SUM(inventory_value), 0), 1)
                                                                AS excess_pct_of_category,
    ROUND(100 * SUM(high_cover_excess_value) / SUM(SUM(high_cover_excess_value)) OVER (), 1)
                                                                AS pct_of_all_excess
FROM analytics.v_inventory_position
GROUP BY category
ORDER BY excess_value DESC;


-- name: q08_no_demand_six_months
-- Question: Which SKUs have stock but no customer demand for six months?
-- Why: Dead-stock candidates: capital at risk of write-off. Items still marked
--      ACTIVE, or still with open POs, show where master data and buying
--      have not caught up with reality.
-- Technique: filter on derived flag; months since last demand via AGE().
SELECT
    sku_id,
    category,
    supplier_id,
    lifecycle_status,
    on_hand_qty,
    ROUND(inventory_value, 2)                                   AS inventory_value,
    last_demand_month,
    EXTRACT(YEAR FROM AGE(as_of_date, last_demand_month)) * 12
        + EXTRACT(MONTH FROM AGE(as_of_date, last_demand_month)) AS months_since_last_demand,
    open_po_qty                                                 AS still_on_order,
    COUNT(*) OVER ()                                            AS dead_stock_candidates,
    ROUND(SUM(inventory_value) OVER (), 0)                      AS total_dead_stock_value
FROM analytics.v_inventory_position
WHERE dead_stock_candidate
ORDER BY inventory_value DESC
LIMIT 25;


-- name: q09_supplier_spend_pareto
-- Question: Which suppliers account for the greatest purchase spend?
-- Why: Spend concentration shows negotiating leverage and supply risk: if a
--      few suppliers carry most of the spend, their performance matters most.
-- Technique: running SUM ordered by spend (cumulative share = Pareto curve).
SELECT
    spend_rank,
    supplier_id,
    region,
    skus_purchased,
    ROUND(spend, 0)                                             AS spend,
    ROUND(100 * spend_share, 1)                                 AS spend_share_pct,
    ROUND(100 * SUM(spend_share) OVER (ORDER BY spend DESC
                                       ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW), 1)
                                                                AS cumulative_share_pct
FROM analytics.v_supplier_performance
ORDER BY spend DESC;


-- name: q10_unusually_high_inventory
-- Question: Which SKUs hold unusually high inventory relative to their demand?
-- Why: "High" depends on the category (fasteners turn faster than power
--      tools). Comparing each SKU with its own category avoids flagging
--      items that are normal for their category.
-- Technique: PERCENT_RANK within category; category median via
--            PERCENTILE_CONT; both conditions must hold.
WITH ranked AS (
    SELECT
        v.*,
        PERCENT_RANK() OVER (PARTITION BY category ORDER BY months_of_supply) AS pct_rank_in_category
    FROM analytics.v_inventory_position AS v
    WHERE avg_monthly_demand > 0
),
category_median AS (
    SELECT category,
           PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY months_of_supply) AS median_months_of_supply
    FROM ranked
    GROUP BY category
)
SELECT
    r.sku_id,
    r.category,
    r.on_hand_qty,
    ROUND(r.avg_monthly_demand, 1)                              AS avg_monthly_demand,
    ROUND(r.months_of_supply, 1)                                AS months_of_supply,
    ROUND(c.median_months_of_supply::numeric, 1)                AS category_median_months,
    ROUND(r.pct_rank_in_category::numeric, 3)                   AS pct_rank_in_category,
    ROUND(r.high_cover_excess_value, 2)                         AS excess_value
FROM ranked AS r
JOIN category_median AS c USING (category)
CROSS JOIN analytics.v_parameters AS prm
WHERE r.pct_rank_in_category >= 0.95
  AND r.months_of_supply > prm.high_cover_months
ORDER BY r.high_cover_excess_value DESC
LIMIT 25;


-- name: q11_open_pos_creating_excess
-- Question: Which open POs are most likely to create excess inventory?
-- Why: Cash not yet spent is the easiest to save: these lines can still be
--      pushed out, reduced or cancelled. MOQ-driven lines show where supplier
--      terms, not demand, set the quantity.
-- Technique: JOIN PO lines to the SKU position; LEAST/GREATEST to attribute
--            only the part of each line that pushes cover above the limit.
SELECT
    l.po_line_id,
    l.sku_id,
    l.supplier_id,
    l.order_date,
    l.promised_date,
    l.open_qty,
    p.on_hand_qty,
    ROUND(p.avg_monthly_demand, 1)                              AS avg_monthly_demand,
    ROUND(p.months_of_supply_incl_open_po, 1)                   AS months_cover_after_receipt,
    LEAST(l.open_qty,
          GREATEST(p.inventory_position - prm.high_cover_months * p.avg_monthly_demand, 0))
                                                                AS excess_qty_from_line,
    ROUND(LEAST(l.open_qty,
                GREATEST(p.inventory_position - prm.high_cover_months * p.avg_monthly_demand, 0))
          * l.unit_price, 2)                                    AS excess_value_from_line,
    l.ordered_qty <= p.moq                                      AS ordered_at_moq,
    l.is_stale
FROM analytics.v_po_line_status AS l
JOIN analytics.v_inventory_position AS p USING (sku_id)
CROSS JOIN analytics.v_parameters AS prm
WHERE l.is_open
  AND p.inventory_position > prm.high_cover_months * p.avg_monthly_demand
ORDER BY excess_value_from_line DESC
LIMIT 25;


-- name: q12_stockouts_despite_open_pos
-- Question: Which SKUs ran out of stock even though a PO was already open, and
--           was that the supplier's fault or ours?
-- Why: Separates root causes. SUPPLIER_LATE -> supplier management;
--      ORDERED_TOO_LATE_OR_TOO_LITTLE -> planning parameters. Treating every
--      stockout as a supplier problem hides planning errors (and vice versa).
-- Rule: SUPPLIER_LATE only if the supplier had promised delivery by the end
--      of the stockout month AND missed that promise. If the promise was
--      after the month, the PO was simply placed too late (or too small).
-- Technique: correlated JOIN of stockout months to PO lines open at the start
--            of that month; DISTINCT ON to keep the first-promised line;
--            window COUNT/SUM to show the root-cause split on every row.
WITH stockout_months AS (
    SELECT sku_id, month_start, demand_qty, shipped_qty, lost_qty, lost_revenue
    FROM analytics.v_demand_monthly
    WHERE stockout_month
),
open_at_month_start AS (
    SELECT DISTINCT ON (s.sku_id, s.month_start)
        s.*,
        l.po_line_id,
        l.supplier_id,
        l.order_date,
        l.promised_date,
        l.last_receipt_date,
        CASE
            WHEN l.promised_date + prm.on_time_tolerance_days::int
                     <= (s.month_start + INTERVAL '1 month - 1 day')::date
             AND COALESCE(l.last_receipt_date, a.as_of_date)
                     > l.promised_date + prm.on_time_tolerance_days::int
                THEN 'SUPPLIER_LATE'
            ELSE 'ORDERED_TOO_LATE_OR_TOO_LITTLE'
        END AS root_cause
    FROM stockout_months AS s
    JOIN analytics.v_po_line_status AS l
        ON  l.sku_id = s.sku_id
        AND l.order_date < s.month_start                                  -- ordered before the month
        AND (l.last_receipt_date IS NULL OR l.last_receipt_date >= s.month_start)  -- not yet received
        AND l.status <> 'CLOSED_SHORT'
    CROSS JOIN analytics.v_parameters AS prm
    CROSS JOIN analytics.v_as_of AS a
    ORDER BY s.sku_id, s.month_start, l.promised_date
)
SELECT
    sku_id,
    month_start,
    demand_qty,
    shipped_qty,
    ROUND(lost_revenue, 2)                                      AS lost_revenue,
    po_line_id,
    supplier_id,
    promised_date,
    last_receipt_date,
    root_cause,
    COUNT(*) OVER (PARTITION BY root_cause)                     AS stockout_months_with_this_cause,
    ROUND(SUM(lost_revenue) OVER (PARTITION BY root_cause), 0)  AS lost_revenue_with_this_cause
FROM open_at_month_start
ORDER BY lost_revenue DESC
LIMIT 25;


-- name: q13_inventory_value_trend
-- Question: How has total inventory value changed month over month?
-- Why: Finance tracks inventory as working capital; a rising trend with flat
--      sales means cash is being absorbed. Days of inventory translates
--      value into time.
-- Technique: LAG for month-over-month change; moving AVG over 3 rows; running
--            MAX to show how far below the peak we are.
WITH monthly AS (
    SELECT
        i.month_end,
        SUM(i.inventory_value)                                  AS inventory_value,
        SUM(d.cogs)                                             AS cogs,
        SUM(d.revenue)                                          AS revenue
    FROM analytics.v_inventory_monthly AS i
    JOIN analytics.v_demand_monthly AS d
        ON d.sku_id = i.sku_id AND d.month_start = i.month_start
    GROUP BY i.month_end
)
SELECT
    month_end,
    ROUND(inventory_value, 0)                                   AS inventory_value,
    ROUND(inventory_value - LAG(inventory_value) OVER w, 0)     AS mom_change,
    ROUND(100 * (inventory_value / LAG(inventory_value) OVER w - 1), 1) AS mom_change_pct,
    ROUND(AVG(inventory_value) OVER (w ROWS BETWEEN 2 PRECEDING AND CURRENT ROW), 0)
                                                                AS rolling_3m_avg_value,
    ROUND(MAX(inventory_value) OVER (w ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW), 0)
                                                                AS running_peak_value,
    ROUND(inventory_value / NULLIF(cogs, 0) * (month_end - date_trunc('month', month_end)::date + 1), 0)
                                                                AS days_of_inventory
FROM monthly
WINDOW w AS (ORDER BY month_end)
ORDER BY month_end;


-- name: q14_inventory_value_concentration
-- Question: What share of inventory value sits in the highest-value SKUs?
-- Why: If a small share of SKUs holds most of the value, cycle counting,
--      review frequency and reduction projects should focus there (the
--      inventory-side view of ABC thinking).
-- Technique: NTILE(10) deciles by value; cumulative SUM over deciles.
WITH deciles AS (
    SELECT
        NTILE(10) OVER (ORDER BY inventory_value DESC)          AS value_decile,
        inventory_value
    FROM analytics.v_inventory_position
    WHERE on_hand_qty > 0
)
SELECT
    value_decile,
    COUNT(*)                                                    AS skus,
    ROUND(SUM(inventory_value), 0)                              AS inventory_value,
    ROUND(100 * SUM(inventory_value) / SUM(SUM(inventory_value)) OVER (), 1) AS pct_of_value,
    ROUND(100 * SUM(SUM(inventory_value)) OVER (ORDER BY value_decile)
              / SUM(SUM(inventory_value)) OVER (), 1)           AS cumulative_pct_of_value
FROM deciles
GROUP BY value_decile
ORDER BY value_decile;


-- name: q15_high_spend_poor_delivery
-- Question: Which suppliers combine high spend with poor delivery performance?
-- Why: These are the escalation priorities: large enough to matter, unreliable
--      enough to cost safety stock and stockouts. Linking to the stockout
--      months of their SKUs shows the service impact.
-- Technique: NTILE(4) for spend quartile; CASE segmentation using configured
--            thresholds; LEFT JOIN to SKU-level stockout counts.
WITH supplier_stockouts AS (
    SELECT supplier_id,
           SUM(stockout_months_12m)                             AS stockout_months_12m,
           COUNT(*) FILTER (WHERE out_of_stock)                 AS skus_out_of_stock_now
    FROM analytics.v_inventory_position
    GROUP BY supplier_id
),
scored AS (
    SELECT
        sp.*,
        NTILE(4) OVER (ORDER BY sp.spend DESC)                  AS spend_quartile
    FROM analytics.v_supplier_performance AS sp
)
SELECT
    s.supplier_id,
    s.region,
    ROUND(s.spend, 0)                                           AS spend,
    ROUND(100 * s.spend_share, 1)                               AS spend_share_pct,
    ROUND(100 * s.otif_pct, 1)                                  AS otif_pct,
    ROUND(s.lead_time_cv, 2)                                    AS lead_time_cv,
    CASE
        WHEN s.otif_pct < prm.otif_watch THEN 'AT_RISK'
        WHEN s.otif_pct < prm.otif_target OR s.lead_time_cv > prm.lead_time_cv_high THEN 'WATCH'
        ELSE 'RELIABLE'
    END                                                         AS delivery_segment,
    st.stockout_months_12m                                      AS sku_stockout_months_12m,
    st.skus_out_of_stock_now
FROM scored AS s
JOIN supplier_stockouts AS st USING (supplier_id)
CROSS JOIN analytics.v_parameters AS prm
WHERE s.spend_quartile = 1
  AND s.otif_pct < prm.otif_target
  AND s.enough_history
ORDER BY s.spend DESC;
