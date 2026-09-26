# Decision Log

Where several accepted approaches exist, this log records the one chosen, the
alternatives, and why. It is the quickest way to prepare for "why didn't you
use X?" questions.

| # | Decision | Chosen | Alternatives considered | Why |
|---|---|---|---|---|
| 1 | Inventory policy | Periodic review (R, s, S), R = 1 month | Continuous review (s, Q); pure order-up-to (R, S) | Matches the monthly planning process. Continuous-review ROP would under-protect by one review period. (s) avoids tiny orders every cycle, which pure (R, S) would place |
| 2 | Safety-stock uncertainty | RMSE of one-step forecast errors | Std of raw demand | Raw demand σ counts predictable seasonality/trend as risk and over-stocks those SKUs. Demand σ kept as config option and fallback |
| 3 | Lead-time variability | Included (combined formula) | Demand-only formula | Unreliable suppliers are a stated problem; excluding σ_L would understate risk |
| 4 | Service-level measure | CSL as policy input; fill rate as achieved KPI | Fill-rate-based safety stock | CSL has a transparent closed form. Fill-rate sizing is a documented future improvement |
| 5 | ABC basis | Annual consumption value at cost | Revenue, margin, order-line frequency (hits) | Policy controls inventory investment. Hits-based ABC is useful for warehouse slotting, not replenishment |
| 6 | Intermittency rule | ADI > 1.32 → Z | Ad-hoc "fewer than N non-zero months" | Recognised criterion (Syntetos–Boylan–Croston) instead of an invented threshold |
| 7 | Forecast methods | Naive, MA, SES, Holt, seasonal naive with fixed parameters | ARIMA, Prophet, ML (gradient boosting) | Transparent, fast for 5,000 SKUs, explainable to planners; ML without causal drivers rarely beats these on monthly data. Complexity is not added without evidence |
| 8 | Accuracy metric | WAPE (+ MAE, RMSE, bias) | MAPE, sMAPE, MASE | MAPE breaks on zeros; WAPE is volume-weighted and business-readable. MASE is a reasonable addition later |
| 9 | Validation | Time-based hold-out, rolling one-step-ahead | Random k-fold | Random splits leak the future |
| 10 | Excess definition | Above policy max `S` + 30 days | Fixed months of supply (e.g. > 6 months) | Keeps health and replenishment consistent; months-of-supply rule is simpler but ignores lot size and lead time |
| 11 | Lot size | max(EOQ, MOQ) rounded to multiple | Lot-for-lot; fixed period quantity | EOQ explains the ordering vs. holding trade-off and exposes when MOQ dominates |
| 12 | OTIF basis | Original promised date, PO-line level, split receipts summed | Re-promised date; per receipt; requested date | Non-gameable; line level is the common strict definition. Requested date would measure the buyer's ask, not the supplier's commitment |
| 13 | Unfilled demand | Lost sales | Backorders | Typical for industrial distribution; affects inventory position |
| 14 | Demand signal | Customer orders (with shipments separate) | Shipments / invoices | Shipments are censored by stockouts |
| 15 | Scenario engine | Re-run the same functions with modified config | Separate simulation model | One set of tested logic; no divergence between base and scenario |
| 16 | Build order | Supplier analytics (Phase 6) before safety stock (Phase 7) | Original plan (safety stock first) | Safety stock needs supplier σ_L |
| 17 | Synthetic operations | Simulate a legacy buyer rule + supplier behaviour month by month | Paint stockouts/excess directly onto the data | Problems emerge from causes (lagging averages, MOQs, unreliable suppliers), so the analysis finds real mechanisms, and stock balances reconcile exactly |
| 18 | Legacy buyer averaging window | 6-month trailing average | 3-month average | 3 months over-reacted to lumpy orders (44% of value above 6 months of supply — implausible); 6 months gave ~24% and is a common ERP default |
| 19 | Burn-in | 12 months | 6 months | 6 months left an artificial 20% inventory decline in early 2024 |
| 20 | Volume dispersion | `volume ∝ cost^−0.45`, log-sd 1.3 | `cost^−0.6`, log-sd 0.8 | First version put 80% of value in 40% of SKUs — too flat for a distributor; now ~24% |
| 21 | Answer key | Separate `synthetic_truth/` folder | Columns on the raw tables | Prevents the generator's labels from leaking into analysis |
| 22 | Dirty data | Clean generator; defects injected in tests | Randomly dirty generator | Keeps analysis interpretable while proving each validation check works |
| 23 | Database layout | Three schemas: `core`, `analytics`, `audit` | Staging/ODS/mart warehouse layers | Shows lineage without enterprise overhead; enough for ~300k rows |
| 24 | Keys | Natural ERP keys (`SKU-00001`) | Surrogate integer keys | Stable, readable; no slowly changing history to track |
| 25 | Calendar | Daily `dim_date` generated in SQL | Monthly calendar | Lead time and OTIF are measured in days |
| 26 | Derived data | Views, not stored tables | ETL into summary tables | Always current, logic visible, small data; can become materialized views later |
| 27 | Load gate | Python validation (ERROR blocks) + DB constraints as second line | DB constraints only | Cross-table and statistical rules need code; warnings must not block loads |
| 28 | Bad data | Refuse and record in `audit` | Silently fix or drop rows | Silent fixes hide source-system problems and break lineage |
| 29 | Load mechanics | One transaction, PostgreSQL `COPY` from CSV | Row-by-row inserts; per-table commits | Fast, and a failure never leaves half-new data |
| 30 | SQL thresholds | `analytics.planning_parameter` written from `config.py` | Literals in SQL | One source of truth for Python and SQL |
| 31 | SQL reorder point | Uniform 95% screen, clearly labelled | Replicating the full Phase 7 policy in SQL | Keeps SQL simple; avoids two diverging policy implementations |
| 32 | OTIF population | Closed lines + open lines past promise | Closed lines only | Overdue open lines are failures, not "not yet measured" |
| 33 | Output formats | CSV for small answers (recruiter-readable), Parquet for SKU-month datasets | CSV only | Parquet keeps column types (dates, booleans, decimals) and is ~4.6× smaller (demand panel: 3.2 MB vs 14.6 MB CSV) |
| 34 | Dependencies | `requirements.in` → pip-compile → pinned `requirements.txt` | Hand-pinned list | Readable intent plus a reproducible lock including transitive packages |
| 35 | Dirty data | Separate `data/raw_dirty/` copy, opt-in flag | Dirty data in `data/raw/` | The analytical default can never be contaminated |
