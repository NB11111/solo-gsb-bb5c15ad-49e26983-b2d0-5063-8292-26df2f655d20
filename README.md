# Civil Schedule Expander

基于 **FastAPI + 标准库 zoneinfo** 的民用日程展开 API。输入 IANA 时区、日期区间、
星期集合与本地时刻，服务通过 **UTC 往返校验**区分正常 / 跳时（gap）/ 叠时（fold），
按指定策略生成**去重且按 UTC 稳定排序**的实例，并返回每个实例的本地时间、UTC 偏移、
UTC 时间与决策原因。

## 快速启动（单条命令）

```bash
docker compose up --build
```

依赖（FastAPI / uvicorn / tzdata）在镜像构建时安装完毕，容器启动即可服务。

- 服务地址： http://localhost:8000
- 交互式文档（Swagger UI）： http://localhost:8000/docs
- 健康检查： http://localhost:8000/health

## 配置

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `PORT` | `8000` | 宿主机映射端口，如 `PORT=9000 docker compose up --build` 后访问 http://localhost:9000 |

容器内服务固定监听 8000 端口，无需其他配置；时区数据库由镜像内系统 tzdata
及 pip 包 `tzdata`（兜底）提供。

## API

### `POST /expand`

请求体：

```json
{
  "timezone": "Europe/Berlin",
  "start_date": "2026-03-28",
  "end_date": "2026-03-30",
  "weekdays": [5, 6, 0],
  "local_time": "02:30",
  "gap_strategy": "shift_forward",
  "fold_strategy": "both"
}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `timezone` | string | IANA 时区名，如 `America/New_York` |
| `start_date` / `end_date` | date | 日期区间（两端均含） |
| `weekdays` | int[] | 星期集合，`0`=周一 … `6`=周日，至少 1 个 |
| `local_time` | string | 本地时刻，如 `02:30` |
| `gap_strategy` | string | 不存在时刻策略：`skip`（默认，跳过）/ `shift_forward`（按跳变差值前移） |
| `fold_strategy` | string | 重复时刻策略：`first`（默认，取第一次）/ `second`（取第二次）/ `both`（两次都保留） |

响应 `200`：

```json
{
  "timezone": "Europe/Berlin",
  "count": 3,
  "skipped_count": 0,
  "skipped_dates": [],
  "instances": [
    {"local": "2026-03-28T02:30:00+01:00", "offset": "+01:00", "utc": "2026-03-28T01:30:00Z", "reason": "normal"},
    {"local": "2026-03-29T03:30:00+02:00", "offset": "+02:00", "utc": "2026-03-29T01:30:00Z", "reason": "gap_shifted_forward"},
    {"local": "2026-03-30T02:30:00+02:00", "offset": "+02:00", "utc": "2026-03-30T00:30:00Z", "reason": "normal"}
  ]
}
```

决策原因（`reason`）：

| 值 | 含义 |
| --- | --- |
| `normal` | 正常时刻，本地时间与 UTC 一一对应 |
| `gap_shifted_forward` | 原时刻不存在（春跳），已按跳变差值前移 |
| `fold_first` | 重复时刻（秋回），取第一次出现（UTC 较早） |
| `fold_second` | 重复时刻（秋回），取第二次出现（UTC 较晚） |

被 `skip` 策略跳过的日期不计入 `instances`，汇总在 `skipped_count` / `skipped_dates`。

### `POST /overlaps`

判断**两个民用周期日程在真实时间轴（UTC）上的冲突**。请求体携带两侧日程 `a` / `b`，
每侧就是一份 `/expand` 的展开参数再加上 `duration_minutes`（1～1440）。两侧各自按原
gap/fold 策略展开（逻辑与 `/expand` 完全一致），每个实例被解释为半开 UTC 区间
`[start, start+duration)`，随后用扫描线枚举**全部跨侧重叠**：

- 只比较 A、B 两侧之间的区间；同一侧内部实例（如 `fold=both` 产生的两段）相交不算冲突。
- 区间为**半开**，边界相接（一侧起点恰好等于另一侧终点）不算冲突。
- 每侧日期区间同样限制不超过 366 天，非法请求返回 422。

请求体：

```json
{
  "a": {
    "timezone": "Europe/Berlin",
    "start_date": "2026-07-01",
    "end_date": "2026-07-03",
    "weekdays": [0, 1, 2, 3, 4, 5, 6],
    "local_time": "09:00",
    "gap_strategy": "skip",
    "fold_strategy": "first",
    "duration_minutes": 90
  },
  "b": {
    "timezone": "America/New_York",
    "start_date": "2026-07-01",
    "end_date": "2026-07-03",
    "weekdays": [0, 1, 2, 3, 4, 5, 6],
    "local_time": "03:30",
    "gap_strategy": "skip",
    "fold_strategy": "first",
    "duration_minutes": 60
  }
}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `a` / `b` | object | 两侧日程，字段同 `/expand` 请求体，另加 `duration_minutes` |
| `a.duration_minutes` / `b.duration_minutes` | int | 每实例持续分钟数，`1`～`1440` |

