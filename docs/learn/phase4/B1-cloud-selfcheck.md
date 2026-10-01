<!--
SPDX-License-Identifier: MIT
-->

# B1 · 真机环境一键自检工具 — 云侧基线报告

> 阶段：Phase 4 / B1
> 日期：2026-10-01（Asia/Shanghai）
> 执行者：云 VM 沙箱（无 RTL-SDR、无 GNSS、无音频设备）
> 脚本：`tools/hw_selfcheck/selfcheck.py`（Python 3.12，纯 stdlib）
> 测试：`tools/hw_selfcheck/test_selfcheck.py`（pytest，离线，14/14 通过）

本报告记录云 VM 上实跑自检脚本得到的**基线结果**，作为后续真机（用户本地插好
RTL-SDR 0bda:2838 + GNSS 模块 + 天线）联调时的对照基准。云 VM 本身不具备
SDR/GNSS 硬件，所有与硬件相关的项按预期输出 FAIL/WARN/SKIP，不做任何掩饰。

---

## 1. 云 VM 环境事实（实跑前手动勘察）

| 项 | 观测值 | 备注 |
|----|--------|------|
| 主机名 | `vefaas-hwz9n2xb-hvujuvt976-dav6c90209ovvvl97ecg-sandbox` | 沙箱容器 |
| 内核 | `Linux 6.6.95.bck.2-rc1-amd64 x86_64` | glibc 2.35 |
| Python | `3.12.11`（`/opt/python3.12/bin/python3`） | 系统自带 |
| 当前用户 / 组 | `user(1234)`，仅在 `user` 组 | **不在** `plugdev`/`dialout` |
| `lsusb` | 缺失 | 脚本自动降级为 `/sys/bus/usb/devices` 扫描 |
| `aplay` / `arecord` | 缺失（无 alsa-utils） | 脚本降级 WARN |
| `gpsd` / `cgps` / `gpsmon` | 缺失 | 脚本降级 WARN |
| `rtl_test` / `rtl_sdr` | **存在**于 `~/.local/bin/` | 仓库 `repos/librtlsdr` 已编译安装到用户目录 |
| `/sys/bus/usb/devices/` | 空目录 | 无 USB 设备暴露到容器 |
| `/dev/ttyUSB*`、`/dev/ttyACM*`、`/dev/serial/` | 不存在 | 无串口 |
| `/dev/snd/` | 不存在 | 无声卡 |
| `/dev/dvb/` | 不存在 | 无 DVB 设备节点 |
| `/lib/udev/rules.d/` | 存在但无 rtl-sdr 规则 | 脚本仅读取，不写入 |
| Python 绑定 `rtlsdr` / `SoapySDR` | 均 `importlib.util.find_spec` 返回 None | 未 pip 安装 |

**结论性事实**：云 VM 是一台“裸软件容器”——librtlsdr 命令行工具链已就位，
但没有任何硬件、没有音频、没有串口、没有 Python SDR 绑定。这正是检验
自检脚本“无硬件环境能否诚实、稳定、不崩”的理想场景。

---

## 2. 实跑结果（人类可读）

命令：

```bash
cd /home/user/Doubao/chats/38438160041798146
python3 tools/hw_selfcheck/selfcheck.py
echo "EXIT=$?"
```

退出码：**1**（存在 1 项 FAIL，符合预期）。

完整输出（去除 ANSI 颜色码后）：

