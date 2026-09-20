"""FastAPI 入口：民用日程展开服务。"""

from __future__ import annotations

from datetime import timedelta
from zoneinfo import ZoneInfo

from fastapi import FastAPI

from .models import (
    ExpandRequest,
    ExpandResponse,
    OverlapsRequest,
    OverlapsResponse,
    ScheduleSpec,
)
from .scheduler import (
    Instance,
    expand_schedule,
    find_overlaps,
    format_utc,
    instance_payload,
)

app = FastAPI(
    title="Civil Schedule Expander",
    version="1.0.0",
    description=(
        "基于标准库 zoneinfo 的民用日程展开 API："
        "通过 UTC 往返校验区分正常 / 跳时 / 叠时，"
        "按策略生成去重且按 UTC 稳定排序的实例。"
    ),
)


@app.get("/health", summary="健康检查")
def health() -> dict:
    return {"status": "ok"}


@app.post("/expand", response_model=ExpandResponse, summary="展开日程")
def expand(req: ExpandRequest) -> ExpandResponse:
    tz = ZoneInfo(req.timezone)
    instances, skipped = expand_schedule(
        tz=tz,
        start_date=req.start_date,
        end_date=req.end_date,
        weekdays=req.weekdays,
        local_time=req.local_time,
        gap_strategy=req.gap_strategy.value,
        fold_strategy=req.fold_strategy.value,
    )
    return ExpandResponse(
        timezone=req.timezone,
        count=len(instances),
        skipped_count=len(skipped),
        skipped_dates=[d.isoformat() for d in skipped],
        instances=[instance_payload(i, tz) for i in instances],
    )


def _expand_spec(spec: ScheduleSpec) -> tuple[list[Instance], timedelta]:
    """按 /expand 同款参数与 gap/fold 策略展开一侧日程。"""
    tz = ZoneInfo(spec.timezone)
    instances, _skipped = expand_schedule(
        tz=tz,
        start_date=spec.start_date,
        end_date=spec.end_date,
        weekdays=spec.weekdays,
        local_time=spec.local_time,
        gap_strategy=spec.gap_strategy.value,
        fold_strategy=spec.fold_strategy.value,
    )
    return instances, timedelta(minutes=spec.duration_minutes)


@app.post(
    "/overlaps",
    response_model=OverlapsResponse,
    summary="判断两个民用周期日程在真实时间轴上的冲突",
)
def overlaps(req: OverlapsRequest) -> OverlapsResponse:
    instances_a, duration_a = _expand_spec(req.a)
    instances_b, duration_b = _expand_spec(req.b)
    found = find_overlaps(instances_a, instances_b, duration_a, duration_b)
    return OverlapsResponse(
        count=len(found),
        overlaps=[
            {
                "a_instance_start_utc": format_utc(o.a_start),
                "b_instance_start_utc": format_utc(o.b_start),
                "overlap_start_utc": format_utc(o.overlap_start),
                "overlap_end_utc": format_utc(o.overlap_end),
                "overlap_minutes": o.overlap_minutes,
            }
            for o in found
        ],
    )
