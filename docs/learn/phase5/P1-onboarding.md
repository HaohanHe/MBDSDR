# P1：真机一条命令跑通联调向导

> 对应 _PHASE5_SPEC.md §1 Wave1 P1。
> 脚本：`tools/onboarding/onboard.py`
> 测试：`tools/onboarding/test_onboarding.py`

## 0. 这是什么

`onboard.py` 是 MBDSDR 真机联调的**分步向导**，一条命令从"插好 RTL-SDR"走到"出图/出报文"：

```
detect（自检复用 hw_selfcheck）
  → capture（rtl_sdr 起流写 uint8 IQ）
  → record（uint8 → SigMF cf32_le，meta+data 与 playback.py 对齐）
  → decode（mbdsdr_ai 真实解码器：ADS-B / AX.25 / CW / APT）
  → output（报文转储 + 频谱 PNG + manifest，全标口径/参数/时间戳）
```

**红线**：无硬件时每步明确失败原因，绝不 mock、绝不假数据。数字全部真实自洽。

---

## 1. 快速开始

### 1.1 先自检（不需要收信号）

```bash
cd /path/to/MBDSDR
python3 tools/onboarding/onboard.py --step detect
```

无设备时输出（云 VM 上的真实样例）：

```
========================================================================
MBDSDR 真机联调向导  2026-10-01T23:48:13+08:00
========================================================================

[FAIL] step=detect
  → 未检测到 RTL-SDR 设备（0bda:2838/2832）
  下一步建议：
   - 确认 RTL-SDR 已插入 USB2.0 口（避免 USB3.0 Hub）
   - 执行 `lsusb | grep -E '0bda:(2838|2832)'` 复核
   - 若有设备但打不开：sudo modprobe -r dvb_usb_rtl28xxu
   - 插入 RTL-SDR (0bda:2838) 到 USB2.0 口，确认 lsusb 可见
   - 插入 GNSS 模块到 USB-TTL，确认 /dev/ttyUSB* 出现
   - 把当前用户加入 plugdev,dialout 组并重新登录
   - 安装 librtlsdr udev 规则（参考 repos/librtlsdr/rtl-sdr.rules）

========================================================================
失败 1 步：['detect']
========================================================================
```

退出码 `1`，`--json` 输出结构化 JSON（见 `--step detect --json`）。

### 1.2 全链跑通（有设备时）

#### 示例 A：ADS-B 飞机报文（1090 MHz，最简单）

```bash
python3 tools/onboarding/onboard.py \
    --step all \
    --freq 1090e6 \
    --mode adsb \
    --sr 2.4e6 \
    --n 240000
```

- 中心频率：1090 MHz（全球 ADS-B 通用）
- 采样率：2.4 MS/s（RTL-SDR 典型值）
- 采样点数：240,000 ≈ 0.1 秒（够收几帧飞机报文）
- 产物：`paper/experiments/onboarding_adsb_<timestamp>/` 下
  - `*_messages.txt` / `*_messages.json`：解码出的飞机报文
  - `spectrum__captured__adsb__N240000__*.png`：频谱图
  - `*_manifest.json`：运行清单（口径/参数/时间戳）

#### 示例 B：NOAA APT 气象卫星云图

```bash
python3 tools/onboarding/onboard.py \
    --step all \
    --freq 137.5e6 \
    --mode apt \
    --sr 2.4e6 \
    --n 4_800_000
```

- 频率：137.5 MHz（NOAA 15）；NOAA 18 = 137.9125 MHz，NOAA 19 = 137.1 MHz
- 采样点数：480 万 ≈ 2 秒（APT 一行约 0.5 s，2 s 能出几行）
- **注意**：APT 需要卫星过境 + 天线对准，录 2 s 可能不够；建议过境时录 30~60 s
- 产物：额外出 `apt_image__captured__apt__*.png`

#### 示例 C：CW 莫尔斯电报

```bash
python3 tools/onboarding/onboard.py \
    --step all \
    --freq 7.02e6 \
    --mode cw \
    --sr 2.4e6 \
    --n 2_400_000
```

- 频率：7.02 MHz（40 m 波段 CW 呼叫频率）；HF 需接室外天线
- 采样点数：240 万 ≈ 1 秒
- 产物：`*_messages.txt` 里是解码出的莫尔斯文本

#### 示例 D：AX.25 / APRS 报文

```bash
python3 tools/onboarding/onboard.py \
    --step all \
    --freq 144.39e6 \
    --mode ax25 \
    --sr 2.4e6 \
    --n 4_800_000
```

- 频率：144.39 MHz（中国 APRS 频率；国际 144.80 MHz）
- 采样点数：480 万 ≈ 2 秒
- 产物：`*_messages.txt` 里是 APRS 位置/状态报文

---

## 2. 分步单跑

只跑某一步（调试用）：

