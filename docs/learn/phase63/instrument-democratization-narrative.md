# 仪器普及化：立项叙述素材页（论文支持，非论文正文）

> 用途：给后续论文/立项书整理 problem→approach→evidence 骨架与实证清单；
> 只整理素材与数据缺口，不写论文正文。干净室 MIT，不预置呼号/电台。

## 1. Problem
- 专业无线电仪器（对讲机亚音静噪、卫星数据广播、天线/馈线网络分析仪） historically
  昂贵、闭源、厂商锁定；爱好者/教学者难获得可复现、可审计的全链路工具。
- 通用 SDR 接收机能听，但缺乏：
  - 亚音静噪（CTCSS 模拟 / CDCSS 数字）检测与静噪门控；
  - 卫星数字广播（LRPT）与窄带通信（FT8）的可审计解码链；
  - 射频阻抗/网络分析（扫频、Smith 圆图、谐振/滤波器/TDR/等效电路）能力。
- 现有开源实现多为 GPL、GUI 黑盒、不可嵌入；缺乏可干净室重写、可单元测试、
  可被 AI 三通道工具调用的现代化 C++ 内核。

## 2. Approach
- 单仓 C++ 内核（offscreen 可测）+ Qt UI + AI 三通道工具（schema/executor/hub），
  同一组纯函数被 UI 与 AI 工具复用。
- 干净室纪律：GPL 只看公开公式/功能清单，代码自写（MIT）；每算法给干净室自述。
- 诚实空态：无硬件/点数不足/数据无效 → 显式 false/NaN/"--"，不伪造读数。
- 确定性合成测试先行：合成已知信号/扫频夹具 → 数值钉扎，离线 offscreen 全绿。

## 3. Evidence（实证清单）

### 3.1 SDR 接收侧（第一实证）
| 能力 | 机制 | 状态 |
|---|---|---|
| CTCSS 模拟亚音静噪 | Goertzel 单音检测 + 诚实去抖（DetectHits/Misses） | 已落地，引擎 gate 互斥域 |
| CDCSS 数字亚音 | 134.4bps 3-of-8 码表 + Golay(23,12) 纠错 | 已落地，全链 e2e |
| LRPT 状态控制 | C++ 状态层 + set_lrpt/get_lrpt_status | 已落地（解码链 C++ 移植 PENDING） |
| FT8 | 并行线 A（Python run-loop） | 线 A 域 |

### 3.2 VNA 网络分析侧（第二实证）
| 能力 | 机制 | 状态 |
|---|---|---|
| 扫频数据读回 | NanoVNA 串口协议（公开命令序列，干净室） | 已落地 |
| Smith/Polar | Γ→归一化 z / mag-phase→xy 纯函数 + 自绘圆图 | 已落地，R/X 网格弧 |
| 谐振分析 | |Z| 极值 + -3dB BW + 有载 Q + ESR | 已落地 |
| TDR | S11 频域→时域（Kaiser 窗 IFFT）+ 速度因子→长度 | 已落地（多峰 best-effort） |
| 同轴损耗 | S21 dB/长度（TDR 长度反算） | 已落地 |
| 滤波器识别 | BP/BS/HP/LP 自动识别 + 截止/带宽/插损/阻带 | 已落地，四形态数值钉扎 |
| 等效电路 | 串联 RLC（ESR/Q/L/C）提取 | 已落地，analyze_vna_equivalent 工具 |
| Touchstone s1p/s2p | 解析/写出 round-trip | 已落地 |
| 磁导率 NRW | μr'/μr'' 一阶薄板近似 | 已落地（近似标注） |
| 群延迟 | S21 相位差分 | 已落地 |
| EFHW/LC 匹配 | 端馈匹配网络 L/C 建议 | 已落地，analyze_vna_match 工具 |

### 3.3 AI 三通道工具面
- **70 工具注册**，三通道一致（registry/schema/agent/hub/ai_real_link/mobile/phase31/README 八处全绿）；
- 写工具手动模式 gate 拦截（ControlHub manual 语义 + MCP 侧 arm_manual），读工具诚实空态；
- **校准闭环**：vna_calibration 门面（单工具状态机，只读引导给客观下一步 + 写步骤人在环）+ get_vna_status 校准结构化解读（每步 done/missing、补偿态、扫频范围变更后旧校准失效显式提示）；
- **VNA 设备级杂项**：vna_device 门面（status/vbat/freq_offset/edelay/averaging 单工具状态机）；
- **MCP 外部 agent 接入**：C++ mbdsdr-mcp（JSON-RPC 2.0 over stdio，initialize/tools/list/tools/call，工具表=toolDefs() 唯一事实源，零模型依赖）与 Python mbdsdr.decoders 可同时接入，**9 域分组视图**（SDR 调谐/VFO、亚音数字解码、VNA 测量、VNA 分析、VNA 校准、导出演制、书签扫描卫星、系统设备、记忆编排；每工具恰一域，C++/Python 域名集合钉扎一致；Python 另含「解码器长尾」扩展域）；
- **去预制化**：产品本体=Agent+tool call、模型自主推理；代码零场景话术/零固定多步剧本（onramp 为真实工具链薄编排）。

## 4. PENDING（真机列，诚实数据缺口）
- [ ] 真机 NanoVNA-H 实测扫频（当前 all synthetic seed 夹具）；
- [ ] 真机手台 PTT 频偏校准实测（409.750/438.500）；
- [ ] 真机 LRPT 卫星下行 → C++ 解码链（当前 Python 原型）；
- [ ] 真机 CTCSS/CDCSS 亚音发射 → 检测门控 e2e；
- [ ] bandCombo 频段预设真机交互取景（离线快照框未取景）；
- [ ] 并联 RLC 拟合（高 Q 并联谐振场景，非明确缺口）；
- [ ] vbat/freqoffset/edelay/averages 设备命令真机核对（命令词为 H 协议事实，行为待真机验证，protocol-study §2.2）；
- [ ] RTL-SDR ENABLED（librtlsdr）构建只能在真机/Windows 侧验证。

## 5. 可直接引用的数据点
- test_vna_rf 24 纯函数钉扎全绿（含 xps/xs/xpl 三电抗拓扑 + R 补偿）；
- 金集 70 工具三通道全绿（registry 8/schema 10/agent 56/hub 34/ai_real_link 17/mcp_server 12/nanovna_client 14）；
- Python pytest 335 passed（含 mcp_server 分组 7 用例）；
- 三档 UI 快照 640/960/1920 零裁切零叠字（窄档 caption 截断诚实 floor）。
