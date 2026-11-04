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

### 3.6 UI 接口契约（交操作逻辑线接线；本线**不改** `desktop/main_window.py`）

界面只需一个读数入口 + 一个开关状态。三者返回**同名同义字段**（字段只增不改）：

| 入口 | 适用 |
| --- | --- |
| `SDRBackend.get_doppler_compensation_status()` | 桌面 Python 本地硬件（推荐） |
| `ReceiveChain.doppler_status()` | 直接持有接收链时 |
| `DopplerCompensationStage.status()` | 单元/嵌入调试 |

一句话纪律：**数字只取 `applied_hz`，归属只取 `applied_by`，预测只取
`predicted_hz`；三者不可互相顶替。** `applied_hz` 为 `null` 时界面显示
「—」，不得渲染成 `0`。

#### 字段与语义

| 字段 | 类型 | 语义 |
| --- | --- | --- |
| `enabled` | bool | 用户开关。**按钮状态只映射它**，不要用 `active` 驱动按钮 |
| `active` | bool | Python 基带此刻是否真的在施加非零补偿 |
| `applied_by` | `"python"`/`"backend"`/`"none"` | 谁在真正施加 |
| `applied_hz` | float/null | 实际施加量。`python` 时为 NCO 实测累计值；`backend` 时**必为 `null`**（Python 不知道对方加了多少）；`none` 时为 `0.0` |
| `predicted_hz` | float/null | 链路预测 Δ，**只是预测**；`backend` 时可作旁注，不可当选读数 |
| `python_applied_hz` | float | Python 自己 NCO 的累计量（后端接管后应回 0，可用于自证没有双施加） |
| `python_stream_owner` | bool | 本 IQ 流是否已声明由 Python 独占（诊断行用） |
| `target_doppler_hz` | float/null | Python 正在追赶的目标 Δ |
| `settling` | bool | 是否仍在 20 ms ramp 渐变中 |
| `application` | `"tuner"`/`"baseband"` | 声明的施加点；`tuner` 表示偏移加在调谐器侧 |
| `target_name` / `predicted_at_unix` / `tle_tier` / `tle_age_days` | — | 溯源（目标、预测时刻、星历级别与年龄） |
| `reason` | str | 未补偿原因（无目标/未声明归属/后端接管/调谐器生效/已关闭） |
| `error` | str | 预测或绑定失败原因；非空即本块音频被丢弃，**不得显示为 0 Hz 已补偿** |

#### 显示文案（按顺序判定，先命中先显示）

| 条件 | 补偿读数 | 旁注 |
| --- | --- | --- |
| `applied_by == "backend"` | `—（由后端施加）` | 可显示「预测 {predicted_hz:+.1f} Hz」，标明是预测 |
| `applied_by == "python"` | `{applied_hz:+.1f} Hz（Python 基带）` | `settling` 时前缀「切换中」 |
| `applied_by == "none"` 且 `application == "tuner"` 且 `enabled` 且 `predicted_hz != null` | `—（由调谐器施加）` | 「预测 {predicted_hz:+.1f} Hz」（调谐器侧不向 Python 回报实际值） |
| 其余 | `0 Hz（未补偿）` | 显示 `reason` 的短句 |
| `error` 非空（优先级最高） | `补偿失败` | 显示 `error` 原文，本块无音频 |

开关行：`多普勒补偿：开` / `多普勒补偿：关`（直接取 `enabled`）。
诊断行（可选）：`IQ 流归属：Python 独占` / `IQ 流归属：未声明（不施加基带补偿）`。

`reason` → 短文案建议：`无目标` → 「未绑定卫星目标」；`未声明 Python 独占
IQ 流` → 「本部署形态不参与基带补偿」；`后端…已接管` → 「由后端施加」；
`调谐器侧补偿生效` → 「由调谐器施加」；`补偿关闭` → 「已关闭」。

#### 接线骨架（UI 线照抄即可，字段名以本节为准）

```python
st = backend.get_doppler_compensation_status()
if st.get("error"):
    text, note = "补偿失败", st["error"]
elif st["applied_by"] == "backend":
    text = "—（由后端施加）"
    note = f"预测 {st['predicted_hz']:+.1f} Hz" if st["predicted_hz"] is not None else ""
elif st["applied_by"] == "python":
    text = f"{st['applied_hz']:+.1f} Hz（Python 基带）"
elif (st["application"] == "tuner" and st["enabled"]
      and st["predicted_hz"] is not None):
    text = "—（由调谐器施加）"
    note = f"预测 {st['predicted_hz']:+.1f} Hz"
else:
    text, note = "0 Hz（未补偿）", st["reason"]
switch_on = bool(st["enabled"])          # 只由 enabled 驱动
```

轮询：≤ 1 Hz（或开关/目标变化时）。读数只读缓存，不做轨道传播，可安全
放在状态栏定时器里。`main_window.py` 的接线由操作逻辑线负责，本批未改动
该文件。

### 3.7 发射侧（TX）仲裁

发波频点与接收走**同一个** `doppler_link.tx_freq()` 与同一符号约定（接近
→ 压低、远离 → 抬高），`backend_comp_active` 互斥闸同样生效：

- `sdr_transmit_cw` / `sdr_transmit_iq`（真接 HAL 发波）：缺省由状态推导
  ——有跟踪目标自动按发射时刻 `refresh()` 后预偏，读数写「已预偏（自动）」；
  无目标不偏并写「无目标，未预偏」；`doppler_precomp=false` 严格按标称。
- **回放已知录音必须显式 `doppler_precomp=false`**：IQ 回放要表达的是录音
  原本的频率，自动预偏会把整段录音搬到另一个频点。工具描述已写明该提示，
  桌面「回放」入口也必须传 `false`。
