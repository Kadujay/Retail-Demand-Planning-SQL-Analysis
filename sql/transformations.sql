-- =============================================================================
-- transformations.sql — Analytical views (core -> analytics)
-- =============================================================================
-- Each view turns operational facts into an analysis-ready dataset and states
-- the business question it answers. Views (not tables) keep lineage simple:
-- they always reflect the current core data and cannot drift from it.
--
--   View                           Grain                     Answers
--   v_parameters                   one row                   Which thresholds are in force?
--   v_as_of                        one row                   What date is "today" for this extract?
--   v_demand_monthly               SKU x month               What did customers want vs. get? (demand, shipments,
--                                                            lost sales, rolling demand, stockout months)
--   v_receipts_monthly             SKU x month               What arrived from suppliers each month?
--   v_inventory_monthly            SKU x month end           What was in stock, what was it worth, and does the
--                                                            stock flow reconcile?
--   v_po_line_status               PO line                   What is open, late, complete? Lead time, OTIF per line
--   v_supplier_performance         supplier                  How reliable and how important is each supplier?
--   v_inventory_position           SKU (as of today)         Position, value, days of supply, screening ROP,
--                                                            dead-stock and high-cover flags
--
-- Assumption for window functions: SKU-month panels are dense from the launch
-- month (every month has a row, zero-demand months included), so ROWS-based
-- windows equal calendar-month windows. The Phase 2 tests guarantee this.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- Parameters and as-of date
-- -----------------------------------------------------------------------------
-- Conditional aggregation pivots the name/value parameter table into one row,
-- so every view can CROSS JOIN it and use named columns.
CREATE OR REPLACE VIEW analytics.v_parameters AS
SELECT
    MAX(value) FILTER (WHERE name = 'days_per_month')              AS days_per_month,
    MAX(value) FILTER (WHERE name = 'review_period_days')          AS review_period_days,
    MAX(value) FILTER (WHERE name = 'trailing_demand_months')      AS trailing_demand_months,
    MAX(value) FILTER (WHERE name = 'screening_service_level_z')   AS screening_z,
    MAX(value) FILTER (WHERE name = 'high_cover_months')           AS high_cover_months,
    MAX(value) FILTER (WHERE name = 'dead_stock_months')           AS dead_stock_months,
    MAX(value) FILTER (WHERE name = 'on_time_tolerance_days')      AS on_time_tolerance_days,
    MAX(value) FILTER (WHERE name = 'in_full_tolerance')           AS in_full_tolerance,
    MAX(value) FILTER (WHERE name = 'stale_open_po_days')          AS stale_open_po_days,
    MAX(value) FILTER (WHERE name = 'otif_target')                 AS otif_target,
    MAX(value) FILTER (WHERE name = 'otif_watch')                  AS otif_watch,
    MAX(value) FILTER (WHERE name = 'lead_time_cv_high')           AS lead_time_cv_high,
    MAX(value) FILTER (WHERE name = 'min_lines_for_supplier_stats') AS min_lines_for_supplier_stats
FROM analytics.planning_parameter;

-- The extract's "today" is the latest month-end snapshot, never now():
-- results must be reproducible whenever the queries are run.
CREATE OR REPLACE VIEW analytics.v_as_of AS
SELECT
    MAX(month_end)                              AS as_of_date,
    date_trunc('month', MAX(month_end))::date   AS as_of_month_start
FROM core.fact_inventory;


-- -----------------------------------------------------------------------------
-- 1, 2, 6, 10. Monthly demand, shipments, rolling demand, stockout months
-- Business question: per SKU and month, what did customers order, what did we
-- ship, what did we lose, and what is the recent demand trend?
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW analytics.v_demand_monthly AS
SELECT
    d.sku_id,
    p.category,
    p.supplier_id,
    d.month_start,
    d.ordered_qty                                           AS demand_qty,       -- customer demand
    d.shipped_qty,                                                               -- customer shipments
    d.ordered_qty - d.shipped_qty                           AS lost_qty,         -- unfilled = lost sales
    d.shipped_qty < d.ordered_qty                           AS stockout_month,   -- demand not fully served
    d.shipped_qty::numeric / NULLIF(d.ordered_qty, 0)       AS fill_rate,
    d.ordered_qty * p.unit_cost                             AS demand_value_at_cost,
    d.shipped_qty * p.unit_price                            AS revenue,
    d.shipped_qty * p.unit_cost                             AS cogs,
    (d.ordered_qty - d.shipped_qty) * p.unit_price          AS lost_revenue,
    -- Rolling demand: smooths noise to show the underlying rate.
    AVG(d.ordered_qty) OVER w3                              AS rolling_3m_avg_demand,
    AVG(d.ordered_qty) OVER w6                              AS rolling_6m_avg_demand,
    SUM(d.ordered_qty) OVER w12                             AS rolling_12m_demand,
    COUNT(*)           OVER w12                             AS months_in_12m_window,  -- < 12 for new SKUs
    -- Month-over-month change and year-to-date cumulative demand.
    d.ordered_qty - LAG(d.ordered_qty) OVER (PARTITION BY d.sku_id ORDER BY d.month_start)
                                                            AS demand_mom_change,
    SUM(d.ordered_qty) OVER (
        PARTITION BY d.sku_id, EXTRACT(YEAR FROM d.month_start)
        ORDER BY d.month_start
    )                                                       AS demand_ytd
