# 重复实现审计：库 vs 面板内联，谁是超集（只判定，2026-11-12）

> 结论：五组「库 vs 面板内联」逐一比完，**三组该删库、一组该接库、一组要带前置条件接**；
> 另有 `spacetime_predict` 与 `orbit.predict_passes_report` 的同名实现，**不是「没人用」，
> 是 `tle.status` 的阈值算错了**。
>
> | 对比 | 谁强 | 判定 |
> | --- | --- | --- |
> | `device_watchdog` vs `device_panel` + `sdr_backend` + `reconnection` | 产品侧三件合起来是超集 | **C 删库** |
> | `usb_watchdog` vs `device_panel` + `main_window._disconnect` | 产品侧是超集且更准 | **C 删库** |
> | `hal_enhanced` vs `device_panel` | **库强**（4 项检查面板没有） | **A 该接**（接 `diagnostics_panel`，且先移植 `read_rx`） |
> | `spectrum_accumulator` vs `spectrum_widget` | **面板强**（线性域平均 + min hold） | **C 删库** |
> | `s_meter` vs `status_panel` | **库强**（校准/平滑/峰值/异常），但库有 NaN 崩溃 | **A 该接**，两个前置条件 |
> | `spacetime_predict` vs `orbit.predict_passes_report` | 同引擎；前者 `tle.status` 阈值错 + 吞异常 | **C 删**（理由从「没人用」升级为「算错」） |
>
> 直接回答 `hal_enhanced` 的疑问：**它没有做 USB 复位，也没有按序列号重连**。
> 它只做只读健康探测（读 1024 点判全零/NaN/采样率偏差 + 音频队列积压 + 系统信息）。
> 所以「热插拔 2 秒轮询」这件事的修法**不是换实现**，仍是 `device_panel` 自己的定位符比对；
> 但它的 4 项检查确实是 `device_panel` 的缺口，该接的是 `diagnostics_panel`。
>
> 本文修订上一份判定：`device_watchdog` / `usb_watchdog` / `spectrum_accumulator` 由 B 改 C，
> `hal_enhanced` 由 B 改 A。修订后 **A 13 / B 32 / C 6**，仍是 51 条。

---

## 1. 判法：超集看四件事，不看方法数量

方法数量多不代表超集。判「谁强」只看：

1. **探测对象**：同一件事是否观察同一层（枚举 / 读流 / 状态标志）；
2. **身份**：能不能区分「拔掉的是哪台设备」——多设备场景下没有身份就是错，不是弱；
3. **动作**：检出之后做什么（只报信号 / 停读 / 重连 / 恢复设置）；
4. **正确性**：数值口径是否一致（dBFS vs dBm、dB 域 vs 线性域、阈值天数）。

最后一列是本文的重点：**两边都在跑、结果不一致，比只有一边跑更坏。**

---

## 2. `device_watchdog`：两半都被替代，删

