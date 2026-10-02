# P1 贴回应答模板：收到用户 --paste 块 / 演练检查表后的标准回复

> 用途：用户在真机跑完 `tools/acceptance_run.sh`（或 phase10 的
> `selfcheck --json | diag_wizard --paste`），把围栏回传块 / `tools/acceptance_out.json`
> 贴回聊天后，我方按本模板**结构化应答**。目标：用户不用再猜"报完然后呢"。
>
> 解析入口：`python3 tools/onboarding/parse_hw_report.py <贴回文本>`（自动识别
> selfcheck / onboard / diag-wizard 三类块）。本模板与 parse_hw_report 输出口径对齐。

## 标准应答四段式（每次必给）

1. **结论摘要**：一句话定性（设备就绪 / 有阻断 FAIL / 无硬件空态）+ 关键计数
   `PASS/WARN/FAIL/SKIP`、`device_present`、`overall/exit_code`。
2. **下一步命令（可复制）**：只给**与当前事实挂钩**的命令——设备就绪才给 onboard，
   有 FAIL 才给修复命令，无硬件就给"插好后重跑哪条"。
3. **常见故障对照表**：按 `checks[].status` / `summary.counts` 分支对号入座（见下）。
4. **需用户补跑的命令**：若信息不够下结论（如贴回被截断、只有人类文本无 JSON），
   明确列出要补跑的一条命令，不要让用户猜。

## 关键状态怎么读（速查）

| 回传字段 | 含义 |
|---|---|
| `device_present: true/false` | selfcheck 第 1 项是否枚举到 0bda:2838/2832 |
| `no_hardware_expected: true` | 报告来自没插设备的机器，下面的 FAIL 属预期 |
| `overall: PASS / FAIL / NO_HARDWARE` | 演练结论；NO_HARDWARE=诚实空态（退出码 2，不是错误） |
| `summary.counts` | selfcheck 6 项检查的 PASS/WARN/FAIL/SKIP 计数 |
| `checks[i].status` | 单项：PASS/WARN/FAIL/SKIP；`skipped_reason=no_usb_device` = 无设备跳过 |
| onboard `steps[]` | detect→capture→record→decode→output；**capture/record PASS + SigMF 落盘**即过，decode 出 0 帧是信号侧诚实结果 |

## 常见故障对照表（按 checks/status 分支）

| 你看到的分支 | 根因 | 给用户的处理命令 |
|---|---|---|
| 第 1 项 FAIL、`target_hits=[]`、`no_hardware_expected=true` | 没插设备 / 云 VM | `lsusb \| grep -E '0bda:(2838\|2832)'` 复核；真机插到 USB2.0 直连口后重跑 |
| 第 1 项 FAIL、设备在场但提示 `usermod -aG` | 当前用户不在 plugdev/dialout 组 | `sudo usermod -aG plugdev,dialout $USER`（**注销重登**才生效） |
| 第 1 项 WARN、提示写 udev 规则 | 未装 99-rtlsdr.rules | 复制 diag_wizard 打印的整块 `tee /etc/udev/rules.d/...` 命令执行，再 `sudo udevadm control --reload && sudo udevadm trigger` |
| 第 2 项 FAIL、`No supported devices` | dvb_usb_rtl28xxu 抢了设备 | `sudo modprobe -r dvb_usb_rtl28xxu` 后重插 USB |
| 第 2 项 WARN / `未找到 rtl_test` | librtlsdr 未装 | `sudo apt install rtl-sdr`（或见 repos/librtlsdr/） |
| 第 3 项 FAIL、文件为空 | 设备被 rtl_tcp/gqrx/dump1090 占用 | `pgrep -a rtl_tcp; pgrep -a gqrx; pgrep -a dump1090`，杀掉或重插 |
| 第 3 项 WARN、`lost at least N bytes` | USB 带宽压力 | 换主板后置 USB2.0 直连口，别用 USB3.0 Hub |
| onboard capture FAIL / 空文件 | 同上：占用或权限 | 杀占用进程；确认 udev 规则；重跑 onboard |
| onboard decode adsb 0 帧 | 1090 MHz 暂无飞机 / 天线差 | 换窗边；默认 24 dB 增益已够；等几分钟再跑（**不判环境故障**） |
| onboard decode cw 0 字符 | 7.02 MHz 当时无 CW 电台 | 确认频率上有台；调增益/偏调点（诚实空结果） |
| onboard decode apt 无图 | NOAA 卫星未过境 | 查过境预报，另约时间复跑（15 分钟窗口多半等不到） |
| ota_backfill SKIP / `空态` | 没有 SigMF 录制可回填 | 确认 onboard record 步 PASS，`--recordings-dir` 指向 onboarding_<mode>_<时间戳>/ |
| 贴回被截断 / 无围栏 / 无 JSON | 用户只贴了半句 | 让用户补跑"需补跑命令"里的一条（见下） |

