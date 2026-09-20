"""核心展开逻辑：UTC 往返校验区分正常 / 跳时（gap）/ 叠时（fold）。

往返校验原理（PEP 495）：对一个朴素本地时刻分别按 fold=0 / fold=1
赋予时区，换算到 UTC 后再换算回来；能还原原时刻的解释才是真实存在的。

- 两种 fold 都有效且 UTC 相同 -> 正常时刻
- 两种 fold 都有效但 UTC 不同 -> 叠时（秋回，本地时刻出现两次）
- 仅一种 fold 有效          -> 正常时刻（罕见过渡边界）
- 两种 fold 都无效          -> 跳时（春跳，本地时刻不存在）
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

Kind = Literal["normal", "fold", "gap"]


@dataclass(frozen=True)
class Instance:
    """一个展开出的日程实例（以 UTC 时刻为唯一标识）。"""

    utc: datetime
    reason: str


@dataclass(frozen=True)
class Overlap:
    """两个半开 UTC 区间跨侧重叠的一段。

    区间 A=[a_start, a_start+duration_a) 与 B=[b_start, b_start+duration_b)
    严格相交（lo < hi）时产生一条记录。
    """

    a_start: datetime
    b_start: datetime
    overlap_start: datetime
    overlap_end: datetime

    @property
    def overlap_minutes(self) -> int:
        """重叠时长（分钟，向下取整）。"""
        return (self.overlap_end - self.overlap_start) // timedelta(minutes=1)


def _valid_interpretations(naive: datetime, tz: ZoneInfo) -> dict[int, datetime]:
    """返回 {fold: UTC 时刻}，仅保留能通过 UTC 往返校验的解释。"""
    valid: dict[int, datetime] = {}
    for fold in (0, 1):
        aware = naive.replace(tzinfo=tz, fold=fold)
        utc = aware.astimezone(timezone.utc)
        if utc.astimezone(tz).replace(tzinfo=None) == naive:
            valid[fold] = utc
    return valid


def classify(
    naive: datetime, tz: ZoneInfo
) -> tuple[Literal["normal", "fold"], list[datetime]] | tuple[Literal["gap"], timedelta]:
    """对朴素本地时刻分类。

    返回 ("normal", [utc])、("fold", [first_utc, second_utc])
    或 ("gap", gap_delta)。gap_delta 为跳变差值（前移量），
    即 naive + gap_delta 是政策上“顺延”后的本地时刻。
    """
    valid = _valid_interpretations(naive, tz)
    if len(valid) == 2:
        if valid[0] == valid[1]:
            return "normal", [valid[0]]
        # fold=0 对应过渡前偏移，UTC 更早，即“第一次出现”
        return "fold", [valid[0], valid[1]]
    if len(valid) == 1:
        return "normal", [next(iter(valid.values()))]

    # 跳时：fold=0 按过渡前偏移解释，fold=1 按过渡后偏移解释，
    # 两者之差即为时钟向前跳变的差值。
    utc_pre = naive.replace(tzinfo=tz, fold=0).astimezone(timezone.utc)
    utc_post = naive.replace(tzinfo=tz, fold=1).astimezone(timezone.utc)
    return "gap", utc_pre - utc_post


def expand_schedule(
    *,
    tz: ZoneInfo,
    start_date: date,
    end_date: date,
    weekdays: set[int],
    local_time: time,
    gap_strategy: str,
    fold_strategy: str,
) -> tuple[list[Instance], list[date]]:
    """展开日程。

    返回 (实例列表, 被跳过的本地日期列表)。实例按 UTC 去重（保留先出现者）
    并按 UTC 稳定排序。
    """
    instances: list[Instance] = []
    skipped: list[date] = []

    day = start_date
    one_day = timedelta(days=1)
    while day <= end_date:
        if day.weekday() in weekdays:
            naive = datetime.combine(day, local_time)
            kind, payload = classify(naive, tz)

            if kind == "normal":
                instances.append(Instance(payload[0], "normal"))
            elif kind == "fold":
                first_utc, second_utc = payload
                if fold_strategy in ("first", "both"):
                    instances.append(Instance(first_utc, "fold_first"))
                if fold_strategy in ("second", "both"):
                    instances.append(Instance(second_utc, "fold_second"))
            else:  # gap
                gap = payload
                if gap_strategy == "shift_forward" and gap > timedelta(0):
                    shifted = naive + gap
                    kind2, payload2 = classify(shifted, tz)
                    if kind2 == "gap":  # 极端情况下前移后仍不存在，放弃
                        skipped.append(day)
                    else:
                        instances.append(
                            Instance(payload2[0], "gap_shifted_forward")
                        )
                else:
                    skipped.append(day)
        day += one_day

    # 按 UTC 去重（保留先出现者），再按 UTC 稳定排序
    deduped: dict[datetime, Instance] = {}
    for inst in instances:
        deduped.setdefault(inst.utc, inst)
    ordered = sorted(deduped.values(), key=lambda i: i.utc)
    return ordered, skipped


def find_overlaps(
    a: list[Instance],
    b: list[Instance],
    duration_a: timedelta,
    duration_b: timedelta,
) -> list[Overlap]:
    """扫描线枚举两侧活动区间的全部跨侧重叠。

    每个实例解释为半开区间 ``[start, start+duration)``：边界相接（一个区间的
    起点恰好落在另一区间的终点上）不算冲突。同侧实例即使相交也不产生结果。

    返回结果按 ``overlap_start``（随后按两侧实例起点）稳定排序。
    """
    # (时刻, 类型: 0=结束 / 1=开始, 侧别: 0=A / 1=B, 起点)
    events: list[tuple[datetime, int, int, datetime]] = []
    for inst in a:
        events.append((inst.utc, 1, 0, inst.utc))
        events.append((inst.utc + duration_a, 0, 0, inst.utc))
    for inst in b:
        events.append((inst.utc, 1, 1, inst.utc))
        events.append((inst.utc + duration_b, 0, 1, inst.utc))
    # 同一时刻先处理结束再处理开始 -> 半开相接的区间不会被同时视为活动
    events.sort()

    active: tuple[set[datetime], set[datetime]] = (set(), set())
    overlaps: list[Overlap] = []

    for _, kind, side, start in events:
        own = active[side]
        other = active[1 - side]
        if kind == 0:
            own.discard(start)
            continue
        own.add(start)
        for other_start in other:
            lo = max(start, other_start)
            if side == 0:
                hi = min(start + duration_a, other_start + duration_b)
            else:
                hi = min(start + duration_b, other_start + duration_a)
            if lo < hi:  # 半开区间：相接不算
                if side == 0:
                    overlaps.append(Overlap(start, other_start, lo, hi))
                else:
                    overlaps.append(Overlap(other_start, start, lo, hi))

    overlaps.sort(
        key=lambda o: (o.overlap_start, o.a_start, o.b_start, o.overlap_end)
    )
    return overlaps


def _format_offset(td: timedelta) -> str:
    total = int(td.total_seconds())
    sign = "+" if total >= 0 else "-"
    total = abs(total)
    hh, rem = divmod(total, 3600)
    mm, ss = divmod(rem, 60)
    if ss:
        return f"{sign}{hh:02d}:{mm:02d}:{ss:02d}"
    return f"{sign}{hh:02d}:{mm:02d}"


def format_utc(dt: datetime) -> str:
    """把 aware datetime 格式化为以 Z 结尾的 UTC ISO 8601 字符串。"""
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def instance_payload(inst: Instance, tz: ZoneInfo) -> dict:
    """把实例格式化为 API 响应字段（本地时间 / 偏移 / UTC / 决策原因）。"""
    local = inst.utc.astimezone(tz)
    return {
        "local": local.isoformat(),
        "offset": _format_offset(local.utcoffset()),
        "utc": format_utc(inst.utc),
        "reason": inst.reason,
    }
