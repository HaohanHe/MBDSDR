<!--
SPDX-License-Identifier: MIT
Phase34 块3：活动形态真机验收/值班手册（15 分钟手册收口）
-->

# 活动真机验收手册（SSTV / SSDV 出图值班单）

> 本手册是 `docs/learn/phase10/P1-acceptance-runbook.md`（通用 15 分钟手册：
> adsb/cw/apt）在 **2026-10-08~10 卫星图像接收活动** 上的收口版：
> 把通用链路换成活动两路径（SSTV=JAMX01 / SSDV=ASRTU-1），给出
> **过境预测命令 → 一条/两条接收命令 → 出图判断标准 → 邮件核对项**。
>
> **接收端归属**：出图全链走 Python（`tools/onboarding/onboard.py` + `mbdsdr_ai/`），
> C++ 端不做 SSTV/SSDV 解码（理由见 `recv-chain-conclusion.md` §4）。
>
> **诚实边界（务必先读）**
> - 活动参数（频率/TLE/排班）以 `docs/learn/phase14/P3-event-params.md` 录入位为准；
>   本手册不重复登记、不硬编码。文中出现的 435.075 / 436.210 MHz 均为
>   **社区协调/实解旁证值**（`SSDV_SSTV_SPEC.md` §8.2、`P3-receive-sop.md` §3），
>   **活动实际下发以主办方公告为准**，收到公告先改 phase14 录入位再执行。
> - 云内无硬件：下列命令均来自真实代码（已逐行核对）；SSDV 字节/包层以上已用
>   合成包流端到端验出图（`P3-receive-sop.md` §6）；**真机射频出图部分尚未在
>   真实卫星过境中验证**，凡涉「真机收到图」处均标注 **【待真机确认】**。

---

## 耗时与阶段（活动当天）

| 阶段 | 内容 | 性质 |
|---|---|---|
| 0（活动前一天，不计过境） | 重拉 TLE → 跑 `pass_predict.py` 锁定主攻过境 | 一次性准备 |
| 1（每过境前 ~5 min） | 设备自检 + 对准频率/方位 + 开 AFC/多普勒补偿 | 值班重复 |
| 2（过境中 6.5~7.5 min） | 整段过境录制（SSTV 一条命令 / SSDV 两步） | 主动作 |
| 3（过境后） | 出图判定 + 记录 UTC/仰角/频率 + 邮件材料勾选 | 收尾 |

> 注：原 15 分钟手册的「15 分钟出成果」对卫星不成立——**必须等过境**。
> 本手册的「15 分钟」指**一次过境前后的值班动作**，不是「开机 15 分钟必出图」。

---

## 阶段 0：过境预测（活动前一天务必重拉 TLE）

> 前置：已按 `P3-event-params.md` §3 把两星 TLE 粘进 `pass_predict.py` 顶部
> `tle_lines`，并把本机经纬度填进 `GROUND_STATION`。

```bash
# 人读过境表（UTC 时刻 / 中天仰角 / 多普勒峰 |fd|）
python3 docs/learn/phase14/pass_predict.py

# 机读（抄录 UTC/多普勒进邮件清单与值班笔记）
python3 docs/learn/phase14/pass_predict.py --json
```

- **通过判据**：打印过境表，两星 `TLE 新鲜度=fresh`（age<3 天）；
  `status=none` 或 `TLE 新鲜度=stale` 时**不要盲收**（重拉 TLE / 催主办方）。
- **选主攻**：优先中天仰角 ≥30° 的过境（参考计划见 `P3-receive-sop.md` §3，
  长春站示例；活动当天按本机坐标重跑，勿套示例坐标）。
- **【待真机确认】**：JAMX01 在 CelesTrak 无公开 GP，须用 AutoTLE 聚合源三行
  （`P3-receive-sop.md` §2）；活动当天 TLE 历元 >3 天建议催主办方发正式 TLE。

---

## 阶段 1：设备自检（每过境前 ~5 min）

```bash
# 1) RTL-SDR 在不在（0bda:2838/2832）
lsusb | grep -E '0bda:(2838|2832)'

# 2) onboard detect（确认设备可被 Python 端打开）
python3 tools/onboarding/onboard.py --step detect
```