FROM core.fact_demand AS d
JOIN core.dim_product AS p USING (sku_id)
WINDOW
    w3  AS (PARTITION BY d.sku_id ORDER BY d.month_start ROWS BETWEEN 2 PRECEDING AND CURRENT ROW),
    w6  AS (PARTITION BY d.sku_id ORDER BY d.month_start ROWS BETWEEN 5 PRECEDING AND CURRENT ROW),
    w12 AS (PARTITION BY d.sku_id ORDER BY d.month_start ROWS BETWEEN 11 PRECEDING AND CURRENT ROW);


-- -----------------------------------------------------------------------------
-- Receipts per SKU and month (received purchase orders)
-- Business question: how much stock did suppliers actually deliver each month?
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW analytics.v_receipts_monthly AS
SELECT
    po.sku_id,
    date_trunc('month', r.receipt_date)::date   AS month_start,
    SUM(r.received_qty)                         AS received_qty,
    SUM(r.received_qty * po.unit_price)         AS received_value,
    COUNT(*)                                    AS receipt_count
FROM core.fact_supplier_delivery AS r
JOIN core.fact_purchase_order AS po USING (po_line_id)
GROUP BY po.sku_id, date_trunc('month', r.receipt_date);


-- -----------------------------------------------------------------------------
-- 4, 10. Monthly inventory, value and stock-flow reconciliation
-- Business question: what was in stock each month end, what was it worth, and
-- does opening + receipts - shipments = closing hold (proof the data is sound)?
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW analytics.v_inventory_monthly AS
SELECT
    i.sku_id,
    p.category,
    p.supplier_id,
    i.month_end,
    dm.month_start,
    LAG(i.on_hand_qty) OVER (PARTITION BY i.sku_id ORDER BY i.month_end)  AS opening_qty,
    COALESCE(r.received_qty, 0)                                          AS received_qty,
    dm.shipped_qty,
    i.on_hand_qty                                                        AS closing_qty,
    i.allocated_qty,
    i.on_hand_qty * p.unit_cost                                          AS inventory_value,
    i.on_hand_qty = 0                                                    AS zero_stock_at_month_end,
    dm.stockout_month,
    -- 0 when the stock flow reconciles; NULL in the SKU's first month (no opening).
    LAG(i.on_hand_qty) OVER (PARTITION BY i.sku_id ORDER BY i.month_end)
        + COALESCE(r.received_qty, 0) - dm.shipped_qty - i.on_hand_qty  AS balance_difference
FROM core.fact_inventory AS i
JOIN core.dim_product AS p USING (sku_id)
JOIN analytics.v_demand_monthly AS dm
    ON dm.sku_id = i.sku_id AND dm.month_start = date_trunc('month', i.month_end)::date
LEFT JOIN analytics.v_receipts_monthly AS r
    ON r.sku_id = i.sku_id AND r.month_start = dm.month_start;


