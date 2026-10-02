# P1 真机 15 分钟验收手册：从插设备到出成果逐步清单

> 目标读者：拿着一台插好 RTL-SDR 的 Linux 真机，在 **15 分钟内**验证
> MBDSDR 真机链路是否闭环的人。只敲本文给出的命令；每步都标注"看到什么算过"。
>
> 前置（只做一次，不计入 15 分钟）：当前用户在 `plugdev,dialout` 组并已重新登录、
> udev 规则已装。详细见 [phase9/P1-first-run.md](../phase9/P1-first-run.md) 第 2、3 步。
>
> 全链路四步：
> **① 插设备 + diag_wizard（--paste 回传）→ ② onboard 跑 adsb/apt/cw 三类信号
> → ③ exp_ota_run 回填 → ④ 桌面时空视图核对**。

## 耗时预算（合计 15 分钟）

| 步骤 | 内容 | 预算 | 累计 |
|------|------|------|------|
| ① | 插设备 → selfcheck → diag_wizard --paste | 3 min | 3 min |
| ②a | onboard adsb（最容易出成果） | 3 min | 6 min |
| ②b | onboard cw（需 7.02 MHz 上有电台） | 2 min | 8 min |
| ②c | onboard apt（录 2 s 云图 raw；解码需等卫星过境） | 3 min | 11 min |
| ③ | exp_ota_run 回填 recorded 口径 | 2 min | 13 min |
| ④ | 打开桌面"时空视图"tab 核对四格 | 2 min | 15 min |

> 诚实声明：②c 的 apt **解码出云图**需要 NOAA 15/18/19 正好过境且仰角足够，
> 15 分钟窗口内多半等不到。本步的验收口径是 **capture/record 两步 PASS、
> SigMF 落盘**；decode 是否出图不作为 15 分钟内的通过项（不出图时向导会如实
> 给出"确认过境时间/对准天线"建议，不是机器故障）。

---

## 步骤 ①：插设备 + diag_wizard（3 min）

### 命令

```bash
# 1) 设备插到主板后置 USB2.0 直连口（不要 USB3.0 Hub / 前置面板口）
lsusb | grep -E '0bda:(2838|2832)'

# 2) 一键自检 + 诊断向导，末尾带 --paste 回传块
python3 tools/hw_selfcheck/selfcheck.py --json | python3 tools/diag_wizard.py --paste
```

### 预期输出

`lsusb` 命中一行：

```
Bus 001 Device 005: ID 0bda:2838 Realtek Semiconductor Corp. RTL2838UHIDIR
```

向导人类可读部分末尾汇总（`FAIL=0` 即过；WARN 不阻断）：

```
------------------------------------------------------------------------
汇总：PASS=4  WARN=1  FAIL=0
selfcheck 结论：环境基本就绪：未发现阻断性问题。
```

并在最末尾出现围栏包裹的**回传块**（从 `==== MBDSDR 真机回传块` 行下整段
复制，贴回聊天即可）：

```
==== MBDSDR 真机回传块（从此行下整段复制贴回）====
{
  "tool": "mbdsdr-diag-wizard",
  "version": "0.1.0",
  "timestamp": "2026-10-02T15:29:36+08:00",
  "host": "realbox",
  "exit_code": 0,
  "device_present": true,
  "no_hardware_expected": false,
  "checks_summary": {"PASS": 4, "WARN": 1, "FAIL": 0},
  "summary_conclusion": "环境基本就绪：未发现阻断性问题。",
  "checks": [
    {"name": "1. USB/udev：RTL-SDR 枚举与权限", "status": "PASS",
     "verdict": "已枚举到设备 0bda:2838，用户组与 udev 规则就绪", "commands": []},
    {"name": "2. 设备枚举：rtl_test -t", "status": "PASS",
     "verdict": "tuner=Rafael Micro R820T，增益 29 档，正常", "commands": []}
  ],
  "suggested_commands": [],
  "next_steps": [
    "设备就绪！一条命令跑通首条链路（ADS-B 最容易出成果）：",
    "  python3 tools/onboarding/onboard.py --step all --freq 1090e6 --mode adsb"
  ]
}
==== 回传块结束 ====
```

### 通过判据

- 向导退出码 `0`（`echo $?`）；末尾出现上面这种 `exit_code: 0` 且
  `device_present: true` 的回传块。
- 把整段贴回聊天后，云侧 `parse_hw_report.py` 能识别"诊断向导=有"并复述
  checks_summary——回传链路通。

### 常见失败对照

