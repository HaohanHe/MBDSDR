# MBDSDR 重复实现与一致性台账
> SPDX-License-Identifier: MIT
>
> **为什么有这张表**：同一份公开协议/算法在仓里出现两份实现，是"隐性分叉"的最大来源——
> 改一处忘另一处时，测试可能全绿而行为已经不一致。本表把每一组"看起来重复"的东西
> 摊开，逐条给出**现状、判定依据、处置结论**，使重复实现状态可被审计，而不是靠记忆。
>
> **判据**（三选一，必须落到其中之一）：
> 1. **已收敛**：存在唯一权威实现，另一处是薄委托层，且有等价性断言钉住；
> 2. **有意分层**：能力范围不同（不是同一件事的两次实现），且交叉一致性有断言；
> 3. **待收敛**：确属重复，已识别并写明收敛前置条件（不许默默留着）。
>
> 关联：[能力地图](capability-map.md)（模型能操作什么）、
> 诚实红线见仓库根 `AGENTS.md`。

---

## 1. 已收敛（单一权威实现）

| 主题 | 权威实现 | 薄层/历史位置 | 钉死方式 |
|---|---|---|---|
| CRC | `mbdsdr_ai/crc.py`（参数化内核 + 命名预设，CRC8/14/16/24/32 + 比特域） | 各协议模块原内联实现已改为调用预设 | 回归向量（"123456789" → X.25=0x906E / CCITT-FALSE=0x29B1）+ 既有协议测试 |
| 调制/解调 | `mbdsdr_ai/modulation.py`（ASK/BPSK/QPSK/8PSK，NRZ/RRC，映射参数化） | `freedv_modem.bits_to_qpsk` 已改为委托（mapping="gray"） | `mbdsdr_ai/tests/test_modulation.py`（31 项：往返零误码、BER 与理论对照、Gray 单 bit 相邻、外来实现等价） |
| RRC 抽头 | `mbdsdr_ai/fir_taps.root_raised_cosine_taps` | `modulation._rrc_taps` 复用之 | `test_pulse_shape_taps_matches_fir_taps`（逐点相等断言） |
| ACARS 编解码 | `mbdsdr_ai/acars_protocol.py`（已验证实现：相位搜索 + 极性对齐 + CRC 校验选择） | `acars_decoder.acars_decode_iq` 高层入口已委托之 | `tests/test_acars_consistency.py`（公开入口必须 crc_ok=True；两条入口结果一致；ETB 与 1200 bps 兼容） |

**ACARS 的历史教训**（值得单列）：两实现长期并存，其中被 MCP 工具链使用的
`acars_decoder` 状态机路径对**自己合成**的信号自环不通（`crc_ok=False`），
而既有测试只覆盖了另一份实现，于是该缺陷长期不暴露。交叉实测矩阵：

| 合成 \ 解码 | acars_protocol | acars_decoder |
|---|---|---|
| acars_protocol 合成 | 1 条 / crc_ok=True | **0 条** |
| acars_decoder 合成 | 1 条 / crc_ok=True | 1 条 / **crc_ok=False（字段乱码）** |

结论：规范模块的**合成器是对的、解码器是坏的**。处置是让公开入口走已验证实现，
并在 docstring 里如实标注"自带状态机路径自环不通，待真实采集时域对齐排查后切回"，
同时保留原生实现与测试——不做"两份都留着各修一半"。

---

## 2. 有意分层（不是重复）

| 主题 | 分层依据 | 交叉一致性断言 |
|---|---|---|
| ADS-B/Mode-S | `adsb.py`（完整版：含 CPR 奇偶帧位置解算、状态化配对解码）与 `adsb_lite.py`（精简版：**不做** CPR 位置解算，工具链与桌面面板实际使用的是它） | `tests/test_adsb_consistency.py`：① 帧构造逐字节相同；② 噪声 0/0.003/0.01/0.03/0.06 五档下两实现解出同一呼号/ICAO/crc_ok；③ 无噪退化输入不被漏检 |
| 卫星气象图像 | `gk2a_lrit.py`（GK-2A LRIT 专用解调）、`fengyun_sat.py`（FY-4 专用）、Meteor 侧 LRPT 链路——协议与参数各自不同（频段/调制/帧结构/卷积码率均不同），不是同一件事的两次实现 | 各协议模块自带的协议级往返测试 |

ADS-B 审计中另查出一处真缺陷：精简版门限在 MAD=0（无噪/极干净信号）时退回
`std`，而 OOK 脉冲本身会把 `std` 抬高——"中位数 + k·std"把门限顶到脉冲之上，
恰好踩中该模块 docstring 声称要避免的失效模式，整帧漏检。已改为无噪档使用
"中位数与峰值比例门限"（等价经典 OOK 半幅门限）。