-- -----------------------------------------------------------------------------
-- 8, 9. Purchase-order line status, lead time and OTIF
-- Business question: for each PO line, how much is still open, how long did it
-- take, and did the supplier deliver on time and in full?
--
-- OTIF (methodology §12): a line is OTIF when the quantity received by the
-- ORIGINAL promised date (+ tolerance) covers the ordered quantity. Split
-- deliveries are summed. Lines whose promised date has passed but are still
-- open count as failures, otherwise late suppliers would look better simply
-- because their late lines are not closed yet.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW analytics.v_po_line_status AS
WITH receipts AS (
    SELECT
        r.po_line_id,
        SUM(r.received_qty)                         AS received_qty,
        COUNT(*)                                    AS receipt_count,
        MIN(r.receipt_date)                         AS first_receipt_date,
        MAX(r.receipt_date)                         AS last_receipt_date,
        -- Conditional aggregation: only receipts that arrived by the cutoff.
        SUM(r.received_qty) FILTER (
            WHERE r.receipt_date <= po.promised_date + prm.on_time_tolerance_days::int
        )                                           AS received_by_cutoff
    FROM core.fact_supplier_delivery AS r
    JOIN core.fact_purchase_order AS po USING (po_line_id)
    CROSS JOIN analytics.v_parameters AS prm
    GROUP BY r.po_line_id
),
lines AS (
    SELECT
        po.*,
        COALESCE(r.received_qty, 0)                 AS received_qty,
        COALESCE(r.receipt_count, 0)                AS receipt_count,
        r.first_receipt_date,
        r.last_receipt_date,
        COALESCE(r.received_by_cutoff, 0)           AS received_by_cutoff,
        po.status IN ('OPEN', 'PARTIALLY_RECEIVED') AS is_open,
        po.promised_date + prm.on_time_tolerance_days::int AS on_time_cutoff,
        prm.in_full_tolerance,
        prm.stale_open_po_days,
        a.as_of_date
    FROM core.fact_purchase_order AS po
    LEFT JOIN receipts AS r USING (po_line_id)
    CROSS JOIN analytics.v_parameters AS prm
    CROSS JOIN analytics.v_as_of AS a
)
SELECT
    po_line_id, po_number, sku_id, supplier_id, order_date, promised_date,
    ordered_qty, unit_price, status, is_open,
    received_qty,
    receipt_count,
    receipt_count > 1                                           AS split_delivery,
    first_receipt_date,
    last_receipt_date,
    CASE WHEN is_open THEN GREATEST(ordered_qty - received_qty, 0) ELSE 0 END AS open_qty,
    CASE WHEN is_open THEN GREATEST(ordered_qty - received_qty, 0) * unit_price ELSE 0 END
                                                                AS open_value,
    received_qty * unit_price                                   AS received_value,
    ordered_qty * unit_price                                    AS ordered_value,
    -- Lead time is only final once the line is complete (closed or closed short).
    CASE WHEN status IN ('CLOSED', 'CLOSED_SHORT') THEN last_receipt_date - order_date END
                                                                AS actual_lead_time_days,
    promised_date - order_date                                  AS promised_lead_time_days,
    CASE WHEN status IN ('CLOSED', 'CLOSED_SHORT')
         THEN GREATEST(last_receipt_date - promised_date, 0) END AS days_late,
    -- Evaluable = the outcome is known: line is complete or its cutoff has passed.
    (status IN ('CLOSED', 'CLOSED_SHORT') OR on_time_cutoff < as_of_date) AS otif_evaluable,
    CASE WHEN status IN ('CLOSED', 'CLOSED_SHORT') OR on_time_cutoff < as_of_date
         THEN received_by_cutoff >= ordered_qty * in_full_tolerance END    AS otif,
    CASE WHEN status IN ('CLOSED', 'CLOSED_SHORT') OR on_time_cutoff < as_of_date
         THEN received_qty >= ordered_qty * in_full_tolerance END          AS in_full,
    CASE WHEN is_open THEN as_of_date - order_date END          AS open_age_days,
    is_open AND promised_date < as_of_date - stale_open_po_days::int AS is_stale
FROM lines;


