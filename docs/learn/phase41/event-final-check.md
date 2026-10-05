<!--
SPDX-License-Identifier: MIT
Phase41 块3：活动前最终核对（event-checklist 逐项复核 + 真机未验证项 + 链路结论对齐）
-->

# 活动前最终核对总表（event-final-check）

> 复核时点：**2026-10-05（周一，UTC+8）**，距活动窗口 2026-10-08~10 还有 3 天。
> 本文件是 `docs/learn/phase34/event-checklist.md` 的**活动前最终态复核记录**：
> 逐项标出现状（云端已就绪 / 待真机现场确认），如实登记未验证项，并核对
> `recv-chain-conclusion.md` 与 `event-rx-runbook.md` 是否一致。
>
> **口径**：云内无硬件，凡涉「真机收到信号/出图」一律 **【待真机确认】**；
> 活动参数（频率/TLE/排班）只进 `docs/learn/phase14/P3-event-params.md`，本文件不硬编码。
> 出图主链走 Python（见 `recv-chain-conclusion.md`）。

---

## 0. 一句话结论

**云端可验证部分（代码链路 / 离线闭环 / 合成包流 / 命令模板 / 邮件模板）已就绪并复跑留证；
所有「真实卫星射频」环节（设备枚举、USB/驱动、天线指向、真实 TLE 新鲜度、官方频率、
SSDV 物理层 IQ→包）均无硬件、未在真实过境中验证，须活动现场首度过境前亲手确认。
接收链路结论（Python 主链 / C++ 外围）与 runbook 完全一致；仅一处过时限测试计数
（14/14→26）已在两份文档同步更正。**

---

## 1. event-checklist 逐项复核总表

> 状态图例：✅ **云端已就绪/已复跑留证**；🟡 **代码/模板就绪，但真机环节待确认**；
> ⬜ **待真机现场操作**（云内无法代验）。

### A. 接收设备（RTL-SDR 主机）

| 清单项 | 现状 | 证据 / 说明 |
|---|---|---|
| RTL-SDR 设备在（lsusb 命中 0bda:2838/2832） | ⬜ 待真机 | 云内无 USB 硬件 |
| 插 USB2.0 直连口 | ⬜ 待真机 | 现场确认 |
| 已 `modprobe -r dvb_usb_rtl28xxu` | ⬜ 待真机 | 现场执行 |
| 用户组/udev（plugdev,dialout + rules） | 🟡 文档就绪 | rules 路径见 phase9/P1-first-run；设备本身待插 |
| `onboard.py --step detect` 打印 PASS | 🟡 代码就绪 | `tools/onboarding/onboard.py` detect 步在位；真机输出待现场 |
| 无占用进程（rtl_tcp/gqrx/dump1090） | ⬜ 待真机 | 现场确认 |
| `rtl_sdr` 在 PATH | ⬜ 待真机 | 现场 `which rtl_sdr` |
| Python 依赖（numpy/scipy/skyfield/sgp4/Pillow） | ✅ 就绪 | 本次复跑 ssdv/sstv 测试均成功 import 这些依赖 |
| 增益 `--gain 24` 默认 | 🟡 模板就绪 | 现场按信号微调【待真机】 |

### B. 天线与对准

| 清单项 | 现状 | 证据 / 说明 |
|---|---|---|
| 70 cm 波段匹配（435/436 MHz 段） | 🟡 旁证 | 下行在 UHF 70 cm；旁证频率见 §3 第3项 |
| AOS/LOS 方位从 pass_predict 读出并对准 | ⬜ 待真机 | 预测脚本就绪，但**站坐标/TLE 尚未录入**（见 D），方位须活动前录入后跑 |
| 优先中天仰角 ≥30° 过境 | ✅ 规则就位 | 脚本 `MIN_ELEVATION_DEG=10°`，手册建议 ≥30° 选主攻 |
| 馈线/接头无松动进水 | ⬜ 待真机 | 现场目检 |