```
========================================================================
MBDSDR 真机环境自检  2026-10-01T22:00:34+08:00
主机：vefaas-hwz9n2xb-...-sandbox
OS：Linux-6.6.95.bck.2-rc1-amd64-x86_64-with-glibc2.35  Python：3.12.11
========================================================================

[FAIL] 1. USB/udev：RTL-SDR 枚举与权限
  lsusb 不可用：lsusb not found（降级为 /sys 扫描）
  未在 lsusb / /sys/bus/usb 中找到 0bda:2838 (RTL2838UHIDIR) 或 0bda:2832
  当前用户组：['user']（不含 plugdev/dialout）
  未在 /lib/udev/rules.d 或 /etc/udev/rules.d 找到含 0bda:2838/2832 的规则

[SKIP] 2. 设备枚举：rtl_test -t（tuner 型号 / 增益档数）
  rtl_test 路径：/home/user/.local/bin/rtl_test
  USB 层未命中 0bda:2838/2832，跳过 rtl_test（运行也会报 No supported devices）

[SKIP] 3. 流读取：rtl_sdr 实读 ≤3s（2.4 MS/s）丢包统计
  USB 层未命中设备，跳过实读（rtl_sdr 会直接报错退出）

[WARN] 4. 声卡：aplay -l / arecord -l
  aplay / arecord 均不可用（alsa-utils 未安装）
  /dev/snd 不存在（容器/无头云主机常见，真机一般会有）

[WARN] 5. GNSS 串口：/dev/ttyUSB*,/dev/ttyACM*,/dev/serial/by-id
  未发现任何 /dev/ttyUSB* /dev/ttyACM* 或 /dev/serial/by-id 条目

[WARN] 6. 依赖：pyrtlsdr / SoapySDR / gpsd
  python3 版本：3.12.11 (/opt/python3.12/bin/python3)
  import rtlsdr     : MISSING
  import SoapySDR   : MISSING
  which gpsd        : MISSING
  which cgps/gpsmon : MISSING

========================================================================
汇总：PASS=0  WARN=3  FAIL=1  SKIP=2
结论：未检测到 RTL-SDR 真机设备；本机适合做软件/CI 自检，
      真机联调请在插好硬件的机器上重跑。
========================================================================
```

### 2.1 `--json` 机器可读结果

命令 `python3 tools/hw_selfcheck/selfcheck.py --json` 产出合法 JSON，顶层字段：

```json
{
  "tool": "mbdsdr-hw-selfcheck",
  "version": "0.1.0",
  "timestamp": "2026-10-01T22:00:34+08:00",
  "host": "vefaas-hwz9n2xb-...",
  "os": "Linux-6.6.95.bck.2-rc1-amd64-x86_64-with-glibc2.35",
  "python": "3.12.11",
  "checks": [ /* 6 项 CheckResult */ ],
  "summary": {
    "counts": {"PASS": 0, "WARN": 3, "FAIL": 1, "SKIP": 2},
    "conclusion": "未检测到 RTL-SDR 真机设备；本机适合做软件/CI 自检，真机联调请在插好硬件的机器上重跑。",
    "real_machine_todo": [
      "插入 RTL-SDR (0bda:2838) 到 USB2.0 口，确认 lsusb 可见",
      "插入 GNSS 模块到 USB-TTL，确认 /dev/ttyUSB* 出现",
      "把当前用户加入 plugdev,dialout 组并重新登录",
      "安装 librtlsdr udev 规则（参考 repos/librtlsdr/rtl-sdr.rules）",
      "重跑本脚本，期待第 1/2/3 项变 PASS"
    ]
  }
}
```

### 2.2 基线状态矩阵

| # | 检查项 | 云 VM 基线 | 真机预期 |
|---|--------|------------|----------|
| 1 | USB/udev 枚举与权限 | **FAIL**（无设备 + 无组 + 无规则） | PASS（看到 0bda:2838 + 在 plugdev 组 + 规则就位） |
| 2 | rtl_test -t 设备枚举 | **SKIP**（无设备） | PASS（打印 `Found Rafael Micro R820T tuner` + `Supported gain values (29): ...`） |
| 3 | rtl_sdr ≤3s 实读 | **SKIP**（无设备） | PASS（临时文件 ≈ 14.4 MB，无 `lost at least`） |
| 4 | 声卡 | **WARN**（无 alsa-utils + 无 /dev/snd） | PASS（`aplay -l` 至少 1 个播放卡） |
| 5 | GNSS 串口 | **WARN**（无 /dev/ttyUSB*） | PASS（某 ttyUSB@9600 报 NMEA-OK） |
| 6 | Python/工具依赖 | **WARN**（rtlsdr/SoapySDR/gpsd 全缺） | WARN→PASS（`pip install pyrtlsdr` 后 rtlsdr 项变 OK） |

