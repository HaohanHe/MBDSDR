# R2 审查 29：time_sync.py & desktop/rf_sky_view.py

- 审查范围：`mbdsdr_ai/time_sync.py`（51 行）、`desktop/rf_sky_view.py`（1156 行）
- 交叉对照：`mbdsdr_ai/orbit.py`（294 行）、`mbdsdr_ai/decoders.py`、`mbdsdr_ai/new_spacetime.py`、`mbdsdr_ai/sdr_backend.py`、`desktop/main_window.py`
- 审查方式：只读，通读全部目标文件 + 全仓 grep 调用方

---

## 一、结论速览

| 维度 | 结论 |
|---|---|
| time_sync.py 真实授时能力 | **只读 NTP offset，从不校钟；无 GPS PPS；无亚毫秒路径** |
| 与 orbit.py 时间系统一致性 | 一致（都用 `time.time()` Unix 秒），但 time_sync.py 本身**没有任何下游消费**（除 agent 一次性调用） |
| FT8 等严格时序模式是否依赖它 | **不依赖**。`ft8_lite.py` 是无源音峰检测，不读墙钟 |
| rf_sky_view 是否真画卫星轨迹 | **是**，真 sgp4 卫星位置由 `SatelliteTracker` 驱动（orbit.py） |
| 频谱热力图是否真接 SDR | **否**，仅启动时一次性写入的 `sin/cos` 假数据，永不刷新 |
| 时间控制条（暂停/快进/快退） | **纯 UI 装饰**，不改变卫星位置 |
| 性能 | 30 fps 无条件全量重绘 + UI 线程里同步跑 sgp4/网络 |

---

## 二、time_sync.py（51 行）

### 2.1 它到底做了什么

整份文件只有一个函数 `ntp_offset()`（`time_sync.py:18-46`）：
- 发一个 48 字节 NTP 请求到 `ntp.aliyun.com:123`（UDP），
- 解析返回包里的"传输时间戳"，
- 与本地 `time.time()` 比较，返回偏差秒数。

**它从不调用 `settimeofday` / `adjtime` / 任何系统调时接口**，也没有任何 PPS/GNSS 串口读取路径。文件 docstring 写的是"返回本地时钟偏差"——即只读观测，不是真正的"同步"。

### 2.2 发现

#### [真bug] T1. NTP 时间戳字段读错（注释与代码自相矛盾）
- 位置：`mbdsdr_ai/time_sync.py:32-35`
- 注释写 `# NTP 传输时间在字节 40-47`，但代码：
  ```python
  recv_ts   = struct.unpack("!12I", data)[8]   # 实际是 bytes 32-35 = Receive timestamp sec
  recv_frac = struct.unpack("!12I", data)[9]   # 实际是 bytes 36-39 = Receive timestamp frac
  ```
- NTP 包布局（RFC 5905）：bytes 32-39 = Receive Timestamp，bytes 40-47 = **Transmit** Timestamp。要读 Transmit 应该取 `[10]`、`[11]`。
- 后果：用服务器收到请求的时刻代替服务器发出响应的时刻，引入服务器端处理时延（通常亚毫秒~数 ms）。对一个"观测偏差"的工具而言不致命，但与自身注释不符，且对比 `new_spacetime.py:95-96` 的正确写法（`[8]`+`[10]` 分别对应 recv/transmit）可确认这是笔误。

#### [真bug] T2. 偏差估计算法不是标准 NTP offset
- 位置：`mbdsdr_ai/time_sync.py:37-39`
- 代码：
  ```python
  delay = (t1 - t0) / 2.0
  local_now = (t0 + t1) / 2.0
  offset = ntp_sec - local_now
  ```
- 标准 NTP offset 公式是 `offset = ((t2-t1)+(t3-t4))/2`（需要 t2=服务器收、t3=服务器发两个时间戳）。这里把 RTT/2 当成"单程延迟"直接对称化，在非对称路径（家用宽带常见）下误差可达几十 ms。
- 结论：**不存在亚毫秒级精度**。公网单跳 NTP 典型偏差 10–50 ms，抖动更大；本实现还少了 t3，精度只会更差。任何"FT8 严格时序依赖它"的假设都不成立。

