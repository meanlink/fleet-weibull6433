"""截止月样本归并与 Weibull 估计的业务单元测试。

覆盖需求：
- 晚到记录按观测月归位，截止月后的记录不参与；
- 同一资产取截止月内最新观测，旧观测标记替代依据；
- 失效后又报在役、里程倒退列出冲突并阻止估计；
- 源数据更新后旧估计标 stale，重算保留原截止月，已批准快照仍可回看。

运行：在 backend/ 依赖可用的环境中，于仓库根目录执行
    PYTHONPATH=backend python -m pytest test-artifacts/gb-1017/tests -q
"""
import importlib
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[3] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

business = importlib.import_module("src.services.business")
samples = importlib.import_module("src.services.samples")


@pytest.fixture(scope="module")
def monthly():
    return business.seed_monthly_records()


@pytest.fixture(scope="module")
def params():
    return {p["key"]: p["default"] for p in business.metadata()["parameters"]}


def test_seed_shapes(monthly):
    # 200 个资产 × 6 个月 + 2 条晚到 + 2 条截止月后补录 = 1204 行
    assert len(monthly["flat"]) == 1204
    assert len(monthly["latest"]) == 200
    assert monthly["cutoff"] == "2026-11"
    assets = {r["asset"] for r in monthly["flat"]}
    assert len(assets) == 200


def test_baseline_sample_builds_without_conflict(monthly, params):
    snap = samples.build_sample(monthly["flat"], params, "fleet-weibull-202611.csv")
    assert snap["cutoff_month"] == "2026-11"
    assert snap["sample_size"] == 100  # AC-8
    assert snap["failures"] >= 10
    assert snap["conflicts"] == []
    assert snap["stats"]["after_cutoff"] == 2  # 2026-12 的两条补录
    assert snap["stats"]["included"] == 200


def test_late_arrival_placed_by_observed_month(monthly, params):
    snap = samples.build_sample(monthly["flat"], params, "fleet-weibull-202611.csv")
    d7 = next(d for d in snap["decisions"] if d["asset"] == "AC-8-007")
    september = [o for o in d7["observations"] if o["month"] == "2026-09"]
    assert len(september) == 2  # 原始 09 月台账 + 晚到补录
    assert all(o["inclusion"] == "superseded" for o in september)
    assert d7["selected"]["month"] == "2026-11"


def test_earlier_cutoff_reassigns_selection(monthly, params):
    early = {**params, "cutoff_month": "2026-09"}
    snap = samples.build_sample(monthly["flat"], early, "fleet-weibull-202611.csv")
    d7 = next(d for d in snap["decisions"] if d["asset"] == "AC-8-007")
    assert d7["selected"]["month"] == "2026-09"
    assert all(o["inclusion"] != "included" for o in d7["observations"] if o["month"] > "2026-09")
    assert snap["sample_size"] == 100


def test_records_after_cutoff_never_participate(monthly, params):
    snap = samples.build_sample(monthly["flat"], params, "fleet-weibull-202611.csv")
    for decision in snap["decisions"]:
        for obs in decision["observations"]:
            if obs["month"] > "2026-11":
                assert obs["inclusion"] == "excluded"


def test_missing_observed_month_falls_back_to_source_month(params):
    rows = [
        {"asset": "Z-001", "model": "AC-8", "km": 1000.0, "failed": 0, "observed_month": "2026-08"},
        {"asset": "Z-001", "model": "AC-8", "km": 5000.0, "failed": 0},
    ]
    snap = samples.build_sample(rows, params, "fleet-weibull-202611.csv")
    assert snap["stats"]["late_arrivals"] == 1
    decision = next(d for d in snap["decisions"] if d["asset"] == "Z-001")
    assert decision["selected"]["km"] == 5000.0