---

## 3. 真机用户应执行的步骤与预期输出

真机 = 用户本地 Linux 机器（插好 RTL-SDR USB 狗、GNSS 模块、天线）。按顺序：

### 3.1 一次性环境准备（需要 sudo，脚本不代做）

```bash
# 1) 用户组
sudo usermod -aG plugdev,dialout $USER

# 2) udev 规则（直接复用仓库里 librtlsdr 自带的）
sudo cp /home/user/Doubao/chats/38438160041798146/repos/librtlsdr/rtl-sdr.rules \
        /etc/udev/rules.d/99-rtlsdr.rules
sudo udevadm control --reload
sudo udevadm trigger

# 3) 安装 alsa-utils（可选，声卡检查用）
sudo apt install -y alsa-utils

# 4) 退出并重新登录，让组生效
exit
```

### 3.2 重新登录后插硬件

1. 把 RTL-SDR 插到**主板后置 USB 2.0 直连接口**（避免 USB3.0 口 + Hub，这是
   librtlsdr 丢包的最常见原因）。
2. GNSS 模块插到任意 USB 口（CP210x/CH340/FT232 都会枚举为 `/dev/ttyUSBx`）。
3. 接好天线。

### 3.3 跑自检，对照预期

```bash
cd /home/user/Doubao/chats/38438160041798146
python3 tools/hw_selfcheck/selfcheck.py
```

**预期关键输出**（按出现顺序）：

1. 第 1 项变 `PASS`：
   ```
   [PASS] 1. USB/udev：RTL-SDR 枚举与权限
     命中 USB 设备 0bda:2838 — RTL2838UHIDIR (Realtek RTL2838UHIDIR)
     当前用户组：['user', 'plugdev', 'dialout']
     已存在 RTL-SDR udev 规则：['/etc/udev/rules.d/99-rtlsdr.rules']
   ```
2. 第 2 项变 `PASS`：
   ```
   [PASS] 2. 设备枚举：rtl_test -t（tuner 型号 / 增益档数）
     tuner 型号：Rafael Micro R820T
     增益档数：29（示例：['0.0', '0.9', '1.4', '2.7', '3.7', '7.7']）
   ```
3. 第 3 项变 `PASS`：
   ```
   [PASS] 3. 流读取：rtl_sdr 实读 ≤3s（2.4 MS/s）丢包统计
     临时文件：/tmp/mbdsdr_selfcheck_xxxx.bin 写入 14400000 字节
     3 秒实读无丢包上报
   ```
4. 第 5 项变 `PASS`（GNSS 定位前也可能只读到部分 NMEA，属于正常）：
   ```
   [PASS] 5. GNSS 串口：...
     /dev/ttyUSB0 @9600: NMEA-OK — matched NMEA line: $GNRMC,...
   ```

退出码回到 `0` 即代表真机环境就绪，可以开始跑 cpp/ 桌面端或 mbdsdr_ai/ 原型。

---

## 4. 阻塞项与待办

### 4.1 云 VM 上的阻塞（不影响交付）

| 阻塞 | 影响 | 处置 |
|------|------|------|
| 无 lsusb / 无 /sys 暴露 | 第 1 项只能靠 /sys 兜底（且 /sys 为空） | 真机上 lsusb 一般默认装，自动恢复 |
| 无 /dev/snd | 第 4 项 WARN | 真机上内核自带 snd-hda-intel/snd-usb-audio |
| 无 /dev/ttyUSB* | 第 5 项 WARN | 真机插 GNSS 后自动出现 |
| `rtlsdr`/`SoapySDR` Python 绑定缺失 | mbdsdr_ai Python 原型跑不起来 | 真机按需 `pip install pyrtlsdr`（脚本已在修复建议里给出） |
| 无 gpsd | GNSS NMEA 守护缺失 | 真机按需 `sudo apt install gpsd gpsd-clients` |

