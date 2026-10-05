# Phase43 块1+2：SSTV 自动制式识别 + Robot36 + SSDV 物理层一条命令

日期：2026-10-05 ｜ 域：Python（mbdsdr_ai / tools/onboarding / tests）｜ 基线：pytest 144 → **150 passed（本阶段半径内，+6 新增，0 回归）**

---

## 0. 红线自查

- 活动参数（JAMX01 / ASRTU-1 的 436.210 MHz / 9600 Bd 等）**未进任何通用代码**，仅在 docs 引用；
  本层符号率/中频偏移一律参数传入，`ssdv_phy.DEFAULT_SYMRATE_HZ=4800` 为通用演示值、**非活动参数**。
- 未预置 TLE；真机频率/符号率只走 `docs/learn/phase14/P3-event-params.md`。
- 诚实空态：纯噪声 IQ / 无符号率 → 显式 FAIL，不伪造 JPEG（见测试 `test_ssdv_iq_noise_honest_empty`）。
- 无比赛字样；未碰 `cpp/`、`mobile/`（B 域）。
- 改动文件仅限 `mbdsdr_ai/`、`tools/onboarding/`、`tests/`（新增）、`docs/learn/phase43/`。
- **未 commit / 未 push**；只 `git add` 本任务自己的文件（见 §5）。

---

## 1. SSTV：自动制式识别 + Robot36（两行组）解码

### 1.1 识别机制（数据驱动，不依赖易解错的 VIS）

入口 `mbdsdr_ai/sstv_decoder.py:404 _identify_sstv_mode`。以 1200Hz 行同步的**脉宽 / 周期 / markers 数(=行数或组首数)** 为主判据，VIS 仅作旁证。

**Robot36 vs Robot72 的区分**（两者同步脉宽均 ~9ms、周期均 ~300ms，几乎重合）——
靠【行数/markers 数】主判（`sstv_decoder.py:458`）：

| 信号 | 脉宽 | 周期 | markers 数 | VIS | 判型 |
|---|---|---|---|---|---|
| Robot72（真实 `real_sstv.wav`） | 8.75ms | 300ms | **239（逐行，240 行）** | 12 | Robot 72 |
| Robot36 组首式（合成） | 8.75ms | 300ms | **119（120 组×2 行=240 行）** | 8 | Robot 36/grouped |
| Robot36 逐行式（pysstv 合成） | 8.75ms | 150ms | 239 | 8 | Robot 36/per_line |

```python
# sstv_decoder.py:458（~300ms 周期、9ms 脉宽分支）
if n_markers >= 180 or vis_code == 12:   # 240行逐行 > 180 → Robot72；vis=12 兜底
    return "Robot 72", {**info, "robot_layout": "robot72"}
return "Robot 36", {**info, "robot_layout": "grouped"}   # ~120 markers → 两行组
```

> 阈值取 180：把"240 行逐行"与"120 组两行"这 2:1 的 marker 数干净分开；即使弱信号 markers 不足，仍有 `vis==12` 兜底判 Robot72。

解码：`_decode_robot36`（`sstv_decoder.py:516`）支持 `per_line`/`grouped` 两种发送变体，均出 **320×240**（组首式=120 个两行组、共 240 行；本任务书所称"120×240"即 120 组×240 行）。

### 1.2 离线测试（确定性）

新增 `tests/test_sstv_mode_id.py`（3 用例）：
- `test_real_sstv_wav_auto_identifies_robot72` — 真实 OTA 样本 `real_sstv.wav` → `mode=Robot 72`、320×240、rows=239、method=timing（**此前仅在脚本里断言，未进 pytest**）。
- `test_synthetic_perline_robot36_not_misidentified_as_robot72` — pysstv 逐行 Robot36 → `Robot 36`（不得误判 Robot72）、320×240。
- `test_synthetic_grouped_robot36_two_line_group` — **本文件内置确定性组首式合成器**（pysstv 只发逐行式），补齐两行组路径 → `Robot 36`/`layout=grouped`、234 行。

### 1.3 样本覆盖诚实登记（勿夸大）

| 制式 | 合成样本 | OTA 真实样本 |
|---|---|---|
| Robot 72 | ✗ 无 | ✓ `real_sstv.wav` |
| Robot 36 逐行式 | ✓ pysstv | ✗ 无 |
| Robot 36 组首式（两行组） | ✓ 本文件内置合成 | ✗ 无 |
| Martin M1 | ✓ pysstv（onboarding 既有） | ✗ 无 |
| **Martin M2 / Scottie S1/S2/DX / PD90..290** | ✗ **无** | ✗ **无**（仅有解码代码路径，无测试样本） |

---

## 2. SSDV 物理层一条命令

### 2.1 新增物理层模块 `mbdsdr_ai/ssdv_phy.py`

- `bpsk_modulate_bits`（:33）— 比特→BPSK 复 IQ（矩形脉冲，带内中频 `f_if`；云内合成/测试用）。
- `demod_bpsk`（:51）— 复 IQ→硬比特：**NCO 带内下变频 → 矩形匹配低通 → 盲符号定时(眼图张开度最大相位扫描) → 符号中心按实部正负判决**；纯噪声/符号不足诚实返回空。
- `iq_to_ssdv_bytes`（:96）— 硬比特→MSB-first 字节，交 `SsdvDecoder` 自同步。

### 2.2 onboard.py 串链（`--mode ssdv`）

