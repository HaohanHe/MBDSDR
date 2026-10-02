# P1 依赖完备性核对表（真机）

> 范围：`tools/hw_selfcheck/selfcheck.py`、`tools/onboarding/onboard.py`、
> `experiments/exp_ota_run.py` 三者在**真机（Linux + RTL-SDR）**上跑通一条完整链路
> 所需的全部依赖。逐项给出：缺失症状 → 影响哪一步 → 修复命令。
> 对照源码逐项核对（2026-10-02，基线 commit d82247f）。
>
> 红线：本脚本群**只读探测、绝不自动安装**；下表命令由用户复制执行。

## 一、必装依赖清单（缺了就跑不通）

| # | 依赖 | 提供方 | 缺了的症状（selfcheck/onboard 输出） | 影响步骤 | 修复命令（复制执行） |
|---|------|--------|--------------------------------------|----------|----------------------|
| 1 | `rtl_sdr` / `rtl_test` 二进制 | librtlsdr（包名 `rtl-sdr`，或 `repos/librtlsdr/` 源码编译） | check2/check3 WARN「未找到 rtl_test/rtl_sdr」；onboard capture FAIL「未找到 rtl_sdr 命令」 | selfcheck 第 2/3 项、onboard capture | `sudo apt install rtl-sdr`（Debian/Ubuntu）；源码：`cd repos/librtlsdr && mkdir build && cd build && cmake .. && make && sudo make install` |
| 2 | USB 枚举可见 `0bda:2838`（或 `0bda:2832`） | RTL-SDR 硬件本身 | check1 FAIL「未在 lsusb / /sys/bus/usb 中找到 0bda:2838/2832」 | selfcheck 第 1 项；其后 2/3 自动 SKIP；onboard detect FAIL | `lsusb \| grep -E '0bda:(2838\|2832)'` 复核；换主板后置 **USB2.0 直连口**、换线（见故障路径表 P-1） |
| 3 | udev 规则（0bda:2838/2832 → 0660/0666） | librtlsdr 自带 `rtl-sdr.rules`，或手写 | check1 WARN「未找到含 0bda:2838/2832 的规则」；真机表现：`rtl_sdr` 报 `Permission denied` | 设备重插/重启后无读权限 | 见下方「udev 规则一键写入块」 |
| 4 | 当前用户在 `plugdev`、`dialout` 组 | 系统用户组 | check1 FAIL「当前用户不在 plugdev/dialout 组」；GNSS 串口 `PermissionError` | check1；GNSS 串口读取（check5） | `sudo usermod -aG plugdev,dialout $USER`（**注销重新登录**生效） |
| 5 | Python 3 标准库 + `numpy` | Python ≥3.8 / PyPI | onboard 启动即 `ModuleNotFoundError: No module named 'numpy'`；exp_ota_run 同理 | onboard record/decode/output 全链；exp_ota_run | `python3 -m pip install --user numpy` |
| 6 | 仓库内 `mbdsdr_ai` 包可 import | 仓库本身（`mbdsdr_ai/`） | onboard decode FAIL「解码器模块导入失败」 | onboard decode（adsb/ax25/cw/apt）、exp_ota_run 全部指标 | 在**仓库根目录**下运行命令；确认 `ls mbdsdr_ai/__init__.py` 存在 |

### udev 规则一键写入块（对应 #3）

```bash
sudo tee /etc/udev/rules.d/99-mbdsdr-rtlsdr.rules >/dev/null <<'EOF'
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2838", MODE="0660", GROUP="plugdev"
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2832", MODE="0660", GROUP="plugdev"
EOF
sudo udevadm control --reload && sudo udevadm trigger
```

## 二、可选依赖（缺了降级为 WARN/跳过，不阻断首跑）

