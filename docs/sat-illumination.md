# 卫星受晒 / 地影判定（sat_illumination）

> 本篇结论未经 commit 取证，引用前须复核（判据见 `scripts/verify_claims.py`；补齐四元组 `<结论> — <hash> — <path>:<line> — <改动后字面量>` 后可摘除本行）。

面向天空视图、过境预报与链路预算的后端物理判据。实现位于
`mbdsdr_ai/sat_illumination.py`，行为测试在
`mbdsdr_ai/tests/test_sat_illumination.py`。

## 1. 模型

两套几何模型，返回值里**显式标注**用的是哪一套：

| 模型常量 | 名称 | 判据 | 输出状态 |
|---|---|---|---|
| `MODEL_CONICAL`（默认） | 圆锥影 | 卫星处地球视半径 `sd_e=asin(R_e/r)`、太阳视半径 `sd_s=asin(R_s/|r_sun-r|)`、卫星→太阳与卫星→地球夹角 `δ`；`δ < sd_e-sd_s` 本影，`sd_e-sd_s ≤ δ < sd_e+sd_s` 半影，其余受晒 | `sunlit` / `umbra` / `penumbra` |
| `MODEL_CYLINDRICAL` | 圆柱影 | 卫星在反日方向且到地日轴垂距 `< R_e` 即本影，否则受晒 | `sunlit` / `umbra` |

- 圆锥影与 gpredict `Sat_Eclipsed`（`reference/gpredict/src/sgpsdp/solar.c:46-64`，
  由 `sat-vis.c:55-105` 调用）同构。gpredict 本身把半影当受晒（二值），
  本仓保留半影三态供渲染晨昏线。
- 圆柱影**没有半影带**：这是模型近似，不是漏算；边界靠不确定度字段诚实标注。
- 既有 `sat_passes._is_sunlit_geometric()` 已改为委托本模块，避免第二份地影数学源。〔证：`3fc46b98` — `mbdsdr_ai/sat_passes.py:681` — `from .sat_illumination import STATUS_UMBRA, evaluate_illumination`〕

## 2. 输入纪律（时间不读墙钟）

| 入口 | 时间来源 | 说明 |
|---|---|---|
| `evaluate_illumination(position_eci_km, sun_direction, *, model, at_unix)` | 位置与太阳方向显式 | 纯几何核心，`at_unix` 仅作溯源 |
| `evaluate_from_position(position_eci_km, sun_direction=None, *, at_unix=None, time_engine=None, model)` | `at_unix` > `time_engine.now_unix()` > 全局 TimeEngine | 太阳方向缺省时按该时刻计算 |
| `evaluate_from_tle(tle1, tle2, *, at_unix=None, time_engine=None, sun_direction=None, model)` | 同上；skyfield 传播卫星位置 | TLE 传播失败抛异常，不返回假状态 |
| `sun_direction_eci(at_unix)` | 显式 Unix UTC 秒 | 低精度 NOAA 公式，误差约 0.1° |

模块不 import `time` / `datetime.now`。太阳方向为 `None` → `unknown`（不猜）；
零向量 / NaN/Inf / 位置低于地心半径 → `ValueError`（非法输入不当成状态）。

## 3. 输出契约（供天空视图渲染层）

```python
Illumination(
    status="sunlit" | "umbra" | "penumbra" | "unknown",
    model="conical" | "cylindrical",
    basis="圆锥影：δ=...° < ...° → 本影（太阳完全被遮）",
    boundary_uncertainty_s=2.0,   # 状态切换边界附近不保证的秒数
    sunlit=True | False | None,  # 半影/unknown 为 None（部分受照不可二值化）
    at_unix=1700000000.0,
    geometry={...},              # 角度/距离依据，便于复核
)
```

- `to_dict()` 输出可直接 JSON 序列化；字段只增不改。
- 渲染层只依赖 `status` 与 `basis`；`unknown` 必须显示“未知”，不得渲染成受晒。
- `boundary_uncertainty_s` 是**边界附近**经验值，不是全局误差：
  - 圆锥影 ±2 s：太阳方向误差 ~0.1°、LEO 交线角速度 ~0.06°/s（≈1.7 s），
    半影带宽 ~8 s，取整 2 s；
  - 圆柱影 ±5 s：半影被压成零宽 + 本影半径用赤道半径（比同高度圆锥本影
    半径大约 30 km），LEO 下折合 4~5 s。

## 4. 卫星状态快照透传（界面/工具统一取这里）

