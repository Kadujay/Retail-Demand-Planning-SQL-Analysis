# Data Dictionary

> **Status: planned columns (Phase 1).** Data types, units and example values
> are confirmed against the generated outputs in Phase 11.

Conventions: quantities in **units**, values in **USD at standard unit cost**
unless the column says `_revenue` (at selling price), durations in **days**
unless the name says otherwise. Columns prefixed `scenario_` are projections,
never historical actuals. KPI definitions: [`docs/kpi_definitions.md`](../docs/kpi_definitions.md).

## `inventory_health.csv` — one row per SKU (as-of snapshot)

| Column | Description |
|---|---|
| sku_id, category, supplier_id | Keys |
| abc_class, xyz_class, abc_xyz | Classification (methodology §2–4) |
| xyz_flag | NEW / INTERMITTENT / NO_DEMAND where applicable |
| on_hand_qty | Physical stock |
| open_po_qty | Remaining quantity on open PO lines |
| allocated_qty | Committed to customer orders, not yet shipped |
| inventory_position | on_hand + open_po − allocated |
| forecast_monthly_demand | Selected-method forecast, next month |
| daily_demand | forecast_monthly_demand / 30.42 |
| days_of_supply | on_hand / daily_demand (blank if no demand) |
| lead_time_days, review_period_days | Protection-interval components |
| target_service_level | CSL by ABC class |
| safety_stock | Units (methodology §9) |
| sigma_source | forecast_error / demand (fallback) |
| reorder_point | s, units (methodology §10) |
| policy_max | S = s + Q |
| stockout_probability | Probability of stocking out within protection interval |
| inventory_value | on_hand × unit_cost |
| excess_on_hand_units / _value | Above policy max + tolerance |
| excess_on_order_units / _value | Open PO quantity beyond need |
| dead_stock_value | Full on-hand value if DEAD_STOCK, else 0 |
| revenue_at_risk | Expected shortage × unit price |
| health_status | STOCKOUT … HEALTHY |
| priority_rank | Rank by money at stake |

## `forecast_results.csv` — SKU × month

| Column | Description |
|---|---|
| sku_id, month_start | Keys |
| ordered_qty | Historical demand (blank for future months) |
| forecast_qty | Forecast from the selected method |
| method | NAIVE / MA / SES / WMA / HOLT / SEASONAL_NAIVE |
| period_type | HISTORY / TEST / FUTURE |
| mae, rmse, wape, bias, tracking_signal, fva | SKU-level accuracy on hold-out (repeated per row for Power BI convenience) |

## `replenishment_recommendations.csv` — one row per SKU

| Column | Description |
|---|---|
| sku_id, supplier_id | Keys |
| current_inventory, open_po_qty, allocated_qty, inventory_position | Supply |
| lead_time_demand, protection_demand | Forecast demand over L and over L + R |
| safety_stock, reorder_point, policy_max | Policy |
| projected_inventory | inventory_position − lead_time_demand |
| eoq, moq, order_multiple | Lot-size inputs |
| recommended_order_qty | After MOQ and multiple rounding |
| moq_excess_units / _value | Units bought only because of MOQ / multiple |
| order_value | recommended_order_qty × unit_cost |
| reason | EXCESS_ALREADY_PRESENT / STOCKOUT_RISK / SAFETY_STOCK_RISK / BELOW_ROP / NO_ORDER_REQUIRED |
| projected_reorder_date | For SKUs not yet due: when IP is expected to reach s |

## `supplier_performance.csv` — one row per supplier

| Column | Description |
|---|---|
| supplier_id, supplier_name, region | Keys |
| total_spend, spend_share, sku_count | Scale and concentration |
| avg_lead_time_days, lead_time_std_days, lead_time_cv | Lead-time performance |
| otif_pct, otif_spend_weighted_pct | Line-level OTIF (methodology §12) |
| fill_rate, early_rate, avg_delay_days | Delivery performance |
| ppv | Purchase price variance |
| receipts_count, low_confidence_flag | Sample size |
| segment | Reliable / Watch / At Risk |

## `executive_kpis.csv` — long format

| Column | Description |
|---|---|
| scenario | BASE or scenario name |
| kpi_name, kpi_value, unit | KPI per `kpi_definitions.md` |
| value_type | ACTUAL (historical) or PROJECTION |