---

## 分支示例（照抄改数字即可）

### 分支 A：全 PASS（设备就绪，演练绿）

> **结论摘要**：设备就绪（`device_present=true`），自检 `PASS=4 WARN=1 FAIL=0`，
> onboard adsb capture/record 已落盘 SigMF，演练 `overall=PASS`。唯一 WARN 是 GNSS
> 串口未接——属可选增强，不阻断。
>
> **下一步命令（可复制）**：
> ```bash
> # 1) 继续录 CW / APT（参数核对自 onboard.MODES）
> python3 tools/onboarding/onboard.py --step all --freq 7020000 --mode cw
> python3 tools/onboarding/onboard.py --step all --freq 137.5e6 --mode apt --n 4800000
>
> # 2) 把 ADS-B 录制回填成 recorded 口径（CSV + 图）
> python3 experiments/exp_ota_run.py \
>     --recordings-dir paper/experiments/onboarding_adsb_<时间戳>
>
> # 3) 打开桌面端 [时空视图] tab 人工核对四格（见验收手册步骤④）
> ```
>
> **故障对照**：当前无阻断项。GNSS WARN：接了 GNSS 模块后重跑
> `onboard.py --gnss <nmea.log>` 即可把时间源从 system 切 gnss。
>
> **需补跑**：无需补跑，已可推进 P2/P3。

### 分支 B：有 FAIL（设备在场但环境有阻断）

> **结论摘要**：设备在场（`device_present=true`），但自检 `FAIL=1`：第 1 项提示当前
> 用户不在 `plugdev/dialout` 组；onboard detect 因此连带 FAIL。这是权限问题，不是
> 机器坏了。
>
> **下一步命令（可复制，按顺序）**：
> ```bash
> # 1) 加入用户组（关键：执行后必须注销/重新登录，否则不生效）
> sudo usermod -aG plugdev,dialout $USER
> # ---- 现在注销重登，再继续 ----
>
> # 2) 重跑自检确认第 1 项变 PASS
> python3 tools/hw_selfcheck/selfcheck.py --json | python3 tools/diag_wizard.py --paste
>
> # 3) 组生效后跑首条链路
> python3 tools/onboarding/onboard.py --step all --freq 1090e6 --mode adsb
> ```
>
> **故障对照**：若第 1 项改提示 udev 规则缺失，则执行 diag_wizard 打印的整块
> `tee /etc/udev/rules.d/99-mbdsdr-rtlsdr.rules` 命令 + `udevadm control --reload`；
> 若第 2 项 `No supported devices`，执行 `sudo modprobe -r dvb_usb_rtl28xxu` 后重插。
>
> **需补跑**：重登后把第 2 步的 `--paste` 围栏块整段贴回，我复核第 1 项是否转 PASS。

### 分支 C：无硬件（云 VM / 未插机器的诚实空态）

> **结论摘要**：未检测到 RTL-SDR 设备（`device_present=false`、
> `no_hardware_expected=true`）。这是**预期空态**，不是故障：自检 `FAIL=1`（USB 未
> 命中）、第 2/3 项 `SKIP=no_usb_device`，onboard 三类与 ota 全部按红线 SKIP、
> 未 mock 任何采集。演练 `overall=NO_HARDWARE`（退出码 2）。
>
> **下一步命令（可复制）**：
> ```bash
> # 1) 在插了 RTL-SDR 的 Linux 真机上（不是云 VM）执行：
> bash tools/acceptance_run.sh
> #    或最小化只跑自检向导：
> python3 tools/hw_selfcheck/selfcheck.py --json | python3 tools/diag_wizard.py --paste
>
> # 2) 确认内核已枚举设备：
> lsusb | grep -E '0bda:(2838|2832)'   # 应命中一行 Realtek RTL2838
> ```
>
> **故障对照**：`lsusb` 无输出 → 换 USB2.0 直连口/换线，`dmesg | tail -20` 看枚举；
> 有设备但打不开 → `sudo modprobe -r dvb_usb_rtl28xxu` 后重插。
>
> **需补跑**：真机上把 `acceptance_out.json` 内容或 `--paste` 围栏块整段贴回，
> 我据 `device_present=true` 切换到分支 A/B 的应答。

---

## 红线提醒（应答时自检）

- 无硬件时**绝不**说"检测到设备"或编造 onboard 帧数；如实说空态。
- onboard decode 0 帧（adsb/cw/apt）是信号侧结果，**不要**当环境 FAIL 让用户反复修。
- 缺卷期/真值的地方（SNR 标定、AMR 准确率、定轨收敛）保持 `需真机校准 / N/A`，
  不替用户编数字。
