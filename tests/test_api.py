"""API 层测试：正常展开与 422 校验。"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

BASE = {
    "timezone": "America/New_York",
    "start_date": "2026-03-08",
    "end_date": "2026-03-08",
    "weekdays": [6],
    "local_time": "02:30",
    "gap_strategy": "shift_forward",
    "fold_strategy": "both",
}


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_expand_gap_shift_forward():
    resp = client.post("/expand", json=BASE)
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert body["skipped_count"] == 0
    inst = body["instances"][0]
    assert inst["local"] == "2026-03-08T03:30:00-04:00"
    assert inst["offset"] == "-04:00"
    assert inst["utc"] == "2026-03-08T07:30:00Z"
    assert inst["reason"] == "gap_shifted_forward"


def test_expand_gap_skip():
    resp = client.post("/expand", json={**BASE, "gap_strategy": "skip"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 0
    assert body["skipped_count"] == 1
    assert body["skipped_dates"] == ["2026-03-08"]


def test_expand_fold_both():
    payload = {
        **BASE,
        "start_date": "2026-11-01",
        "end_date": "2026-11-01",
        "local_time": "01:30",
    }
    resp = client.post("/expand", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 2
    first, second = body["instances"]
    assert first["reason"] == "fold_first"
    assert first["offset"] == "-04:00"
    assert first["utc"] == "2026-11-01T05:30:00Z"
    assert second["reason"] == "fold_second"
    assert second["offset"] == "-05:00"
    assert second["utc"] == "2026-11-01T06:30:00Z"
    # 本地时钟面相同，但为两个不同的 UTC 实例
    assert first["local"] == "2026-11-01T01:30:00-04:00"
    assert second["local"] == "2026-11-01T01:30:00-05:00"


def test_invalid_timezone_422():
    resp = client.post("/expand", json={**BASE, "timezone": "Not/AZone"})
    assert resp.status_code == 422


def test_reversed_range_422():
    resp = client.post(
        "/expand",
        json={**BASE, "start_date": "2026-04-01", "end_date": "2026-03-01"},
    )
    assert resp.status_code == 422


def test_range_over_366_days_422():
    resp = client.post(
        "/expand",
        json={**BASE, "start_date": "2026-01-01", "end_date": "2027-01-03"},
    )
    assert resp.status_code == 422


def test_range_exactly_366_days_ok():
    resp = client.post(
        "/expand",
        json={
            **BASE,
            "start_date": "2026-01-01",
            "end_date": "2027-01-01",  # 相差 366 天
            "local_time": "09:00",
        },
    )
    assert resp.status_code == 200


def test_invalid_weekday_422():
    resp = client.post("/expand", json={**BASE, "weekdays": [7]})
    assert resp.status_code == 422


def test_empty_weekdays_422():
    resp = client.post("/expand", json={**BASE, "weekdays": []})
    assert resp.status_code == 422
