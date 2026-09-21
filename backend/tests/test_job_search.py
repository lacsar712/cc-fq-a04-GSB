"""API tests for GET /api/jobs/search (metric-threshold search).

Uses an in-memory SQLite DB via dependency override; DATABASE_URL is pointed
at sqlite by tests/conftest.py before any app module is imported.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import create_access_token
from app.database import Base, get_db
from app.main import app
from app.models import Job
from app.pipeline.runner import create_job_stages, run_pipeline_sync


DATA_DIR = Path(__file__).resolve().parent.parent / "data"


engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db

# 一条"合格"作业：指标已知，用于自测场景（下限命中 → 抬高变空）
GOOD_METRICS = {"reads": 2, "mean_quality": 35.5, "n_rate": 0.25}
OTHER_METRICS = {"reads": 100, "mean_quality": 20.0, "n_rate": 0.01}


def _auth(username: str, role: str) -> dict:
    return {"Authorization": f"Bearer {create_access_token(username, role)}"}


@pytest.fixture()
def client():
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    db.add_all(
        [
            Job(
                sample_name="demo-good-r1",
                status="success",
                created_by="bioops",
                metrics=GOOD_METRICS,
                fastq_snapshot="@A\nACGT\n+\nIIII\n",
            ),
            Job(
                sample_name="bulk-run",
                status="success",
                created_by="bioops",
                metrics=OTHER_METRICS,
                fastq_snapshot="@A\nACGT\n+\nIIII\n",
            ),
            Job(
                sample_name="demo-broken-malformed",
                status="failed",
                created_by="bioops",
                metrics=None,
                error_message="parse error",
                fastq_snapshot="@A\nACGT\nX\nIIII\n",
            ),
        ]
    )
    db.commit()
    db.close()
    with TestClient(app) as c:
        yield c
    Base.metadata.drop_all(bind=engine)


def test_search_hits_with_qualified_mean_quality(client):
    """自测场景 1：用合格作业的 mean_quality 作下限 → 命中该作业。"""
    resp = client.get(
        "/api/jobs/search",
        params={"min_mean_quality": GOOD_METRICS["mean_quality"]},
        headers=_auth("bioops", "bioops"),
    )
    assert resp.status_code == 200
    rows = resp.json()
    names = {r["sample_name"] for r in rows}
    assert "demo-good-r1" in names
    hit = next(r for r in rows if r["sample_name"] == "demo-good-r1")
    assert hit["status"] == "success"
    assert hit["created_by"] == "bioops"
    assert hit["metrics"]["mean_quality"] == GOOD_METRICS["mean_quality"]


def test_search_empty_when_threshold_raised(client):
    """自测场景 2：抬高下限 → 空列表，且绝不回退为全量。"""
    resp = client.get(
        "/api/jobs/search",
        params={"min_mean_quality": GOOD_METRICS["mean_quality"] + 0.1},
        headers=_auth("bioops", "bioops"),
    )
    assert resp.status_code == 200
    rows = resp.json()
    assert all(
        r["metrics"]["mean_quality"] > GOOD_METRICS["mean_quality"] for r in rows
    )
    assert "demo-good-r1" not in {r["sample_name"] for r in rows}

    # 抬到所有作业都达不到 → 必须为空，而不是全量 3 条
    resp = client.get(
        "/api/jobs/search",
        params={"min_mean_quality": 999},
        headers=_auth("bioops", "bioops"),
    )
    assert resp.status_code == 200
    assert resp.json() == []


def test_search_combined_thresholds(client):
    """读段数下限 + N 率上限组合（AND 语义）。"""
    resp = client.get(
        "/api/jobs/search",
        params={"min_reads": 2, "max_n_rate": 0.25},
        headers=_auth("bioops", "bioops"),
    )
    assert resp.status_code == 200
    names = {r["sample_name"] for r in resp.json()}
    assert names == {"demo-good-r1", "bulk-run"}

    # 收紧 N 率上限后只剩 bulk-run（n_rate=0.01）
    resp = client.get(
        "/api/jobs/search",
        params={"min_reads": 2, "max_n_rate": 0.1},
        headers=_auth("bioops", "bioops"),
    )
    assert resp.status_code == 200
    assert {r["sample_name"] for r in resp.json()} == {"bulk-run"}

    # 读段数下限超过两者 → 空
    resp = client.get(
        "/api/jobs/search",
        params={"min_reads": 101},
        headers=_auth("bioops", "bioops"),
    )
    assert resp.status_code == 200
    assert resp.json() == []


def test_search_excludes_jobs_without_metrics(client):
    """失败/无指标作业不满足任何数值门槛。"""
    resp = client.get(
        "/api/jobs/search",
        params={"min_reads": 0},
        headers=_auth("bioops", "bioops"),
    )
    assert resp.status_code == 200
    assert "demo-broken-malformed" not in {r["sample_name"] for r in resp.json()}


def test_search_requires_at_least_one_threshold(client):
    resp = client.get("/api/jobs/search", headers=_auth("bioops", "bioops"))
    assert resp.status_code == 400
    assert "门槛" in resp.json()["detail"]


def test_search_allows_both_roles(client):
    """两角色（bioops / auditor）都可使用检索。"""
    for username, role in (("bioops", "bioops"), ("auditor", "auditor")):
        resp = client.get(
            "/api/jobs/search",
            params={"min_reads": 2},
            headers=_auth(username, role),
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 2


def test_search_requires_auth(client):
    resp = client.get("/api/jobs/search", params={"min_reads": 1})
    assert resp.status_code == 401


def test_search_rejects_negative_threshold(client):
    resp = client.get(
        "/api/jobs/search",
        params={"max_n_rate": -0.5},
        headers=_auth("bioops", "bioops"),
    )
    assert resp.status_code == 422


def test_search_with_real_pipeline_output(client):
    """端到端自测：真实跑 good.fastq 流水线 → 用其 mean_quality 检索命中 → 抬高变空。"""
    db = TestingSessionLocal()
    try:
        job = Job(
            sample_name="demo-good-r1",
            status="pending",
            created_by="bioops",
            fastq_snapshot=(DATA_DIR / "good.fastq").read_text(encoding="utf-8"),
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        create_job_stages(db, job.id)
        run_pipeline_sync(db, job)
        job_id = job.id
        mean_q = job.metrics["mean_quality"]
        reads = job.metrics["reads"]
        n_rate = job.metrics["n_rate"]
    finally:
        db.close()
    assert job.status == "success"

    headers = _auth("auditor", "auditor")

    # 用该作业的实际指标作门槛 → 必命中
    resp = client.get(
        "/api/jobs/search",
        params={
            "min_reads": reads,
            "min_mean_quality": mean_q,
            "max_n_rate": n_rate,
        },
        headers=headers,
    )
    assert resp.status_code == 200
    hit_ids = {r["id"] for r in resp.json()}
    assert job_id in hit_ids

    # 平均质量下限抬高一点 → 该作业落空
    resp = client.get(
        "/api/jobs/search",
        params={"min_mean_quality": mean_q + 0.001},
        headers=headers,
    )
    assert resp.status_code == 200
    assert job_id not in {r["id"] for r in resp.json()}