- **通过判据**：`lsusb` 命中 RTL283x；`detect` 打印 `[PASS] step=detect 检测到 RTL-SDR`。
- **失败对照**（同 phase10 手册 §①）：`未检测到`→重插 USB2.0 直连口；
  设备被 rtl_tcp/gqrx/dump1090 占用→关掉占用进程；`modprobe -r dvb_usb_rtl28xxu`。
- **对准**：按阶段 0 给出的 AOS/LOS 方位转天线；70 cm 段务必**开 AFC/多普勒补偿**
  （AOS 接收频率偏高约 +10 kHz、LOS 偏低约 −10 kHz，见 `P3-event-params.md` §5）。

---

## 阶段 2-A：SSTV 接收（JAMX01，模拟 FM）—— 一条命令全链

```bash
python3 tools/onboarding/onboard.py --step all \
    --freq <官方下行Hz，旁证 435.075e6> --mode sstv \
    --sr 250000 --n 90000000 --gain 24 \
    --out-dir ./phase14_rx
```

> `--mode sstv` 默认 `sr=250k`、`n=30,000,000`（≈120 s）；过境 6.5~7.5 min，
> 故这里显式 `--n 90000000`（≈360 s）**整段过境都录**。
> 链路：FM 解调(max_dev=5kHz) → 重采样 48k → `sstv_decoder.decode_sstv_from_samples()`
> 自动识别 Martin/Scottie/Robot/PD（`onboard.py` MODES :93-99、decode 分支 :753-777）。

### SSTV 出图判断标准

- **产物**：`./phase14_rx/sstv_image__recorded__N…__.png`。
- **判为成功**：
  1. `step=decode [PASS]`，PIL 能打开该 PNG；
  2. 自动识别出制式（活动 JAMX01 预期 **Robot 72 = 320×240**，
     `sstv_decoder.py:649` width/height=320/240、VIS=0x0C）；
  3. **行数接近整帧**：Robot72 目标 240 行，现场以 `rows_decoded` 接近全帧
     （约 239/240 行、无大段缺失/无大斜条纹）为准；缺个位数行可接受，
     缺半帧以上按失败处置。
- **判为失败（诚实空态，非机器故障）**：纯噪声/无信号时脚本应 `FAIL`、不产假图——
  此时按 `P3-receive-sop.md` §5.2 对照表处置（没盖完整帧 / 频率多普勒没对准 /
  仰角太低 / 增益不当）。
- **【待真机确认】**：真实卫星 SSTV 的制式是否确为 Robot 72、以及现场实际出图行数，
  须以活动过境实测为准；代码支持的其他制式（Martin/Scottie/PD）也会自动识别。

---

## 阶段 2-B：SSDV 接收（ASRTU-1，数字）—— 活动期两步走

> ⚠️ **链路边界**：`onboard --mode ssdv` 目前只做**字节/包层以上**
> （包同步→RS/魔数CRC→MCU 重组 JPEG），输入是「解调后的包字节流 `.bin`」，
> **不是** complex64 IQ；物理层（IQ→BPSK→ASM→Viterbi→解扰→RS→包）尚未串成
> 一条命令（`P3-receive-sop.md` §8 缺口1）。故真机分两步：

```bash
# 第 1 步：把下行 IQ 整段过境录成 raw（旁证频率 436.210e6）
python3 tools/onboarding/onboard.py --step all \
    --freq <官方下行Hz，旁证 436.210e6> --mode apt --sr 250000 \
    --n 90000000 --gain 24 --out-dir ./phase14_rx

# 第 2 步：把解调链产出的包字节流存成 .bin，喂 ssdv 解码入口
python3 tools/onboarding/onboard.py --step decode \
    --mode ssdv --sigmf-data ./phase14_rx/<demodulated_packets>.bin \
    --out-dir ./phase14_rx
```

> `--mode ssdv` 的 `SsdvDecoder` 默认 **auto 方言**（`ssdv_decoder.py:384`）：
> 先找 fsphil 经典（256B/15头/sync 0x55），找不到则按 **DSLWP 变体**
> （218B/9头/魔数 CRC 0x4EE4FDE1，`ssdv_decoder.py:76-81,433,498`）逐 218B 切包。
> ASRTU-1 实走 DSLWP 方言（`SSDV_SSTV_SPEC.md` §8）。

