"""服务端指标门槛检索 /api/jobs/search 的接口测试（SQLite + TestClient）。

测试库由 conftest.py 在导入 app 之前通过 DATABASE_URL 切到临时 SQLite。
"""

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Job  # noqa: E402


GOOD_MEAN = 38.5


def _token(client: TestClient, username: str, password: str) -> str:
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200
    return resp.json()["access_token"]


@pytest.fixture(scope="module")
def client():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    db.query(Job).delete()
    db.add_all(
        [
            Job(
                sample_name="demo-good-r1",
                status="success",
                created_by="bioops",
                fastq_snapshot="@good",
                metrics={"reads": 2, "mean_quality": GOOD_MEAN, "n_rate": 0.25},
            ),
            Job(
                sample_name="demo-good-hq",
                status="success",
                created_by="bioops",
                fastq_snapshot="@hq",
                metrics={"reads": 10, "mean_quality": 40.0, "n_rate": 0.0},
            ),
            Job(
                sample_name="demo-broken-malformed",
                status="failed",
                created_by="bioops",
                fastq_snapshot="@broken",
                metrics=None,
            ),
        ]
    )
    db.commit()
    db.close()
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def bioops_headers(client):
    return {"Authorization": f"Bearer {_token(client, 'bioops', 'fastq123456')}"}


@pytest.fixture(scope="module")
def auditor_headers(client):
    return {"Authorization": f"Bearer {_token(client, 'auditor', 'audit123456')}"}


def _names(payload):
    return {row["sample_name"] for row in payload}


def test_min_reads_hits_threshold_rows(client, bioops_headers):
    r = client.get("/api/jobs/search", params={"min_reads": 10}, headers=bioops_headers)
    assert r.status_code == 200
    assert _names(r.json()) == {"demo-good-hq"}
    for row in r.json():
        assert row["metrics"]["reads"] >= 10


def test_mean_quality_threshold_self_check(client, bioops_headers):
    # 以合格作业的平均质量为下限（含边界）可以命中
    r = client.get(
        "/api/jobs/search",
        params={"min_mean_quality": GOOD_MEAN},
        headers=bioops_headers,
    )
    assert r.status_code == 200
    assert "demo-good-r1" in _names(r.json())
    for row in r.json():
        assert row["metrics"]["mean_quality"] >= GOOD_MEAN

    # 抬高到所有作业之上 -> 空表，绝不回退全量
    r = client.get(
        "/api/jobs/search",
        params={"min_mean_quality": 99.0},
        headers=bioops_headers,
    )
    assert r.status_code == 200
    assert r.json() == []


def test_combined_thresholds_are_AND(client, bioops_headers):
    r = client.get(
        "/api/jobs/search",
        params={"min_reads": 1, "min_mean_quality": 39.0, "max_n_rate": 0.1},
        headers=bioops_headers,
    )
    assert r.status_code == 200
    # 只有高 Q、零 N 的作业同时满足三项
    assert _names(r.json()) == {"demo-good-hq"}


def test_max_n_rate_filters_high_n(client, bioops_headers):
    r = client.get(
        "/api/jobs/search",
        params={"max_n_rate": 0.2},
        headers=bioops_headers,
    )
    names = _names(r.json())
    assert "demo-good-hq" in names
    assert "demo-good-r1" not in names  # n_rate=0.25 超上限
    for row in r.json():
        assert row["metrics"]["n_rate"] <= 0.2


def test_no_thresholds_returns_empty_not_full_list(client, bioops_headers):
    r = client.get("/api/jobs/search", headers=bioops_headers)
    assert r.status_code == 200
    assert r.json() == []


def test_rows_without_metrics_never_match(client, bioops_headers):
    r = client.get(
        "/api/jobs/search",
        params={"min_reads": 0, "min_mean_quality": 0, "max_n_rate": 1},
        headers=bioops_headers,
    )
    assert "demo-broken-malformed" not in _names(r.json())


def test_auditor_can_search(client, auditor_headers):
    # 两角色都能用：只读账号同样可检索
    r = client.get(
        "/api/jobs/search",
        params={"min_mean_quality": GOOD_MEAN},
        headers=auditor_headers,
    )
    assert r.status_code == 200
    assert "demo-good-r1" in _names(r.json())


def test_anonymous_rejected(client):
    r = client.get("/api/jobs/search", params={"min_reads": 1})
    assert r.status_code == 401


def test_invalid_thresholds_rejected(client, bioops_headers):
    r = client.get(
        "/api/jobs/search",
        params={"max_n_rate": 1.5},
        headers=bioops_headers,
    )
    assert r.status_code == 400
    r = client.get(
        "/api/jobs/search",
        params={"min_reads": -1},
        headers=bioops_headers,
    )
    assert r.status_code == 400


def test_search_route_shadowing_detail_route(client, bioops_headers):
    # /jobs/search 命中检索接口而非 /jobs/{job_id}
    r = client.get("/api/jobs/search", params={"min_reads": 1}, headers=bioops_headers)
    assert r.status_code == 200
    assert isinstance(r.json(), list)
