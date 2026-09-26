# Data Dictionary

Column definitions for every layer. Grain and design rationale are in
[`database_architecture.md`](database_architecture.md). Power BI output
files are described in [`dashboard/DATA_DICTIONARY.md`](../dashboard/DATA_DICTIONARY.md).

Conventions: quantities in **units**; money in **USD** (at standard unit cost
unless stated as price/revenue); durations in **days** unless the name says
months; dates ISO `YYYY-MM-DD`.

## 1. Raw extract (`data/raw/*.csv`) → `core` tables

Raw files and `core` tables share column names 1:1, so lineage is obvious.

### `suppliers.csv` → `core.dim_supplier`
| Column | SQL type | Required | Description |
|---|---|---|---|
| supplier_id | text PK | ✓ | `SUP-001` … |
| supplier_name | text | ✓ | Neutral placeholder name |
| region | text | optional | `Domestic` / `Import` |
| quoted_lead_time_days | integer | ✓ | Supplier's standard quoted lead time (1–365) |

### `products.csv` → `core.dim_product`
| Column | SQL type | Required | Description |
|---|---|---|---|
| sku_id | text PK | ✓ | `SKU-00001` … |
| category | text | ✓ | One of 8 product categories |
| supplier_id | text FK | ✓ | Primary supplier |
| unit_cost | numeric(12,2) | ✓ | Standard cost (> 0) |
| unit_price | numeric(12,2) | ✓ | Selling price (> 0) |
| moq | integer | ✓ | Supplier minimum order quantity |
| order_multiple | integer | ✓ | Case pack; orders are rounded up to it |
| supplier_lead_time_days | integer | ✓ | Quoted lead time for this SKU |
| launch_date | date | optional | First sellable date |
| lifecycle_status | text | optional | ERP status `NEW` / `ACTIVE` / `DISCONTINUED` (maintained imperfectly) |

### `demand_monthly.csv` → `core.fact_demand`
| Column | SQL type | Description |
|---|---|---|
| sku_id, month_start | PK | One SKU in one month (month_start = day 1) |
| ordered_qty | integer ≥ 0 | **Customer demand**: units customers ordered |
| shipped_qty | integer ≥ 0, ≤ ordered | **Customer shipments**: units delivered. The gap is lost sales |

### `inventory_snapshot.csv` → `core.fact_inventory`
| Column | SQL type | Description |
|---|---|---|
| sku_id, month_end | PK | One SKU at one month end (month_end = last day) |
| on_hand_qty | integer ≥ 0 | **On-hand inventory**: physical stock |
| allocated_qty | integer ≥ 0 | Committed to customer orders, not yet shipped |

### `purchase_orders.csv` → `core.fact_purchase_order`
| Column | SQL type | Description |
|---|---|---|
| po_line_id | text PK | `PO-000123-02` (PO number + line) |
| po_number | text | One PO per supplier per order date |
| sku_id, supplier_id | FK | |
| order_date | date FK | When the PO was placed |
| promised_date | date ≥ order_date | **Original** promised delivery date |
| ordered_qty | integer > 0 | Units ordered (≥ MOQ, multiple of case pack) |
| unit_price | numeric(12,2) > 0 | Price paid (differs from standard cost → PPV) |
| status | text | As of the extract: `OPEN`, `PARTIALLY_RECEIVED`, `CLOSED`, `CLOSED_SHORT` |

**Open purchase orders** = lines with status OPEN or PARTIALLY_RECEIVED; open
quantity = ordered − received (derived in `v_po_line_status`).

### `supplier_deliveries.csv` → `core.fact_supplier_delivery`
| Column | SQL type | Description |
|---|---|---|
| receipt_id | text PK | `RCV-0000001` … |
| po_line_id | text FK | PO line received against |
| receipt_date | date FK | **Received purchase orders**: goods-receipt date |
| received_qty | integer > 0 | Units received; several rows per line = partial delivery |

### Five distinct quantities, never mixed
| Concept | Column | Table |
|---|---|---|
| Customer demand | `ordered_qty` | fact_demand |
| Customer shipments | `shipped_qty` | fact_demand |
| On-hand inventory | `on_hand_qty` | fact_inventory |
| Open purchase orders | `open_qty` (derived) | v_po_line_status |
| Received purchase orders | `received_qty` | fact_supplier_delivery |

## 2. Analytics views (`analytics.*`)