#### 路径 A（活动期首选，Phase47 已通）：`--ssdv-mode ccsds` 级联一条命令

> Phase47 已把 IQ→BPSK→ASM→Viterbi(终态0)→解扰→RS→218B→JPEG 串成一条命令
> （复用层 `mbdsdr_ai/ccsds_ssdv.py`，云内合成级联 IQ 已实测出图）。
> 录 IQ 后**直接喂 complex64 IQ**，不必再手动拼包字节：

```bash
# 录整段过境 IQ（complex64；频率/采样率走 phase14 活动参数，代码零硬编码）
python3 tools/onboarding/onboard.py --step all \
    --freq <官方下行Hz> --mode ssdv --sr <活动采样率Hz> \
    --n <采样点数> --gain 24 --out-dir ./phase14_rx

# 直接对录下的 complex64 IQ 跑级联解码（符号率/帧长走 phase14 活动参数；
# 多普勒不确定时加 --ssdv-blind-cfo 平方环盲估频偏，不必手填 --ssdv-tone-offset）
python3 tools/onboarding/onboard.py --step decode --mode ssdv \
    --sr <活动采样率Hz> \
    --ssdv-input iq --ssdv-mode ccsds \
    --ssdv-symrate <活动符号率Hz> \
    --ssdv-frame-bits <每帧卷积后编码比特数> \
    --ssdv-blind-cfo --ssdv-timing gardner \
    --sigmf-data ./phase14_rx/<录制>.iq
```

- 产物：`./phase14_rx/ssdv_ccsds_rebuilt.jpg`。
- **判据（现场看这几行判断走哪条/是否降级）**：
  1. 先看 `盲 CFO 估计 = ±x Hz`：prominence 不足时诚实报 0 且不继续出图；估计值应与
     预期带内偏移同量级（真机多普勒几十~几百 Hz）。
  2. 再看 `ASM 同步 N 帧`：
     - `ASM 同步 0 帧` → 物理层没锁住。**降级顺序**：(a) 用 `--ssdv-timing gardner`
       （弱信号首选；Phase48 实测 sd=0.5+CFO30Hz 下 gardner 0/30 滑移、优于 coarse 的
       2~6/30）；(b) 仍 0 帧 → 核对符号率/帧长；(c) 再不行 → 回退路径 B。
     - `ASM 同步 ≥1 帧` → 继续。
  3. 再看每帧 `RS nerrors = [[..]]`：
     - 全 `0` → 干净链路；有小正数 → FEC 真实纠错（可接受）；出现 `-1` → 该 RS 块
       不可纠，后级 CRC 会丢该包，对应 MCU 缺失。
  4. 最终 `[PASS] … MCU x/y, 缺失 z`：`缺失 0` 理想；少量缺失仍算收到图。

#### 路径 B（兜底）：`--ssdv-mode fsphil`（默认）解解调后包字节流

即上文「两步走」：若级联路径 A 锁不住，退回把下行交 `gr_satellites 61781`（或外部
解调链）产出的 **256B/218B 包字节流 `.bin`**，用默认 fsphil/auto 方言自同步解：

```bash
python3 tools/onboarding/onboard.py --step decode --mode ssdv \
    --sigmf-data ./phase14_rx/<demodulated_packets>.bin --out-dir ./phase14_rx
```

### SSDV 出图判断标准

- **产物**：`./phase14_rx/ssdv_rebuilt.jpg`（标准命名副本 `ssdv_image__…jpg`）。
- **判为成功**：
  1. `step=decode [PASS]`，出现 `ssdv_rebuilt.jpg`；
  2. JPEG **可被标准解码器打开**：头尾 `FF D8 … FF D9` 完整、宽高与预期一致
     （实解旁证为 320×256，`SSDV_SSTV_SPEC.md` §8.3）；
  3. **`missing_mcus == []`（理想）**；有少量缺 MCU 时脚本仍出图并在旁证报告
     `missing_mcus`（缺处补空白、局部花屏但不整图崩）——缺 MCU 在可接受范围
     内仍算「收到图」，缺大块则按失败重收。
