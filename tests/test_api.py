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


DAILY = {"weekdays": [0, 1, 2, 3, 4, 5, 6]}

# 柏林每日 09:00（2026-07 为 CEST，UTC 07:00），每次 90 分钟
A_BERLIN_MORNING = {
    "timezone": "Europe/Berlin",
    "start_date": "2026-07-01",
    "end_date": "2026-07-03",
    **DAILY,
    "local_time": "09:00",
    "gap_strategy": "skip",
    "fold_strategy": "first",
    "duration_minutes": 90,
}

# 纽约每日 03:30 EDT = UTC 07:30，每次 60 分钟，与柏林 90 分钟段重叠 60 分钟
B_NY_MORNING = {
    "timezone": "America/New_York",
    "start_date": "2026-07-01",
    "end_date": "2026-07-03",
    **DAILY,
    "local_time": "03:30",
    "gap_strategy": "skip",
    "fold_strategy": "first",
    "duration_minutes": 60,
}


def test_overlaps_cross_timezone():
    resp = client.post(
        "/overlaps", json={"a": A_BERLIN_MORNING, "b": B_NY_MORNING}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 3
    days = ["01", "02", "03"]
    for item, day in zip(body["overlaps"], days):
        assert item["a_instance_start_utc"] == f"2026-07-{day}T07:00:00Z"
        assert item["b_instance_start_utc"] == f"2026-07-{day}T07:30:00Z"
        assert item["overlap_start_utc"] == f"2026-07-{day}T07:30:00Z"
        assert item["overlap_end_utc"] == f"2026-07-{day}T08:30:00Z"
        assert item["overlap_minutes"] == 60


def test_overlaps_touching_boundary_not_conflict():
    # A [09:00, 10:00) 柏林 = [07:00, 08:00) UTC；B 04:00 EDT = 08:00 UTC 起，
    # 仅边界相接，不算冲突
    resp = client.post(
        "/overlaps",
        json={
            "a": {**A_BERLIN_MORNING, "duration_minutes": 60},
            "b": {**B_NY_MORNING, "local_time": "04:00", "duration_minutes": 60},
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {"count": 0, "overlaps": []}


def test_overlaps_sorted_by_overlap_start():
    # 两天各一段重叠：A [07:00,09:00)Z，B [08:30,09:30)Z，每天重叠 30 分钟
    payload_a = {
        **A_BERLIN_MORNING,
        "start_date": "2026-07-01",
        "end_date": "2026-07-02",
        "duration_minutes": 120,  # [07:00, 09:00)
    }
    payload_b = {
        **B_NY_MORNING,
        "start_date": "2026-07-01",
        "end_date": "2026-07-02",
        "local_time": "04:30",  # 08:30Z
        "duration_minutes": 60,  # [08:30, 09:30)，重叠 [08:30, 09:00) 30 分钟
    }
    resp = client.post("/overlaps", json={"a": payload_a, "b": payload_b})
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 2
    starts = [o["overlap_start_utc"] for o in body["overlaps"]]
    assert starts == sorted(starts)
    assert starts == ["2026-07-01T08:30:00Z", "2026-07-02T08:30:00Z"]
    assert all(o["overlap_minutes"] == 30 for o in body["overlaps"])


def test_overlaps_fold_both_enumerated():
    # 纽约 2026-11-01 01:30 fold=both：05:30Z 与 06:30Z 两个实例（各 60 分钟）
    # 另一侧 UTC 06:00Z 起 60 分钟（柏林 07:00），与两个 fold 实例分别重叠 30 分钟
    a = {
        "timezone": "Europe/Berlin",
        "start_date": "2026-11-01",
        "end_date": "2026-11-01",
        **DAILY,
        "local_time": "07:00",
        "gap_strategy": "skip",
        "fold_strategy": "first",
        "duration_minutes": 60,
    }
    b = {
        "timezone": "America/New_York",
        "start_date": "2026-11-01",
        "end_date": "2026-11-01",
        **DAILY,
        "local_time": "01:30",
        "gap_strategy": "skip",
        "fold_strategy": "both",
        "duration_minutes": 60,
    }
    resp = client.post("/overlaps", json={"a": a, "b": b})
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 2
    first, second = body["overlaps"]
    assert first["b_instance_start_utc"] == "2026-11-01T05:30:00Z"
    assert first["overlap_start_utc"] == "2026-11-01T06:00:00Z"
    assert first["overlap_end_utc"] == "2026-11-01T06:30:00Z"
    assert second["b_instance_start_utc"] == "2026-11-01T06:30:00Z"
    assert second["overlap_start_utc"] == "2026-11-01T06:30:00Z"
    assert second["overlap_end_utc"] == "2026-11-01T07:00:00Z"


def test_overlaps_gap_skipped_side_has_no_instances():
    # 纽约 2026-03-08 02:30 春跳 + skip -> B 侧无实例，无冲突
    a = {
        "timezone": "UTC",
        "start_date": "2026-03-08",
        "end_date": "2026-03-08",
        **DAILY,
        "local_time": "07:30",
        "gap_strategy": "skip",
        "fold_strategy": "first",
        "duration_minutes": 60,
    }
    b = {
        "timezone": "America/New_York",
        "start_date": "2026-03-08",
        "end_date": "2026-03-08",
        **DAILY,
        "local_time": "02:30",
        "gap_strategy": "skip",
        "fold_strategy": "first",
        "duration_minutes": 60,
    }
    resp = client.post("/overlaps", json={"a": a, "b": b})
    assert resp.status_code == 200
    assert resp.json() == {"count": 0, "overlaps": []}


def test_overlaps_gap_shifted_forward_still_conflicts():
    # 同一春跳日 shift_forward 后实例落在 03:30 EDT = 07:30Z，与 A 冲突
    a = {
        "timezone": "UTC",
        "start_date": "2026-03-08",
        "end_date": "2026-03-08",
        **DAILY,
        "local_time": "07:00",
        "gap_strategy": "skip",
        "fold_strategy": "first",
        "duration_minutes": 120,
    }
    b = {
        "timezone": "America/New_York",
        "start_date": "2026-03-08",
        "end_date": "2026-03-08",
        **DAILY,
        "local_time": "02:30",
        "gap_strategy": "shift_forward",
        "fold_strategy": "first",
        "duration_minutes": 60,
    }
    resp = client.post("/overlaps", json={"a": a, "b": b})
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    item = body["overlaps"][0]
    assert item["a_instance_start_utc"] == "2026-03-08T07:00:00Z"
    assert item["b_instance_start_utc"] == "2026-03-08T07:30:00Z"
    assert item["overlap_start_utc"] == "2026-03-08T07:30:00Z"
    assert item["overlap_end_utc"] == "2026-03-08T08:30:00Z"
    assert item["overlap_minutes"] == 60


def test_overlaps_duration_bounds_422():
    for bad in (0, 1441):
        resp = client.post(
            "/overlaps",
            json={"a": {**A_BERLIN_MORNING, "duration_minutes": bad}, "b": B_NY_MORNING},
        )
        assert resp.status_code == 422


def test_overlaps_range_over_366_days_422():
    resp = client.post(
        "/overlaps",
        json={
            "a": {
                **A_BERLIN_MORNING,
                "start_date": "2026-01-01",
                "end_date": "2027-01-03",
            },
            "b": B_NY_MORNING,
        },
    )
    assert resp.status_code == 422


def test_overlaps_invalid_timezone_422():
    resp = client.post(
        "/overlaps",
        json={"a": {**A_BERLIN_MORNING, "timezone": "Mars/Phobos"}, "b": B_NY_MORNING},
    )
    assert resp.status_code == 422