```bash
python3 tools/onboarding/onboard.py --step capture --freq 1090e6 --n 240000 --raw-path /tmp/test.bin
python3 tools/onboarding/onboard.py --step record --raw-path /tmp/test.bin --freq 1090e6 --mode adsb
python3 tools/onboarding/onboard.py --step decode --sigmf-data ./paper/experiments/xxx/xxx.sigmf-data --mode adsb --sr 2.4e6 --freq 1090e6
python3 tools/onboarding/onboard.py --step output --mode adsb --freq 1090e6 --n 240000
```

**--json 输出**：所有步骤都可加 `--json`，输出结构化 JSON（供上层管道消费）。

---

## 3. 输出产物说明

默认输出目录：`paper/experiments/onboarding_<mode>_<timestamp>/`

| 文件 | 说明 |
|---|---|
| `*.sigmf-data` | SigMF 复数 64-bit little-endian IQ（与 C++ recorder.cpp 格式一致） |
| `*.sigmf-meta` | SigMF JSON 元数据（采样率/中心频率/时间戳/增益/模式） |
| `*_messages.txt` | 解码报文人类可读转储 |
| `*_messages.json` | 解码报文机器可读 JSON |
| `spectrum__captured__*.png` | FFT 频谱图（图标口径 + N + 日期） |
| `apt_image__*.png` | APT 模式额外出云图 |
| `*_manifest.json` | 运行清单（口径/参数/时间戳/git sha/license） |

**口径标注**：所有产物均标 `data_origin=captured`（真机实时捕获），不与 synthetic / OTA 混淆。

---

## 4. 常见问题（FAQ）

### Q1：detect 步 FAIL："未检测到 RTL-SDR 设备"

**原因**：USB 没插好 / 被 dvb 内核驱动占用 / 权限不对。

**排查**：
```bash
lsusb | grep -E '0bda:(2838|2832)'     # 看设备是否枚举
dmesg | tail -20                         # 看 USB 枚举日志
sudo modprobe -r dvb_usb_rtl28xxu       # 卸载 DVB 占用驱动（仅建议）
```

**权限**：
```bash
sudo usermod -aG plugdev,dialout $USER   # 加组（重新登录生效）
# udev 规则（参考 repos/librtlsdr/rtl-sdr.rules）
```

### Q2：capture 步 FAIL："rtl_sdr 未写入任何数据"

**原因**：设备被其他进程占用（rtl_tcp / gqrx / dump1090）。

**排查**：
```bash
ps aux | grep -E 'rtl_|gqrx|dump1090'    # 看谁在占用
sudo kill <PID>                           # 杀掉占用进程
```

### Q3：capture 步有丢包（"lost at least N bytes"）

**原因**：USB 带宽 / 调度压力。

**建议**：
- 插 USB 2.0 口（不要用 USB 3.0 Hub）
- 降低采样率（2.4 M → 1.6 M）
- 关闭其他 USB 设备

### Q4：采样率越界报错

rtl-sdr 支持范围 **225 kHz ~ 3.2 MHz**。脚本会自动检查：
- `--sr 2.4e6`（2.4 MS/s）是推荐值
- ADS-B 需要 ≥ 2 MS/s
- APT 需要 ≥ 2 MS/s（带宽 ~34 kHz）

### Q5：decode 步 FAIL："0 有效帧"

**原因**：频率上没信号 / 信号太弱 / 模式与频率不匹配。

**建议**：
- ADS-B：确认 1090 MHz 附近有飞机飞过（城市区一般有）
- APRS：确认当地 APRS 频率（中国 144.39 MHz）
- CW：HF 需室外天线，室内一般收不到
- APT：必须卫星过境 + 天线对准，平时录不到正常

### Q6：云 VM / 无硬件能跑吗？

**能跑 detect 步**（诚实报无设备）。其他步会明确失败：
- capture：`未找到 rtl_sdr` 或 `rtl_sdr 未写入任何数据`
- record/decode/output：可通过 `--sigmf-data` 指定已录制的 SigMF 文件离线跑

**离线测试**见 `test_onboarding.py`（注入合成 IQ，验证全链路数字自洽）。

---

## 5. 红线与约束

- **无硬件绝不 mock**：detect 无设备 → FAIL；capture 无 rtl_sdr → FAIL
- **数字真实自洽**：采样点数 = 文件字节 / 2（uint8 raw）= 文件字节 / 8（cf32 SigMF）
- **MIT SPDX**：所有新文件头 `# SPDX-License-Identifier: MIT`
- **不改 hw_selfcheck 既有接口**：onboard.py 通过 subprocess 调 `selfcheck.py --json`，不 import 其内部函数
- **产物目录**：默认落 `paper/experiments/`（被 .gitignore，生成物）；文档受版本控制在 `tools/onboarding/` + `docs/learn/phase5/`

---

## 6. 退出码

| 退出码 | 含义 |
|---|---|
| 0 | 全部步骤 PASS |
| 1 | 至少一步 FAIL（含无设备时 detect FAIL） |
| 2 | 脚本自身参数错误 |