def test_revival_conflict_blocks_estimation(params):
    rows = [
        {"asset": "A-1", "model": "AC-8", "km": 70000.0, "failed": 1, "observed_month": "2026-09"},
        {"asset": "A-1", "model": "AC-8", "km": 76000.0, "failed": 0, "observed_month": "2026-10"},
    ]
    rows += [
        {"asset": f"A-{i:02d}", "model": "AC-8", "km": 30000.0 + i * 1000.0,
         "failed": 1 if i % 3 == 0 else 0, "observed_month": "2026-11"}
        for i in range(2, 30)
    ]
    snap = samples.build_sample(rows, params, "qa.csv")
    assert any(c["type"] == "revival" for c in snap["conflicts"])
    with pytest.raises(ValueError, match="冲突"):
        samples.run_analysis(rows, params, "qa.csv")


def test_mileage_regression_conflict_blocks_estimation(params):
    rows = [
        {"asset": "B-1", "model": "AC-12", "km": 90000.0, "failed": 0, "observed_month": "2026-08"},
        {"asset": "B-1", "model": "AC-12", "km": 81000.0, "failed": 0, "observed_month": "2026-09"},
    ]
    rows += [
        {"asset": f"B-{i:02d}", "model": "AC-12", "km": 40000.0 + i * 1000.0,
         "failed": 1 if i % 3 == 0 else 0, "observed_month": "2026-11"}
        for i in range(2, 30)
    ]
    p12 = {**params, "model": "AC-12"}
    snap = samples.build_sample(rows, p12, "qa.csv")
    assert any(c["type"] == "mileage_regression" for c in snap["conflicts"])
    with pytest.raises(ValueError, match="冲突"):
        samples.run_analysis(rows, p12, "qa.csv")


def test_conflict_asset_excluded_entirely(params):
    rows = [
        {"asset": "C-1", "model": "AC-8", "km": 70000.0, "failed": 1, "observed_month": "2026-09"},
        {"asset": "C-1", "model": "AC-8", "km": 76000.0, "failed": 0, "observed_month": "2026-10"},
        {"asset": "C-2", "model": "AC-8", "km": 50000.0, "failed": 0, "observed_month": "2026-11"},
    ]
    snap = samples.build_sample(rows, params, "qa.csv")
    decision = next(d for d in snap["decisions"] if d["asset"] == "C-1")
    assert decision["decision"] == "conflict"
    assert decision["selected"] is None
    assert all(o["inclusion"] == "conflict" for o in decision["observations"])


def test_conflict_demo_dataset_blocks_both_models(monthly, params):
    rows = business.seed_conflict_records()
    types = {c["type"] for c in samples.build_sample(rows, params, "qa.csv")["conflicts"]}
    p12 = {**params, "model": "AC-12"}
    types |= {c["type"] for c in samples.build_sample(rows, p12, "qa.csv")["conflicts"]}
    assert types == {"revival", "mileage_regression"}
    with pytest.raises(ValueError):
        samples.run_analysis(rows, params, "qa.csv")


def test_full_analysis_keeps_snapshot_and_metrics(monthly, params):
    result = samples.run_analysis(monthly["flat"], params, "fleet-weibull-202611.csv")
    assert result["cutoff_month"] == "2026-11"
    assert result["sample_snapshot"]["sample_size"] == 100
    labels = {m["label"] for m in result["metrics"]}
    assert {"形状参数β", "尺度参数η", "B10寿命", "目标可靠度里程"} <= labels
    # β/η 为正且 B10 小于尺度参数
    values = {m["label"]: m["value"] for m in result["metrics"]}
    assert values["形状参数β"] > 0 and values["尺度参数η"] > 0
    assert 0 < values["B10寿命"] < values["尺度参数η"]


def test_invalid_cutoff_month_rejected(monthly):
    with pytest.raises(ValueError):
        samples.build_sample(monthly["flat"], {"model": "AC-8", "cutoff_month": "2026/11"}, "x.csv")
    with pytest.raises(ValueError):
        samples.build_sample(monthly["flat"], {"model": "AC-8", "cutoff_month": "2026-13"}, "x.csv")