### C. 时间基准

| 清单项 | 现状 | 证据 / 说明 |
|---|---|---|
| 本机 NTP 时钟同步 | ⬜ 待真机 | 现场确认（接收 UTC 要写进邮件） |
| 记录口径 = UTC（勿写北京时） | ✅ 已贯穿 | checklist/runbook/email 模板三处均强制 UTC |
| （可选）GNSS 写 SigMF | 🟡 文档就绪 | `--gnss <nmea.log>` 路径在位；不接则诚实退化 `time_source=system` |

### D. 过境预测与频率录入

| 清单项 | 现状 | 证据 / 说明 |
|---|---|---|
| TLE 粘进 `pass_predict.py` `tle_lines` | 🟡 **快照已登记、脚本录入位仍空** | 见下方「TLE 现状」 |
| `GROUND_STATION` 填本机坐标 | ⬜ 待用户填 | `pass_predict.py:59-63` 当前 `lat/lon=None`（未套长春示例，符合红线） |
| 过境表跑出（两星 fresh、status≠none） | ⬜ 待录入后跑 | 录入位为空时脚本诚实打印「未录入，跳过」不伪造 |
| 主攻过境 AOS/仰角/fd 抄进值班笔记 | ⬜ 待 D 完成后 | 依赖上一项 |
| 官方下行频率填 `P3-event-params.md §2` + `downlink_hz` | ⬜ **待官方发布** | `P3-event-params.md §2` 四个模式行均 `_待官方发布填入_`；`pass_predict.py:71,77` `downlink_hz=None` |
| 多普勒补偿预案（70 cm \|fd\|≈±10 kHz） | ✅ 文档就绪 | runbook §阶段1 开 AFC/偏调；`doppler_compensation.remove_doppler_shift` 在位 |

> **TLE 现状（关键，活动前一天必做）**：TLE 快照已在 `P3-receive-sop.md §2` 带来源 URL 登记，
> 但**尚未粘进 `pass_predict.py` 录入位**（当前为空，符合「录入位留空」设计）。
> 按历元核算（截至活动日 2026-10-08 00:00 CST）：
>
> | 卫星 | 快照来源 | 快照历元 | 今天(10-05)龄 | 活动日(10-08)龄 | 处置 |
> |---|---|---|---|---|---|
> | JAMX01 | BI4PYM/AutoTLE 聚合源（目录号 70001/COSPAR 2026-195A） | 2026-10-02T04:07Z | **≈3.08 天** | **≈5.50 天** | **必须重拉/催主办方发正式 TLE**（CelesTrak 无公开 GP） |
> | ASRTU-1 | CelesTrak gp.php?CATNR=61781 | 2026-10-03T14:33Z | ≈1.64 天 | ≈4.06 天 | 活动前一天重拉（或填 `norad_catnr=61781` 联网拉） |
>
> 注：脚本自动判 `stale` 阈值为历元 >7 天；但 runbook §阶段0 的**工作口径**是 LEO 取 **<3 天**
> 才信方位/多普勒——故上表两星到活动日均超 3 天，**务必重拉后再粘录入位**。

### E. 接收链路预演（出图前自检）

| 清单项 | 现状 | 证据 / 说明 |
|---|---|---|
| SSTV 一条命令模板就绪 | 🟡 模板就绪 | runbook §2-A；`--mode sstv` 分支在位（`onboard.py:753`） |
| SSDV 两步走明确（录 IQ→解调 `.bin`→decode） | 🟡 模板就绪 | runbook §2-B；**物理层 IQ→`.bin` 待真机**（见 §2 第2项） |
| 出图产物目录 `--out-dir ./phase14_rx` | ✅ 约定就位 | 模板已统一 |
| 离线回放已验 | ✅ **本次复跑留证** | 见下方「离线复跑证据」 |