### `v_demand_monthly`: SKU × month
| Column | Definition |
|---|---|
| demand_qty, shipped_qty | Customer demand and shipments |
| lost_qty | demand − shipped |
| stockout_month | shipped < demand |
| fill_rate | shipped ÷ demand (NULL if no demand) |
| demand_value_at_cost, revenue, cogs, lost_revenue | Quantities × cost or price |
| rolling_3m_avg_demand, rolling_6m_avg_demand | Average of this and the previous 2 / 5 months |
| rolling_12m_demand, months_in_12m_window | Trailing 12-month total and how many months it covers (< 12 for new SKUs) |
| demand_mom_change | demand − previous month |
| demand_ytd | Year-to-date cumulative demand |

### `v_inventory_monthly`: SKU × month end
| Column | Definition |
|---|---|
| opening_qty | Previous month's closing stock |
| received_qty | Receipts during the month |
| closing_qty, allocated_qty | Month-end stock |
| inventory_value | closing × unit cost |
| zero_stock_at_month_end, stockout_month | Stockout indicators (snapshot vs. during-month) |
| balance_difference | opening + received − shipped − closing (0 = reconciled) |

### `v_po_line_status`: PO line
| Column | Definition |
|---|---|
| received_qty, receipt_count, split_delivery | Receipts to date; split = more than one receipt |
| first_receipt_date, last_receipt_date | |
| open_qty, open_value | Remaining quantity/value on open lines |
| actual_lead_time_days | last receipt − order date (complete lines only) |
| promised_lead_time_days | promised − order date |
| days_late | max(0, last receipt − promised), complete lines |
| otif_evaluable | Complete, or promise (+ tolerance) already passed |
| otif | Qty received by the original promise ≥ ordered × in-full tolerance |
| in_full | Qty received to date ≥ ordered × tolerance |
| open_age_days, is_stale | Age of open lines; open and > 90 days past promise |

### `v_supplier_performance`: supplier
| Column | Definition |
|---|---|
| po_lines, skus_purchased | Activity |
| spend, spend_share, spend_rank | Received value, share of total, rank |
| open_po_value | Value still on order |
| otif_lines, otif_pct | Evaluable lines and share OTIF |
| fill_rate | Received ÷ ordered on complete lines |
| split_delivery_pct | Share of lines with split receipts |
| avg / median / p90_lead_time_days, lead_time_std_days, lead_time_cv | Lead-time level and variability |
| avg_promised_lead_time_days, avg_days_late | Promise vs. reality |
| purchase_price_variance | Σ (price paid − standard cost) × received |
| enough_history | otif_lines ≥ minimum for ranking |

### `v_inventory_position`: SKU as of the extract date
| Column | Definition |
|---|---|
| on_hand_qty, allocated_qty | Stock components |
| open_po_qty | Open PO quantity **excluding stale lines** (credible supply) |
| stale_open_po_qty | Open quantity on lines > 90 days past promise: not counted as supply, needs chasing or cancelling |
| inventory_position | on hand + open PO (non-stale) − allocated |
| inventory_value, open_po_value | At cost / at PO price |
| avg_monthly_demand, demand_std_monthly | Trailing 6 months |
| avg_daily_demand | avg monthly ÷ 30.42 |
| days_of_supply, months_of_supply, months_of_supply_incl_open_po | Coverage (NULL if no demand) |
| demand_12m, stockout_months_12m, last_demand_month | History indicators |
| screening_safety_stock, screening_reorder_point, below_screening_rop | Uniform 95% screen (Phase 7 replaces) |
| high_cover, high_cover_excess_qty/value | Stock above 6 months of demand |
| dead_stock_candidate | Stock on hand and no demand in 6 months |
| out_of_stock | No stock while demand exists |

## 3. Audit tables
| Table | Columns |
|---|---|
| `audit.load_run` | run_id, started_at, finished_at, dataset_label, source_folder, as_of_date, status (`RUNNING` / `ACCEPTED` / `ACCEPTED_WITH_WARNINGS` / `REJECTED` / `FAILED`), failed_checks, warning_checks, rows_loaded, message |
| `audit.data_quality_result` | run_id, check_name, table_name, severity, status, failed_rows, total_rows, description |

## 4. Parquet exports (`data/processed/`)
`demand_monthly`, `inventory_monthly`, `po_line_status`, `supplier_performance`
and `inventory_position` (the views above, typed). They let the Python
analytics phases read analysis-ready data without a running database.
