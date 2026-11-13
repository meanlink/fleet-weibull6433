"""数据集更新失效标记、重算口径保留与样本快照回看的集成测试。

使用 SQLite 临时库与 FastAPI TestClient，不依赖 Docker/PostgreSQL/Redis。
运行：PYTHONPATH=backend python -m pytest test-artifacts/gb-1017/tests -q
"""
import importlib
import os
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BACKEND = Path(__file__).resolve().parents[3] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'it.db'}")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("JWT_SECRET", "it-secret")
    monkeypatch.setenv("ARTIFACT_DIR", str(tmp_path / "artifacts"))
    # 确保配置与模型在隔离环境变量下重新装配。
    for name in list(sys.modules):
        if name.startswith("src."):
            del sys.modules[name]
    database = importlib.import_module("src.config.database")
    models_pkg = importlib.import_module("src.models")
    security = importlib.import_module("src.utils.security")
    main = importlib.import_module("src.main")
    worker = importlib.import_module("src.worker")
    business = importlib.import_module("src.services.business")

    database.Base.metadata.create_all(database.engine)
    with database.Session() as db:
        for role, name in [("admin", "管理员"), ("analyst", "分析员"), ("viewer", "观察员")]:
            db.add(models_pkg.User(email=f"{role}@demo.local", name=name, role=role,
                               password=security.hash_password("Demo123!")))
        db.commit()

    c = TestClient(main.app)
    headers = {}
    for role in ("admin", "analyst", "viewer"):
        resp = c.post("/auth/login", json={"email": f"{role}@demo.local", "password": "Demo123!"})
        headers[role] = {"Authorization": f"Bearer {resp.json()['access_token']}"}

    monthly = business.seed_monthly_records()
    params = {p["key"]: p["default"] for p in business.metadata()["parameters"]}
    yield c, headers, worker, business, monthly, params, models_pkg, database


def _create_dataset(c, headers, name, rows, source="fleet-weibull-202611.csv"):
    resp = c.post("/datasets", headers=headers["analyst"],
                  json={"name": name, "source": source, "records": rows})
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_preview_and_job_flow_with_snapshot(client):
    c, headers, worker, business, monthly, params, models, database = client
    ds = _create_dataset(c, headers, business.metadata()["dataset_name"], monthly["flat"])
    resp = c.post("/jobs/preview", headers=headers["viewer"],
                  json={"dataset_id": ds["id"], "params": params})
    assert resp.status_code == 200
    snapshot = resp.json()
    assert snapshot["sample_size"] == 100 and snapshot["conflicts"] == []
    assert snapshot["dataset"]["checksum"] == ds["checksum"]

    resp = c.post("/jobs", headers=headers["analyst"],
                  json={"name": "11月口径", "dataset_id": ds["id"], "params": params})
    job = resp.json()
    assert job["stale"] is False and job["dataset_checksum"] == ds["checksum"]
    worker.execute(job["id"])
    resp = c.get(f"/jobs/{job['id']}/sample", headers=headers["viewer"])
    assert resp.status_code == 200
    assert resp.json()["cutoff_month"] == "2026-11"
    assert len(resp.json()["decisions"]) == 200


def test_conflict_blocks_job_creation(client):
    c, headers, worker, business, monthly, params, models, database = client
    ds = _create_dataset(c, headers, "冲突台账批次.csv", business.seed_conflict_records(), "qa.csv")
    resp = c.post("/jobs/preview", headers=headers["viewer"],
                  json={"dataset_id": ds["id"], "params": params})
    assert len(resp.json()["conflicts"]) == 2
    resp = c.post("/jobs", headers=headers["analyst"],
                  json={"name": "冲突估计", "dataset_id": ds["id"], "params": params})
    assert resp.status_code == 422
    assert "冲突" in resp.json()["detail"]


def test_stale_marking_recompute_and_approved_snapshot(client):
    c, headers, worker, business, monthly, params, models, database = client
    ds = _create_dataset(c, headers, business.metadata()["dataset_name"], monthly["flat"])
    job = c.post("/jobs", headers=headers["analyst"],
                 json={"name": "基线估计", "dataset_id": ds["id"], "params": params}).json()
    worker.execute(job["id"])
    c.post(f"/jobs/{job['id']}/approve", headers=headers["admin"])

    # 接入源数据更新批次，旧任务（含已批准）标记失效。
    updated = monthly["flat"] + [
        {"asset": "AC-8-NEW", "model": "AC-8", "km": 42000.0, "failed": 0, "observed_month": "2026-11"}
    ]
    ds2 = _create_dataset(c, headers, "12月补报批次.csv", updated, "fleet-weibull-202612.csv")
    old = c.get(f"/jobs/{job['id']}", headers=headers["viewer"]).json()
    assert old["stale"] is True and old["approved"] is True
    # 已批准任务的当时样本快照仍可回看。
    snap = c.get(f"/jobs/{job['id']}/sample", headers=headers["viewer"]).json()
    assert snap["cutoff_month"] == "2026-11" and len(snap["decisions"]) == 200

    # 重算沿用原截止月。
    job2 = c.post("/jobs", headers=headers["analyst"],
                  json={"name": "重算保留截止月", "dataset_id": ds2["id"], "params": params}).json()
    assert job2["params"]["cutoff_month"] == "2026-11"
    worker.execute(job2["id"])
    with database.Session() as db:
        j2 = db.get(models.Job, job2["id"])
        assert j2.status == "completed" and j2.stale is False
        assert j2.result["sample_snapshot"]["stats"]["assets"] == 201
        assert j2.result["sample_snapshot"]["stats"]["after_cutoff"] == 2

    # 审计留痕。
    actions = [a["action"] for a in c.get("/audits", headers=headers["admin"]).json()]
    assert "job.stale_marked" in actions


def test_recompute_with_earlier_cutoff_keeps_that_cutoff(client):
    c, headers, worker, business, monthly, params, models, database = client
    ds = _create_dataset(c, headers, business.metadata()["dataset_name"], monthly["flat"])
    early = {**params, "cutoff_month": "2026-09"}
    job = c.post("/jobs", headers=headers["analyst"],
                 json={"name": "09月口径", "dataset_id": ds["id"], "params": early}).json()
    worker.execute(job["id"])
    with database.Session() as db:
        j = db.get(models.Job, job["id"])
        assert j.result["sample_snapshot"]["cutoff_month"] == "2026-09"


def test_viewer_cannot_create_and_bad_month_rejected(client):
    c, headers, worker, business, monthly, params, models, database = client
    ds = _create_dataset(c, headers, business.metadata()["dataset_name"], monthly["flat"])
    resp = c.post("/jobs", headers=headers["viewer"],
                  json={"name": "无权任务", "dataset_id": ds["id"], "params": params})
    assert resp.status_code == 403
    resp = c.post("/jobs", headers=headers["analyst"],
                  json={"name": "坏月份", "dataset_id": ds["id"],
                        "params": {**params, "cutoff_month": "202611"}})
    assert resp.status_code == 422


def test_optional_observed_month_accepted(client):
    c, headers, worker, business, monthly, params, models, database = client
    legacy = [{"asset": f"L-{i:02d}", "model": "AC-8", "km": 30000.0 + i,
               "failed": 1 if i < 12 else 0} for i in range(30)]
    ds = _create_dataset(c, headers, "无观测月旧台账.csv", legacy, "fleet-weibull-202611.csv")
    resp = c.post("/jobs/preview", headers=headers["viewer"],
                  json={"dataset_id": ds["id"], "params": params})
    assert resp.status_code == 200
    assert resp.json()["stats"]["late_arrivals"] == 30