> **离线复跑证据（2026-10-05，本次实测）**：
> - **SSTV**：`python3 tests/test_sstv_onboard_e2e.py` → `status=PASS`，
>   制式 **Robot 72 320×240、行=239/240**，PNG 116192 B。
>   ⚠ 这是 `real_sstv.wav` **重 FM 调制合成 IQ** 的离线 bench 闭环，**不是 JAMX01 over-the-air**。
> - **SSDV**：`pytest tests/test_ssdv_e2e.py` → **26 passed**（1.96 s）。
>   ⚠ 这是**合成包流**端到端，**不是真机 IQ 解调**。

### F. 换证书邮件模板（收图后即用）

| 清单项 | 现状 | 证据 / 说明 |
|---|---|---|
| 收件人 CASC70@asesspace.com | ✅ 已登记 | `P3-email-checklist.md` 与 runbook §阶段3 一致 |
| 静态项（姓名/单位/呼号/设备/天线描述） | 🟡 模板就绪 | 用户本人静态信息现场填入；模板含完整正文 |
| 动态项（UTC/卫星制式/频率/仰角/截图/附件/幅数） | ⬜ 收图后补 | 模板字段齐全；累计 n/34 |
| 不冒用呼号 / 不伪造接收证据 | ✅ 已红线写入 | 模板 §3 备注明确 |

---

## 2. 真机未验证项汇总（如实登记，活动现场首度过境前必查）

| # | 未验证项 | 验证缺口（云内为何验不了） | 活动日处置 |
|---|---|---|---|
| 1 | **SSTV 真实出图** | 仅有 `real_sstv.wav` 离线重调制闭环（Robot72 239/240 行）；真实 JAMX01 过境射频的制式/行数未在 over-the-air 验过 | 首度过境按 runbook §2-A 一条命令录整段；以现场 `rows_decoded` 接近 240 行、无大斜条纹判成功；缺半帧以上按失败重收 |
| 2 | **SSDV 物理层 IQ→包 `.bin`** | `--mode ssdv` 只吃解调后包字节流（合成包流 26/26 绿）；`IQ→BPSK→ASM→Viterbi→解扰→RS→218B DSLWP 包` 未串成一条命令（`ccsds_rx.py` 仅零件单测） | **活动期最大操作不确定项**：先用 `--mode apt` 录整段 IQ，再用 `gr_satellites 61781` 或 `ccsds_rx.py` 零件手动产出 218B 包 `.bin`，喂 `onboard --step decode --mode ssdv`；先 DSLWP 方言试解、失败退 fsphil 经典 |
| 3 | **活动实际下行频率/排班** | 435.075（JAMX01 SSTV）/ 436.210（ASRTU-1 9k6 SSDV）均为**社区协调/实解旁证**（AMSAT ANS-235、satnogs、`SSDV_SSTV_SPEC.md §8.2`），非主办方规格书 | 收到官方公告先改 `P3-event-params.md §2` 录入位与 `downlink_hz` 再执行；现场按实际对准频率填邮件，不编造 |
| 4 | **JAMX01 真实 TLE** | AutoTLE 快照历元 2026-10-02，活动日龄 ≈5.5 天；CelesTrak 无公开 GP | 活动前一天重拉 AutoTLE，或催主办方发正式 TLE；粘入 `pass_predict.py` 后确认 `fresh` |
| 5 | **设备/USB/驱动/占用** | 云内无 RTL-SDR 硬件 | 现场按 checklist A 段逐项 `lsusb` / `detect` / 关占用进程 |
| 6 | **天线指向精度与信号强度** | 无 70 cm 实天线 | 按 AOS/LOS 方位大致对准；优先 ≥30° 仰角过境；增益 24dB 现场微调 |
| 7 | **本机接收点经纬度** | 不得套长春示例坐标 | 用户把本机 lat/lon 填进 `GROUND_STATION` 后再跑预测 |

---

## 3. 接收链路结论 vs runbook 对齐核对