上例中柏林 09:00（CEST）= 07:00Z（90 分钟段），纽约 03:30（EDT）= 07:30Z
（60 分钟段），每天重叠 60 分钟。响应 `200`（按 `overlap_start_utc` 升序）：

```json
{
  "count": 3,
  "overlaps": [
    {
      "a_instance_start_utc": "2026-07-01T07:00:00Z",
      "b_instance_start_utc": "2026-07-01T07:30:00Z",
      "overlap_start_utc": "2026-07-01T07:30:00Z",
      "overlap_end_utc": "2026-07-01T08:30:00Z",
      "overlap_minutes": 60
    }
    // …… 2026-07-02、2026-07-03 各一条
  ]
}
```

| 响应字段 | 说明 |
| --- | --- |
| `a_instance_start_utc` / `b_instance_start_utc` | 双方发生重叠的实例起点（UTC，Z 结尾） |
| `overlap_start_utc` / `overlap_end_utc` | 重叠区间起止，终点半开不含（UTC，Z 结尾） |
| `overlap_minutes` | 重叠分钟数（整数） |

跳时（gap）被 `skip` 的日期不产生实例，自然不参与冲突；`shift_forward` 后与
`fold=both` 展开出的实例都作为普通半开区间参与比较，秋回产生的两个实例会分别与对侧
枚举重叠。

### 错误（422）

以下情况返回 `422 Unprocessable Entity`：

- 非法 IANA 时区名
- `end_date` 早于 `start_date`（逆序区间）
- 区间超过 366 天（`(end_date - start_date).days > 366`）；`/overlaps` 两侧分别校验
- 星期值越界（不在 0–6）或集合为空等其他字段校验失败
- `/overlaps` 的 `duration_minutes` 不在 1～1440 之间

## 判定原理

对每个候选本地时刻分别按 `fold=0` / `fold=1` 赋予时区，换算到 UTC 再换算回来
（UTC 往返校验）：能还原原时刻的解释才真实存在。两种解释均有效且 UTC 相同为正常；
均有效但 UTC 不同为叠时；均无效为跳时，跳变差值取两种解释对应的 UTC 之差，
`shift_forward` 即将本地时刻加上该差值后重新校验。所有实例按 UTC 去重
（保留先出现者）并按 UTC 稳定排序。

`/overlaps` 复用同一套展开逻辑：两侧实例各自映射为半开 UTC 区间后，扫描线按时刻
排序开始 / 结束事件（同一时刻先处理结束，保证边界相接不冲突），维护两侧当前活动
区间，每个开始事件与对侧全部活动区间求交，枚举所有跨侧重叠段并按重叠起点排序。

## 本地开发（不用 Docker）

```bash
pip install -r requirements.txt -r requirements-dev.txt
uvicorn app.main:app --reload        # http://localhost:8000
pytest                               # 运行测试
```

## 项目结构

```
app/
  main.py        # FastAPI 入口，/expand、/overlaps 与 /health
  models.py      # 请求/响应模型与 422 校验规则
  scheduler.py   # UTC 往返校验分类 + 展开 / 去重 / 排序 + 扫描线重叠枚举
tests/           # pytest 测试（核心逻辑 + API）
Dockerfile
docker-compose.yml
requirements.txt
requirements-dev.txt
```