| 你看到的 | 根因 | 处理 |
|----------|------|------|
| `lsusb` 无输出 | 线/口问题 | 换 USB2.0 直连口、换线；`dmesg \| tail -20` 看枚举 |
| 向导第 1 项 FAIL、提示 `usermod -aG plugdev,dialout` | 组未生效 | 执行后**注销重登**再跑 |
| 第 1 项 FAIL、提示写 `99-mbdsdr-rtlsdr.rules` | 无 udev 规则 | 复制向导打印的整块命令执行并重载 udev |
| 第 2 项 FAIL、`modprobe -r dvb_usb_rtl28xxu` | DVB 电视驱动抢设备 | 执行后重插 USB |
| 全 FAIL 且 `no_hardware_expected: true` | 云 VM / 没插设备 | 属诚实空态，换真机 |

---

## 步骤 ②：onboard 跑三类信号（adsb 3 + cw 2 + apt 3 = 8 min）

三条命令共用同一条 detect→capture→record→decode→output 流水线，参数核对自
`tools/onboarding/onboard.py` 的 `MODES` 默认值。

### ②a ADS-B（飞机报文，先跑这个）

```bash
python3 tools/onboarding/onboard.py --step all --freq 1090e6 --mode adsb
```

**预期输出**：末尾 `全链路通过 ✓`；decode 步打印类似：

```
[PASS] step=decode
  → ADS-B 解码完成，3 有效帧
  ADS-B 解码：3 帧 CRC 校验通过
  帧[0] icao=AABBCC callsign=CES259X altitude=11500m speed=432kt
```

产物目录 `paper/experiments/onboarding_adsb_<时间戳>/` 内含：
`*.sigmf-data` + `*.sigmf-meta`（cf32_le 标准 SigMF）、
`*_messages.txt` / `*_messages.json`、`spectrum__captured__adsb__*.png`、
`*_manifest.json`。

### ②b CW（莫尔斯电报）

```bash
python3 tools/onboarding/onboard.py --step all --freq 7020000 --mode cw
```

**预期输出**：capture/record 必过；decode 步打印
`CW 解码完成，N 字符`（`wpm≈? conf=?`）。**7.02 MHz 上当时没有 CW 电台时，
decode 0 字符是诚实结果**——向导会给"确认频率上有 CW 信号/调增益"建议，
不计为环境故障；本步通过口径=capture/record PASS + SigMF 落盘。

### ②c APT（NOAA 气象卫星云图）

```bash
python3 tools/onboarding/onboard.py --step all --freq 137.5e6 --mode apt --n 4800000
```

**预期输出**：capture 采 2 s（4.8e6 样本 @ 2.4 MS/s）、record 写 SigMF。
decode 步打印 `APT 解码: N 行, image_a shape=...`；**窗口内无卫星过境时
image_a 为空、decode 判 FAIL 属预期**（见页首诚实声明）。

### 三类模式参数速查（核对自 onboard.MODES）

| 模式 | --freq | --mode | --n | 采样率 | 说明 |
|------|--------|--------|-----|--------|------|
| ADS-B | `1090e6` | `adsb` | 默认 240000（0.1 s） | 2.4 MS/s | 最易出成果 |
| CW | `7020000` | `cw` | 2400000（1 s） | 2.4 MS/s | 需电台在播 |
| APT | `137.5e6` | `apt` | 4800000（2 s） | 2.4 MS/s | 需卫星过境 |

### 常见失败对照

| 你看到的 | 根因 | 处理 |
|----------|------|------|
| detect FAIL：`未检测到 RTL-SDR` | 设备没被认到 | 回到步骤 ① |
| capture 写出空文件 | 设备被 rtl_tcp/gqrx/dump1090 占用 | `pgrep -a rtl_tcp` 等找占用进程，杀之或重插 |
| capture：`采样率 ... 超出 rtl-sdr 范围` | --sr 手改越界 | 用默认 2400000，别传 --sr |
| capture：`中心频率 ... 超出范围` | --freq 单位错 | 单位 Hz，如 `1090e6` |
| capture：`lost at least N bytes` | USB 带宽压力 | 换主板后置 USB2.0 直连口 |
| decode adsb 0 帧 | 1090 MHz 附近暂无飞机/天线差 | 换窗边、增益默认 24 dB 已够；等几分钟再跑 |
| apt decode 无图像 | 卫星未过境 | 查 NOAA 过境预报，另约时间复跑 |

---

## 步骤 ③：exp_ota_run 回填 recorded 口径（2 min）

```bash
python3 experiments/exp_ota_run.py \
    --recordings-dir paper/experiments/onboarding_adsb_<时间戳>
```

