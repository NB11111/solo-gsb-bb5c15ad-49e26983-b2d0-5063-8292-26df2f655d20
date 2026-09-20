"""核心展开逻辑测试（仅依赖标准库 + pytest）。"""

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app.scheduler import classify, expand_schedule

NY = ZoneInfo("America/New_York")
BERLIN = ZoneInfo("Europe/Berlin")


def test_classify_normal():
    kind, payload = classify(datetime(2026, 3, 7, 2, 30), NY)
    assert kind == "normal"
    assert payload[0] == datetime(2026, 3, 7, 7, 30, tzinfo=timezone.utc)


def test_classify_gap():
    # 2026-03-08 02:30 在纽约不存在（02:00 -> 03:00）
    kind, gap = classify(datetime(2026, 3, 8, 2, 30), NY)
    assert kind == "gap"
    assert gap == timedelta(hours=1)


def test_classify_fold():
    # 2026-11-01 01:30 在纽约出现两次
    kind, payload = classify(datetime(2026, 11, 1, 1, 30), NY)
    assert kind == "fold"
    first, second = payload
    assert first == datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc)  # EDT
    assert second == datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc)  # EST


def test_gap_skip():
    instances, skipped = expand_schedule(
        tz=NY,
        start_date=date(2026, 3, 8),
        end_date=date(2026, 3, 8),
        weekdays={0, 1, 2, 3, 4, 5, 6},
        local_time=time(2, 30),
        gap_strategy="skip",
        fold_strategy="first",
    )
    assert instances == []
    assert skipped == [date(2026, 3, 8)]


def test_gap_shift_forward():
    instances, skipped = expand_schedule(
        tz=NY,
        start_date=date(2026, 3, 8),
        end_date=date(2026, 3, 8),
        weekdays={6},
        local_time=time(2, 30),
        gap_strategy="shift_forward",
        fold_strategy="first",
    )
    assert skipped == []
    assert len(instances) == 1
    inst = instances[0]
    assert inst.reason == "gap_shifted_forward"
    # 02:30 + 1h = 03:30 EDT -> 07:30 UTC
    assert inst.utc == datetime(2026, 3, 8, 7, 30, tzinfo=timezone.utc)
    assert inst.utc.astimezone(NY).replace(tzinfo=None) == datetime(2026, 3, 8, 3, 30)


def test_fold_strategies():
    base = dict(
        tz=NY,
        start_date=date(2026, 11, 1),
        end_date=date(2026, 11, 1),
        weekdays={6},
        local_time=time(1, 30),
        gap_strategy="skip",
    )
    first, _ = expand_schedule(fold_strategy="first", **base)
    assert [i.reason for i in first] == ["fold_first"]
    assert first[0].utc == datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc)

    second, _ = expand_schedule(fold_strategy="second", **base)
    assert [i.reason for i in second] == ["fold_second"]
    assert second[0].utc == datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc)

    both, _ = expand_schedule(fold_strategy="both", **base)
    assert [i.reason for i in both] == ["fold_first", "fold_second"]
    assert both[0].utc < both[1].utc


def test_weekday_filter_and_utc_sort():
    # 2026-03-01 是周日；每周一、三 09:00，柏林时间
    instances, skipped = expand_schedule(
        tz=BERLIN,
        start_date=date(2026, 3, 1),
        end_date=date(2026, 3, 15),
        weekdays={0, 2},
        local_time=time(9, 0),
        gap_strategy="skip",
        fold_strategy="first",
    )
    assert skipped == []
    assert len(instances) == 4  # 3/2, 3/4, 3/9, 3/11
    utcs = [i.utc for i in instances]
    assert utcs == sorted(utcs)
    assert len(set(utcs)) == len(utcs)  # 已去重
    assert all(i.reason == "normal" for i in instances)


def test_berlin_gap_shift():
    # 柏林 2026-03-29 02:30 不存在（02:00 -> 03:00）
    instances, skipped = expand_instances = expand_schedule(
        tz=BERLIN,
        start_date=date(2026, 3, 29),
        end_date=date(2026, 3, 29),
        weekdays={6},
        local_time=time(2, 30),
        gap_strategy="shift_forward",
        fold_strategy="both",
    )
    assert skipped == []
    assert len(instances) == 1
    inst = instances[0]
    assert inst.reason == "gap_shifted_forward"
    # 02:30 + 1h = 03:30 CEST -> 01:30 UTC
    assert inst.utc == datetime(2026, 3, 29, 1, 30, tzinfo=timezone.utc)