| 维度 | `DeviceWatchdog`（库） | `device_panel` / `sdr_backend` / `reconnection`（在用） |
| --- | --- | --- |
| 探活 | 每 2 s 读 `get_status().connected` | `sdr_backend` 在**首次读失败**就 `_mark_link_lost` 置 `connected=False`（[sdr_backend.py:2531-2544](../../mbdsdr_ai/sdr_backend.py#L2531-L2544)）；面板每 2 s 枚举（[device_panel.py:215-224](../../desktop/device_panel.py#L215-L224)） |
| 身份 | 无 | 稳定定位符（序列号/调谐器）比对，能报「哪台拔了」 |
| 重连 | 每 5 s 盲调 `backend.connect()`，无退避、无上限、不带身份 | `reconnection.ReconnectionManager`：`compute_backoff` + 身份捕获/比对/设置恢复 + 状态机（[reconnection.py:99](../../mbdsdr_ai/reconnection.py#L99)） |
| 结果 | 重连对象由后端当前目标决定，不校验是不是原来那台 | 身份不符会拒绝重连（`_identity_mismatch`） |

结论：`device_watchdog` 的探活半被后端覆盖、重连半被 `reconnection` 覆盖，且两半都更弱。
**删库不丢任何产品能力**——产品现在本来就没有自动重连，缺口在 A6（接 `reconnection`），
不在保留一个重连时不校验身份的看门狗。

## 3. `usb_watchdog`：身份缺失是错，不是弱

| 维度 | `USBWatchdog`（库） | `device_panel` + `main_window`（在用） |
| --- | --- | --- |
| 探测 | 读 `is_alive()` / `read_rx(64)` / `get_status()` | 2 s 枚举 + 稳定定位符 |
| 身份 | 无——任何一次读失败都当成「设备拔了」 | 有 |
| 动作 | `stop_reading` + `stop_audio` + 回调 | `device_unplugged` → `_disconnect` → `_teardown_connection`，先停 IQ/音频/录制/跟踪再断后端（[main_window.py:7130-7186](../../desktop/main_window.py#L7130-L7186)） |
| 多设备 | 第二台拔出会停掉第一台的会话 | 只处理被拔的那台 |

结论：动作顺序语义已被 `_disconnect` 覆盖，而身份缺失是**行为错误**。
**删库。**

## 4. `hal_enhanced`：不解决热插拔，但补 4 项诊断

先说否证：`hal_enhanced` 里**没有** USB 复位、没有序列号、没有重连循环；
`check_device(backend)` 只是 `read_rx(1024)` 后看数据（[hal_enhanced.py:86](../../mbdsdr_ai/hal_enhanced.py#L86)）。
所以「2 秒轮询」的修法与它无关。

还要先说明一个接口事实：`read_rx` 只存在于旧 HAL（[hal.py:123](../../mbdsdr_ai/hal.py#L123)、
[hal.py:365](../../mbdsdr_ai/hal.py#L365)），**当前桌面后端 `sdr_backend` 只有
`read_samples` + `get_status`，没有 `read_rx`**。所以现在直接把它接上去，每次都会
`AttributeError` → 返回 `alive=False`。这条不是「库不好」，是「库停在上一代 HAL 上」：
接线 = **移植（`read_rx`→`read_samples`）+ 接线**，不是挂个按钮。

它真正比 `device_panel` 多的四件事：

| 检查 | 面板有没有 | 能发现什么 |
| --- | --- | --- |
| `has_signal`（全零 IQ） | **没有** | 天线没接 / 前端静默，但设备「在线」 |
| `nan_detected` | **没有** | 驱动返回 NaN，后续 DSP 全污染 |
| `sample_rate_correct`（±5%） | **没有** | 设备没按请求速率出数（降采样/协商失败） |
| 音频队列 `queue_fraction` > 90% | **没有** | 音频积压/卡顿，界面看起来正常 |

外加 `get_system_info`（CPU/内存/音频延迟）。

结论：**库强 → 功能缺口，优先级高**；但接在 [diagnostics_panel.py](../../desktop/diagnostics_panel.py)，
不是设备页。接之前有两个约束：

1. 移植成 `read_samples(1024)` 后，它会和主读循环抢同一路 USB 样点——要么只在空闲/暂停时探测，
   要么让后端提供「看一眼不下沉」的快照接口；不能挂 2 s 定时器直接读；
2. 「全零 IQ」在静默频点/零增益下也会出现，判「天线没接」前要结合增益与频率，
   否则就是误报。

## 5. `spectrum_accumulator`：方向反了，库更弱

| 能力 | `SpectrumAccumulator`（库） | `spectrum_widget`（在用） |
| --- | --- | --- |
| 平均 | **dB 域算术平均**（`np.mean(frame_db)`）+ dB 域 EMA | **线性幅度域**累加后取 dB（[spectrum_widget.py:568-578](../../desktop/spectrum_widget.py#L568-L578)） |
| 峰值保持 | 有，按 `peak_decay_db`/帧衰减 | 有，`np.maximum`，不衰减（[:327-332](../../desktop/spectrum_widget.py#L327-L332)） |
| 最小保持 | **无** | 有（`np.minimum`） |
| 重采样到显示 bins | 无 | 有（max 抽取） |
| `auto_tune` | 有，但只是设一个 alpha 值 | — |

关键在平均域：dB 域算术平均等于功率的几何平均，**起伏信号会被系统性压低**，
且遇到 `-inf` 无法恢复；面板在线性域累加再取 log，方向是对的（严格 RMS 应平均功率，
但至少不是 dB 平均）。库还缺面板已有的 min hold。

结论：**面板强 → 删库**（C）。想要峰值衰减，在面板的 `np.maximum` 后加一行乘性衰减即可，
不需要为此保留一个平均语义更差的库。`auto_tune` 是两行 setter，不是「将来可扩展」的理由。

## 6. `s_meter`：库强，但接线前必须先补 NaN 守卫

| 能力 | `SMeter`（库） | `status_panel._dbfs_to_smeter`（在用） |
| --- | --- | --- |
| 输入 | 线性 IQ → `rms_dbm` → 校准偏移 `ref_dbfs_per_dbm` | 直接拿 dBFS |
| 校准 | 有（默认 `0 dBFS = +10 dBm`） | **没有**，`S9 = -73 dBFS` 当 `-73 dBm` 用 |
| 平滑 | EMA `alpha=0.3` | 无（每帧直读） |
| 峰值 | 有，带衰减 | 无 |
| 异常 | 有（突升/突降告警） | 无 |
| 非有限值 | **无守卫**：`dbm_to_s_unit(nan)` 走 `int(round(nan))` → `ValueError` | 有，显式返回 `—`（[status_panel.py:148-169](../../desktop/status_panel.py#L148-L169)） |

两套读数的实际差异：同一信号，面板把 `S9` 定在 **-73 dBFS**，库的默认口径把 `S9` 定在
**-83 dBFS**（`-73 dBm - (-10)`）——**整整 10 dB**。两边都不算「物理校准过」，
但库至少把校准偏移暴露成参数，面板把 dBFS 当 dBm 用。

结论：按「库比面板强」判 **A 类、优先级高**，但有两个硬前置：

1. **先补 NaN/Inf 守卫**（照抄面板的 `math.isfinite` 分支）。否则电平断流时 S-meter 会抛
   `ValueError` 打断整条刷新路径——这正是面板注释里写的那个坑，库没吸取。
2. **先定校准口径**。接上后读数会整体移动约 10 dB，属于用户可见变化 → FZ-04。
   在定之前，「接库」= 换一套未经验证的数，不如不接。

另外库对空块返回 `-200 dBm` → 显示 `S0`，而面板对无值显示 `—`。接的时候要区分
「真的 S0」和「没测到」，否则又把「没测出」伪装成「有信号」。

## 7. `spacetime_predict`：同引擎，但 `status` 字段算错

先确认不是传播器的问题：两者最终都走 `sat_passes.predict_passes`
（skyfield SGP4，60 s 粗扫 + 2 s 细扫 + 0.5 s 二分），站址高度都是米。
**过境时刻本身没有算错。**

错在报告层：

| 字段 | `spacetime_predict` | `orbit.predict_passes_report` |
| --- | --- | --- |
| `tle.status` | `tle_freshness(..., max_days=7)` → `fresh` / `stale` / `none` | — |
| `tle.tier` | `tle_integrity` → `fresh`(≤24h) / `usable`(≤7d) / `expired`(≤30d) / `dead` | `tle_policy` 同一套 24h/7d/30d |
| `tle.accuracy` | 由 `tier` 推出 | 由 `tier` 推出 |
| 异常 | `except Exception: passes = []`，**原因不报** | 收集 `degraded` 清单，时间基准缺失直接抛 |

两个具体缺陷：

1. **同一份 TLE 会同时被标成 `status="fresh"` 和 `tier="usable"`。** 3 天龄的 TLE：
   `status` 用 7 天阈值 → `fresh`；`tier` 用 24 小时阈值 → `usable`；`accuracy` 跟着
   `tier` 说「分钟级」。三个字段互相矛盾，下游读哪个都可能错。
2. **吞异常**。skyfield 缺失、TLE 格式坏、真无过境，三种情况都返回 `n_passes=0`，
   没有 `degraded` 原因。这与项目「如实留痕」的红线相反——`orbit` 那套专门留了证据。

结论：**C 删**，而且理由不是「没人用」，是「用了会得到自相矛盾的 TLE 状态」。
删之前把 `time_source`（gnss/system）并入 `orbit.predict_passes_report` 的返回体；
`status` 字段不要迁移——它的 7 天阈值本身就是错的。

---

## 8. 对上一份判定的修订

| 模块 | 上一份 | 现在 | 依据 |
| --- | --- | --- | --- |
| `device_watchdog` | B3 保留 | **C 删** | 探活被后端覆盖、重连被 `reconnection` 覆盖，且无身份 |
| `usb_watchdog` | B31 保留 | **C 删** | 动作被 `_disconnect` 覆盖，无身份 = 多设备误停 |
| `spectrum_accumulator` | B29 保留 | **C 删** | dB 域平均弱于面板线性域，且缺 min hold |
| `hal_enhanced` | B11 保留 | **A 接 `diagnostics_panel`** | 4 项健康检查面板没有；但不解决热插拔 |
| `s_meter` | A8 该接 | **A8 该接（升为高优先）** | 校准/平滑/峰值/异常是真空缺；两个前置：NaN 守卫 + 校准口径 |
| `spacetime_predict` | C3 合并后删 | **C3 删（算错）** | `status` 7 天阈值与 `tier` 24 小时阈值矛盾 + 吞异常 |

修订后计数：**A 13 / B 32 / C 6 = 51**。
上一份文档的 §2.2/§2.3 对应行以本文为准；其余 45 条判定不变。

## 9. 不动代码

- 本文只出判定，未改任何产品代码。
- 删库前各自要处理测试：`tests/test_usb_watchdog.py`、
  `tests/test_spectrum_accumulator.py`、`tests/test_hal_enhanced.py`、
  `mbdsdr_ai/tests/test_tle_freshness_predict.py`；`device_watchdog` 没有对应测试。
- `hal_enhanced` / `s_meter` 的接线都属于 FZ-04 解冻后；`s_meter` 还会改读数，
  必须先定校准口径。