`orbit._state_from_satrec`（及 `compute_satellite_state` 返回的同一字典）
在原有仰角/方位/距离/多普勒之外，固定带上四个受晒字段。值来自同一份
`sat_illumination.evaluate_illumination`，**没有第二份地影数学**。

| 字段 | 类型 | 取值 | 语义 / 界面必须遵守 |
|---|---|---|---|
| `sunlit` | `bool \| None` | `True` / `False` / `None` | `True`=受晒，`False`=本影，`None`=半影（部分受照，不可二值化）。**`None` 不许画成受晒或本影**，应显示“半影/部分受照”。 |
| `sunlit_status` | `str` | `sunlit` / `umbra` / `penumbra` | 精确三态。快照路径不会出现 `unknown`（位置与太阳方向都由同一时刻推导）。 |
| `illumination_model` | `str` | `conical` | 快照固定用圆锥影（gpredict `Sat_Eclipsed` 同构）。要圆柱影请直接调用 `evaluate_illumination(..., model="cylindrical")`。 |
| `boundary_uncertainty_s` | `float` | `2.0` | 状态切换边界附近不保证的秒数；界面不要把边界 2 s 内的翻转当成异常。 |

- 时间同源：快照的 `jd_utc` 来自 `when`/TimeEngine，受晒判定用同一时刻的
  TEME 位置算出的 Unix 秒，不另取时钟。
- 未知语义：直接调用 `sat_illumination` 且没有太阳数据时 `status="unknown"`、
  `sunlit=None`；快照路径不会走到这里。
- 一致性：`test_orbit_sunlit_snapshot.py` 整轨 200 点逐点比对快照与直接
  调用（状态、`sunlit`、模型、不确定度全等）。
- 过境口径：`sat_passes._is_sunlit_geometric` 已委托同一判据（圆锥影），
  `require_sunlit` 过滤“可见过境”时只有本影算不亮、半影按照亮；测试在同一
  位置/时刻比对该函数与直接调用。
- 性能（本机 Python 3.14 实测）：`evaluate_illumination` ≈ **9.4 µs/次**，
  完整快照 ≈ **16.4 µs/次**；50 颗 × 30fps（1500 次/秒）≈ 25 ms/s。
  因此**不加缓存**。若未来要每秒上千颗，可按 `(satnum, 整秒)` 缓存，
  精度代价是跨秒边界时受晒状态最多滞后 1 s（落在 ±2 s 不确定度内，
  但需要显式记录 `at_unix`）。

## 5. 边界用例（17 + 4 个测试）

| 用例 | 断言 |
|---|---|
| 刚进影 | `θ = 本影边界 +0.05°` → `umbra`，`sunlit=False` |
| 刚出影 | `θ = 半影外边界 -0.05°` → `sunlit` |
| 半影擦边 | 半影带正中 → `penumbra`，`sunlit=None` |
| 极区夏季不落影 | 太阳赤纬 +23.44°、晨昏面轨道 72 个相位 → 全部 `sunlit` |
| 太阳方向零向量 | `ValueError`（非静默受晒） |
| 无太阳数据 | `unknown`，`sunlit=None` |
| 位置低于地心半径 | `ValueError` |
| 圆柱影 | 不产生 `penumbra`；边界垂距 = `R_e` |
| 时间入口 | `at_unix` 原样记录；无参数走全局 TimeEngine |
| 渲染契约 | 7 个字段齐全、状态枚举闭合、`to_dict` 可 JSON |
| TLE 入口 | 显式时刻 + skyfield 传播，状态/依据/半径均有效 |
| 快照一致性 | 整轨 200 点，快照字段与直接调用逐点全等 |
| 快照契约 | 四个受晒字段齐全、取值域闭合、半影必为 `None` |
| 过境口径 | `_is_sunlit_geometric` == `status != umbra`（同一位置/时刻） |
| 性能预算 | 单次快照 < 0.5 ms，1500 次/秒 < 50 ms/s |

## 6. 与 gpredict 对拍

`test_matches_gpredict_sat_eclipsed_over_grid` 在测试内**独立直译**
`Sat_Eclipsed`（`sd_e`、`sd_s`、`δ`、`depth >= 0`），沿 θ 从 100° 扫到 120°
（步进 0.2°，101 点，覆盖本影/半影/受晒三段），逐点比对“是否本影”。
不依赖运行 gpredict 进程，也不链接 GPL 代码（只对拍判据）。