以上全部是**环境层面的缺失**，脚本本身行为完全符合预期（诚实降级、不崩、
不伪造设备）。

### 4.2 后续真机联调待办

- [ ] 在真机（用户本地 Linux）上重跑 `selfcheck.py`，把输出归档为
      `docs/learn/phase4/B1-realdevice-selfcheck.md`（与本报告对照）。
- [ ] 若第 3 项出现 `lost at least >0 bytes`：
  - 换 USB2.0 直连接口；
  - 把采样率从 2.4 MS/s 降到 1.92 MS/s 复测；
  - 用 `rtl_test` 长跑 1 分钟看 `Samples per million lost`。
- [ ] 若第 5 项 NMEA 不出来：
  - 用 `stty -F /dev/ttyUSB0 9600 raw -echo; cat /dev/ttyUSB0` 肉眼看；
  - 换 38400/4800 波特率复测（脚本目前只试 9600/115200）。
- [ ] 等 cpp/ 桌面端跑通后，把 `selfcheck.py --json` 接进 MCP server，作为
      “环境体检”工具对外暴露。

---

## 5. 自检脚本本身的红线核对

| 红线要求 | 落实位置 | 云 VM 实跑结果 |
|----------|----------|----------------|
| 只读设备与系统 | 仅 `open()` 读 `/sys`/`/dev`；rtl_sdr 写 `tempfile` 跑完即删 | 无任何写 `/etc`/`/dev` 操作 |
| 不安装系统包 | 脚本无 `apt`/`pip install` 调用 | 未触发 |
| 不改 udev | 仅在 `fix` 列表打印 `sudo cp ... && udevadm ...` 建议 | `/etc/udev/rules.d` 未被修改 |
| 无硬件时诚实报告 | USB 未命中 → 第 2/3 项 SKIP，结论明确写“未检测到 RTL-SDR” | 已验证 |
| 命令缺失不 traceback | `run_cmd()` 统一吞 `FileNotFoundError`/`TimeoutExpired` | lsusb/aplay/arecord/gpsd 全缺，仍跑完 6 项 |
| rtl_sdr 限时 ≤3s | `-n 7200000`（3s @ 2.4MS/s）+ `subprocess timeout=11s` 兜底 | 云 VM 上 SKIP 未触发；真机将走此路径 |
| MIT SPDX 头 | `selfcheck.py` / `test_selfcheck.py` / 本文件均带 `SPDX-License-Identifier: MIT` | 已核对 |

---

## 6. 复现命令

```bash
cd /home/user/Doubao/chats/38438160041798146

# 1) 人类报告
python3 tools/hw_selfcheck/selfcheck.py

# 2) JSON
python3 tools/hw_selfcheck/selfcheck.py --json

# 3) 离线测试
python3 -m pytest tools/hw_selfcheck/test_selfcheck.py -v
# 期望：14 passed
```

---

## 7. 交付物清单（本次新增文件）

| 绝对路径 | 用途 |
|----------|------|
| `/home/user/Doubao/chats/38438160041798146/tools/hw_selfcheck/selfcheck.py` | 主入口（stdlib-only，~470 行） |
| `/home/user/Doubao/chats/38438160041798146/tools/hw_selfcheck/README.md` | 使用说明、检查项表、真机步骤 |
| `/home/user/Doubao/chats/38438160041798146/tools/hw_selfcheck/test_selfcheck.py` | pytest 离线测试（14 用例） |
| `/home/user/Doubao/chats/38438160041798146/docs/learn/phase4/B1-cloud-selfcheck.md` | 本报告 |

未执行 `git commit` / `git push`，等待人工审阅后再决定是否入库。