- **判为失败（诚实空态）**：`ASM 未同步出任何帧` / `未同步到任何有效包` / 字节流为空
  时应 `FAIL`、不造图（`onboard.py _step_decode_ssdv_iq_ccsds` / `_finish_ssdv`）。
- **【待真机确认】**：路径 A 已在**云内合成级联 IQ** 上一条命令出图（确定性：注入 37Hz
  CFO 时盲 CFO 估 +36.9Hz、1 ASM 帧、RS nerrors=0、36/36 MCU 无缺失），但**真实卫星过境
  IQ 尚未验证**；真机的符号率/中频偏移/帧长仍以 phase14 活动参数录入位为准。Phase48 已修
  Gardner 眼心 seed（盲平均幅度扫描找眼心）+ 平方环盲 CFO：sd=0.5+CFO30Hz 下 gardner
  0/30 滑移、优于 coarse 的 2~6/30，弱信号现场优先 `--ssdv-blind-cfo --ssdv-timing gardner`。

---

## 阶段 3：收图后邮件材料核对（换证书）

> 完整模板见 `docs/learn/phase14/P3-email-checklist.md`，收件人
> **CASC70@asesspace.com**。发邮件前逐项勾选：

- [ ] 成功解码的图像 PNG/JPG 附件（SSTV 或 SSDV，清晰完整）
- [ ] 接收瞬间频谱/瀑布截图（含时间与频率）
- [ ] 接收 **UTC** 时间（抄 SigMF meta `core:datetime` 或 `pass_predict.py --json`；**不写北京时**）
- [ ] 卫星与制式（JAMX01 / ASRTU-1；SSTV / SSDV）
- [ ] 下行频率（按实际对准频率，MHz）+ 最大仰角
- [ ] 接收地点经纬度（实际接收点，勿套长春示例）+ 城市
- [ ] 接收设备（RTL-SDR 型号/采样率/增益）+ 天线（波段/类型）
- [ ] 姓名 / 单位 / 合法呼号（无呼号按主办方公告注明，不冒用）
- [ ] 累计已收幅数（活动共 34 幅）

---

## 常见失败对照（活动版）

| 现象 | 最可能原因 | 处置 |
|---|---|---|
| detect `未检测到 RTL-SDR` | USB/驱动被占 | 重插 USB2.0；`lsusb \| grep 0bda`；`modprobe -r dvb_usb_rtl28xxu` |
| capture 文件为空 | 设备被 rtl_tcp/gqrx 占用 | 关掉占用进程 |
| SSTV 解码不出图 | ①没盖完整帧 ②频率/多普勒没对准 ③仰角太低/天线偏 ④增益不当 | 录满整段过境；按 fd 偏调中心频或开 AFC；对准方位；微调增益 |
| SSDV `未同步到任何有效包` | 喂的不是解调后包字节流；物理层参数不对 | 确认 `--sigmf-data` 指向解调后包 `.bin`；检查 BPSK 速率/ASM/解扰参数 |
| SSDV 出图但 `missing_mcus` 多 | 丢包/误码超纠错能力 | 整段过境多录几轮重收；开多普勒补偿+AFC；优先高仰角过境 |
| `TLE 新鲜度=stale` | TLE 历元 >7 天 | 重拉 TLE，或催主办方发最新 TLE |

---

## 未真机验证项汇总（如实登记）

1. **【待真机确认】** SSTV：`real_sstv.wav` 离线录音已闭环出图，但**真实 JAMX01
   过境射频出图**（Robot72 实际行数/制式）未验证。
2. **【待真机确认】** SSDV 物理层：IQ→BPSK→ASM→Viterbi→解扰→RS→218B DSLWP 包
   这条在真机上的串联、以及现场 `.bin` 的快速产出方式未验证；仅合成包流端到端验过。
3. **【待真机确认】** 活动实际下行频率/排班是否等同社区旁证值（435.075 / 436.210 MHz）。
4. **【待真机确认】** JAMX01 正式 TLE（AutoTLE 快照仅至 2026-10-04，超 3 天需重拉）。

> 以上各项在活动现场首度过境时逐项核实，并把实测结果回写本手册对应行。
