# Power BI Implementation Guide

> **Status: skeleton (Phase 1).** Page layout and intent are defined here;
> relationships, DAX measures and field-level details are completed in
> Phase 11, once the output datasets exist.

Power BI `.pbix` files cannot be generated reliably from code, so this
project provides **clean, analysis-ready CSV/Parquet outputs** plus this
step-by-step guide to build the report.

## 1. Data sources

| File | Grain | Used on pages |
|---|---|---|
| `outputs/inventory_health.csv` | One row per SKU | 1, 2 |
| `outputs/forecast_results.csv` | SKU × month (history + forecast) | 3 |
| `outputs/replenishment_recommendations.csv` | One row per SKU | 2 |
| `outputs/supplier_performance.csv` | One row per supplier | 4 |
| `outputs/executive_kpis.csv` | One row per KPI (base + scenarios) | 1 |
| `outputs/inventory_trend.csv` | SKU × month-end (planned, Phase 9) | 1 |

## 2. Data model (star schema)

To be completed in Phase 11: dimension tables (Product, Supplier, Date),
fact tables, relationship cardinality and filter direction.

## 3. Report pages

### Page 1 — Executive Overview
**Question answered:** *Where is the capital, and what is at risk?*
- KPI cards: Total Inventory Value, Excess Inventory Value, Dead Stock Value,
  Stockout Risk %, Service Level (fill rate vs. target), Supplier OTIF —
  every card uses the definitions in `docs/kpi_definitions.md`
- Inventory value by health status
- Inventory value by ABC class
- Inventory trend (24 month-end snapshots)
- Turns / DIO / GMROI
- Top inventory risks table (ranked by value at risk)
- Scenario comparison (base vs. demand / service level / lead time), clearly
  labelled as projections

### Page 2 — Inventory & Replenishment
**Question answered:** *What should we order now?*
- Days of supply distribution
- Safety stock, reorder point and inventory position per SKU
- Recommended orders (quantity, value, reason code)
- MOQ-driven excess and projected reorder dates
- Top stockout-risk SKUs

### Page 3 — Demand Planning
**Question answered:** *How good is our forecast, and where is it biased?*
- Historical demand vs. forecast
- Forecast accuracy (WAPE), bias and tracking-signal alerts by ABC-XYZ segment
- Forecast value added vs. naive
- Seasonality view
- ABC-XYZ matrix (SKU count and value)

### Page 4 — Supplier Performance
**Question answered:** *Which suppliers are driving our inventory problems?*
- Spend by supplier; supplier concentration (Pareto)
- OTIF %, fill rate
- Lead time and lead-time variability
- Supplier segment × spend

## 4. Measures (DAX)

To be completed in Phase 11.

## 5. Filters and drill-through

To be completed in Phase 11 (planned: category, ABC class, XYZ class,
supplier, health status slicers; drill-through from any SKU to a SKU detail
page and from supplier to supplier detail).

## 6. Screenshots

Placeholders — added once the report is built:

- `dashboard/screenshots/01_executive_overview.png`
- `dashboard/screenshots/02_inventory_replenishment.png`
- `dashboard/screenshots/03_demand_planning.png`
- `dashboard/screenshots/04_supplier_performance.png`
