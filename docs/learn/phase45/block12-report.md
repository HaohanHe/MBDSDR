# Phase45 块1+2（Python 域）交付报告：CCSDS Viterbi + RS 级联全链

> 范围：仅 Python 域。云内合成、确定性；干净室按公开 CCSDS 标准重写，未复制任何 GPL/Karn 源码。
> 基线 HEAD 未动（不 commit/push）；只改动 `mbdsdr_ai/`、`mbdsdr_ai/tests/`、本目录。

## 0. 改动文件清单（file:line）

| 文件 | 改动 |
|---|---|
| `mbdsdr_ai/ccsds_rx.py:182-235` | `ViterbiDecoder.decode()` 增加可选 `final_state` 参数（默认 `None` = 自由收尾，行为不变；flush 收尾传 `0` 即教科书最优已知终态回溯）。ACS/回溯主体本就完整，仅补此入口。 |
| `mbdsdr_ai/tests/test_phase45_cascade.py` | 新增 13 个确定性测试（Viterbi 5 + RS 边界 4 + 级联全链 2 + 诚实空态 2）。 |
| `docs/learn/phase45/block12-report.md` | 本报告。 |
| `docs/learn/phase45/phase45_cascade_out.jpg` | 级联全链出图留证（48×48 JPEG）。 |

未碰 `mobile/`、`cpp/`（B 域）、`tools/onboarding/onboard.py`（其 ssdv 模式走 fsphil 256B 自同步方言，未接 Viterbi/RS，按红线不动）。

---

## 1. Viterbi (2,1,7) 核实/补齐

**机制**（`mbdsdr_ai/ccsds_rx.py`）：
- 约束长度 K=7（6 级移位寄存器 + 当前输入），码率 1/2；生成多项式 G1=0o133、G2=0o171（`:50-52`），状态数 64。
- 编码器 `ConvolutionalEncoder`（`:116-151`）：7-bit 约束向量 `full=(b<<6)|reg`，两支路输出为 `full & g` / `full & g` 的 GF(2) 奇偶；每信息比特先发 G1 支路、再发 G2。
- 解码器 `ViterbiDecoder`（`:158-235`）：
  - 分支度量 = 接收符号对与期望符号对的**汉明距离**（硬判决）。
  - ACS：状态打包 `ns=((s<<1)|u)&63`；从 `ns` 反推信息位 `u=ns&1`，两个前驱 `ns>>1` 与 `(ns>>1)|32`（仅差被移出的最老比特），选度量小者并记录走向。
  - 回溯：信息位 = 路径状态 LSB；`final_state=0`（flush tail=6 已归零）固定回溯起点。

**合成确定性往返**（测试 `TestViterbiK7`）：
- 卷积编码（tail=6 flush 收尾）→ 干净往返精确还原。
- 自由收尾 vs 已知终态=0 在干净链路结果一致。
- 注入 ~3% 硬判决符号错误 → 完全还原；~18% 超能力 → 输出 ≠ 原数据（诚实失败，不伪造）。
- 全 0 / 全 1 边界用例通过。

实测纠错瀑布（256B 数据，tail=6）：BER=0%→0 错字节；2%→0；5%→3；8%→17；15%→171（优雅降级，非静默错误传播）。

---

## 2. RS(255,223) CCSDS 131.0-B 域参数 + 纠错边界

**机制**（`mbdsdr_ai/fec.py:148-325`）：
- GF(2^8) 本原多项式 **0x187**（x^8+x^7+x^2+x+1，`_GF256` `:69-114`）。
- RS(255,223)：32 校验符号，k=223，t=16。
- 生成多项式根集 **α^112..α^143**（fcr=112、prim step=1）；编解码对称 `^0xFF`（CCSDS 字节呈现约定）。
- 解码：Berlekamp-Massey → Chien 搜索 → Forney 差错估值。

**域参数交叉验证**：本仓 `ReedSolomon(nsym=32, fcr=112, prim=1, prim_poly=0x187, ccsds_invert=False)` 对 `bytes(range(223))` 的编码结果与公开 **reedsolo** 库 `RSCodec(nsym=32, nsize=255, fcr=112, prim=0x187, generator=2, c_exp=8)` **逐字节完全一致**（parity `9ee74a9b...bfe51`）。fcr=0 不匹配，确认 CCSDS 首根指数为 112。

**纠错边界实测**（每 60~200 次确定性样本，seed 固定）：

