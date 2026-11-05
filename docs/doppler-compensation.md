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
- 四个发射入口共用同一公共仲裁 `sdr_tools.resolve_tx_doppler(nominal_hz,
  requested)`，返回 `(实际发波频率, 读数)`；UI 预览只能调它，不得自己算 Δ。
- 工具 `data.doppler` 字段：`requested` / `mode` / `applied` / `nominal_hz`
  / `freq_hz` / `delta_hz` / `target`；未预偏必须带 `reason`，不得用预测值
  冒充实际。
- 反证测试 `mbdsdr_ai/tests/test_transmit_doppler.py`：CW/IQ 已知频偏下
  `发波频率 − 标称 == 预测 Δ`；显式关时**严格等于**标称；无目标两种
  （自动/显式开）都不偏；后端接管时也不追加预偏；回放路径默认严格标称
  且 `doppler.mode=off`。

全仓真发波入口清点（2026-11-05，不含纯预览/纯建模）：

| 入口 | 类型 | 过 `resolve_tx_doppler` | 说明 |
| --- | --- | --- | --- |
| `sdr_transmit_cw` | MCP 工具 | 是 | 有目标自动预偏、`doppler_precomp=false` 严格标称 |
| `sdr_transmit_iq` | MCP 工具 | 是 | 同上；回放已知录音必须显式 `false` |
| `tc_uplink_transmit` | MCP 工具 | 是 | 同上，TC 上行同一符号约定 |
| `playback.transmit_iq_playback` | 库 API（IQ 回放给硬件） | 是（默认 `doppler_precomp=False`） | 回放是「这段录音本来要表达的频率」，默认严格标称；结果带 `data.doppler` 读数 |
| `hal.HardwareManager.transmit_cw` / `transmit_modulated` | HAL 底层原语 | 否 | 接收**已仲裁**的频点，禁止二次施加；全仓无生产调用方（仅测试直调），不是用户面入口 |
| C++ `soapy_transmit` / `tx_controller.sendCw` / AX.25 beacon | C++ 发射 | 否 | 不接 Python `doppler_link`；见限制总账 L42（桌面 C++ 发射无预偏） |

桌面 UI 的「回放」入口若未来接线，必须走 `transmit_iq_playback`（或显式
传 `doppler_precomp=false`），不能绕过公共仲裁。

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

#### 3.8.1 发射能力与验证状态（PTT 按下前必须可见）

**事实（2026-11-05）**：HackRF / USRP / Pluto 三个后端都是「驱动级已实现、
真机未验证」——`SDRDevice.tx_verified=False`，测试只在 mock/假后端上真调过
`write_tx`。界面不能把「代码路径存在」显示成「支持发射」。

- **位置**：发射控制区（PTT/发射/回放按钮所在面板）常驻一行能力声明，
  **在 PTT 可按下之前就必须显示**；不得只放 tooltip、toast 或日志。
- **数据源**：统一读 `backend.tx_capability()`，禁止 UI 自己拼
  `supports_tx()` 的布尔值（布尔会把「驱动可发但本路径未接」压成「不能发」）。
  字段：`declared` / `implemented` / `ready` / `verified`；Soapy 通用另带
  `driver_capable` / `driver_tx_channels` / `blocked_reason` / `notice`。
- **文案表（不得省略「真机未验证」）**：

| `tx_capability()` | 必须显示 |
| --- | --- |
| `ready=true, verified=false` | `本后端发射：驱动级已实现 / 真机未验证` |
| `ready=true, verified=true` | `本后端发射：驱动级已实现 / 真机已验证` |
| `declared=true, implemented=false` | `本后端发射：仅有声明 / 未实现（不可发射）` |
| `driver_capable=true, blocked_reason=driver_capable_path_not_implemented`（Soapy 通用） | `本后端发射：驱动级可发 / 本路径未接入（不可发射）` |
| `driver_capable=false, blocked_reason=no_tx_channels` | `本后端发射：硬件仅接收（驱动报告 0 个 TX 通道）` |
| `driver_capable=null, blocked_reason=driver_capability_unknown` | `本后端发射：能力未知（未连接/驱动未报告）` |
| `declared=false, implemented=false, driver_capable=null` | `本后端发射：未实现` |