-- -----------------------------------------------------------------------------
-- 7, 8. Supplier performance scorecard
-- Business question: how important (spend) and how reliable (OTIF, lead-time
-- level and variability, fill rate, price) is each supplier?
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW analytics.v_supplier_performance AS
WITH per_supplier AS (
    SELECT
        l.supplier_id,
        COUNT(*)                                                    AS po_lines,
        COUNT(DISTINCT l.sku_id)                                    AS skus_purchased,
        SUM(l.received_value)                                       AS spend,
        SUM(l.open_value)                                           AS open_po_value,
        COUNT(*) FILTER (WHERE l.otif_evaluable)                    AS otif_lines,
        AVG(l.otif::int) FILTER (WHERE l.otif_evaluable)            AS otif_pct,
        SUM(l.received_qty) FILTER (WHERE l.status IN ('CLOSED', 'CLOSED_SHORT'))::numeric
            / NULLIF(SUM(l.ordered_qty) FILTER (WHERE l.status IN ('CLOSED', 'CLOSED_SHORT')), 0)
                                                                    AS fill_rate,
        AVG(l.split_delivery::int)                                  AS split_delivery_pct,
        AVG(l.actual_lead_time_days)                                AS avg_lead_time_days,
        STDDEV_SAMP(l.actual_lead_time_days)                        AS lead_time_std_days,
        PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY l.actual_lead_time_days) AS median_lead_time_days,
        PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY l.actual_lead_time_days) AS p90_lead_time_days,
        AVG(l.promised_lead_time_days)                              AS avg_promised_lead_time_days,
        AVG(l.days_late)                                            AS avg_days_late,
        -- Purchase price variance: paid vs. standard cost, on received quantity.
        SUM((l.unit_price - p.unit_cost) * l.received_qty)          AS purchase_price_variance
    FROM analytics.v_po_line_status AS l
    JOIN core.dim_product AS p USING (sku_id)
    GROUP BY l.supplier_id
)
SELECT
    s.supplier_id,
    s.supplier_name,
    s.region,
    s.quoted_lead_time_days,
    ps.po_lines, ps.skus_purchased, ps.spend, ps.open_po_value,
    ps.otif_lines, ps.otif_pct, ps.fill_rate, ps.split_delivery_pct,
    ps.avg_lead_time_days, ps.lead_time_std_days, ps.median_lead_time_days,
    ps.p90_lead_time_days, ps.avg_promised_lead_time_days, ps.avg_days_late,
    ps.purchase_price_variance,
    ps.spend / SUM(ps.spend) OVER ()                                AS spend_share,
    RANK() OVER (ORDER BY ps.spend DESC)                            AS spend_rank,
    ps.lead_time_std_days / NULLIF(ps.avg_lead_time_days, 0)        AS lead_time_cv,
    ps.otif_lines >= prm.min_lines_for_supplier_stats               AS enough_history
FROM core.dim_supplier AS s
JOIN per_supplier AS ps USING (supplier_id)
CROSS JOIN analytics.v_parameters AS prm;


