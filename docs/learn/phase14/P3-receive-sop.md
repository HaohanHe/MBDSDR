<!--
SPDX-License-Identifier: MIT
Phase14 P3 活动接收 SOP 定稿（端到端演练后产出）
-->

# P3 活动接收 SOP 定稿：航天七十载·星火传未来 业余无线电图像通联

> 本 SOP 是 2026-10-04 Phase23 B 块**端到端演练后**的可执行接收作业指导书。
> 它把 `P3-receive-guide.md`（原理手册）压缩成「照着做就能收」的值班单：
> 每条命令（freq/mode/时长）+ 成功判定 + 失败对照 + 邮件勾选。
>
> **诚实边界（务必先读）**
> - 活动事实/频率/TLE 的**录入位口径**见 `P3-event-params.md`；本 SOP 引用之。
> - 下列频率为**公开协调值**（AMSAT/satnogs，见 §2 来源），仅用于对准与多普勒
>   估算；**活动实际下发频率/排班以主办方公告为准**，收到公告后请先改
>   `P3-event-params.md` 与 `pass_predict.py` 录入位再执行。
> - 云内无硬件：本 SOP 的命令均来自真实代码（已逐行核对），SSDV 字节/包层
>   全链已于 2026-10-04 用合成包流端到端验证（见 §6）；真机射频待活动现场。

---

## 1. 活动速览

| 项 | 值 |
|---|---|
| 活动日期 | **2026-10-08 / 09 / 10**（接收时刻一律记 **UTC**，括号内为北京时 CST=UTC+8） |
| 卫星 | JAMX01（静安梦想星）、ASRTU-1（阿斯图友谊号 / AO-123） |
| 图像制式 | SSTV（模拟 FM）+ SSDV（数字），合计 34 幅 |
| 换证书邮箱 | **CASC70@asesspace.com**（材料清单见 `P3-email-checklist.md`） |
| 地面站示例 | 长春 43.81°N, 125.32°E（活动当天按实际接收点改 `pass_predict.py` 的 `GROUND_STATION`） |

---

## 2. TLE 拉取（活动前一天务必重拉一次）

本次演练（2026-10-04 06:30 UTC+8）实际拉取结果，**带来源 URL**：

| 卫星 | TLE 来源 | URL | 拉取结果 |
|---|---|---|---|
| ASRTU-1 (61781) | **CelesTrak gp.php** | `https://celestrak.org/NORAD/elements/gp.php?CATNR=61781&FORMAT=tle` | ✅ 成功，历元 2026-10-03T14:33Z（age≈0.3d，fresh） |
| JAMX01 | CelesTrak gp.php `CATNR=100465` | `https://celestrak.org/NORAD/elements/gp.php?CATNR=100465&FORMAT=tle` | ❌ `No GP data found`（100465 未入 CelesTrak 公开 GP） |
| JAMX01 | **BI4PYM/AutoTLE 聚合源** | `https://raw.githubusercontent.com/BI4PYM/AutoTLE/refs/heads/master/AutoTLE.txt` | ✅ 成功（目录号 70001 / COSPAR 2026-195A），历元 2026-10-02T04:07Z（age≈1.8d，fresh） |

实测拿到的 TLE（活动当天请按上面 URL 重拉，勿用本快照超 3 天）：

```
JAMX01
1 70001U 26195A   26275.17197745  .00002540  00000+0  16216-3 0  9993
2 70001  97.5359 348.3292 0014404 125.0795 235.1787 15.09488358  5730

ASRTU-1 (AO-123)
1 61781U 24199AY  26276.60665302  .00005378  00000+0  15024-3 0  9996
2 61781  97.2809 144.3027 0011472 281.0042  78.9912 15.37064577150572
```

> 录法：把上面三行粘进 `pass_predict.py` 顶部对应卫星的 `tle_lines`；
> ASRTU-1 也可只填 `norad_catnr=61781` 让脚本联网拉 CelesTrak。
> JAMX01 在 CelesTrak 暂无 GP，**必须手动粘 AutoTLE 三行**（或催主办方发正式 TLE）。

---

## 3. 可执行接收计划（长春站，仰角 ≥10°；演练实测输出）

预测：`pass_predict.py`（PREDICT_HOURS=168，MIN_ELEVATION=10°）。活动窗口内共
**21 次**过境；下表只列**推荐主攻（中天仰角 ≥30°）**，低仰角备份见
`scratch/phase23b/pass_plan.json`（机读全表）。