- 未验证时按钮可用不代表「已验证可发」：按下后失败必须按真实错误显示
  （`status.error` / 工具信封），不得用「已发射」占位。
- **桌面现状**：`desktop/main_window.py` 的 PTT 置灰文案仍只按
  `supports_tx()` 显示「当前设备不支持发射（后端没有 TX 通道）」，且 Soapy
  通用路径没有 `write_tx`，会走到这一句——对「HackRF/USRP 经 Soapy 打开、
  驱动能发」的场景是**把能说成不能**。该文件属操作逻辑线，本线只写契约、
  不改代码；替换后按上表文案取值。
- 契约测试锚点：`tests/test_backend_tx_capability.py`（三后端 verified=False；
  Soapy 第三态三种取值）。

#### 3.8.2 UI 接线顺序（供操作逻辑线照抄）

**结论：三步都不能提前下结论。第 1/2 步只回答「本后端有没有写流 / 是否声明」，
任何一步为否都不许直接显示「没有 TX 通道」——必须走到第 3 步取
`tx_capability()` 的 `driver_capable` 才能区分「硬件不能发」与「本路径未接」。**

1. `impl = callable(getattr(type(backend), "write_tx", None))` —— 本类是否有
   写流实现（Soapy 通用路径为 False，但这**不代表**硬件不能发）。
2. `declared = bool(backend.supports_tx())` —— 声明 ∧ 就绪（Soapy 为 False）。
3. `cap = backend.tx_capability()` —— 唯一文案事实源：
   `impl/declared/ready/verified` + `driver_capable/driver_tx_channels/
   blocked_reason/notice`。前两步得到的值跟 `cap` 冲突时，**以 `cap` 为准**。

判定与文案（按优先级从上到下，命中即停）：

| # | 条件（以 `cap` 字段为准） | 用户看到原文 | PTT 按钮 |
|---|---|---|---|
| 1 | `implemented=true && ready=true && verified=false` | 本后端发射：驱动级已实现 / 真机未验证 | **可点 + 常驻警示**（黄色条，非 tooltip） |
| 2 | `implemented=true && ready=true && verified=true` | 本后端发射：驱动级已实现 / 真机已验证 | 可点 |
| 3 | `declared=true && implemented=false` | 本后端发射：仅有声明 / 未实现（不可发射） | 置灰 |
| 4 | `driver_capable=true`（`blocked_reason=driver_capable_path_not_implemented`） | 本后端发射：驱动级可发 / 本路径未接入（不可发射） | 置灰 |
| 5 | `driver_capable=false`（`blocked_reason=no_tx_channels`） | 本后端发射：硬件仅接收（驱动报告 0 个 TX 通道） | 置灰 |
| 6 | `blocked_reason=driver_capability_unknown` | 本后端发射：能力未知（未连接/驱动未报告） | 置灰 |
| 7 | 其余（`declared=false && implemented=false`） | 本后端发射：未实现 | 置灰 |

照抄骨架（只替换 `main_window.py` 里生成置灰文案的那个分支；**不要改门语义**）：

```python
def ptt_state(backend) -> tuple[str, bool, bool]:
    """返回 (常驻文案, 是否可点, 是否带警示)。"""
    impl = callable(getattr(type(backend), "write_tx", None))
    declared = bool(backend.supports_tx())
    try:
        cap = backend.tx_capability()
    except Exception:
        cap = {}
    impl = bool(cap.get("implemented", impl))
    declared = bool(cap.get("declared", declared))
    driver = cap.get("driver_capable")
    reason = str(cap.get("blocked_reason") or "")

    if impl and bool(cap.get("ready")) and bool(cap.get("verified")):
        return "本后端发射：驱动级已实现 / 真机已验证", True, False
    if impl and bool(cap.get("ready")):
        return "本后端发射：驱动级已实现 / 真机未验证", True, True
    if declared and not impl:
        return "本后端发射：仅有声明 / 未实现（不可发射）", False, False
    if driver is True:
        return "本后端发射：驱动级可发 / 本路径未接入（不可发射）", False, False
    if driver is False:
        return "本后端发射：硬件仅接收（驱动报告 0 个 TX 通道）", False, False
    if reason == "driver_capability_unknown":
        return "本后端发射：能力未知（未连接/驱动未报告）", False, False
    return "本后端发射：未实现", False, False
```