-- -----------------------------------------------------------------------------
-- 3, 4, 5, 10. Current inventory position per SKU
-- Business question: for every SKU today, what supply do we have (on hand +
-- credible open POs - allocated), what is it worth, how long will it last, and does a
-- simple screen say we should reorder, or that stock is excessive or dead?
--
-- The screening reorder point uses the textbook demand-only formula with ONE
-- service level for all SKUs:
--     P   = (lead time + review period) / days per month      [months]
--     SS  = z x sigma(monthly demand, trailing window) x sqrt(P)
--     ROP = average monthly demand x P + SS
-- It is a first-pass screen. Phase 7 replaces it with the class-based policy
-- (forecast-error sigma, lead-time variability) in docs/methodology.md.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW analytics.v_inventory_position AS
WITH prm AS (SELECT * FROM analytics.v_parameters),
asof AS (SELECT * FROM analytics.v_as_of),
latest_stock AS (
    SELECT i.sku_id, i.on_hand_qty, i.allocated_qty
    FROM core.fact_inventory AS i
    JOIN asof ON i.month_end = asof.as_of_date
),
-- Stale lines (open long past their promise) are NOT counted as supply: a
-- planner would not rely on them, and counting them would suppress reorders.
-- They are reported separately so someone chases or cancels them.
open_supply AS (
    SELECT
        sku_id,
        SUM(open_qty)   FILTER (WHERE NOT is_stale) AS open_po_qty,
        SUM(open_value) FILTER (WHERE NOT is_stale) AS open_po_value,
        SUM(open_qty)   FILTER (WHERE is_stale)     AS stale_open_po_qty,
        MIN(promised_date) FILTER (WHERE NOT is_stale) AS next_promised_date
    FROM analytics.v_po_line_status
    WHERE is_open
    GROUP BY sku_id
),
demand_stats AS (
    SELECT
        d.sku_id,
        AVG(d.ordered_qty) FILTER (
            WHERE d.month_start > asof.as_of_month_start - make_interval(months => prm.trailing_demand_months::int)
        )                                           AS avg_monthly_demand,
        STDDEV_SAMP(d.ordered_qty) FILTER (
            WHERE d.month_start > asof.as_of_month_start - make_interval(months => prm.trailing_demand_months::int)
        )                                           AS demand_std_monthly,
        SUM(d.ordered_qty) FILTER (
            WHERE d.month_start > asof.as_of_month_start - make_interval(months => prm.dead_stock_months::int)
        )                                           AS demand_dead_stock_window,
        SUM(d.ordered_qty) FILTER (WHERE d.month_start > asof.as_of_month_start - INTERVAL '12 months')
                                                    AS demand_12m,
        COUNT(*) FILTER (
            WHERE d.shipped_qty < d.ordered_qty
              AND d.month_start > asof.as_of_month_start - INTERVAL '12 months'
        )                                           AS stockout_months_12m,
        MAX(d.month_start) FILTER (WHERE d.ordered_qty > 0) AS last_demand_month
    FROM core.fact_demand AS d
    CROSS JOIN asof
    CROSS JOIN prm
    GROUP BY d.sku_id
),
base AS (
    SELECT
        p.sku_id, p.category, p.supplier_id, p.lifecycle_status,
        p.unit_cost, p.unit_price, p.moq, p.order_multiple,
        p.supplier_lead_time_days,
        COALESCE(s.on_hand_qty, 0)                  AS on_hand_qty,
        COALESCE(s.allocated_qty, 0)                AS allocated_qty,
        COALESCE(o.open_po_qty, 0)                  AS open_po_qty,
        COALESCE(o.open_po_value, 0)                AS open_po_value,
        COALESCE(o.stale_open_po_qty, 0)            AS stale_open_po_qty,
        o.next_promised_date,
        COALESCE(ds.avg_monthly_demand, 0)          AS avg_monthly_demand,
        COALESCE(ds.demand_std_monthly, 0)          AS demand_std_monthly,
        COALESCE(ds.demand_dead_stock_window, 0)    AS demand_dead_stock_window,
        COALESCE(ds.demand_12m, 0)                  AS demand_12m,
        COALESCE(ds.stockout_months_12m, 0)         AS stockout_months_12m,
        ds.last_demand_month,
        (p.supplier_lead_time_days + prm.review_period_days) / prm.days_per_month
                                                    AS protection_months,
        prm.screening_z, prm.days_per_month, prm.high_cover_months,
        asof.as_of_date, asof.as_of_month_start
    FROM core.dim_product AS p
    LEFT JOIN latest_stock AS s USING (sku_id)
    LEFT JOIN open_supply AS o USING (sku_id)
    LEFT JOIN demand_stats AS ds USING (sku_id)
    CROSS JOIN prm
    CROSS JOIN asof
),
calc AS (
    SELECT
        b.*,
        b.on_hand_qty + b.open_po_qty - b.allocated_qty             AS inventory_position,
        b.on_hand_qty * b.unit_cost                                 AS inventory_value,
        b.avg_monthly_demand / b.days_per_month                     AS avg_daily_demand,
        b.screening_z * b.demand_std_monthly * SQRT(b.protection_months) AS screening_safety_stock
    FROM base AS b
)
SELECT
    sku_id, category, supplier_id, lifecycle_status, unit_cost, unit_price,
    moq, order_multiple, supplier_lead_time_days,
    on_hand_qty, allocated_qty, open_po_qty, stale_open_po_qty, next_promised_date,
    inventory_position,
    inventory_value,
    open_po_value,
    avg_monthly_demand,
    demand_std_monthly,
    avg_daily_demand,
    demand_12m,
    stockout_months_12m,
    last_demand_month,
    on_hand_qty / NULLIF(avg_daily_demand, 0)                       AS days_of_supply,
    on_hand_qty / NULLIF(avg_monthly_demand, 0)                     AS months_of_supply,
    inventory_position / NULLIF(avg_monthly_demand, 0)              AS months_of_supply_incl_open_po,
    screening_safety_stock,
    avg_monthly_demand * protection_months + screening_safety_stock AS screening_reorder_point,
    avg_monthly_demand > 0
        AND inventory_position < avg_monthly_demand * protection_months + screening_safety_stock
                                                                    AS below_screening_rop,
    avg_monthly_demand > 0 AND on_hand_qty > high_cover_months * avg_monthly_demand
                                                                    AS high_cover,
    GREATEST(on_hand_qty - high_cover_months * avg_monthly_demand, 0)  AS high_cover_excess_qty,
    GREATEST(on_hand_qty - high_cover_months * avg_monthly_demand, 0) * unit_cost
                                                                    AS high_cover_excess_value,
    on_hand_qty > 0 AND demand_dead_stock_window = 0                AS dead_stock_candidate,
    on_hand_qty = 0 AND avg_monthly_demand > 0                      AS out_of_stock,
    as_of_date
FROM calc;