逐项核对 `recv-chain-conclusion.md`（Phase34 块2）与 `event-rx-runbook.md`（Phase34 块3）：

| 对齐点 | recv-chain-conclusion | event-rx-runbook | 结论 |
|---|---|---|---|
| 出图主链归属 | Python（onboard.py + mbdsdr_ai ssdv/sstv decoder） | §首页「出图全链走 Python」 | ✅ 一致 |
| C++ 角色 | 仅外围（Qt 时空视图/ControlHub/recording_library），SSTV/SSDV 整图解码 YAGNI 不双写 | §首页「C++ 端不做 SSTV/SSDV 解码」 | ✅ 一致 |
| SSTV 路径 | `--mode sstv` 一条命令，Robot72 320×240 | §2-A 一条命令，判据 rows≈239/240 | ✅ 一致 |
| SSDV 路径 | 两步走（录 IQ→解调 `.bin`→decode），DSLWP 218B auto 方言 | §2-B 两步走，DSLWP 218B/9 头/魔数 CRC | ✅ 一致 |
| 物理层缺口 | §6 缺口1：ccsds_rx 零件未串成一条命令 | §2-B ⚠ 链路边界同述 | ✅ 一致 |
| 失败诚实空态 | 不造图、`未同步到任何有效包`→FAIL | 常见失败对照表同 | ✅ 一致 |
| 未验证项清单 | §6 四缺口 | 文末「未真机验证项汇总」4 条 | ✅ 一致 |
| **SSDV e2e 计数** | 旧记「14/14 passed」 | （runbook 未记具体数） | ⚠ **已更正**：本次复跑 26 passed；两份文档已同步改为当前实测值，旧 14/14 标注为演练时点 |

> **对齐结论**：手册与接收端结论在「Python 主链 / C++ 外围 / SSTV 一条 / SSDV 两步 /
> 物理层最大缺口 / 诚实空态」上**完全一致**，无矛盾；唯一偏差是 SSDV e2e 测试计数
> 随测试扩充从演练时 14 涨到当前 26，已在 `event-checklist.md` 与 `recv-chain-conclusion.md`
> 两处同步更正，不影响架构结论。

---

## 4. 本次复核对文档的改动（仅文档，不改功能代码）

| 文件 | 改动 |
|---|---|
| `docs/learn/phase34/event-checklist.md` | E 段「离线回放已验」由 `[ ]`→`[x]`，补本次复跑证据（SSTV Robot72 239/240、SSDV 26 passed），并把过时限「14/14」更正为 26 |
| `docs/learn/phase34/recv-chain-conclusion.md` | §4 第3条把「14/14 passed」更正为「26 passed（2026-10-05 复跑）」，补 SSTV 复跑 239/240 行证据 |
| `docs/learn/phase41/event-final-check.md` | 本文件（新建） |

---

## 5. 活动前一天（2026-10-07）必做 / 未解决项

1. **【高】重拉两星 TLE**：JAMX01（AutoTLE 聚合源）+ ASRTU-1（CelesTrak 61781），
   粘进 `pass_predict.py` `tle_lines`；活动日两星历元均将 >3 天，不重拉不盲收。
2. **【高】填本机经纬度**进 `GROUND_STATION`（勿套示例），跑 `pass_predict.py` 锁定 ≥30° 仰角主攻过境，抄 AOS UTC/仰角/\|fd\|。
3. **【高】等官方频率公告**后填 `P3-event-params.md §2` 与 `downlink_hz`；未公布前不跑真机。
4. **【高】SSDV 现场物理层预案**：确认 `gr_satellites 61781` 或 `ccsds_rx.py` 零件能把 IQ 快速解成 218B 包 `.bin`——活动期最大操作风险。
5. **【中】现场设备自检**：lsusb / detect / 关占用进程 / USB2.0 / 增益微调。
6. **【中】邮件静态项**提前备好（姓名/单位/呼号/设备/天线描述）。

---