- `_step_decode_ssdv_iq`（`onboard.py:615`）：复 IQ → `ssdv_phy` 解调 → 字节流 → `_ssdv_feed_core` → MCU 重组 JPEG。
- `_finish_ssdv`（`onboard.py:560`）：字节文件模式与 IQ 模式共用喂核心/写旁证尾部。
- `step_decode(..., ssdv_input="bytes"|"iq", ssdv_symrate, ssdv_tone_offset)`（:675 分支）。
- CLI（:1135 起）：`--ssdv-input {bytes,iq}`、`--ssdv-symrate`、`--ssdv-tone-offset`。
- 向后兼容：默认 `--ssdv-input bytes`，既有字节流测试 26 条全绿。

### 2.3 一条命令（云内合成 IQ 全链，确定性）

```bash
# 1) 合成 BPSK IQ（云内替代 rtl_sdr 录 IQ；真机符号率/频率走 docs 活动参数）
python3 tools/onboarding/onboard.py --step decode --mode ssdv \
  --sr 48000 --sigmf-data ssdv_capture.sigmf-data \
  --ssdv-input iq --ssdv-symrate 4800 --ssdv-tone-offset 3000 --out-dir out/
```

实测输出：`PASS step=decode → SSDV 重组完成：2 包, 48x48；IQ 物理层 40,960 complex64 @48ksps BPSK 4800 Bd/中频 3000Hz；同步 2 包(方言=fsphil)；JPEG 48x48 MCU 36/36`。
链路上每一段都真实：**合成 IQ → 带内下变频 → BPSK 解调 → 512B 字节 → fsphil 自同步 → 36/36 MCU → JPEG**。

新增测试 `tools/onboarding/test_onboarding.py::TestSsdvIqPhysicalLayer`（3 用例）：
- 全链 48×48 JPEG 无缺 MCU、方言=fsphil、Pillow 可开；
- 纯噪声 IQ → 诚实 FAIL 且不出 JPEG；
- 未给符号率 → 诚实 FAIL（不猜速率）。

### 2.4 default_sr 低于 rtl_sdr 下限——如实处置

`MODES["ssdv"].default_sr = 19200.0`（`onboard.py:104`）是**字节流/音频参考率，非 rtl_sdr 采集率**。rtl_sdr 实采 IQ 须 `--sr ≥ 225k`（`step_capture` 既有量程检查：<225k 直接 FAIL 并提示"建议 2_400_000 Hz"）。处置：
- 保留 `--sr` 可选项，真机实采显式 `--sr 2.4e6`；
- 注释与 MODES.desc 已写明该 19200 仅供 `--ssdv-input bytes`/音频参考，不作为 rtl_sdr 采集率；
- 云内合成 IQ 直接 `--sigmf-data` 指定 complex64 文件，绕开 rtl_sdr，无需录机。

---

## 3. pytest 全量（实际计数）

本阶段改动半径内（确定性、无真机）：

```
$ python3 -m pytest -q \
    tools/test_acceptance_run.py tools/onboarding/test_onboarding.py \
    tools/onboarding/test_parse_hw_report.py tests/test_ssdv_e2e.py \
    tests/test_sstv_mode_id.py docs/learn/phase14/test_pass_predict.py \
    mbdsdr_ai/tests/test_ssdv_decoder.py mbdsdr_ai/tests/test_ccsds_rx.py
150 passed, 1 warning in 53.49s
```

另跑 `mbdsdr_ai/tests + tools/onboarding + tools/hw_selfcheck`：**166 passed, 7 skipped**。

> 说明：任务书基线称 145（五根合计、acceptance_run 单根 42）。本地完整五根含 `tests/` 大量 offscreen-QT UI 用例，在本环境极慢/挂起、且与本阶段半径无关；故以上报本阶段半径内可复现集合的实际计数。基线 144 → 现在 150（+3 SSTV 识别 +3 SSDV 物理层），**0 回归**。

---

## 4. 未解决项 / 诚实边界

1. **完整 CCSDS 级联信道链未建**：本层只到"硬字节 + fsphil 0x55 自同步"。ASRTU-1 真机为差分 BPSK + CCSDS(K=7,r=1/2 Viterbi + 解扰 + RS(255,223)) + DSLWP 218B/无同步包，需要上游帧同步/Viterbi 输出**已对齐的 218B 字节**才能解；`ccsds_rx.py` 已有比特级 Viterbi/ASM，但从"裸 BPSK IQ→Viterbi→解扰→RS→218B"的完整真机链路未在云内跑（需真实射频参数与信道编码样本）。当前云内全链用 fsphil 256B 自同步方言证明物理层→字节→JPEG 通。
2. **BPSK 载波相位/精定时**：合成信号收发两端同 fs/symrate/中频而确定往返；真机的载波恢复、Gardner/M&M 精定时、0/π 相位模糊处理未做（依赖差分译码或 ASM）。
3. **PD 系列 / Martin M2 / Scottie 各型 SSTV**：仅有解码路径，无合成/OTA 样本，未加测试（诚实空态）。
4. **真机 SSDV IQ 实测**：云内无射频，未接 rtl_sdr；真机频率/符号率/中频偏移须按 `P3-event-params.md` 由 CLI 传入后联调。

---

## 5. 本任务改动文件（待暂存，未 add -A / 未 commit）

- 改 `mbdsdr_ai/sstv_decoder.py`（识别分支 :458 用 markers 行数主判）
- 新 `mbdsdr_ai/ssdv_phy.py`（BPSK 物理层）
- 改 `tools/onboarding/onboard.py`（IQ 物理层串链 + CLI + default_sr 注释）
- 新 `tests/test_sstv_mode_id.py`
- 改 `tools/onboarding/test_onboarding.py`（+`TestSsdvIqPhysicalLayer`）
- 新 `docs/learn/phase43/P1-sstv-robot36-ssdv-phy.md`（本文件）