#### [空壳] T3. time_sync.py 几乎是死代码
- 全仓 grep `ntp_offset(` 仅 2 处命中：
  - `time_sync.py:18`（定义）、`time_sync.py:50`（`__main__` 自测）
  - `mbdsdr_ai/agent.py:1103`（AI 工具一次性调用）
- 桌面端 UI 实际走的是 **另一份独立实现**：`mbdsdr_ai/new_spacetime.py:73-112` 的 `get_ntp_time()`，由 `main_window.py:705` 导入 `get_time_info` 后喂给天空图。
- 也就是说项目里有**两份 NTP 客户端**（time_sync.py 与 new_spacetime.py），互相不复用，前者没人用。

#### [占位] T4. 无 GPS PPS / 无硬件守时路径
- 全仓 grep `pps|PPS|gpstme|gpstime|settimeofday|adjtime` 无任何命中（除本文件 `gps_week/gps_tow` 字段名）。
- `new_spacetime.py:66` 硬编码 `leap_seconds: int = 18`（2017-01-01 起正确值；下次闰秒未自动跟踪）。
- `sdr_backend.py:732-738` 的 `get_gps()/get_imu()` 只是 `_send_mcp("get_gps")` 透传到固件；而固件侧 `sdr_tools._get_gps` 已在 R2 23/24 号审查中确认是**硬编码长春坐标字符串**，真机接 GNSS 模块也不会更新（见 `docs/audit_r2/23_sdr_tools_part1.md:S1`）。
- 因此 UI 上 `rf_sky_view.py:776-779` 画的 `GPS W{week} {tow}s` 永远是 `GPS --:--:--`（因为 `info.gps_time` 在无 NTP 时为 0）。

#### [建议] T5. FT8 时序与 time_sync 完全解耦
- `ft8_lite.py:1-12` 自述：纯 Python 做音峰检测与 8FSK 硬判决，"15s 周期边界检测"是**信号域内自相关**（找周期），不读墙钟。
- `ft8_decode.py` / `ft8_ldpc.py` grep `time.time|utc|clock` 无命中。
- 含义：本项目当前**没有任何模块真正依赖亚毫秒授时做发射/接收时序对齐**。如果未来要发 FT8，需要补：(a) 真 PPS/GNSS  disciplined clock，(b) 15s slot 边界对其，(c) tx 触发延迟补偿。现在的 time_sync.py 离这个目标差得远。

---

## 三、desktop/rf_sky_view.py（1156 行）

### 3.1 真实渲染 vs 占位

| 控件 | 数据源 | 结论 |
|---|---|---|
| 极坐标网格/罗盘/天线波束 | 自绘，无外部数据 | 真绘制 |
| 卫星菱形标记 | `SatelliteTracker` → `orbit.compute_satellite_state`（真 sgp4 + celestrak TLE） | **真接 orbit.py** |
| 卫星未来 10 分钟轨迹虚线 | `SatelliteTracker.refresh()` 每 10s 重算 11 个未来点 | 真计算 |
| 信号强度热力图（方位×仰色斑） | `main_window.py:657-663` 启动时一次性 `sin/cos` 公式生成 90 个格子，之后**再无调用 set_heatmap** | **[占位] 永久静态假数据** |
| 天线指向十字 | 初始 `(az=0, el=0)`；点击卫星时更新（`main_window.py:729-737`） | 半真（不接云台/IMU） |
| 左下角授时卡片（UTC/GPS Wk/Tow） | `new_spacetime.get_time_info()` 每 5s 一次 | 真但 NTP 每 150s 才一次，其余是系统时钟 |
| 底部时间控制条（<< -10m / 暂停 / 实时 / +10m >>） | 只改一个 label 字符串，**不反馈给轨道计算** | **[空壳]** |

### 3.2 发现

