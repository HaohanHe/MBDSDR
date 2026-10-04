<!--
SPDX-License-Identifier: MIT
Phase34 块2：活动接收端结论（C++ / Python 职责边界收口）
-->

# 接收端结论：活动图像接收走 Python 端，C++ 端不双写 SSTV/SSDV 解码

> 本文件是 2026-10-08~10「航天七十载·星火传未来 业余无线电图像通联」活动
> （JAMX01 发 SSTV、ASRTU-1 发 SSDV，共 34 幅）的**接收端归属与职责边界收口**。
> 所有结论均逐条核对仓内真实代码（file:line 附后），未真机验证处显式标注。
> 活动参数（频率/TLE/排班）**只进 docs/learn/phase14/**，本文件不重复登记、不硬编码。

---

## 1. 一句话结论

**活动期间的「采集 → 解码 → 出图」全链走 Python 端（`tools/onboarding/onboard.py`
+ `mbdsdr_ai/`）。C++ 端（`cpp/`）在活动期只承担桌面观察 / ControlHub 远程控制 /
解调音频录制库等外围职能，**不承担、也不需要承担 SSTV/SSDV 的整图解码**——
这是一次明确的 **YAGNI 取舍**，不是「待补的半成品」。**

- SSTV（JAMX01）：`onboard.py --mode sstv` → `mbdsdr_ai/sstv_decoder.py`，
  已对真实录音闭环（`real_sstv.wav` → PNG）。
- SSDV（ASRTU-1，DSLWP 方言）：`onboard.py --mode ssdv`（吃解调后字节流）→
  `mbdsdr_ai/ssdv_decoder.py`，字节/包层以上已用合成包流端到端验出图；
  物理层尚未串成一条命令（见 §4 缺口，活动期需两步走）。
- C++ 端：P4 评估（`docs/learn/phase14/P4-cpp-assessment.md`）已把整图移植明确
  列入**活动后 backlog**，本次只落了字节层/纯函数小零件，活动期不投人移植。

---

## 2. 端到端接收链（活动形态）

```
RTL-SDR (RTL2832U)  IQ
  │
  ├─[Python 采集/录制]  tools/onboarding/onboard.py  detect→capture→record
  │      rtl_sdr 采 uint8 I/Q → 转 cf32_le SigMF（core:datetime 落盘）
  │
  ├─[SSTV 路径] onboard --mode sstv
  │      FM 解调(max_dev=5kHz) → 重采样 48k → sstv_decoder.decode_sstv_from_samples()
  │      → PNG（自动识别 Martin/Scottie/Robot/PD；含 Robot 72 = 320×240）
  │
  ├─[SSDV 路径] onboard --mode ssdv（当前输入 = 解调后 256B/218B 包字节流 .bin）
  │      SsdvDecoder.feed()（auto 方言：fsphil 256B 或 DSLWP 218B 魔数CRC）
  │        → SsdvImage.add()/build() MCU 重组 → ssdv_rebuilt.jpg（报 missing_mcus）
  │      ⚠ 物理层 IQ→字节 这一段活动期需先手动产出包字节文件（见 §4 缺口1）
  │
  └─[C++ 外围，不在出图主链]
         桌面 Qt「时空视图」观察、ControlHub HTTP 远程控制、recording_library
         （扫描/回放解调音频 .wav）；不产出 SSTV/SSDV 图像。
```

---

## 3. 职责边界表（Python 主链 vs C++ 外围）

| 职能 | 归属 | 真实位置（已核对） | 活动期是否出图主链 |
|---|---|---|---|
| RTL-SDR 枚举/采集（rtl_sdr） | **Python** | `tools/onboarding/onboard.py` detect/capture 步 | ✅ 是 |
| IQ 落盘 SigMF（cf32_le，带 core:datetime） | **Python** | `onboard.py` record 步（`data_origin=recorded`，:1212） | ✅ 是 |
| 过境预测 / 多普勒（TLE→AOS/LOS/仰角/fd） | **Python** | `docs/learn/phase14/pass_predict.py`（复用 `mbdsdr_ai/sat_passes.py`） | ✅ 对准依据 |
| FM 解调 → 音频 | **Python** | `onboard.py --mode sstv` 的 `_demod_fm` 分支（:753-777） | ✅ SSTV |
| **SSTV 解调音频 → PNG 整图解码** | **Python** | `mbdsdr_ai/sstv_decoder.py`（`decode_sstv` :1001、`decode_sstv_from_samples` :1077） | ✅ 是 |
| SSDV 包同步/头解析/CRC/RS | **Python** | `mbdsdr_ai/ssdv_decoder.py`（`SsdvDecoder` :384，auto 方言） | ✅ 是 |
| SSDV **DSLWP 变体包层**（218B/9头/魔数CRC 0x4EE4FDE1） | **Python** | `ssdv_decoder.py:76-81` 常量、`correct_dslwp_packet` :433、`_step_dslwp` :498 | ✅ 是（ASRTU-1 实走这条） |
| SSDV MCU→JPEG 重组（含 missing_mcus 报告） | **Python** | `ssdv_decoder.py` `SsdvImage.build()`；`onboard.py _ssdv_feed_core` :506、`_step_decode_ssdv` :558 | ✅ 是 |
| SSDV 物理层（BPSK 9k6→ASM→Viterbi→解扰→RS→包字节） | Python 零件就绪、**未串成一条命令** | `mbdsdr_ai/ccsds_rx.py`（零件单测）；`onboard.py` 未串 `--step all` | ⚠ 活动期需两步（§4） |
| 桌面 Qt「时空视图」四格观察 | C++ | `cpp/src/ui/main_window.cpp refreshSpacetimeView()` | ❌ 外围观察 |
| ControlHub HTTP 远程控制 | C++ | `cpp/src/control/control_hub.cpp`、`control_http_server.cpp` | ❌ 外围控制 |
| 解调音频 .wav 录制库扫描/回放 | C++ | `cpp/src/ui/recording_library.{h,cpp}` | ❌ 外围资产管理 |
| **C++ 端 SSTV 整图解码** | **未实现（backlog）** | 仅 `cpp/src/dsp/sstv_vis.{h,cpp}`（VIS/过零频/频→像素纯函数） | ❌ 不承担 |
| **C++ 端 SSDV 整图解码** | **未实现（backlog）** | 仅 `cpp/src/dsp/ssdv_packet.{h,cpp}`（**SP5WWP 6 头**，非本活动方言）+ `RsCcsds` 编码器 | ❌ 不承担 |

---

## 4. C++ 端为什么不做 SSTV/SSDV 解码（YAGNI 及理由）

> 结论来自 `docs/learn/phase14/P4-cpp-assessment.md`（2026-10-02，活动前 6 天评估），
> 此处收口为活动期的明确取舍，**不是漏做**。

1. **全量移植工作量超过活动窗口**：SSTV ≈ 32–44 人时、SSDV ≈ 27–37 人时，
   合计 ≈ 55–80 人时；活动窗口仅剩数日，且 P1/P2/P3 取证主线不能分流人力
   （P4 §2、§3.2）。
2. **C++ 侧无对应 DSP 引擎，弱信号启发式无法盲搬**：Python 的弱信号门限
   （周期 CV、同步标记形态学、逐行/组首布局自识别、色差直流恢复）是对着
   **真实 over-the-air 录音**反复调出来的；C++ 侧云内只能合成信号自测，
   无真机录音迭代，极易「合成全绿、真机斜条纹」（P4 §3.1）。
3. **活动期 Python 已全链出图**：SSTV 已对 `real_sstv.wav` 闭环；SSDV 字节/包层以上
   已用合成包流端到端验出图（`pytest tests/test_ssdv_e2e.py` 14/14 passed，
   见 `P3-receive-sop.md` §6）。再在 C++ 重写一遍同一功能 = 双倍维护、双倍调参，
   对「活动期收到图」这个唯一目标零增量。
4. **C++ 现有零件与本活动方言也不匹配**：`cpp/src/dsp/ssdv_packet.*` 是
   **SP5WWP 6 字节头**变体（`SSDV_SSTV_SPEC.md` §8.4、§4 资产盘点），
   与 ASRTU-1 实际的 **DSLWP 218B/9 头**方言不是一回事，已被真实解码证据排除；
   硬接等于重写。
5. **无真机验收条件**：云内无硬件，C++ 新写的解码也无法在活动前做射频联调。

**故：活动期「出图」只认 Python 端产物；C++ 端只做观察/控制/资产管理。
活动后若要在桌面端原生出图，再在本次已落的字节层纯函数基座（`sstv_vis` /
`ssdv_packet` / `RsCcsds`）上续做，列入 backlog，不在活动期开工。**

---

## 5. 活动期真机操作口径（与边界一致）

- **SSTV（JAMX01）**：一条命令即可全链出图——
  `python3 tools/onboarding/onboard.py --step all --freq <官方下行Hz> --mode sstv ...`
  （命令模板见 `P3-receive-sop.md` §4.A / `P3-receive-guide.md` 路径 A）。
- **SSDV（ASRTU-1）**：活动期分两步（物理层未串入一条命令）——
  1) 先用 `--mode apt`/`rtl_sdr` 把 436.210 MHz 整段过境 IQ 录下来；
  2) 把解调链产出的 256B/218B 包字节流存成 `.bin`，再
     `onboard.py --step decode --mode ssdv --sigmf-data <...>.bin` 出图
  （见 `P3-receive-sop.md` §4.B）。**这一步是当前最大的活动期操作缺口，见 §6。**
- **不绕过 Python 去 C++ 找解码**：C++ 桌面端此时只用于观察信号/远程改频率，
  出图以 Python 产物目录里的 PNG/JPG 为准。

---

## 6. 已知缺口 / 未解决项（如实登记，活动前需知晓）

| # | 缺口 | 现状 | 活动期应对 |
|---|---|---|---|
| 1 | SSDV 物理层未串入 `onboard --step all` | `--mode ssdv` 只吃「解调后包字节流」，`IQ→BPSK→ASM→Viterbi→解扰→RS→218B 包` 这段（`ccsds_rx.py` 零件就绪）未成一条命令 | 真机先手动录 IQ、手动解调产出 `.bin` 再喂 ssdv decode；**待真机确认**能否在活动现场快速产出包字节文件 |
| 2 | C++ 端无 SSTV/SSDV 整图解码 | 纯 YAGNI 取舍（§4），非 bug | 活动期出图只认 Python；C++ 不双写 |
| 3 | SSDV 物理层参数未官方发布 | BPSK 9600 / CCSDS Concatenated 223B 为社区实解旁证（`SSDV_SSTV_SPEC.md` §8.6），非主办方规格书 | 以活动现场实际信号为准；先 DSLWP 方言试解，失败再退 fsphil 经典（§8.5 试解顺序） |
| 4 | SSTV 多普勒补偿未内嵌进 `--mode sstv` decode | `P3-receive-guide.md` A-4：需 SDR 端 AFC 或离线 `doppler_compensation.remove_doppler_shift` | 开 SDR 软件 AFC，或按 `pass_predict.py` 的 fd 偏调中心频 |

---

## 7. 关联文件

| 文件 | 作用 |
|---|---|
| `docs/learn/phase14/P3-event-params.md` | 活动事实 + 频率/TLE/站坐标录入位（活动参数唯一登记处） |
| `docs/learn/phase14/P3-receive-sop.md` | 可执行接收值班单（命令+判定+失败对照+邮件勾选） |
| `docs/learn/phase14/P3-receive-guide.md` | SSTV/SSDV 两路径原理手册 |
| `docs/learn/phase14/SSDV_SSTV_SPEC.md` §8 | ASRTU-1 = DSLWP 方言核实（证据链 + 试解顺序） |
| `docs/learn/phase14/P4-cpp-assessment.md` | C++ 移植评估（本结论 §4 的出处） |
| `docs/learn/phase34/event-rx-runbook.md` | 活动形态真机验收/值班手册（本阶段收口） |
| `docs/learn/phase34/event-checklist.md` | 活动前核对清单（设备/天线/时间/预测/邮件） |
