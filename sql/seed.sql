-- =============================================================================
-- seed.sql — Reference data that does not come from the ERP extract
-- =============================================================================
-- The calendar dimension is generated, not loaded: one row per day from
-- 1 January of the year before the history window (covers POs still in
-- transit when the window starts) to 31 December of the year after the as-of
-- date (covers promised dates beyond it). The loader binds :start_date and
-- :end_date from DataGenerationConfig, so changing the history window in
-- config.py never leaves fact dates outside the calendar.
--
-- Operational data (products, demand, POs, ...) is NOT loaded here: it goes
-- through `python -m src.database load`, which validates it first.
-- =============================================================================

INSERT INTO core.dim_date (
    date_key, year, quarter, month, month_name, month_start, month_end,
    is_month_end, iso_week, day_of_week
)
SELECT
    d::date                                                         AS date_key,
    EXTRACT(YEAR FROM d)::smallint                                  AS year,
    EXTRACT(QUARTER FROM d)::smallint                               AS quarter,
    EXTRACT(MONTH FROM d)::smallint                                 AS month,
    to_char(d, 'Mon')                                               AS month_name,
    date_trunc('month', d)::date                                    AS month_start,
    (date_trunc('month', d) + INTERVAL '1 month - 1 day')::date     AS month_end,
    d::date = (date_trunc('month', d) + INTERVAL '1 month - 1 day')::date AS is_month_end,
    EXTRACT(WEEK FROM d)::smallint                                  AS iso_week,
    EXTRACT(ISODOW FROM d)::smallint                                AS day_of_week
FROM generate_series(CAST(:start_date AS date), CAST(:end_date AS date), INTERVAL '1 day') AS g (d);
