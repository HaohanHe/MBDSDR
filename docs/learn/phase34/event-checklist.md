<!--
SPDX-License-Identifier: MIT
Phase34 块4：活动前核对清单（设备/天线/时间/预测/邮件）
-->

# 活动前核对清单：航天七十载·星火传未来 业余无线电图像通联

> 活动窗口 **2026-10-08 ~ 10**（接收时刻一律记 **UTC**）。本清单是活动**开始前**
> 逐项打勾的总表，覆盖：设备 / 天线 / 时间 / 预测 / 频率录入 / 邮件模板。
>
> **口径**：凡标 **【待真机确认】** 的项，云内无硬件、尚未在真实卫星过境中验证，
> 须由用户在活动现场首度过境前亲手确认。活动参数只进
> `docs/learn/phase14/P3-event-params.md`，本清单不硬编码频率/TLE。
> 出图主链走 Python（见 `recv-chain-conclusion.md`）；接收命令见 `event-rx-runbook.md`。

---

## A. 接收设备（RTL-SDR 主机）

- [ ] **RTL-SDR 接收机在**：`lsusb | grep -E '0bda:(2838|2832)'` 命中 RTL2838/2832
- [ ] **插对口**：主板后置 **USB 2.0 直连口**（不走 USB3.0 Hub / 前置面板）【待真机确认】
- [ ] **驱动不抢设备**：`sudo modprobe -r dvb_usb_rtl28xxu` 已执行（DVB 电视驱动）
- [ ] **用户组/udev 就绪**：当前用户在 `plugdev,dialout` 组且已重登录；
      `99-mbdsdr-rtlsdr.rules` 已装（详见 phase9/P1-first-run）
- [ ] **onboard detect 通过**：`python3 tools/onboarding/onboard.py --step detect`
      打印 `[PASS] step=detect 检测到 RTL-SDR`
- [ ] **无占用进程**：rtl_tcp / gqrx / dump1090 等已关闭（否则 capture 空文件）
- [ ] **rtl_sdr 在 PATH**：`which rtl_sdr` 有输出
- [ ] **Python 依赖装好**：`pip install -r requirements.txt`（numpy/scipy/skyfield/sgp4/Pillow）
- [ ] **增益可用**：`--gain 24` 默认；弱信号可逐步加大（现场微调）【待真机确认】

## B. 天线与对准

- [ ] **天线波段匹配**：活动下行在 **70 cm（435 MHz 段）**，配 70 cm 八木/棒状天线
      （旁证：JAMX01 435.075 / ASRTU-1 436.210 MHz；**以官方频率为准**）
- [ ] **AOS/LOS 方位已知**：从 `pass_predict.py` 读出本次过境的升起/落下方位，
      天线按方位大致对准卫星过境弧段【待真机确认：实际指向精度】
- [ ] **仰角优先**：优先选中天仰角 ≥30° 的过境（低仰角易被遮挡/多普勒快）
- [ ] **天线馈线/接头**：无松动、无明显进水【待真机确认】

## C. 时间基准

- [ ] **本机时钟已同步**：接收 UTC 时间将写进邮件，时钟要准（NTP 同步）
- [ ] **记录口径 = UTC**：邮件/SigMF 一律记 UTC（CST=UTC+8，勿写北京时）
- [ ] **（可选）GNSS**：接 GNSS 模块时 `onboard --gnss <nmea.log>` 把 GNSS UTC
      写进 SigMF（`time_source=gnss`）；不接则诚实退化 `time_source=system`

## D. 过境预测与频率录入

- [ ] **TLE 已录入**：两星三行 TLE 粘进 `docs/learn/phase14/pass_predict.py` 顶部
      `tle_lines`（JAMX01 须用 AutoTLE 聚合源；ASRTU-1 可 `norad_catnr=61781`）
      【待真机确认：活动当天 TLE 历元是否 <3 天，超期重拉/催主办方】
- [ ] **GROUND_STATION 已填本机坐标**：`pass_predict.py` 顶部 `lat_deg/lon_deg`
      填实际接收点（勿套长春示例 43.81/125.32）【待真机确认：实际接收点坐标】
- [ ] **过境表已跑出**：`python3 docs/learn/phase14/pass_predict.py`，
      两星 `TLE 新鲜度=fresh`、`status != none`