- `tc_uplink_transmit`（TC 上行）：同一规则，`doppler.requested=null` 即
  自动、`mode` 取 `auto/on/off`，`applied` 与 `delta_hz` 如实给。
- 三个发射工具共用同一公共仲裁 `sdr_tools.resolve_tx_doppler(nominal_hz,
  requested)`，返回 `(实际发波频率, 读数)`；UI 预览只能调它，不得自己算 Δ。
- 工具 `data.doppler` 字段：`requested` / `mode` / `applied` / `nominal_hz`
  / `freq_hz` / `delta_hz` / `target`；未预偏必须带 `reason`，不得用预测值
  冒充实际。
- 反证测试 `mbdsdr_ai/tests/test_transmit_doppler.py`：CW/IQ 已知频偏下
  `发波频率 − 标称 == 预测 Δ`；显式关时**严格等于**标称；无目标两种
  （自动/显式开）都不偏；后端接管时也不追加预偏。

### 3.8 发射可见性（桌面 UI 契约，交操作逻辑线接线）

**现状：没有。** `desktop/` 内检索 `sdr_transmit_*` / `TxSession` 零命中，
桌面形态目前没有发射入口，也没有任何发射频率/预偏读数——发射只能经工具面，
用户在界面上看不到自己发的是什么频率。以下为接线契约（本线不改
`desktop/main_window.py`）：

- **数据源**：发射前调用 `sdr_tools.resolve_tx_doppler(nominal_hz, None)`
  取 `(freq_hz, note)`；真正发波时把同一个 `freq_hz` 传给发射函数；发射后
  用返回值 `data.doppler` 回填同一控件。禁止 UI 自行用预测值计算。
- **控件位置**：发射控制区（发射/回放按钮旁）常驻一行「TX 频率」；状态栏
  TX 段可复用（与 3.6 的多普勒读数并列）。**不能只写在 toast/日志**。
- **必显三项**：实际发波频率、标称频率、预偏状态。文案：

| 条件（`data.doppler`） | 主读数 | 次行 |
| --- | --- | --- |
| `applied=true, mode=auto` | `发波 145.799027 MHz（已预偏 −973.0 Hz，自动）` | `标称 145.800000 MHz，目标 ISS (ZARYA)` |
| `applied=true, mode=on` | `发波 145.799027 MHz（已预偏 −973.0 Hz，显式）` | 同上 |
| `applied=false, mode=off` | `发波 145.800000 MHz（未预偏：已关闭）` | — |
| `applied=false, mode=auto`（无目标） | `发波 145.800000 MHz（未预偏：无目标）` | — |
| `applied=false`（后端接管） | `发波 145.800000 MHz（未预偏：后端补偿已接管）` | — |
| 发射失败/未发射 | `未发射（原因）` | 不显示任何「已预偏」 |

- 频率格式：MHz 保留 **6 位小数**（才能看见 1 kHz 级预偏）；Δ 保留 0.1 Hz；
  `null` 显示「—」，不得渲染成 0。
- **回放入口**：IQ 回放必须传 `doppler_precomp=false`，控件显示
  `未预偏（回放，保持原频率）`，并置灰/隐藏自动预偏开关，避免误改录音频率。
- 预览调用时机：发射频率变化、面板打开、目标切换时，不做每帧轮询。

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
| 过境全程（真实 TLE） | 内嵌 ISS(25544) 真实 TLE，示例站点 (0,0)，近天顶过境（TCA 仰角 86.3°），1 s / 块跑满 600 s；多普勒 +3358 → −3361 Hz 过零；稳态残余峰值实测 **59.31 Hz ≤ 80 Hz**；逐样本相位差 ≤ 2π·100/fs（无爆音）；零交叉前后块边界频移跳变 < 5 Hz |

残余容差依据（固定频偏）：CW 解调 BFO 800 Hz、信道带宽 200 Hz，音频峰
分辨率由 FFT 长度决定（≥0.7 Hz/格）；留 ±5 Hz 覆盖滤波器群延迟与 ramp 残留。

过境阈值依据（80 Hz）：1 s ZOH 的目标更新间隔 × 该过境窗口内 SGP4 实测
最大多普勒斜率 **59.3 Hz/s**（近天顶零交叉处）= 59.3 Hz 一阶滞后上界；
20 ms ramp 额外 ≤ 斜率×0.02 s ≈ 1.2 Hz；再留约 30% 余量取整到 80 Hz。
实测峰值正好落在斜率上界（59.31 Hz），说明残余由 ZOH 采样间隔而非 NCO
相位/滤波引入——把更新间隔降到 0.5 s 时该上界同比例减半。

## 5. 诚实边界

- 除「过境全程」一条外，其余为固定频偏的**合成信号自测**；
- 「过境全程」是**真实 TLE + SGP4 真轨迹**（时间、仰角、range-rate 都是真的），
  但 IQ 仍是按该轨迹合成的，**不是真硬件/真 OTA 录制**；真实接收链路的
  晶振误差、电离层/对流层延迟未在此背书，见限制总账；
- TLE 新鲜度、真实 range-rate 预测精度未在本文档背书，见限制总账；
- 补偿失败时按块丢弃音频并报错，绝不静默按 0 补偿。

## 6. 复跑

```powershell
$env:MBDSDR_NO_HW = "1"; $env:MBDSDR_OFFLINE = "1"
python -m pytest mbdsdr_ai/tests/test_receive_doppler.py mbdsdr_ai/tests/test_doppler_compensation.py tests/test_doppler_link.py -q
```
