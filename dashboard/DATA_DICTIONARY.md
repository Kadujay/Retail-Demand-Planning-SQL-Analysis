# Data Dictionary

> **Status: skeleton (Phase 1).** Columns are listed as planned; data types,
> units and example values are confirmed against the generated outputs in
> Phase 11.

Conventions: quantities in **units**, values in **currency (USD)** at unit
cost, durations in **days** unless the column name says otherwise. Columns
prefixed `scenario_` are projections, never historical actuals.

## `inventory_health.csv` — one row per SKU

| Column | Description |
|---|---|
| sku_id | Product identifier |
| category | Product category |
| supplier_id | Primary supplier |
| abc_class / xyz_class / abc_xyz | Classification (methodology §2–4) |
| on_hand_qty | Physical stock at snapshot date |
| open_po_qty | Ordered, not yet received |
| allocated_qty | Committed to customer orders |
| inventory_position | on_hand + open_po − allocated |
| avg_daily_demand | Forward-looking demand per day |
| days_of_supply | on_hand / avg_daily_demand |
| safety_stock | Units (methodology §9) |
| reorder_point | Units (methodology §10) |
| inventory_value | on_hand × unit_cost |
| excess_units / excess_value | Above target max (methodology §11) |
| health_status | STOCKOUT … HEALTHY |

## `forecast_results.csv` — SKU × month

| Column | Description |
|---|---|
| sku_id, month | Keys |
| actual_qty | Historical demand (blank for future months) |
| forecast_qty | Forecast from selected method |
| method | MA / SES / WMA / SEASONAL_NAIVE |
| is_test_period | True for hold-out months |
| mae, rmse, wape, bias | SKU-level accuracy on hold-out |

## `replenishment_recommendations.csv` — one row per SKU

| Column | Description |
|---|---|
| sku_id, supplier_id | Keys |
| current_inventory, open_po_qty | Supply |
| forecast_demand | Demand over lead time + review period |
| safety_stock, reorder_point | Policy |
| projected_inventory | Inventory position − lead-time demand |
| recommended_order_qty | After MOQ and order-multiple rounding |
| moq_excess_units | Units ordered only because of MOQ/multiple |
| order_value | recommended_order_qty × unit_cost |
| reason | STOCKOUT_RISK / BELOW_ROP / SAFETY_STOCK_RISK / EXCESS_ALREADY_PRESENT / NO_ORDER_REQUIRED |

## `supplier_performance.csv` — one row per supplier

| Column | Description |
|---|---|
| supplier_id, supplier_name | Keys |
| total_spend, sku_count | Scale |
| avg_lead_time_days, lead_time_std_days, lead_time_cv | Lead-time performance |
| otif_pct, fill_rate, avg_delay_days | Delivery performance |
| ppv | Purchase price variance |
| segment | Reliable / Watch / At Risk |

## `executive_kpis.csv` — one row per KPI per scenario

| Column | Description |
|---|---|
| scenario | BASE or scenario name |
| kpi_name, kpi_value, unit | KPI in long format for easy Power BI cards |
