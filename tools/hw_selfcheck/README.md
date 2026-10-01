<!--
SPDX-License-Identifier: MIT
-->

# MBDSDR 真机环境一键自检（hw_selfcheck）

只读探测本机是否具备运行 MBDSDR 三个端（`cpp/` Qt 桌面端、`mobile/` Flutter 端、
`mbdsdr_ai/` Python 原型）所需的 SDR / 声卡 / GNSS 串口 / 依赖环境。

> 设计红线：**只读设备与系统、不安装系统包、不改 udev**；无硬件时诚实报告，
> 绝不假造“检测到设备”。

## 快速开始

```bash
# 人类可读报告
python3 tools/hw_selfcheck/selfcheck.py

# 机器可读 JSON（供 CI / 上游 MCP 采集）
python3 tools/hw_selfcheck/selfcheck.py --json

# 调整子进程超时（默认 15s）
python3 tools/hw_selfcheck/selfcheck.py --timeout 20
```

退出码：

| 退出码 | 含义 |
|--------|------|
| 0 | 全部检查无 FAIL（允许 WARN / SKIP） |
| 1 | 至少一项 FAIL（典型：没插 SDR 设备） |
| 2 | 参数错误 / 脚本自身崩溃（已被兜底捕获，不会有 traceback） |

## 检查项一览

| # | 检查项 | 命中条件 | 关键命令 / 路径 |
|---|--------|----------|-----------------|
| 1 | USB/udev：RTL-SDR 枚举与权限 | lsusb 或 `/sys/bus/usb/devices` 中出现 `0bda:2838`（兼容 `0bda:2832`）；当前用户在 `plugdev`/`dialout` 组；`/lib/udev/rules.d` 或 `/etc/udev/rules.d` 有 0bda 规则 | `lsusb`（缺失则降级 `/sys` 扫描）、`grp.getgrall()`、glob `*.rules` |
| 2 | 设备枚举：`rtl_test -t` | 解析 tuner 型号（如 `Rafael Micro R820T`）与增益档数 | `~/.local/bin/rtl_test` 或 PATH 中的 `rtl_test`，超时 15s |
| 3 | 流读取：`rtl_sdr` 实读 ≤3s | 在 2.4 MS/s 下读 7.2M 采样点（≈3s）到临时文件，统计 `lost at least N bytes` | `~/.local/bin/rtl_sdr`，硬超时 11s，写 `/tmp/mbdsdr_selfcheck_*.bin` |
| 4 | 声卡 | `aplay -l` / `arecord -l` 列出播放/录音卡；兜底看 `/dev/snd` | `aplay`、`arecord`（缺失降级 WARN） |
| 5 | GNSS 串口 | 枚举 `/dev/ttyUSB*`、`/dev/ttyACM*`、`/dev/serial/by-id`；以 9600 / 115200 波特率读首行，判断是否 NMEA（`$GP/GN/...` 开头） | `termios` + `os.read`，只读 |
| 6 | 依赖 | `python3 -c "import rtlsdr / SoapySDR"`；`which gpsd / cgps` | `importlib.util.find_spec`、`shutil.which` |

每项输出三要素：

- **证据**（evidence）：脚本实际看到的一行行事实（命令输出、文件路径、组名）；
- **状态**（status）：`PASS` / `WARN` / `FAIL` / `SKIP`；
- **修复建议**（fix）：仅给出命令建议，**本工具不代为执行**。

## `--json` 输出结构

```jsonc
{
  "tool": "mbdsdr-hw-selfcheck",
  "version": "0.1.0",
  "timestamp": "2026-10-01T21:58:01+08:00",
  "host": "...",
  "os": "...",
  "python": "3.12.11",
  "checks": [
    {
      "name": "1. USB/udev：RTL-SDR 枚举与权限",
      "status": "FAIL",
      "evidence": ["..."],
      "fix": ["..."],
      "detail": {
        "target_hits": [],
        "user_groups": ["user"],
        "udev_rules_hit": []
      }
    }
    // ... 共 6 项
  ],
  "summary": {
    "counts": {"PASS": 0, "WARN": 3, "FAIL": 1, "SKIP": 2},
    "conclusion": "未检测到 RTL-SDR 真机设备；...",
    "real_machine_todo": ["..."]
  }
}
```

