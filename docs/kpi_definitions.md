# KPI Definitions

Single source of truth for every headline KPI. Each dashboard card, output
column and README figure uses exactly these definitions. Formulas reference
[`methodology.md`](methodology.md).

| KPI | Definition | Formula | Grain / window | Good direction | Primary owner |
|---|---|---|---|---|---|
| Total inventory value | On-hand stock valued at standard cost | Σ on_hand × unit_cost | As-of snapshot | Context-dependent | Finance |
| Excess inventory value | On-hand stock above policy max + tolerance, at cost | Σ excess_on_hand × unit_cost | As-of | ↓ | Supply planning |
| Excess on order value | Open PO quantity beyond need (can be pushed out/cancelled) | Σ excess_on_order × unit_cost | As-of | ↓ | Procurement |
| Dead-stock value | Full on-hand value of DEAD_STOCK SKUs | Σ on_hand × unit_cost (dead) | As-of | ↓ | Supply planning / Finance |
| Excess + dead share | Share of inventory value that is excess or dead | (excess + dead) / total value | As-of | ↓ | Finance |
| Stockout risk % | Share of **active** SKUs projected to stock out before a new order could arrive | SKUs with IP − lead-time demand < 0 / active SKUs | As-of | ↓ | Supply planning |
| Revenue at risk | Expected unfilled units over protection interval × unit price | Σ E[shortage] × unit_price | As-of | ↓ | Supply planning / Sales |
| Service level (fill rate) | Share of ordered units shipped | Σ shipped / Σ ordered | Last 12 months; also by ABC | ↑ | Supply chain manager |
| Target service level (CSL) | Policy probability of no stockout per cycle | Config by ABC class | Policy | – | S&OP |
| Supplier OTIF % | PO lines delivered complete by original promised date | OTIF lines / closed lines | Last 12 months | ↑ | Procurement |
| Supplier fill rate | Received vs. ordered quantity | Σ received / Σ ordered | Last 12 months | ↑ | Procurement |
| Lead-time CV | Lead-time variability relative to mean | σ_L / mean L | Per supplier, all receipts | ↓ | Procurement |
| Forecast WAPE | Volume-weighted absolute error | Σ|A − F| / ΣA | Hold-out months | ↓ | Demand planning |
| Forecast bias | Systematic over(+)/under(−) forecasting | Σ(F − A) / ΣA | Hold-out months | → 0 | Demand planning |
| Inventory turns | How many times inventory is sold per year | Annual COGS / average inventory | Last 12 months | ↑ | Finance / supply chain |
| DIO | Days inventory outstanding | 365 / turns | Last 12 months | ↓ | Finance |
| GMROI | Gross margin per dollar of inventory | Annual gross margin / average inventory | Last 12 months | ↑ | Category management |
| Annual carrying cost | Cost of holding current inventory for a year | Inventory value × 25% | As-of | ↓ | Finance |
| Recommended order value | Value of orders proposed this cycle | Σ order_qty × unit_cost | This run | Context | Buyers |

**Active SKU** = SKU with forecast demand > 0 (excludes NO_DEMAND / dead).
**As-of** = the latest month-end snapshot.

Historical actuals (fill rate, OTIF, turns) and forward-looking projections
(stockout risk, revenue at risk, scenario values) are kept distinct in every
output: scenario columns are prefixed `scenario_`.