| # | 卫星 / 下行 | 模式(拟) | AOS UTC（北京时） | 中天仰角 | LOS UTC | 时长 | 多普勒峰\|fd\| |
|---|---|---|---|---|---|---|---|
| 1 | ASRTU-1 / **436.210 MHz** | SSDV(数字 9k6 BPSK) | 10-09 **23:48**Z（10-10 07:48） | **72.9°** | 23:55Z | 6.9 min | ±10.3 kHz |
| 2 | ASRTU-1 / 436.210 MHz | SSDV(数字) | 10-10 12:42Z（20:42） | **67.7°** | 12:49Z | 6.9 min | ±10.2 kHz |
| 3 | JAMX01 / **435.075 MHz** | SSTV(模拟 FM) | 10-08 02:15Z（10:15） | **53.4°** | 02:23Z | 7.5 min | ±9.9 kHz |
| 4 | ASRTU-1 / 436.210 MHz | SSDV(数字) | 10-09 13:16Z（21:16） | 40.7° | 13:22Z | 6.5 min | ±9.8 kHz |
| 5 | JAMX01 / 435.075 MHz | SSTV(模拟) | 10-09 02:07Z（10:07） | 42.2° | 02:14Z | 7.3 min | ±9.6 kHz |
| 6 | ASRTU-1 / 436.210 MHz | SSDV(数字) | 10-09 00:22Z（08:22） | 37.7° | 00:28Z | 6.5 min | ±9.6 kHz |
| 7 | JAMX01 / 435.075 MHz | SSTV(模拟) | 10-10 01:59Z（09:59） | 33.8° | 02:06Z | 7.0 min | ±9.1 kHz |
| 8 | JAMX01 / 435.075 MHz | SSTV(模拟) | 10-08 13:00Z（21:00） | 33.3° | 13:07Z | 7.0 min | ±9.2 kHz |

- **对准频率**：JAMX01 用 **435.075 MHz**（SSTV/beacon）；ASRTU-1 用 **436.210 MHz**
  （9k6 BPSK 数字）。均为 70 cm 段，多普勒峰偏约 **±10 kHz**，过境中务必开
  **AFC/多普勒补偿**（AOS 偏高约 +9~10 kHz，LOS 偏低约 −9~10 kHz）。
- **录制时长**：单条过境 6.5~7.5 min，**整段过境都录**（SSTV 一帧 ~114 s，
  留足余量；SSDV 数字链多收几轮更利于补包）。
- 模式（SSTV/SSDV）按主办方当次排班；上表「拟」列仅按下行性质推断，**以现场听到/
  看到的信号为准**，用对应命令（§4）解码。

---

## 4. 接收命令（照抄；freq/mode/时长均已给值）

> 前置：先 `python3 tools/onboarding/onboard.py --step detect` 确认 RTL-SDR 在。
> 产物目录统一用 `--out-dir ./phase14_rx`。

### 4.A SSTV（模拟慢扫描；JAMX01 435.075 MHz）

```bash
python3 tools/onboarding/onboard.py --step all \
    --freq 435.075e6 --mode sstv \
    --sr 250000 --n 30000000 --gain 24 \
    --out-dir ./phase14_rx
```
- `--mode sstv`：FM 解调 → 重采样 48k → 自动识别 Martin/Scottie/Robot/PD。
- 时长：默认 30,000,000 样本 @250k ≈ **120 s**；过境更长就加大 `--n`
  （如 `--n 90000000` ≈ 360 s，覆盖整段过境）。
- 出图：`./phase14_rx/sstv_image__recorded__N…__.png`。

### 4.B SSDV（数字慢扫描；ASRTU-1 436.210 MHz）

> ⚠️ **当前链路边界（2026-10-04 演练确认）**：`onboard --mode ssdv` 目前只做
> **字节/包层以上**（包同步→RS→CRC→MCU 重组 JPEG），输入是「解调后的 256B 包
> 字节流文件」，**不是** complex64 IQ。物理层（FM→AFSK→ASM→Viterbi→解扰→RS→包）
> 尚未在 onboard 里串成一条命令（`mbdsdr_ai/ccsds_rx.py` 零件已单测就绪，待接）。
> 故真机分两步：

```bash
# 第 1 步：先把下行 IQ 录下来（用 sstv/通用采集把 436.210 MHz 整段过境录成 raw）
python3 tools/onboarding/onboard.py --step all \
    --freq 436.210e6 --mode apt --sr 250000 --n 90000000 --gain 24 \
    --out-dir ./phase14_rx

# 第 2 步：把解调链产出的 256B 包字节流存成 .bin 后，喂给 ssdv 解码入口：
python3 tools/onboarding/onboard.py --step decode \
    --mode ssdv --sigmf-data ./phase14_rx/<demodulated_packets>.bin \
    --out-dir ./phase14_rx
```
- 出图：`./phase14_rx/ssdv_rebuilt.jpg`（标准命名副本 `ssdv_image__recorded__…jpg`）。
- 演练已用合成包流验证此入口全链可出图（见 §6）。

---

## 5. 成功判定 + 常见失败对照

### 5.1 成功判定（一句话）