- [ ] **主攻过境已锁定**：把 ≥30° 仰角过境的 AOS UTC / 中天仰角 / |fd| 抄进值班笔记
- [ ] **频率已录入 phase14 录入位**：官方公布的下行频率填进
      `P3-event-params.md` §2 与 `pass_predict.py` `downlink_hz`
      【待官方发布填入；未公布前不跑真机】
- [ ] **多普勒补偿预案**：70 cm 段 |fd|≈±10 kHz，AFC/跟频已就绪；
      SSTV 可手动按 fd 偏调中心频（见 `P3-event-params.md` §5）

## E. 接收链路预演（出图前自检）

- [ ] **SSTV 链路可跑**：`onboard --mode sstv` 命令模板已备好
      （`event-rx-runbook.md` §2-A；`--n` 加到 ~90M 覆盖整段过境）
- [ ] **SSDV 两步走已明确**：IQ 录制 → 手动解调产出包 `.bin` →
      `onboard --step decode --mode ssdv --sigmf-data <...>.bin`
      【待真机确认：现场物理层解调手段（gr_satellites 61781 / ccsds_rx 零件）】
- [ ] **出图产物目录约定**：统一 `--out-dir ./phase14_rx`，便于找 PNG/JPG
- [x] **离线回放已验（2026-10-05 复跑留证）**：
      - SSTV：`python3 tests/test_sstv_onboard_e2e.py`（`real_sstv.wav` 重 FM 调制合成 IQ → onboard sstv 解码）
        `status=PASS`，制式 Robot 72 320×240、**行=239/240**，PNG 116192 B —— 仅**离线 bench 闭环**，
        **真实 JAMX01 过境射频出图仍待真机**。
      - SSDV：`pytest tests/test_ssdv_e2e.py` = **26 passed**（2026-10-05 复跑；本清单/recv-chain-conclusion
        旧记「14/14」为 2026-10-04 演练时点数字，测试后续增至 26）—— 仅**合成包流**，真机 IQ→包 `.bin`
        物理层仍待真机。

## F. 换证书邮件模板（收图后即用）

> 收件人 **CASC70@asesspace.com**；完整模板见 `P3-email-checklist.md`。
> 提前把下面这些「静态项」填好，收图后只补动态接收记录：

- [ ] **姓名** / **单位·学校**（无则填「个人」）已备好
- [ ] **合法呼号** 已备好（无呼号者按主办方公告确认申报身份，**不冒用**）
- [ ] **接收地点经纬度 + 城市**（实际接收点）已备好
- [ ] **接收设备描述**（RTL-SDR 型号 / 采样率 250ksps / 增益 24dB）已备好
- [ ] **天线描述**（70 cm 八木/棒状）已备好
- [ ] 收图后补：**接收 UTC 时间**（SigMF `core:datetime` / pass_predict）
- [ ] 收图后补：**卫星 + 制式**（JAMX01/SSTV 或 ASRTU-1/SSDV）、下行频率、最大仰角
- [ ] 收图后补：**图像附件**（成功解码 PNG/JPG）+ **接收瞬间频谱/瀑布截图**
- [ ] 收图后补：**累计已收幅数 / 34**

---

## G. 待用户真机确认项（汇总，活动现场首度过境前必查）

> 以下为云内无硬件无法代验、须用户亲手确认的关键项，按优先级排列：

1. 【高】**活动实际下行频率/排班**是否等同旁证值（JAMX01 435.075 / ASRTU-1 436.210 MHz）——以主办方公告为准。
2. 【高】**SSDV 物理层**：现场能否用 `gr_satellites 61781`（或我方 `ccsds_rx.py` 零件）
   快速把 IQ 解成 218B DSLWP 包字节流 `.bin`——这是活动期最大操作不确定项。
3. 【高】**JAMX01 真实 TLE**：AutoTLE 快照（2026-10-04）超 3 天须重拉/催主办方。
4. 【中】**SSTV 真实出图**：JAMX01 实际制式是否 Robot 72（320×240）、现场 `rows_decoded`。
5. 【中】**天线实际指向精度**与 70 cm 信号强度（增益档位现场微调）。
6. 【低】**本机接收点经纬度**（替换 phase14 示例坐标）。

> 以上确认结果请回写 `event-rx-runbook.md` 对应「【待真机确认】」行，形成活动实战记录。
