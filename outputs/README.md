# Outputs

Result files: data-quality reports (`python -m src.data_validation`), SQL
answers (`python -m src.database analyze`) and, in later phases, Power BI-ready
planning outputs. Column
definitions are in [`dashboard/DATA_DICTIONARY.md`](../dashboard/DATA_DICTIONARY.md).

| File | Produced in |
|---|---|
| `data_quality_report.csv` | Phase 2 ✅ (clean extract: ACCEPTED) |
| `data_quality_report_dirty.csv` | Phase 3 ✅ (dirty extract: REJECTED, shows each injected defect caught) |
| `sql_answers/q01…q15_*.csv` | Phase 3 ✅: answers to the 15 SQL business questions ([guide](../docs/sql_guide.md)) |
| `inventory_health.csv` | Phase 7 |
| `forecast_results.csv` | Phase 5 |
| `replenishment_recommendations.csv` | Phase 8 |
| `supplier_performance.csv` | Phase 6 |
| `executive_kpis.csv` | Phase 9 |

Other files are produced by later phases.
