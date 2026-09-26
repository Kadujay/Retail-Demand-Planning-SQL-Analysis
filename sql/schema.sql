-- =============================================================================
-- schema.sql — PostgreSQL data model for the Inventory Control Tower
-- =============================================================================
-- Three schemas make data lineage visible inside the database:
--
--   core       Validated operational data, loaded 1:1 from the raw CSV extract.
--              Only facts that happened (orders, shipments, stock, POs,
--              receipts) and master data. NO derived classifications
--              (ABC, XYZ, health status) are stored here.
--   analytics  Views derived from core (sql/transformations.sql) plus the
--              planning parameters they use. Rebuildable at any time.
--   audit      Record of every load attempt and its data-quality results.
--
-- Constraints encode only rules that must ALWAYS hold. Business rules that can
-- legitimately be broken in real operations (price below cost, allocated >
-- on hand, missing optional attributes) are WARNINGS in the Python validation
-- layer instead, so warning-level data can still be loaded.
--
-- Re-running this file drops and recreates all three schemas. That is safe:
-- every table is reproducible from the seed.
-- =============================================================================

DROP SCHEMA IF EXISTS analytics CASCADE;
DROP SCHEMA IF EXISTS core CASCADE;
DROP SCHEMA IF EXISTS audit CASCADE;

CREATE SCHEMA core;
CREATE SCHEMA analytics;
CREATE SCHEMA audit;

-- -----------------------------------------------------------------------------
-- Dimensions
-- -----------------------------------------------------------------------------

-- One row = one calendar day. Monthly facts join on the first / last day of the
-- month; PO and receipt facts join on their event dates.
CREATE TABLE core.dim_date (
    date_key        date        PRIMARY KEY,
    year            smallint    NOT NULL,
    quarter         smallint    NOT NULL CHECK (quarter BETWEEN 1 AND 4),
    month           smallint    NOT NULL CHECK (month BETWEEN 1 AND 12),
    month_name      text        NOT NULL,
    month_start     date        NOT NULL,
    month_end       date        NOT NULL,
    is_month_end    boolean     NOT NULL,
    iso_week        smallint    NOT NULL,
    day_of_week     smallint    NOT NULL CHECK (day_of_week BETWEEN 1 AND 7)  -- 1 = Monday
);

-- One row = one supplier.
CREATE TABLE core.dim_supplier (
    supplier_id             text        PRIMARY KEY,
    supplier_name           text        NOT NULL,
    region                  text        CHECK (region IN ('Domestic', 'Import')),  -- optional attribute
    quoted_lead_time_days   integer     NOT NULL CHECK (quoted_lead_time_days BETWEEN 1 AND 365)
);

-- One row = one SKU (item master).
CREATE TABLE core.dim_product (
    sku_id                  text            PRIMARY KEY,
    category                text            NOT NULL,
    supplier_id             text            NOT NULL REFERENCES core.dim_supplier (supplier_id),
    unit_cost               numeric(12, 2)  NOT NULL CHECK (unit_cost > 0),
    unit_price              numeric(12, 2)  NOT NULL CHECK (unit_price > 0),
    moq                     integer         NOT NULL CHECK (moq >= 1),
    order_multiple          integer         NOT NULL CHECK (order_multiple >= 1),
    supplier_lead_time_days integer         NOT NULL CHECK (supplier_lead_time_days BETWEEN 1 AND 365),
    launch_date             date,           -- optional attribute
    lifecycle_status        text            CHECK (lifecycle_status IN ('NEW', 'ACTIVE', 'DISCONTINUED'))
);

-- -----------------------------------------------------------------------------
-- Facts
-- -----------------------------------------------------------------------------