接线注意：
- 文案必须在 PTT **可按下之前**就可见，不能只在按下失败后弹一次。
- 第 1 行（未验证）按钮**可点但带警示**；`verified` 不得由 UI 推导，只能读字段。
- 现有 `if not callable(write_tx): → 「后端没有 TX 通道」` 这句要删掉，改走
  上表第 4/6/7 行；「没有 TX 通道」只允许在 `driver_capable=false`（第 5 行）。

### 3.9 C++ → Python `backend_comp_active` 状态桥接：可做，但现在不能接

**结论：技术上可做，现有通道够用；现在接的是死代码，所以不接——但出事条件
必须写死，不能只挂“开放项”。**

可行的只读上报（不需要新 WebSocket/新协议）：

- C++ `ControlHub::cmdGetStatus` 已有 `doppler_available` / `doppler_enabled`，
  经 loopback HTTP（`127.0.0.1:50732`，`GET /status`）与 MCP `get_status`
  工具对外；
- 主窗口已缓存**引擎实际施加**的累计 offset：`lastSpDopplerHz_`
  （`cpp/src/ui/main_window.cpp:7603-7621`），C++ 的补偿施加点就在
  `engine_->vfoSetOffset(...)`；
- `cpp/src/dsp/doppler_control_surface.h` 是现成的接口面，加一个只读
  `dopplerAppliedHz()`（`compOn && trackedPass ? lastSpDopplerHz_ : 0`）
  后，在 status 里加 `doppler_applied_hz` 即可；它只读，不参与控制；
- Python 侧消费点也已就绪：`doppler_link.use_backend_compensation()`
  （`doppler_link.py:167-178`）能置 `backend_comp_active`，
  `sdr_backend.py:660` 也会从 status 读该字段。

**为什么不接**：`set_backend_compensation_active()` 全仓**零调用方**；而且
Python 只有在本地 `SDRBackend` 真正 claim 流时才置
`python_stream_owner=True`（唯一 claim 点 `_ensure_receive_chain`），
C++ 桌面 / WebSocket 形态下 Python 不读 IQ（`desktop` 无本地后端）。
两侧各自在自己的链上施加，互不相见——这正是「默认保守不施加」成立的原因。
没有消费方就加上报，等于第二个 `tx_backend_comp_active` 式的假接口。

**什么条件下会真的出事（必须同时满足）**：

1. Python 侧 `python_stream_owner=True`（某本地/网络后端由 Python 独占读 IQ）；
2. 同一路 IQ 的中心频率已被 C++ 移动过——只有 `vfoSetOffset` 的**出带**
   分支会动共享 tuner（`|target − center| > usableHalf`，
   `cpp/src/dsp/spectrum_engine.cpp:1140-1148` → `source_->setCenterFreq`）；
3. Python 接收链 NCO 又按自己的预测施加一次。

此时同一 Δ 被施加两次（接近 −2Δ、远离 +2Δ），读数仍显示
`applied_by=python`，只有残余频偏能暴露。注意：常规多普勒 Δ 通常 < 1 kHz，
远小于 `usableHalf`（数百 kHz），走的是 C++ **自己通道内**的 VFO NCO，
不会移动共享 tuner，也不影响 Python 读到的原始流——所以当前不会触发。
真正的触发形态是「C++ 把补偿搬到 tuner / 或别的功能移动了共享中心，同时
Python 还在按旧中心做基带补偿」。

**收口前提（写死，不靠记忆）**：一旦出现「Python 读同一路 IQ 且 C++ 在补偿」
的部署形态（例如同一 rtl_tcp 共享源 + C++ 出带重调 + Python 本地后端），
必须先接 C++ `doppler_applied_hz` 只读上报，再由该形态的 Python 侧调用
`use_backend_compensation()`；在那之前不接。

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

发射侧反证：`mbdsdr_ai/tests/test_transmit_doppler.py`（CW/IQ/TC 与回放
各条，判据见 3.7；回放用例在修复前因缺 `doppler` 读数必红）。

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