#### [真bug] U1. 两条卫星数据通路打架，互相覆盖
- 通路 A：`main_window.py:643` 创建 `SatelliteTracker(sky_view, lat, lon)`，默认 `interval_ms=10000`（`rf_sky_view.py:1113`），内部调 `orbit.compute_satellite_state`（真 sgp4，celestrak TLE）。
- 通路 B：`main_window.py:68-71` 另起一个 QTimer，**每 5 秒**触发 `_update_sky_satellites`（`main_window.py:666-697`），它从 `mbdsdr_ai.decoders` 导入另一套 `compute_satellite_position`，然后 `self.sky_view.set_objects(satellites)`（`main_window.py:696`）。
- 两个定时器都在调用同一个 `RFSkyView.set_objects()`，每 5s 被通路 B 覆盖一次，每 10s 被通路 A 再覆盖一次。两者底层都走 sgp4（decoders.py:81 也回源到 orbit.fetch_tle），结果数值相近，但：
  - 轨迹只由通路 A 设置（`rf_sky_view.py:1155-1156`），通路 B 不更新轨迹，导致**轨迹与当前点短暂错位**；
  - 通路 B 用的字段名是 `elevation_deg/azimuth_deg/distance_km/signal_strength_db`（`main_window.py:685-690`），通路 A 用的是 `elevation/azimuth/range_km`（`rf_sky_view.py:1139-1143`），对象构造参数不同，UI 上 description 文案会每 5s 闪一次格式；
  - 双倍 sgp4 计算浪费 CPU。
- 建议：删 `_update_sky_satellites` 里的卫星部分，只保留 `_update_time_info()`；卫星位置统一由 `SatelliteTracker` 负责。

#### [真bug] U2. SatelliteTracker.refresh() 在 UI 线程里同步做网络+sgp4
- 位置：`rf_sky_view.py:1123-1156`
- 首次调用时 `orbit.fetch_tle`（`orbit.py:88-90`）会 `urllib.request.urlopen(timeout=15)` 同步拉 celestrak。6 颗卫星 ×（1 当前 + 11 未来点）= 78 次 sgp4 + 最多 6 次 HTTPS，全部在 Qt 主线程。
- 首次启动或 TLE 缓存过期（>24h）时，UI 会冻结数秒到十几秒。
- 建议：把 `refresh()` 挪到 `QThreadPool`/`QRunnable`，通过信号回主线程 `set_objects`。

#### [空壳] U3. 底部时间控制条是纯装饰，不影响轨道
- 位置：`rf_sky_view.py:1029-1073`
- `_adjust_time(±600)` / `_toggle_pause` / `_reset_time` 只改 `self._time_offset` 并刷新 label（`rf_sky_view.py:1054-1067`）。
- 对外暴露的 `get_sim_time()`（`rf_sky_view.py:1069-1073`）全仓 grep **零调用方**。
- 而 `SatelliteTracker.refresh()` 写死 `now = _t.time()`（`rf_sky_view.py:1131`），完全不读 panel 的 `_time_offset`。
- 结果：用户点"-10m"按钮，label 变成"模拟: …(-10m)"，但天空里的卫星还是按真实现在时刻画——**点了等于没点**。
- 附带死代码：`rf_sky_view.py:1071-1073` 两个分支返回值完全相同（`if self._paused: return ... ; return ...`），`if` 是多余的。

#### [占位] U4. 热力图永远是启动时那 90 个 sin/cos 点
- 位置：`main_window.py:656-663`
  ```python
  for az in range(0, 360, 20):
      for el in [15, 30, 45, 60, 75]:
          signal = -70 + 20*math.sin(math.radians(az*2)) + 10*math.cos(math.radians(el*3))
          heatmap.append(HeatmapCell(az, el, signal))
  self.sky_view.set_heatmap(heatmap)
  ```
