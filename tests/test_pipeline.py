"""End-to-end test of the implemented pipeline stages (generate -> validate)."""

import pandas as pd

from src.config import Config, PathConfig
from src.pipeline import DATA_QUALITY_REPORT, run
from tests.conftest import SMALL


def test_pipeline_writes_data_and_quality_report(tmp_path):
    paths = PathConfig(
        raw_data_dir=tmp_path / "raw",
        processed_data_dir=tmp_path / "processed",
        output_dir=tmp_path / "outputs",
        synthetic_truth_dir=tmp_path / "truth",
    )
    run(Config(paths=paths, data=SMALL))
    assert (tmp_path / "raw" / "demand_monthly.csv").exists()
    assert (tmp_path / "truth" / "sku_generation_truth.csv").exists()
    report = pd.read_csv(tmp_path / "outputs" / DATA_QUALITY_REPORT)
    assert not ((report["severity"] == "ERROR") & (report["status"] == "FAIL")).any()