### 2.1 新增：能力表面的体能化（非重复实现，只记录一次性收尾）

| 主题 | 事实 | 处置 |
|---|---|---|
| CCSDS 通用链的模型入口 | `ccsds_rx.py` 的整条链（ASM 同步→K=7 Viterbi→PN 解扰→RS）之前只有 SSDV 专用入口，模型对 ASTU-1 这类 CCSDS TM 体制任务无入口 | 新增 `ccsds_tools.py`：`ccsds_encode_frame` / `ccsds_decode_bits` / `ccsds_decode_iq`（参数逐项可覆盖，不替任务猜参数）；自环对拍测试 7 项 |
| 统一调制内核 | 新增 `modulation.py`（ASK/BPSK/QPSK/8PSK，NRZ/RRC，映射参数化），后续把 freedv QPSK 收敛进来 | 已收敛（等价断言钉死） |

### 2.2 新增：定轨链的单一来源与真体定轨固定（不是重复实现，记录一次性收尾）

| 主题 | 事实 | 处置 |
|---|---|---|
| 传播器 | 仓内曾有两份：`orbit_determination.propagate_ecef_state_and_stm`（旋转系直积二体，漏离心/科里奥利）与 `orbit_dynamics.propagate_state_and_stm`（ECI 口径）。实测：传播 600 s 相差 **1388 km / 4601 m/s** | 删除旋转系直积版，统一委托 ECI 口径；新增传播器自洽护栏（与 TLE 真值差 < 5 km） |
| 单位制 | `orbit_determination` 用 km/km/s、`orbit_dynamics` 用 m/m/s，校时与估计跨制传递，偏置差 **1000 倍** | 全链统一 km/km/s（对外字段仍报 m/s）；新增量纲一致性护栏（状态放大 1000 倍预测率同比例放大） |
| 观测噪声单位 | `r_sigma` 按 km/s 传而残差是 m/s → 增益大 1000 倍 | 与残差同量纲 |
| 状态第 7 维语义 | 一处当"钟差×c"、一处当"速率偏置" | 统一为速率偏置（km/s），模型直接相加 |
| 法方程求解 | `np.linalg.solve` 在 1e9 条件数上爆 | 改堆叠加权系统的 `lstsq` |
| 参数化确定性 | 多普勒创新量是"钟差+轨道误差"的和，单次观测无法分离 | 加 `fix_clock`（默认固定钟差）；不固定时创新意被摆到速度上（实测 9 m/s 量级），位置预测随之发散 |
| 估计器物理护栏 | 发散时返回靠谱轨道 | 加轨道合理性校验（半径/速度/有限性），越界即诚实报错 |
| 不确定性可见 | 输出只有状态，不确定性被吞 | 输出补后验协方差 |

---

## 3. 待收敛（确属重复，已识别，前置条件写在表里）

| 主题 | 重复现状 | 为什么现在不动 | 收敛前置条件 |
|---|---|---|---|
| QPSK 映射 | `freedv_modem.bits_to_qpsk` 与统一内核的 Gray 映射逐点相同（已实测确认），两处各写一份易出现位错位 | **已收敛**：`freedv_modem` 改为委托内核，等价性断言已固化为测试 |
| BPSK 映射（极性相反） | `fengyun_sat.bits_to_bpsk`（bit1→+1）与内核 BPSK（bit0→+1，射频约定）极性相反，且它还向符号注入低噪 | **有意变体**：它是"测试信号发生器"，不是通用调制器；强行合并会改变极性与噪声行为，故保留并登记 |
| RRC 成形（协议侧各自一份） | `demod.py.rrc_filter`（已**收敛**：委托 `fir_taps.root_raised_cosine_taps`，归一化后逐点相同，maxdiff=0，行为零漂移；等价性已钉死于 `mbdsdr_ai/tests/test_rrc_single_source.py`）；`gk2a_lrit.rrc_filter`（保留，见下）；`fir_taps.root_raised_cosine_taps`（权威实现） | `gk2a_lrit` 版与标准形式在 α=0.5 时形状有差（归一化内积 0.91，α=0.35 时逐点相同）——它是接收端对"发射端滤波未知"的匹配选择，不是同款重复，故保留并在此登记 | 已完成 demod 收敛（零漂移）；若未来需统一 α=0.5 口径，需先重算 gk2a 的旧期望值再改实现 |
| HAL / MIMO / 遥控三套 | `hal_enhanced` / `mimo_v2` / `remote_control_v2` 各自实现同一层硬件抽象的相邻能力 | 三者面向不同设备族（增强 HAL、多天线、遥控），合并会牵动设备枚举与工具注册 | 先做能力矩阵表（谁能替代谁），再定合并顺序；每步保持工具名与参数不变 |
| 旧 `src/desktop/` 布局的路径引用 | 根目录编号文档 03–07 仍写 `src/desktop/...`（该目录已不存在） | 文档已加"实现落点"勘误头说明真实落点 | 逐份把路径引用改为实际模块位置（勘误头为过渡方案） |