`detail` 字段为各检查项的结构化原始信息（设备列表、tuner 型号、丢包字节数、
临时文件大小等），方便上层 MCP / CI 直接取数。

## 在无硬件机器上的预期行为

云 VM / CI / 开发者本机（没插 RTL-SDR）跑出来应当是：

- 第 1 项 FAIL（没看到 `0bda:2838`）；
- 第 2、3 项 SKIP（USB 层未命中，不浪费时间跑 rtl_test/rtl_sdr）；
- 第 4、5、6 项 WARN（无声卡 / 无串口 / Python 绑定缺失）；
- 结论自动切到“未检测到 SDR 设备”，并打印**真机待办清单**。

整套流程不会因为找不到命令、找不到 `/dev`、找不到 `/sys` 而抛 traceback。

## 真机用户首次上线步骤

1. 插入 RTL-SDR (USB ID `0bda:2838`，RTL2838UHIDIR) 到 **USB 2.0 直连口**；
2. 插入 GNSS 模块到 USB-TTL（CP210x / CH340 / FT232）；
3. 一次性权限准备（需要 sudo，本工具不代做）：
   ```bash
   sudo usermod -aG plugdev,dialout $USER
   # 复制 librtlsdr 自带规则
   sudo cp repos/librtlsdr/rtl-sdr.rules /etc/udev/rules.d/99-rtlsdr.rules
   sudo udevadm control --reload && sudo udevadm trigger
   # 重新登录使组生效
   ```
4. 重跑 `python3 tools/hw_selfcheck/selfcheck.py`，期望：
   - 第 1 项 PASS（看到 `0bda:2838 — RTL2838UHIDIR`）；
   - 第 2 项 PASS（打印 tuner 型号 + 29 档增益）；
   - 第 3 项 PASS（3 秒实读无 `lost at least` 告警）；
   - 第 5 项 PASS（某 `/dev/ttyUSBx` @9600 报 `NMEA-OK`）。

## 运行测试（离线 / 确定性）

```bash
cd <repo_root>
python3 -m pytest tools/hw_selfcheck/test_selfcheck.py -v
```

测试通过 monkeypatch 注入假 `lsusb`/`rtl_test`/`rtl_sdr` 输出与假 `/sys` 树，
不接触真实硬件，不联网。覆盖：

- rtl_test 输出解析（tuner 型号、增益档数、设备列表、无设备分支）；
- rtl_sdr 丢包正则（`lost at least N bytes` 求和）；
- 假 `/sys` USB 树命中与非目标设备过滤；
- aplay/arecord 卡片解析；
- NMEA `$GP/$GN/...` 判定；
- 全缺失环境（无 lsusb / 无 /sys / 无 /dev / 无 Python 绑定）下 `main()` 不 traceback，
  并产出合法 JSON。

## 安全红线（已在代码里落实）

- 全程只读：只 `open()` 读 `/sys`、`/dev`；`rtl_sdr` 仅写 `tempfile.mkdtemp` 下的临时文件，跑完即删；
- 不 `sudo`、不 `apt install`、不写 `/etc/udev/rules.d`（只把建议命令打印出来）；
- `rtl_sdr` 硬上限 = 3 秒采集 + 8 秒启动/收尾余量，超时由 `subprocess.run(timeout=...)` 兜底；
- 任何外部命令缺失 / 异常都被 `run_cmd()` 吞掉，降级为证据行，不冒泡。

## 许可

MIT。详见仓库根目录 `LICENSE` 与各文件头部 `SPDX-License-Identifier: MIT`。