## 6. 红线自检（本块）

- ✅ 只读复核 + 仅改 `docs/learn/phase34/`（2 个文档数字更正）与新建 `docs/learn/phase41/`；**未改任何功能代码**。
- ✅ 活动参数（435.075/436.210/TLE）**未进代码**：全仓 grep 生产代码零命中；
  - `mbdsdr_ai/ssdv_decoder.py:60` 注释提「ASRTU 家族」但标注为**通用 DSLWP 能力**，非活动专用；
  - `scratch/phase23b/run_pass_predict.py`（**未跟踪**本地演练脚本，含旁证频率/TLE）不属交付路径，本次**未暂存**。
- ✅ 无虚构：所有真机项均标【待真机确认】；离线/合成证据均为本次实跑留证。
- ✅ 全文仅用「活动/通联/接收」措辞，未出现赛事类字样。
- ✅ 未 `git add`（尤其禁 `add -A`）、**未 commit/push**；改动文件见 §4。

---

## 7. Phase43 活动解码冲刺更新（2026-10-05）

### 新能力就绪状态
| 能力 | 状态 | 证据 |
|---|---|---|
| **SSTV Robot36 解码**（120 两行组×240 行，per_line/grouped 双路径） | ✅ 已就绪 | `mbdsdr_ai/sstv_decoder.py:516 _decode_robot36`；内置确定性组首合成器往返（vis=8/119 markers/234 行） |
| **SSTV 自动制式识别**（行数主判+VIS 旁证） | ✅ 已就绪 | `sstv_decoder.py:404 _identify_sstv_mode`（改于 :458）：Robot72 逐行 239 markers vs Robot36 组首 119 markers，阈值 180；real_sstv.wav→Robot72 ✓ |
| **SSDV 物理层一条命令** | ✅ 已就绪（合成链路） | `mbdsdr_ai/ssdv_phy.py:51 demod_bpsk`（NCO 带内下变频+盲符号定时）；`onboard.py --mode ssdv --ssdv-input iq` 云内全链 PASS（合成 IQ→BPSK→字节→JPEG 48×48，MCU 36/36，方言 fsphil） |
| **SSDV DSLWP 218B 方言**（Viterbi/RS 级联） | ⚠ 待真机/待补 | 完整 CCSDS 级联未在云内跑（fsphil 256B 自同步方言仅证明物理层→JPEG 通）；BPSK 载波恢复/0-π 模糊未做 |

### §5 第 4 项更新（原"SSDV 现场物理层预案"）
- ~~确认 gr_satellites / ccsds_rx.py 零件~~ → **现有一条命令**：
  `python3 tools/onboarding/onboard.py --step decode --mode ssdv --sr 225000 --sigmf-data <IQ> --ssdv-input iq --ssdv-symrate 9600 --ssdv-tone-offset <f> --out-dir out/`
  （符号率/频偏活动日按官方公告填 docs 活动参数；真机 rtl_sdr 联调未做——活动期最大操作风险从"无链路"降为"真机参数与 CCSDS 级联"）

### TLE 重拉时机提醒（不变，重申）
- 10-07 活动前一天：重拉 JAMX01 + ASRTU-1 TLE 粘进 pass_predict.py tle_lines；填本机经纬度锁定 ≥30° 仰角过境；等官方频率公告填 downlink_hz。

### 真机步骤（Phase43 后最终版）
1. 10-07：重拉 TLE / 填站坐标 / 等频率公告 / 现场设备自检
2. 活动日 SSTV：onboard --mode sstv（一条命令，Robot36/72 自动识别）
3. 活动日 SSDV：rtl_sdr 录 IQ（≥225k）→ onboard --mode ssdv --ssdv-input iq（--ssdv-symrate 按公告）→ JPEG
4. 真机未验证项如实汇总（SSTV OTA / SSDV rtl_sdr 联调 / CCSDS 级联）→ 见 open-items.md A 类