---

## 4. 审计方法（可复现）

**成对工具自洽审计**：单工具冒烟只能证明"调用不崩"，证明不了"能串起来用"。
审计脚本按"编码类工具 → 解码类工具"成对调用真实 MCP 工具面（`tool_registry.call`），
断言输出可回灌、且解码结果与输入一致。

当前已纳入回归的成对链路（`tests/test_tool_pair_chains.py`）：

| 链路 | 断言 |
|---|---|
| `afsk_modulate` → `afsk_demodulate` | 解出原呼号/信息且 FCS 有效；调制输出为完整音频（长度 > 2000，防降采样回归） |
| `fsk_modulate` → `fsk_demodulate` | 比特零误码 |
| `freedv_modulate` → `freedv_demodulate` | 比特零误码 |
| `modulate_symbols` → `demodulate_symbols` | 比特零误码 |
| `m17_4fsk_roundtrip` | 显式比特列表输入可用且往返全对 |
| `urh_modulate` → `urh_decode_iq` | ASK/BPSK 链路零误码；载荷长度 = 2×sps×bits（防截断回归）；PSK 如实报告 Costas 锁定期偏移 |
| `ax25_encode` → `ax25_decode` | 解出原呼号/信息且 FCS 有效 |
| `bell103_modulate_text` → `bell103_demodulate_text` | 文本完整回解 |
| `gmsk_modulate` → `gmsk_demodulate` | 比特零误码 |
| `morse_encode` → `morse_decode` | 文本完整回解（解码结果也在 data） |
| `sdr_encode_ax25` → `sdr_kiss_encode` → `sdr_kiss_decode` | 解出原呼号/信息，结构化载荷 |
| `sdr_aprs_encode` → `sdr_aprs_decode` | 解出源呼号/报文类型 |
| `ft8_encode` / `fst4_encode` | 返回 79 / 160 个 tone 索引（可给解调侧用） |

审计中查出的真缺陷（均已修并加回归）：

> 本表第四轮（数字语音/卫星/协议族）又查出：
> **解码器结果也只在 content 里**（morse_decode / sdr_kiss_decode / sdr_aprs_decode）——
> 模型拿不到解码结果，三者已改为结构化载荷（与编码器同一套契约）；
> **JS8 的 type 只接受整数**（模型说 "CQ" 直接 int('CQ') 报错）——已增名称归一；
> **JSON 读写用平台默认编码**（Windows GBK 环境必烨，CI 的 UTF-8 环境不显见）——
> 已全仓显式 UTF-8（含生产代码 sdr_tools.py 写出侧）。

1. **ACARS 公开解码入口自环不通**（见 §1）；
2. **ADS-B 精简版无噪输入漏检**（见 §2）；
3. **AFSK 调制输出被降采样到 2000 点且只在 content 里** → 编码→解码链路走不通；
4. **M17 往返工具只接受整数**，模型传比特列表时抛晦涩 `int(list)` 错误；
5. **RDS 组块提示示例与"16bit 块"契约矛盾**（文案修正，解析行为保持宽松）；
6. **URH 调制载荷只给第一个采样点**（下游拿到 1 个点 → 位判决永远 0 比特），
   **URHDecoder 已实现却未暴露为工具**（链路断在“调制→解调”），
   **模型说 BPSK/QPSK/8PSK 直接被拒**（"\u6682\u4e0d\u652f\u6301\u7684\u8c03\u5236: BPSK"），
   **载频默认值写死 40 kHz**（48 kSPS 下逼近 Nyquist 混叠），
   **PSK 判决极性用了 ASK 的规则**（整体反相），
   **Costas 锁定期未裁除**（头部出现误码）。上述均已修，并以
   `urh_decode_iq` 工具补全链路（见 §4 新增链路表）。

---

## 5. 新增能力时的规矩（避免再欠账）

1. 新协议解码器**先写自环**：能用本模块的合成器造出信号并解回，才算完成；
2. 与既有实现同题材时，先查本表：要么收敛到权威实现，要么明确写"有意分层"并补
   交叉一致性断言；
3. 编码类工具的**载荷必须完整进 `data`**（content 只放摘要）；需要被别的工具消费的
   载荷**不得降采样/截断**；
4. 参数语义（能量/门限/映射方式/时序口径）一律参数化，不在协议模块里写死——
   已落地的例子：调制映射 `binary|gray`、Eb/N0 口径、判决相位规则。