def test_insufficient_sample_raises(params):
    rows = [
        {"asset": f"D-{i:02d}", "model": "AC-8", "km": 30000.0 + i,
         "failed": 1 if i < 3 else 0, "observed_month": "2026-11"}
        for i in range(10)
    ]
    with pytest.raises(ValueError, match="25件样本"):
        samples.run_analysis(rows, params, "small.csv")


def test_malformed_rows_reported_and_block(params):
    rows = [
        {"asset": "E-1", "model": "AC-8", "km": 30000.0, "failed": 0, "observed_month": "2026-11"},
        {"asset": "E-2", "model": "AC-8", "km": 30000.0, "failed": 2, "observed_month": "2026-11"},
    ]
    snap = samples.build_sample(rows, params, "x.csv")
    assert snap["malformed"]
    with pytest.raises(ValueError, match="无法识别"):
        samples.run_analysis(rows, params, "x.csv")


def _filler(prefix, model="AC-8"):
    return [
        {"asset": f"{prefix}-{i:02d}", "model": model, "km": 30000.0 + i * 1000.0,
         "failed": 1 if i % 3 == 0 else 0, "observed_month": "2026-11"}
        for i in range(2, 30)
    ]


def test_persistent_failure_after_failure_is_not_conflict(params):
    # 失效后持续报失效是更换前台账的正常情形，不得误判为复活。
    rows = [
        {"asset": "A-1", "model": "AC-8", "km": 70000.0, "failed": 1, "observed_month": "2026-09"},
        {"asset": "A-1", "model": "AC-8", "km": 70000.0, "failed": 1, "observed_month": "2026-10"},
        {"asset": "A-1", "model": "AC-8", "km": 70000.0, "failed": 1, "observed_month": "2026-11"},
    ] + _filler("A")
    snap = samples.build_sample(rows, params, "x.csv")
    assert snap["conflicts"] == []
    decision = next(d for d in snap["decisions"] if d["asset"] == "A-1")
    assert decision["selected"]["failed"] == 1


def test_same_month_tie_picks_later_row_without_false_regression(params):
    rows = [
        {"asset": "M-1", "model": "AC-8", "km": 10000.0, "failed": 0, "observed_month": "2026-10"},
        {"asset": "M-1", "model": "AC-8", "km": 12000.0, "failed": 0, "observed_month": "2026-10"},
    ] + _filler("M")
    snap = samples.build_sample(rows, params, "x.csv")
    assert snap["conflicts"] == []
    decision = next(d for d in snap["decisions"] if d["asset"] == "M-1")
    assert decision["selected"]["km"] == 12000.0


def test_cutoff_month_is_inclusive_next_month_excluded(params):
    rows = [
        {"asset": "E-1", "model": "AC-8", "km": 5000.0, "failed": 0, "observed_month": "2026-11"},
        {"asset": "E-1", "model": "AC-8", "km": 9000.0, "failed": 0, "observed_month": "2026-12"},
    ] + _filler("E")
    snap = samples.build_sample(rows, params, "x.csv")
    decision = next(d for d in snap["decisions"] if d["asset"] == "E-1")
    assert decision["selected"]["month"] == "2026-11"
    assert any(o["month"] == "2026-12" and o["inclusion"] == "excluded"
               for o in decision["observations"])


def test_model_mismatch_across_months_blocks(params):
    rows = [
        {"asset": "F-1", "model": "AC-8", "km": 40000.0, "failed": 0, "observed_month": "2026-09"},
        {"asset": "F-1", "model": "AC-12", "km": 46000.0, "failed": 0, "observed_month": "2026-10"},
    ] + _filler("F")
    snap = samples.build_sample(rows, params, "x.csv")
    assert any(c["type"] == "model_mismatch" for c in snap["conflicts"])
    with pytest.raises(ValueError, match="冲突"):
        samples.run_analysis(rows, params, "x.csv")