- 全仓 grep `set_heatmap` 只有这一处调用（除定义与代理）。`sdr_backend.py` 里**没有任何频谱×方位×仰角的数据流**——它只有 `read_samples()` 给基带，没有"指向某个方位时测到的功率"这种二维积累。
- 后果：真机连上 SDR，热力图也永远是这副正弦花纹。这就是典型的"实验室绿、真机红"——演示看着像那么回事，实际没有数据管线。
- 要接真数据需要：转台/相控阵扫描逻辑 + 每个 (az,el) 波束指向时积分功率 + 写入 `set_heatmap`。当前完全没有。

#### [建议] U5. 30 fps 无条件全量重绘
- 位置：`rf_sky_view.py:196-198`
  ```python
  self._refresh_timer = QTimer(self)
  self._refresh_timer.timeout.connect(self.update)
  self._refresh_timer.start(33)   # 30 fps
  ```
- 天空视图在无交互、卫星每 5–10s 才更新一次的情况下，仍以 30 Hz 全量重绘（含热力图 90 个 ellipse + 轨迹 path + 文本）。`paintEvent` 里没有任何脏矩形/差分优化。
- 建议：把定时器停掉，只在 `set_objects/set_trajectory/set_antenna/mouseMove` 等数据变化时调 `self.update()`；真要做实时动画（如卫星移动），可降到 2–5 Hz。

#### [建议] U6. 鼠标追踪常开 + 每次移动都重绘
- 位置：`rf_sky_view.py:192` `self.setMouseTracking(True)` + `rf_sky_view.py:856-863` 每次 mouseMove 都做 `_find_object_at` 并可能 `self.update()`。
- 对象数只有 6 个，O(n) 检测本身不贵，但叠加 30fps 重绘后，鼠标划过会持续触发 `_draw_hover_tooltip` 里的 `QFontMetrics` 测量（`rf_sky_view.py:810-811`）。可接受，但建议 hover 变化才 update（已经是这样做了），主要问题还是 U5。

#### [建议] U7. 波束绘制是近似四边形，非真实方向图
- 位置：`rf_sky_view.py:569-596`
- 注释自己写"简化为椭圆区域"，实际用 4 个角点连了个四边形。方位角方向的波束宽度随仰角变化（天顶附近方位向被压缩），这里没有投影修正。视觉上可接受，但别当成真实方向图。

#### [建议] U8. 仰角裁剪把地平线以下卫星藏掉，但 SatelliteTracker 注释说要画半透明
- 位置：`rf_sky_view.py:462` `if not obj.visible or not obj.is_above_horizon(): continue`
- `rf_sky_view.py:1102-1104` 注释："卫星在地平线下也画（半透明），便于看到过顶前后轨迹"——但 `_draw_objects` 直接 skip 了地平线下的对象，与注释不符。轨迹线倒是会画（`rf_sky_view.py:656-658` 对 el<=0 仅断开 path 不 skip）。

#### [建议] U9. `_draw_info_overlay` 里每帧 import datetime
- 位置：`rf_sky_view.py:762` `from datetime import datetime, timezone` 写在 paintEvent 内部。Python 会缓存模块，但每帧查找一次字典，小瑕疵。

---

## 四、与 orbit.py / sdr_backend.py 的接线核对

| 接线点 | 期望 | 实际 | 判定 |
|---|---|---|---|
| 卫星位置 → 天空图 | 从 orbit.py 取 | `SatelliteTracker` → `orbit.compute_satellite_state`（`rf_sky_view.py:1133`） | ✅ 真接 |
| 卫星 TLE 来源 | celestrak 在线+缓存 | `orbit.fetch_tle`（`orbit.py:74-98`），SSL 校验被关（`orbit.py:46-47` `CERT_NONE`） | ⚠️ 真接但关了证书校验 |
| 未来轨迹 | 从 orbit.py 算 | `rf_sky_view.py:1146-1153` 每 60s 一点 × 11 点 | ✅ 真算 |
| 频谱热力 → 天空图 | 从 sdr_backend 取每个方位功率 | **无任何调用方**；只有启动假数据 | ❌ 占位 |
| 天线指向 → 天空图 | 从云台/IMU 取 | 点击卫星时本地构造一个 `AntennaPointing`（`main_window.py:729-737`），不接 `sdr_backend.get_imu()` | ❌ 半接 |
| 授时 → 天空图 | NTP/GNSS disciplined clock | `new_spacetime.get_time_info` 每 5s 调一次，NTP 每 150s 一次（`main_window.py:710`） | ⚠️ 只读观测，不校钟 |
| 频率联动 | 点卫星 → SDR 调谐 | `main_window.py:748` 调 `sdr_set_frequency` | ✅ 真接 MCP worker |