-- One row = one SKU in one calendar month (from the SKU's launch month).
-- ordered_qty is customer DEMAND; shipped_qty is what was delivered. They are
-- kept separate because stockouts make shipments understate demand.
CREATE TABLE core.fact_demand (
    sku_id          text        NOT NULL REFERENCES core.dim_product (sku_id),
    month_start     date        NOT NULL REFERENCES core.dim_date (date_key),
    ordered_qty     integer     NOT NULL CHECK (ordered_qty >= 0),
    shipped_qty     integer     NOT NULL CHECK (shipped_qty >= 0),
    PRIMARY KEY (sku_id, month_start),
    CHECK (shipped_qty <= ordered_qty),
    CHECK (EXTRACT(DAY FROM month_start) = 1)
);

-- One row = one SKU's stock position at one month end.
CREATE TABLE core.fact_inventory (
    sku_id          text        NOT NULL REFERENCES core.dim_product (sku_id),
    month_end       date        NOT NULL REFERENCES core.dim_date (date_key),
    on_hand_qty     integer     NOT NULL CHECK (on_hand_qty >= 0),
    allocated_qty   integer     NOT NULL CHECK (allocated_qty >= 0),
    PRIMARY KEY (sku_id, month_end),
    CHECK (month_end = (date_trunc('month', month_end) + INTERVAL '1 month - 1 day')::date)
);

-- One row = one purchase-order line (one SKU on one PO). Status is as of the
-- extract date. Open quantity is derived (ordered - received), not stored.
CREATE TABLE core.fact_purchase_order (
    po_line_id      text            PRIMARY KEY,
    po_number       text            NOT NULL,
    sku_id          text            NOT NULL REFERENCES core.dim_product (sku_id),
    supplier_id     text            NOT NULL REFERENCES core.dim_supplier (supplier_id),
    order_date      date            NOT NULL REFERENCES core.dim_date (date_key),
    promised_date   date            NOT NULL,
    ordered_qty     integer         NOT NULL CHECK (ordered_qty > 0),
    unit_price      numeric(12, 2)  NOT NULL CHECK (unit_price > 0),
    status          text            NOT NULL
        CHECK (status IN ('OPEN', 'PARTIALLY_RECEIVED', 'CLOSED', 'CLOSED_SHORT')),
    CHECK (promised_date >= order_date)
);

-- One row = one goods receipt against a PO line. A partial delivery produces
-- several rows for the same PO line.
CREATE TABLE core.fact_supplier_delivery (
    receipt_id      text        PRIMARY KEY,
    po_line_id      text        NOT NULL REFERENCES core.fact_purchase_order (po_line_id),
    receipt_date    date        NOT NULL REFERENCES core.dim_date (date_key),
    received_qty    integer     NOT NULL CHECK (received_qty > 0)
);

-- -----------------------------------------------------------------------------
-- Indexes: only on columns used for joins and filters in the analytics layer.
-- (Primary keys are indexed automatically; the composite PKs already cover
-- "by SKU" lookups on fact_demand and fact_inventory.)
-- -----------------------------------------------------------------------------
CREATE INDEX ix_dim_product_supplier       ON core.dim_product (supplier_id);
CREATE INDEX ix_dim_product_category       ON core.dim_product (category);
CREATE INDEX ix_fact_demand_month          ON core.fact_demand (month_start);          -- monthly totals, trailing windows
CREATE INDEX ix_fact_inventory_month       ON core.fact_inventory (month_end);         -- latest-snapshot lookups
CREATE INDEX ix_fact_po_sku                ON core.fact_purchase_order (sku_id);       -- open supply per SKU
CREATE INDEX ix_fact_po_supplier_date      ON core.fact_purchase_order (supplier_id, order_date);  -- supplier scorecards
CREATE INDEX ix_fact_po_open               ON core.fact_purchase_order (sku_id)
    WHERE status IN ('OPEN', 'PARTIALLY_RECEIVED');                                    -- small partial index: open lines only
CREATE INDEX ix_fact_delivery_po_line      ON core.fact_supplier_delivery (po_line_id); -- FK join to PO lines
CREATE INDEX ix_fact_delivery_date         ON core.fact_supplier_delivery (receipt_date);

-- -----------------------------------------------------------------------------
-- Planning parameters used by the SQL views. Written by the Python loader
-- from src/config.py, so SQL and Python always use the same thresholds.
-- -----------------------------------------------------------------------------
CREATE TABLE analytics.planning_parameter (
    name            text        PRIMARY KEY,
    value           numeric     NOT NULL,
    source          text        NOT NULL,   -- config class the value comes from
    description     text        NOT NULL
);

-- -----------------------------------------------------------------------------
-- Audit trail: every load attempt, accepted or not.
-- -----------------------------------------------------------------------------
CREATE TABLE audit.load_run (
    run_id          integer     GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    started_at      timestamptz NOT NULL DEFAULT now(),
    finished_at     timestamptz,
    dataset_label   text        NOT NULL,                -- 'clean' or 'dirty'
    source_folder   text        NOT NULL,
    as_of_date      date,
    status          text        NOT NULL
        CHECK (status IN ('RUNNING', 'ACCEPTED', 'ACCEPTED_WITH_WARNINGS', 'REJECTED', 'FAILED')),
    failed_checks   integer,
    warning_checks  integer,
    rows_loaded     integer,
    message         text
);

-- One row = one data-quality check result for one load attempt.
CREATE TABLE audit.data_quality_result (
    run_id          integer     NOT NULL REFERENCES audit.load_run (run_id),
    check_name      text        NOT NULL,
    table_name      text        NOT NULL,
    severity        text        NOT NULL CHECK (severity IN ('ERROR', 'WARNING', 'INFO')),
    status          text        NOT NULL CHECK (status IN ('PASS', 'FAIL', 'WARN', 'INFO')),
    failed_rows     integer     NOT NULL,
    total_rows      integer     NOT NULL,
    description     text        NOT NULL,
    PRIMARY KEY (run_id, check_name, table_name)
);
