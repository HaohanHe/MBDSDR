# 接收链多普勒补偿（接线、事实源与验证）

> 维护口径：2026-11-05 接入层批次。只回答三件事：补偿在哪一层真正施加、
> 预测的单一事实源是谁、残余/边界怎么验。未上真机/真过境的项见
> `docs/limitations-register.md`，不在此冒充已验证。

## 1. 结论（先看这一行）

接收链基带 NCO 是 **Python 侧唯一真正施加补偿的位置**（`ReceiveChain.process`
第 2.5 步，抽取之前）；预测值唯一来自 `mbdsdr_ai.doppler_link` 单例；
C++ 桌面 1Hz ZOH 是**调谐器侧、C++ 进程内**的另一条施加路径，不与 Python
共享预测，也不会被 Python 消费——同一路数据流任何时刻只有一侧真正施加。

## 2. 接线位置（Python）

```
SDRBackend._ensure_receive_chain()
  └─ ReceiveChain
       DCBlocker → IQCorrector → DopplerCompensationStage(NCO) → DecimatingFIR
       → XlatingFIR → Squelch → Demod → AudioResampler
```

- `DopplerCompensationStage`（`mbdsdr_ai/doppler_compensation.py`）：
  跨块相位累加，块边界不重置相位；目标/开关切换用 `ramp_s`（默认 20 ms）
  线性渐变；关闭后保留常量相位（无相位跳变）。
- 放在抽取**之前**：大偏移不会被前端抽取先混叠/滤掉。
- `SDRBackend` 在接收链重建后恢复绑定与开关（`_bind_receive_chain_doppler`）；
  跨重建不丢补偿状态。
- 工具面：`sdr_doppler_compensation`（`enabled` 显式开关；可选
  `satellite_name`/`frequency_hz` 绑定目标；返回当前补偿量读数）。
- 开关与读数：

| 字段 | 含义 |
| --- | --- |
| `enabled` | 用户开关是否打开 |
| `active` | 是否真的在施加非零补偿 |
| `applied_hz` | 当前实际施加的补偿量（Hz，带符号） |
| `target_doppler_hz` | 预测源给出的目标 Δ（Hz） |
| `settling` | 是否仍在 ramp 渐变中 |
| `target_name` / `predicted_at_unix` | 目标与预测时刻（溯源） |
| `tle_tier` / `tle_age_days` | 星历级别与年龄 |
| `application` | 固定 `baseband`（本施加点） |
| `reason` | 未补偿原因（无目标/后端已接管/已关闭） |
| `error` | 预测/绑定失败原因（空=正常） |

## 3. 事实源裁定：C++ 1Hz ZOH vs Python

### 3.1 Python 侧

`mbdsdr_ai/doppler_link.py::DopplerLinkCompensator` 是**唯一预测事实源**：

- 接收链补偿级只调用源的 `reading()` 取 `doppler_hz`，不做第二份轨道数学；
- 工具 `sdr_doppler_compensation` 也只从该单例取预测；
- `tx_freq()` / `rx_freq()` 同源（发射用发射时刻预测，不用录制时刻值）。

### 3.2 C++ 侧（只读事实，不在本批改动范围）

- `cpp/src/ui/main_window.cpp` 1Hz 循环（约 `:7594-7629`）：用 C++ 自己的
  `tleClient_->propagate…` 距离率算 `liveFd`，经 `dopplerLimiter_` 限速后写
  `engine_->vfoSetOffset(...)`——即**调谐器侧预偏**。
- 捕获时 `dopplerLimiter_.reset(p.dopplerAtPeakHz)`（约 `:7205`）；
  开关 `onDopplerCompToggled`（约 `:7229`）；程序面桥
  `setDopplerCompensationEnabled`（约 `:7278`）只驱动真实复选框。
- C++ 不读取 Python 的 `doppler_link`，Python 也不读取 C++ 的 limiter 状态。

### 3.3 只允许一个：仲裁规则

| 施加点 | 谁真正施加 | 互斥机制 |
| --- | --- | --- |
| `tuner` | 硬件/VFO 预偏（C++ 1Hz ZOH 或 Python 调谐器路径） | `doppler_link.state.application == "tuner"` 时，接收链补偿 `_target_from_reading` 返回 `None`（不叠加） |
| `baseband` | 接收链 NCO（本文档主体） | `stage.set_enabled(True)` 显式把 `application` 置为 `baseband`；`sdr_satellite_doppler_track` 反之置 `tuner` 并关闭 NCO |
| 后端已补偿 | C++/基带自行补偿 | `backend_comp_active=True` → `set_enabled(True)` 直接拒绝，读数给「防止双倍偏移」原因 |

关键不变量：**桌面 C++ 模式与 Python headless/MCP 模式不共享同一路设备流**。
C++ 应用在桌面进程内持有设备并做调谐器预偏；Python `SDRBackend` 只在
headless/MCP 路径取数。因此不存在「C++ ZOH + Python NCO 同时作用于同一
IQ 流」的形态。若未来引入桥把两者接到同一设备流，C++ 侧必须先调用
`set_backend_compensation_active(True)`，否则 Python 侧无法感知（列为
开放项，不假装已闭环）。

## 4. 验证（离线、合成 IQ）

测试：`mbdsdr_ai/tests/test_receive_doppler.py`

| 用例 | 判据 |
| --- | --- |
| 已知频偏残余 | 信号 +340 Hz、预测源报 300 Hz → CW BFO 音频峰 840 Hz（残余 +40 Hz，容差 ±5 Hz）；报 340 Hz → 峰 800 Hz（残余 ≈0） |
| 开关 | `enabled`/`active`/`applied_hz` 与真实状态一致；关闭后补偿平滑回 0 |
| 无目标 | `applied_hz == 0`、`reason` 含「无目标，未补偿」；输出与未接补偿的链**逐点相等** |
| 目标切换 | 相邻样本瞬时频移连续（边界差 < 1 Hz）；正→负过零约在 ramp 中点（480±80 样本）；单样本相位差不超过 2π·400/fs（无爆音） |
| 预测失败 | 异常/非有限数 → `DopplerCompensationError`；`SDRBackend` 本块返回 `None`，`status.error` 为 `doppler_compensation_failed` 话术（「未按 0 补偿」） |

残余容差依据：CW 解调 BFO 800 Hz、信道带宽 200 Hz，音频峰分辨率由
FFT 长度决定（≥0.7 Hz/格）；留 ±5 Hz 覆盖滤波器群延迟与 ramp 残留。

## 5. 诚实边界

- 上述全部为**合成信号自测**，不是真过境/真硬件验收；
- TLE 新鲜度、真实 range-rate 预测精度未在本文档背书，见限制总账；
- 补偿失败时按块丢弃音频并报错，绝不静默按 0 补偿。

## 6. 复跑

```powershell
$env:MBDSDR_NO_HW = "1"; $env:MBDSDR_OFFLINE = "1"
python -m pytest mbdsdr_ai/tests/test_receive_doppler.py mbdsdr_ai/tests/test_doppler_compensation.py tests/test_doppler_link.py -q
```