---

## 五、"实验室绿、真机红" 清单

| 现象 | 真机下会怎样 |
|---|---|
| 热力图 90 个 sin/cos 色块 | 永远不变，因为没有任何代码从 SDR 功率计喂数据 |
| 时间控制条 -10m/+10m/暂停 | label 变了，卫星位置不变（U3） |
| GPS W?? ?????.?s | 永远显示 `GPS --:--:--`，因为 `info.gps_time` 在无 NTP 分支下为 0（`new_spacetime.py:146` 只在有 utc_time 时算），而 `get_gps()` 透传的固件接口返回硬编码字符串 |
| 卫星轨迹/位置 | **真机正常**（真 sgp4 + celestrak TLE），但会被 5s 一次的 decoders.py 通路偶发覆盖文案（U1） |
| 时钟偏差 ms 显示 | NTP 可达时显示一个 10–50ms 量级的数；不可达时 0.0。这个数**不被任何 DSP 路径使用**，纯展示 |

---

## 六、优先级建议

| 优先级 | 项 | 建议 |
|---|---|---|
| P1 | U1 双通路覆盖 | 删 `_update_sky_satellites` 里的卫星循环，只保留授时刷新；卫星统一走 SatelliteTracker |
| P1 | U2 UI 线程网络阻塞 | `SatelliteTracker.refresh()` 改 QThreadPool + 信号回主线程 |
| P1 | U4 热力图无真数据源 | 要么明确标"演示"，要么补转台/波束扫描功率积累管线 |
| P2 | U3 时间控制条空壳 | 让 SatelliteTracker 读 panel.get_sim_time()；或把按钮灰掉/删掉 |
| P2 | T1 NTP 字段错位 | `[8],[9]` 改 `[10],[11]`（参考 new_spacetime.py:96） |
| P2 | U5 30fps 重绘 | 改事件驱动 update |
| P3 | T3 两份 NTP 实现并存 | 让 time_sync.py 复用 new_spacetime.get_ntp_time，或干脆删掉 time_sync.py |
| P3 | T4 无 PPS/无校钟 | 真要做 FT8 发射，需另立硬件守时模块；当前不要在 UI 上暗示"已亚毫秒同步" |

---

## 七、关键行号索引

- `mbdsdr_ai/time_sync.py:18` — `ntp_offset()` 唯一函数
- `mbdsdr_ai/time_sync.py:33-34` — NTP 时间戳字段读错 [T1]
- `mbdsdr_ai/time_sync.py:37-39` — 非标准 offset 公式 [T2]
- `desktop/rf_sky_view.py:196-198` — 30fps 无条件重绘 [U5]
- `desktop/rf_sky_view.py:656-706` — 热力图绘制（数据源是假的）
- `desktop/rf_sky_view.py:1029-1073` — 时间控制条空壳 [U3]
- `desktop/rf_sky_view.py:1099-1156` — SatelliteTracker 真 sgp4 桥
- `desktop/rf_sky_view.py:1131` — 写死 `time.time()`，不读 panel 模拟时间 [U3]
- `desktop/main_window.py:68-71` — 5s 定时器触发 `_update_sky_satellites` [U1]
- `desktop/main_window.py:636-664` — 启动时写死演示热力图 [U4]
- `desktop/main_window.py:666-700` — 第二条卫星通路 [U1]
- `desktop/main_window.py:702-723` — 授时信息喂给天空图
- `mbdsdr_ai/new_spacetime.py:73-148` — 实际被使用的 NTP 实现（与 time_sync.py 重复）