| 注入符号数 | 结果 |
|---|---|
| t=15 | 200/200 全部纠正，0 误纠 |
| **t=16（边界）** | **200/200 全部纠正，0 误纠** |
| **t=17（失败边界）** | **0 例还原原消息**；69/200 诚实报 nerrors=-1，131/200 误纠到另一码字（res.data≠msg） |

测试断言：t=16 必纠正且 `nerrors==16`；t=17 绝不能 `res.data==msg`（超 guaranteed 半径，不伪造正确数据）。

---

## 3. 完整级联全链（IQ → JPEG，云内合成确定性）

**TX**：48×48 渐变 RGB → `SsdvEncoder.encode_image_dslwp` → 2 个 218B DSLWP 包（stream218=436B）→ 补 5B 零到 446B（2×223）→ 外层 RS 每 223B 编码成 255B（stream_rs=510B）→ CCSDS 加扰 → 卷积 K=7 r=1/2（tail=6，8172 编码比特）→ preamble(0x55) + ASM(0x1ACFFC1D) + 编码比特 → `bpsk_modulate_bits` 复 IQ（fs=48kHz, 符号率=4800, sps=10）。

**RX**：`demod_bpsk`（带内下变频→矩形匹配低通→盲符号定时→实部硬判决）→ 比特 → `AsmFramer` 同步出 1 帧（1022B）→ `ViterbiDecoder(final_state=0)` → 解扰（还原 510B stream_rs）→ RS 解码两块（nerrors=0）→ 还原 436B DSLWP 包流 → `SsdvDecoder(dslwp)` → `SsdvImage.build()` → JPEG。

**真实结果**：
- 无噪：ASM 精确同步 1 帧；Viterbi 还原 stream_scr 逐字节一致；RS 两块 nerrors=0；DSLWP 解出 2 包；**MCU 36/36 全收、missing=[]、eoi_seen=True**；Pillow 可打开 48×48 JPEG（1076B），见 `phase45_cascade_out.jpg`。
- 受控注入：解调后编码帧逐 bit 2% 概率翻转（~200+ 处）→ Viterbi+RS 仍救回 36/36 MCU。
- 开发期 AWGN 实测（seed 固定）：复高斯噪声 sd=0.5 时硬判决原始 **BER=1.66%（137/8268 bit）**，全图 36/36 恢复；sd≥0.7 盲定时整比特滑移导致 ASM 不同步（诚实不出假帧）。pytest 用受控比特注入以保证确定性。
- 纯噪声 IQ（无信号）：ASM 同步 0 帧；纯噪声字节流解出 0 假 DSLWP 包（诚实空态）。

---

## 4. pytest 计数（实际）

| 范围 | 结果 |
|---|---|
| 基线（不含本 phase45 新文件） | **154 passed, 7 skipped** |
| 含本 phase45 新文件 | **167 passed, 7 skipped** |
| 增量 | **+13 passed**（`test_phase45_cascade.py` 全绿） |
| 失败 | **0** |

7 skipped 为 `pytest.importorskip` 等环境跳过（与本 phase 无关）。

---

## 5. 红线自查

- 干净室：Viterbi/RS 均按公开 CCSDS 标准（131.0-B / (2,1,7) 171/133）独立实现，头部仅注明"参考机制"，未复制 GPL/Karn 码；MIT。
- 无比赛字样；无活动参数（符号率 4800 仅为通用演示量，注释标明非活动参数）；不预置 TLE。
- 诚实空态：纯噪声/不可解码输入返回空，不 mock、不伪造。
- 只写 `mbdsdr_ai/`、`mbdsdr_ai/tests/`、`docs/learn/phase45/`；未碰 `mobile/`、`cpp/`；未 `git add -A`；**未 commit/push**。

## 6. 未解决项 / 后续建议

- `tools/onboarding/onboard.py` 的 ssdv 模式仍走 fsphil 256B 自同步方言，**未接线本 Viterbi+RS+ASM 级联**（本块按红线不改 CLI；后续块可加 `--ssdv-mode ccsds` 把 `AsmFramer→Viterbi→RS` 串进去）。
- Viterbi 为**硬判决**；软判决（bit LLR ACS）未做——对合成链路非必需，真机低 Eb/N0 时可升级。
- AWGN 下 `demod_bpsk` 盲定时在高噪声会整比特滑移（非本链缺陷，是盲定时本身能力）；真机需 Gardner/M&M 精定时 + 载波恢复，不在本块范围。
- RS 字节 `^0xFF` 呈现约定与 CCSDS dual-basis 变换为等价自对称；与真实 CCSDS 硬件的字节级互通需真机参数联调（活动参数仍走 docs，禁入代码）。