| # | 依赖 | 缺了的症状 | 影响 | 修复命令 |
|---|------|-----------|------|----------|
| 7 | Python 绑定 `pyrtlsdr`（`import rtlsdr`） | check6 WARN「import rtlsdr: MISSING」 | 仅影响 mbdsdr_ai Python 原型；**不影响** C++/Qt 桌面端与 onboard 命令行 | `python3 -m pip install --user pyrtlsdr`（或 `sudo apt install python3-rtlsdr`） |
| 8 | Python 绑定 `SoapySDR` | check6 WARN「import SoapySDR: MISSING」 | mbdsdr_ai 原型用到 SoapySDR 后端时 | `python3 -m pip install --user SoapySDR` |
| 9 | `gpsd` + `gpsd-clients`（cgps/gpsmon） | check6 WARN「which gpsd: MISSING」 | GNSS 授时守护；不接 GNSS 时无影响 | `sudo apt install gpsd gpsd-clients` |
| 10 | `alsa-utils`（aplay/arecord） | check4 WARN「aplay / arecord 均不可用」 | 声卡检测与 SSTV/SSB 监听回放；RTL-SDR IQ 采集不依赖 | `sudo apt install alsa-utils` |
| 11 | `matplotlib`（Agg 无头后端） | onboard output 打印「(matplotlib 不可用，跳过频谱图)」 | 缺频谱 PNG，其余产物正常 | `python3 -m pip install --user matplotlib` |
| 12 | `Pillow` + `mbdsdr_ai.image_enhance` | onboard apt 模式打印「(图像增强/PIL 不可用，跳过 APT 图像保存)」 | 仅 APT 云图 PNG 落盘 | `python3 -m pip install --user Pillow` |
| 13 | `lsusb`（usbutils） | check1 提示「lsusb 不可用，降级为 /sys 扫描」 | 仅影响交叉验证，/sys 扫描兜底 | `sudo apt install usbutils` |
| 14 | GNSS 模块（USB-TTL 桥，输出 NMEA） | check5 WARN「未发现 /dev/ttyUSB*」 | 可选增强：不接时 SigMF 时间戳自动退化为系统 UTC 并如实标注 `time_source=system` | 插模块后 `ls /dev/ttyUSB*` 复核 |

## 三、常见失败路径表（真机高频坑）

| 编号 | 症状（你看到的输出） | 根因 | 修复动作 |
|------|----------------------|------|----------|
| P-1 | `lsusb` 里没有 `0bda:2838` | USB 线坏 / 插在 USB3.0 Hub / 前置口供电不足 | 换**主板后置 USB2.0 直连口**；换带数据功能的线；`dmesg \| tail -20` 看枚举报错 |
| P-2 | `lsusb` 有设备，但 `rtl_sdr` 报 `Permission denied` | 无 udev 规则或用户不在 plugdev 组 | 执行第一节 udev 写入块 + `sudo usermod -aG plugdev,dialout $USER` 后**重新登录** |
| P-3 | `rtl_test -t` 报 `No supported devices found` | 内核 `dvb_usb_rtl28xxu`（DVB-T 电视驱动）抢先绑定了设备 | `sudo modprobe -r dvb_usb_rtl28xxu`；重插 USB 后复测 |
| P-4 | onboard capture 写出空文件 / check3「未采到任何 IQ 数据」 | 设备被其他进程占用（rtl_tcp / gqrx / dump1090） | `pgrep -a rtl_tcp; pgrep -a gqrx; pgrep -a dump1090` 找占用者并杀掉；拔出重插 |
| P-5 | stderr 反复出现 `lost at least N bytes` | USB 带宽/调度压力（多为 USB3.0 口/Hub） | 换 USB2.0 直连口；必要时把采样率降到 `--sr 1_200_000` |
| P-6 | onboard capture FAIL「采样率超出 rtl-sdr 范围 (225k~3.2M)」 | `--sr` 单位/数值错 | 改回 `--sr 2400000` |
| P-7 | onboard capture FAIL「中心频率超出 RTL-SDR 范围 (24~1700 MHz)」 | `--freq` 单位错（写成了 MHz 而非 Hz） | 用 Hz 科学计数：`--freq 1090e6`（=1090 MHz） |
| P-8 | decode 步 0 有效帧（ADS-B 无帧 / APT 无图） | 频率上当前无信号、增益不对、天线没接 | 先确认天线接好；ADS-B 换开阔位置；APT 查 NOAA 过境时间；`--gain` 从 24 dB 往高调 |
| P-9 | 云 VM / 容器里 selfcheck 第 1/2/3 项全 FAIL/SKIP | **没有硬件**，属预期 | 这不是故障：在插好设备的真机上重跑即可；云内跑是为了验证脚本不崩 |
| P-10 | onboard record 报「raw 文件字节数偏差 >2%」 | 严重丢包导致 | 同 P-5；换口后整链重跑 |

## 四、一分钟自检顺序

```bash
# 1. 二进制在不在
which rtl_sdr rtl_test || sudo apt install rtl-sdr

# 2. 设备枚举到没
lsusb | grep -E '0bda:(2838|2832)'

# 3. 权限组（重新登录后生效）
sudo usermod -aG plugdev,dialout $USER

# 4. udev 规则（见第一节块）

# 5. 一键自检 + 诊断向导（本表全部项的自动化版）
python3 tools/hw_selfcheck/selfcheck.py --json | python3 tools/diag_wizard.py
```

> 跑完第 5 步，向导会按上表逐项把问题翻译成可复制命令；全部 PASS 后它会直接给出
> `onboard.py --step all ...` 一条命令。