> **解出可打开的图像 = 成功**：
> - SSTV：`step=decode [PASS]` 且产物目录出现 `sstv_image__…png`，PIL 能打开、
>   无明显错位/大条纹；
> - SSDV：`step=decode [PASS]` 且出现 `ssdv_rebuilt.jpg`，JPEG 头尾 `FF D8 … FF D9`、
>   宽高与预期一致，`MCU received/total` 缺 MCU 在可接受范围（缺处补空白、非整图崩）。
> 纯噪声/无信号时脚本应**诚实 FAIL、不产出假图**——这是正确行为，不是失败。

### 5.2 常见失败对照表

| 现象（onboard 输出） | 最可能原因 | 处置 |
|---|---|---|
| `detect: 未检测到 RTL-SDR` | USB 没插好 / 驱动被占 | 重插 USB2.0；`lsusb \| grep 0bda`；`sudo modprobe -r dvb_usb_rtl28xxu` |
| `capture: 未写入任何数据 / 文件为空` | 设备被 rtl_tcp/gqrx/dump1090 占用 | 关掉占用进程再跑 |
| `capture: 字节数偏差过大` | USB 丢包 | 降采样率或换 USB2.0 口 |
| `SSTV 未解码出图像` | ①录制没盖完整帧 ②频率/多普勒没对准 ③仰角太低/天线偏 ④增益不当 | 录满整段过境；按 §3 多普勒偏调中心频或开 AFC；对准 AOS/LOS 方位；微调增益 |
| `SSDV 未同步到任何有效 256B 包` | 字节流不是解调后包流；物理层参数不对 | 确认喂的是解调后 256B 包 `.bin`；检查 AFSK 速率/ASM/解扰参数 |
| SSDV 出图但 `missing_mcus` 多 | 丢包/误码超 RS t=16 能力 | 整段过境多录几轮重收；开多普勒补偿+AFC；仰角高的过境优先 |
| 有过境但 `TLE 新鲜度=stale` | TLE 历元 >7 天 | 重拉 §2 URL，或催主办方要最新 TLE |
| `GROUND_STATION 经纬度未填` | 没填本机坐标 | 改 `pass_predict.py` 顶部 `GROUND_STATION` |

---

## 6. 本次演练实测记录（证明链路可用，2026-10-04）

- **SSDV 端到端**：合成 64×64 RGB → `SsdvEncoder`（fsphil 方言，callsign=BTEST 仅
  参数传入）→ **3 个 256B 包 = 768 字节** → `onboard --mode ssdv` decode → output
  → **JPEG 出图**（`scratch/phase23b/ssdv_rebuilt.jpg`，1391 B，64×64，
  sha256 `25b60261…ebf7dd`，SOI/EOI 正确）。纯噪声 4096B 输入诚实 FAIL、不造图。
- **测试**：`pytest tests/test_ssdv_e2e.py` **14/14 passed**；
  `test_pass_predict.py` **5/5 passed**。
- **过境预测**：见 §3，两星 TLE 均 fresh，多普勒符号/量级与 70 cm 段一致
  （峰偏 ~±10 kHz）。

---

## 7. 收图后邮件材料勾选（换证书）

> 完整模板见 `P3-email-checklist.md`（收件人 **CASC70@asesspace.com**）。
> 发邮件前逐项勾选：

- [ ] 成功解码的图像 PNG/JPG 附件（SSTV 或 SSDV，清晰完整）
- [ ] 接收瞬间频谱/瀑布截图（含时间与频率）
- [ ] 接收 **UTC** 时间（抄 SigMF meta `core:datetime` 或 §3 过境表；**不写北京时**）
- [ ] 卫星与制式（JAMX01 / ASRTU-1；SSTV / SSDV）
- [ ] 下行频率（按实际对准频率，MHz）+ 最大仰角
- [ ] 接收地点经纬度（实际接收点，勿套长春示例）+ 城市
- [ ] 接收设备（RTL-SDR 型号/采样率/增益）+ 天线（波段/类型）
- [ ] 姓名 / 单位 / 合法呼号（无呼号按主办方公告注明，不冒用）
- [ ] 累计已收幅数（活动共 34 幅）

---

## 8. 已知链路缺口 / 交接（报 C 块，本次未改产品代码）

1. **SSDV 物理层未串入 onboard**：`onboard --mode ssdv` 仅吃「解调后包字节流」，
   `rtl_sdr IQ → AFSK→ASM→Viterbi→解扰→RS→256B 包` 这段（`ccsds_rx.py` 零件已就绪）
   尚未接成 `--step all` 一条命令；真机需先手动产出包字节文件。建议 C 块评估串联。
2. **JAMX01 无 CelesTrak 公开 GP**：CATNR=100465 拉不到，只能用 AutoTLE 聚合源
   （目录号 70001）。活动当天须重拉，历元超过 3 天建议催主办方发正式 TLE。
3. **onboard `--step all` 会先跑 detect**：无硬件/纯字节流演练时须用
   `--step decode --sigmf-data …` 绕过 detect/capture/record（非 bug，属用法）。