### 预期输出

```
[OTA 回填] 发现 1 段录制（口径=recorded）
  - 处理 paper/experiments/onboarding_adsb_20261002_153000/20261002_153000_1090000000Hz_adsb.sigmf-data
    mode=adsb fs=2400000 S/N≈12.35dB(Eb/N0≈11.10) crc=3/5 amr=AM-DSB doppler=0
[写] paper/experiments/ota_recorded_metrics.csv（1 行，口径=recorded）
[写] paper/experiments/manifest_ota_run.json
```

产物：

- `paper/experiments/ota_recorded_metrics.csv`——逐段录制一行，关键列：
  `mode / fs_hz / center_freq_hz / snr_estimate_db / n_candidates / n_crc_ok /
  success_rate / wilson_lo/hi / amr_predicted / n_doppler_observations`；
- `paper/experiments/figures/ota_recorded_success_vs_ebn0*.png`（有可估
  Eb/N0 的点才出图；未标定，图注如实标注"需真机 SNR 校准"）；
- `paper/experiments/manifest_ota_run.json`——`data_origin=recorded` 口径声明。

### 通过判据

- CSV 行数 = 喂入的录制段数；`snr_calibrated=false`、
  `amr_accuracy=N/A_no_ground_truth_labels` 如实出现（不编准确率）。

### 常见失败对照

| 你看到的 | 根因 | 处理 |
|----------|------|------|
| `未发现任何 .sigmf-data 录制 -> 空态` | 目录给错/onboard 没落盘 | 确认路径指向 `onboarding_<mode>_<时间戳>/`，且步骤 ② record 步 PASS |
| 无 Eb/N0 图 | SNR 估不出 / 无候选前导 | 属诚实空态，CSV 仍落盘；换有信号的录制 |

---

## 步骤 ④：桌面"时空视图"tab 核对（2 min）

启动桌面端（Qt），切到中心区的 **时空视图** tab（四格 2×2 + 三行状态，
接线见 `cpp/src/ui/main_window.cpp` 的 `refreshSpacetimeView()`）。

### 预期输出（接了真机时）

| 格 | 看到什么 |
|----|----------|
| 设备连接 | 从 `--` 变成已连接态（真实 1 Hz 遥测回读驱动） |
| 信号 | RSSI/SNR 有真实读数（非 NaN 空态"等待遥测"） |
| 解码状态 | 与步骤 ② 最近一次 decode 结果一致 |
| GNSS 定位 | 接了 GNSS 模块：NMEA fix 后显示定位；未接：诚实空态"无 fix" |

下方三行：

```
当前接收目标：<当前频率/模式>
时间源 system            # 接了 GNSS 并 --gnss 时才切 gnss
多普勒补偿：未补偿（无目标）  # 有轨道目标时显示实际补偿值
```

### 通过判据

- 四格**有值即绿灯、空态有明确文字**；无硬件/无 GNSS 时不冒充读数
  （时间源恒 `system`、GNSS 格恒空态，这是设计内的诚实空态）。

### 常见失败对照

| 你看到的 | 根因 | 处理 |
|----------|------|------|
| 四格全是 `--` | 桌面端未连 SDR/未起流 | 先在桌面端连接设备，再看本 tab |
| 时间源恒 `system` | 没接 GNSS / onboard 没加 `--gnss` | 预期；接 GNSS 后重跑 onboard --gnss |
| 多普勒补偿恒"未补偿" | 没有设置轨道目标 | 预期；非定轨场景不影响 ADS-B/收发 |

---

## 收尾：回传什么

验收完把两份输出贴回即可，云侧 `parse_hw_report.py` 自动识别：

1. 步骤 ① 的 **diag_wizard --paste 围栏回传块**（整段复制）；
2. 任意一步的 `onboard.py --json` 完整输出（可选，用于逐步核对）。

云侧解析会复述：检查汇总 PASS/WARN/FAIL、每步结论、产物目录与下一步建议。

## 退出码约定（与 phase9 一致）

| 命令 | 退出码 | 含义 |
|------|--------|------|
| `selfcheck.py` | 0/1/2 | 0=无 FAIL；1=有 FAIL；2=脚本错误 |
| `diag_wizard.py [--paste]` | 0/1/2 | 0=报告有效且无 FAIL；1=有 FAIL（已给修复命令）；2=坏输入 |
| `onboard.py` | 0/1 | 0=全链路通过；1=有步骤失败 |
| `exp_ota_run.py` | 0 | 恒 0；无录制写空态 manifest |
