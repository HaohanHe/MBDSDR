# Phase9 P3 — 新时空融合：桌面"时空视图"tab + 一条命令演示

> 范围：`cpp/src/ui/`（追加 tab + 纯函数头）、`cpp/tests/`（纯逻辑测试）、
> `cpp/CMakeLists.txt`（追加注册）、`experiments/`（演示脚本 + 测试）、本目录文档。
> 口径：synthetic-injected（云内无硬件，注入固定 GNSS 时间 + 固定多普勒，全固定种子）。
> 红线：无硬件时一切走**诚实空态**（时间源=system、GNSS=无 fix、多普勒=未补偿），
> 绝不把 system 钟伪装成 GNSS 授时。

## 1. 一条命令演示

```bash
# 仓库根目录
python3 experiments/demo_spacetime.py
# 或指定输出目录：
python3 experiments/demo_spacetime.py --out /tmp/spacetime_demo
```

确定性路径（全程固定种子 `20261002`、固定注入 UTC、固定多普勒）：

1. **注入固定 GNSS 时间** `2026-10-02T07:25:45Z` →
   `mbdsdr_ai/gnss_timestamps.resolve_capture_datetime(gnss_dt=...)` 得
   `CaptureTime(time_source="gnss")`；
2. **写 SigMF**（`cf32_le` data + sidecar meta），`captures[0]` 带
   `core:datetime` + `mbdsdr:time_source="gnss"`（与桌面 C++ 同一字段约定）；
3. 合成一段**带多普勒**（+150 Hz）的基带信号，经
   `mbdsdr_ai/doppler_compensation.remove_doppler_shift`（NCO 数字下混频）逆运算补偿；
4. **"解码"** = 估计载波峰值残余频偏：补偿前 `+150 Hz`，补偿后 `≈0 Hz` → `decode_ok=true`；
5. 出带时空标注的图（英文，云 VM 无中文字体）：补偿前/后 PSD + 时间源/UTC/多普勒状态文本框。

### 产物（落在 `--out`，默认 `experiments/artifacts/spacetime_demo/`）

| 文件 | 内容 |
|---|---|
| `demo_spacetime.sigmf-data` | cf32_le 复基带 IQ（含注入多普勒） |
| `demo_spacetime.sigmf-meta` | `core:datetime` + `mbdsdr:time_source=gnss` |
| `figures/spacetime_demo.png` | 补偿前/后 PSD 对比 + 时空标注 |
| `spacetime_demo.json` | 确定性摘要（注入时刻/时间源/补偿前后残余频偏/decode_ok） |

示例摘要（两次运行逐字段一致）：

```json
{
  "capture_datetime": "2026-10-02T07:25:45Z",
  "time_source": "gnss",
  "doppler_injected_hz": 150.0,
  "doppler_compensated": true,
  "residual_offset_before_hz": 150.0,
  "residual_offset_after_hz": 0.0,
  "decode_ok": true,
  "fallback_when_no_gnss": { "time_source": "system", "datetime_iso": "2026-10-02T07:25:46Z" }
}
```

## 2. 桌面"时空视图"tab

在 `centerTabs_`（频谱 / 世界 / 气象 之后）追加第 4 个 tab **"时空视图"**，最小侵入：
文案与颜色全部由纯函数产出，tab 只持有 label 句柄。

布局：

- **四格总览**（2×2）：设备连接 / 信号 / 解码状态 / GNSS 定位；
- **当前接收目标**：捕获的过境卫星名 + 下行频率（未捕获 → "无"）；
- **时间源**：`gnss`（绿，带 UTC）或 `system`（琥珀，显式标注"本机时钟，非 GNSS 授时"）；
- **多普勒补偿状态**：补偿中（绿）/ 未补偿（无目标，中性）/ 补偿开但无目标（琥珀，状态不一致）。

### 实现要点（file:line）

- 纯函数头：`cpp/src/ui/spacetime_format.h` —— `SpRole` 枚举 + `spTileDevice/Signal/Decode/Gnss`
  + `spLineTimeSource/Target/Doppler`，无 widget、可单测。
- tab 构建：`cpp/src/ui/main_window.cpp:749-809`（`centerTabs_->addTab(spPage, "时空视图")`）。
- 颜色映射：`cpp/src/ui/main_window.cpp:4757`（`paintSpRole`：`SpRole` → tokens 色板，
  ok=`kSuccess`、warn=`kWarning`、danger=`kDanger`、info=`kAccent`、neutral=`kInteract`）。
- 状态汇聚：`cpp/src/ui/main_window.cpp:4774`（`refreshSpacetimeView()` 拉真实状态 → 纯函数 → label）。
- 刷新挂点：遥测 `onSourceTelemetry`(:3802)、源切换 `onSourceChanged`(:3675)、
  1 Hz 主循环 `updateLiveSatellite`(:4564)、GNSS fix `onNewFix`(:4939)、
  GNSS 链路 `onGnssConnectionChanged`(:5019) —— **无新增定时器**。
- 成员：`cpp/src/ui/main_window.h:253-264`。

### 时间源判定（与授时逻辑同一口径）

`refreshSpacetimeView()` 复用 `updateClockBiasLabel()` 的诚实判定：GNSS 串口**已连接**
**且** 收到真实 NMEA 时钟（`hasUtc`）→ `gnss`；否则一律 `system`。本函数**绝不**把
system 升级成 gnss——这与 `spLineTimeSource()` 的纯函数规则（非 `gnss` 即 Warn + 诚实标注）
双重保证。

## 3. 诚实空态（无硬件 / 云内默认）

云 VM 无 GNSS 接收器、无 SDR，tab 打开即：

| 项 | 空态 | 颜色角色 |
|---|---|---|
| 设备连接 | `未连接（非硬件）` | neutral（不报警红） |
| 信号 | `--` | neutral |
| 解码状态 | `无解码` | neutral |
| GNSS 定位 | `无 fix`（绝不画假点/假坐标） | neutral |
| 当前接收目标 | `无` | neutral |
| 时间源 | `system（本机时钟，非 GNSS 授时）· <now>` | warn（诚实告诫） |
| 多普勒补偿 | `未补偿（无目标）` | neutral |

只有真实 NMEA 授时/真实 fix/真实捕获过境出现时，对应格才翻成 ok/info。

## 4. 测试

- C++ 纯逻辑：`cpp/tests/test_spacetime_format.cpp`（10 个槽，含 system 绝不升级 gnss、
  空态文本/角色）。ctest：`spacetime_format`。
- Python 演示：`experiments/tests/test_phase9_spacetime.py`（产物齐全 + SigMF round-trip +
  补偿前后频偏 + 两次 run 摘要逐字段一致）。

```bash
# C++（offscreen）
cd cpp/build && QT_QPA_PLATFORM=offscreen ctest -R spacetime_format --output-on-failure
# Python
python3 -m pytest experiments/tests/test_phase9_spacetime.py -q
```

## 5. 真机待验项（cloud 无硬件）

- [ ] 接真实 RTL-SDR：设备格翻绿、信号格出现真实 RSSI/SNR；
- [ ] 接真实 GNSS 串口（/dev/ttyUSB0）：时间源由 `system` 翻 `gnss` 并带真实 UTC；
- [ ] 捕获一个过境并勾选多普勒补偿：目标行 + 多普勒补偿行实时联动；
- [ ] tab 在真实分辨率下的排版/换行（云内仅 offscreen 构建验证）。
