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
