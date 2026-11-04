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
| `active` | Python 侧是否真的在施加非零补偿 |
| `predicted_hz` | 预测源给出的 Δ（Hz）——**只是预测，不代表已施加** |
| `applied_hz` | 实际施加量（Hz）。`applied_by=python` 时为 NCO 实测累计值；`applied_by=backend` 时为 `null`（Python 不知道对方加了多少）；`applied_by=none` 时为 `0` |
| `applied_by` | 谁在真正施加：`python` / `backend` / `none` |
| `python_applied_hz` | Python 侧 NCO 自己的累计量（后端接管后应回 0） |
| `python_stream_owner` | 是否已显式声明本 IQ 流由 Python 独占（默认 `false`） |
| `target_doppler_hz` | Python 正在追赶的目标 Δ（Hz） |
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
| 后端已补偿 | C++/基带自行补偿 | `backend_comp_active=True` → `set_enabled(True)` 直接拒绝；**已在运行中的 Python NCO 也在下一块读到后 ramp 回 0**，读数 `applied_by=backend` / `applied_hz=null` |
| 未声明归属 | 任何形态 | `python_stream_owner=False`（默认）→ 接收链补偿**拒绝开启**；只有 `SDRBackend` 真正建链时才 `claim_python_stream()` 声明 |

### 3.4 部署形态默认值：保守（宁可不补偿，也不双倍）

`python_stream_owner` **默认 `False`**，不是 `backend_comp_active` 默认 `False`
就能放行。两者职责不同：

- `backend_comp_active`：后端明确宣称「我正在补偿」→ Python 拒绝/退回；
- `python_stream_owner`：Python 明确宣称「这条 IQ 流只有我在读」→ 才允许基带施加。

前者是后端通知，后者是 Python 自己的部署声明。默认两者都是保守值：
即使 `backend_comp_active=False`，没有 `python_stream_owner=True` 也**不补偿**。
声明点只有一个：

- `mbdsdr_ai/sdr_backend.py:582-593` `_ensure_receive_chain()` —— 真正创建
  `ReceiveChain`、准备亲自读 IQ 的路径，才调用
  `claim_python_stream()`。

桌面 Python 本地硬件与 headless/MCP 都走这条；C++ 桌面 / 远端 WebSocket
形态没有本地 `SDRBackend`，不会声明，因此保持「不补偿」。

### 3.5 真实路径实走（文件行号）

**A. 桌面 Python 本地硬件形态**

1. 读 IQ：`desktop/receive_pipeline.py:70-101` `IqReaderThread.run()` 循环
   `self._read_fn(self._block)` → `backend.read_samples`；频谱/瀑布同源。
2. 调谐器加偏移：`desktop/main_window.py:6622-6663`
   `_poll_satellite_track()` 调 `comp.rx_freq()`，把 `下行 + Δf` 传入
   `:3511-3519 _on_tune_sdr()` → `_active_sdr_backend.set_frequency(tune_hz)`。
   即偏移加在**硬件调谐器**上，应用点 `application="tuner"`。
3. Python 是否再施加：**没有**。桌面代码没有任何
   `set_doppler_compensation(...)` 调用；`SDRBackend._doppler_comp_enabled`
   初始 `False`（`mbdsdr_ai/sdr_backend.py:225`），接收链 NCO 默认关闭。
   即便有人显式打开，`application="tuner"` 也会让 `_target_from_reading`
   返回 `None`（`doppler_compensation.py:450-454`）。→ **1 倍，仅调谐器侧**。

**B. 桌面 WebSocket（ai-sdr Mini / C++ 后端）形态**

1. `desktop/main_window.py:3188-3192 _connect_real()` 只启动
   `self._worker = _worker_manager.start(...)`，不创建本地 `SDRBackend`；
   断开时 `:3410 _worker=None`，本地后端仍为空。
2. 调谐/偏移全部通过 `:3512-3514 _worker.request_tool.emit("tune_sdr", ...)`
   发给远端；IQ 与 DSP 都在远端进程。
3. Python 侧没有 `ReceiveChain`、没有 `SDRBackend` → **不施加**。
   即使误调 `sdr_doppler_compensation`，`_ensure_receive_chain` 因无本地
   连接返回 `None`（`sdr_backend.py:563-565`），无法建链 → 不施加。

**C. C++ 桌面进程（若作为独立部署运行）**

1. 1Hz 循环：`cpp/src/ui/main_window.cpp:7574-7602` 计算
   `liveFd = dsp::dopplerHz(...)`，经 `dopplerLimiter_.advance(liveFd)` 后
   `engine_->vfoSetOffset(selVfo, f0 + off)`。这是调谐器侧施加。
2. 该进程与 Python 桌面/headless 不共享设备流（USB 设备独占；WebSocket
   形态下 Python 只发工具请求，不拿 IQ）。Python 侧不会参与。
3. 新的硬闸不依赖这条部署事实：只要 Python 这条流没有
   `python_stream_owner=True`，基带就拒绝施加；若未来桥真的把同一流接到
   Python，C++ 侧必须调用 `set_backend_compensation_active(True)`，Python
   读到后连已运行的 NCO 也会 ramp 回 0（`python_applied_hz` 如实归零）。

## 4. 验证（离线、合成 IQ）

测试：`mbdsdr_ai/tests/test_receive_doppler.py`

| 用例 | 判据 |
| --- | --- |
| 已知频偏残余 | 信号 +340 Hz、预测源报 300 Hz → CW BFO 音频峰 840 Hz（残余 +40 Hz，容差 ±5 Hz）；报 340 Hz → 峰 800 Hz（残余 ≈0） |
| 开关 | `enabled`/`active`/`applied_hz` 与真实状态一致；关闭后补偿平滑回 0 |
| 无目标 | `applied_hz == 0`、`reason` 含「无目标，未补偿」；输出与未接补偿的链**逐点相等** |
| 目标切换 | 相邻样本瞬时频移连续（边界差 < 1 Hz）；正→负过零约在 ramp 中点（480±80 样本）；单样本相位差不超过 2π·400/fs（无爆音） |
| 预测失败 | 异常/非有限数 → `DopplerCompensationError`；`SDRBackend` 本块返回 `None`，`status.error` 为 `doppler_compensation_failed` 话术（「未按 0 补偿」） |
| 未声明归属（默认保守） | `python_stream_owner=False` → `set_enabled(True)` 返回 `False`，`applied_by=none`、`applied_hz=0`；跑 1024 点输出与输入逐点相等，`python_applied_hz` 恒 0 |
| 后端接管（双施加反证） | 先 Python 把 500 Hz 补到 0；后端声明 `backend_comp_active=True` 后，Python 在 20 ms ramp 内退回 0；最终残余实测 **500 Hz（1 倍，±5 Hz）**，不是 0 也不是 1000 |
| 读数不混用 | 后端施加时 `applied_by=backend`、`applied_hz=null`、`predicted_hz=500`、`python_applied_hz=0`；Python 施加时三者分别为 `python` / 实测值 / 预测值 |

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
