<!-- SPDX-License-Identifier: MIT | docs/learn：外部 agent 带用户玩转 MBDSDR 真机的 onboarding 剧本，素材取自 2026-10-13 一晚跑通的真实 session -->

# 外部 agent 带用户玩转 MBDSDR 真机（可复现剧本）

> 角色分工：**外部 agent** = 能跑 shell、能调 MCP 工具的智能体，负责一切敲键盘的事；
> **用户** = 照本宣科做物理操作（插线、手持天线、按确认键）的人。素材：NanoVNA-H（固件 1.2.50，Windows COM 口）+ RTL2832U/FC0012 电视棒 + RH770 拉杆天线，2026-10-13 当晚真机验证，命令均可直接复制执行。

## 0. 边界（先读）

- 剧本以"agent 看到真实测量数据"为止；换频段、天线、设备照各节重跑即可。
- 写操作（connect/set_frequency/set_demod/set_gain/VNA 扫描）走 MCP 写工具手动门
  `arm_manual=true`；agent 只向用户口述"请确认"，不得代替用户确认。
- 无设备、无信号时工具一律空态或明确报错；agent 不得补话术、圆场。

## 1. 阶段 0：环境基线

```bash
python tools/hw_selfcheck/selfcheck.py            # 人类可读报告
python tools/hw_selfcheck/selfcheck.py --json     # 机器可读（回传/贴 issue 用）
```

诚实读法：**FAIL = 阻断项**（照 fix 行修）；**WARN = 不阻断但建议修**（如缺 rtl_test）；
**SKIP = 上游前置未过、本项没条件跑**（USB 未命中时第 2/3 项会 SKIP，不是错误）。

- **Linux 目标**：`0bda:2838`（RTL2838UHIDIR）为主机，`0bda:2832`（RTL2832U OEM）兼容；
  GNSS 项按 `$GP`/`$GN` 等 NMEA 前缀判活，没有 GNSS 模块时该项 WARN，与 SDR 面无关。
- **Windows 上 FAIL 很常见且不致命**：脚本本身有 WARN/SKIP 兜底（绝不抛 traceback），
  但 USB 检查会因无 `lsusb`/`/sys`、声卡因无 `aplay`、组检查因无 `pwd/grp` 而降级。
  Windows 的判定以第 3.1/3.3 节的直接握手为准。

本自检是 **RTL-SDR 向**（USB 0bda:2838、rtl_test tuner、rtl_sdr 3 秒实读丢包），
**不代表 NanoVNA 面**——VNA 是 CDC 虚拟串口（0483:5740），另走第 2 节。

## 2. NanoVNA 路径

### 2.1 探测设备（VID:PID = 0483:5740）

```bash
python -c "from mbdsdr_ai.nanovna_client import find_nanovna_ports as f; print(f())"
```

Windows 上形如 `[('COM11', 1155, 22336)]`，Linux 是 `[('/dev/ttyACM0', ...)]`（1155=0x0483、22336=0x5740）；空列表 = 未枚举到（查线、查口），**不要**进入下一步。权限侧：Linux 需 dialout/plugdev 组或 udev 规则，Windows 只要不并发开多个进程即可。

### 2.2 一次打开，把活干完（Windows 串口纪律）

本机实测：Python 侧 Serial close→reopen 后第二次 `connect()` 抛 `PermissionError`
（CLI 路径 `python -m mbdsdr_ai.nanovna_dump` 复现率 100%；换进程裸开 `serial.Serial()`
就没事，根因疑与 usbser.sys 独占共享模式相关，见 feature-parity.md 登记）。变通与纪律：

- 进程内**预开 SerialTransport 注入**、跳过重复 `connect()`（当晚 6/6 成功）；
- 因此一次连接内**按顺序做完 sweep → 读数 → 截图**，不要"连一下、关一下、再连一下"。

```bash
python -m mbdsdr_ai.nanovna_dump --port COM11 \
    --start 430000000 --stop 440000000 --points 101 \
    --out sweep.json --screenshot screen.bmp
```

产物 `sweep.json` 含 `model/version/sn/cal/sweep/frequencies_hz/S11/S21/vswr/return_loss_db/tcxo_hz`；`--port` 省略则自动探测（探测不到会诚实报错并给权限提示）。

### 2.3 测量流程：sweep → 谐振 → TDR

1. `sweep`：一段频率采 101 点 S11/S21（`--start/--stop/--points`）。
2. 谐振：|Z| 极小点定 fr，实部得 ESR，半功率带宽得 Q（`analyze_vna_resonance`）。
3. TDR：S11 窗化 IFFT 找反射峰估缆长（`vna_tdr_cable`，velocity_factor 必填，0<vf≤1）。

### 2.4 --screenshot 与 tcxo_hz

- `--screenshot <path>`：抓设备当前屏幕存 BMP（153600 字节 RGB565 裸帧 → 24bit BMP），失败为诚实错误信封，不影响测量数据；用途是回证"设备上停在哪一发扫描"。
- `tcxo_hz`：回读晶振频率（本机 `current: 26000000`，即 26.000000MHz）；老固件解析不出时字段缺失，**不伪造 0**。

### 2.5 校准口径必须显式标注

本机设备只做过 **load + isolation** 校准（未做 open/short）。后果：该口径下
**VSWR 深度有系统误差**。引用任何 VSWR 数字必须连带标注口径
（"设备校准 = load+isolation，未全项 OSL"）；未校准数据默认不可信。
校准状态可用 `vna_calibration` 的 `step=status` 只读引导客观取，不靠猜。

## 3. RTL-SDR 路径

### 3.1 Zadig/WinUSB 绑定检查

电视棒默认绑 `usbser`，Osmocom 二进制打不开。确认驱动服务名：

```powershell
Get-PnpDevice | Where-Object InstanceId -match 'VID_0BDA&PID_283[28]' | ForEach-Object {
    ($_ | Get-PnpDeviceProperty -KeyName 'DEVPKEY_Device_Service').Data
}
```

回 `usbser` = 未绑：Zadig → Options 列出全部设备 → 选该棒 → 替换驱动为 **WinUSB**；
回 `WinUSB` = 已绑，继续。

### 3.2 官方 Osmocom Windows 二进制 → C:\rtl-sdr\bin

取 <https://ftp.osmocom.org/binaries/windows/rtl-sdr/> 最新 `rtl-sdr-64bit-*.zip`
（当晚用 20261011 构建），解到 **C:\rtl-sdr\bin**——这是 `mbdsdr_ai/windows_setup.py`
默认 DLL 搜索路径之一（其余有 PATH、`%USERPROFILE%\rtl-sdr\bin`、SDR#/SDR++ 解压目录；
`MBDSDR_RTLSDR_DIR` 可覆盖）。安全红线：不自动下载二进制，只手动取、本地解。

```powershell
Expand-Archive -Path "$HOME\Downloads\rtl-sdr-64bit-*.zip" -DestinationPath C:\rtl-sdr
Test-Path C:\rtl-sdr\bin\rtlsdr.dll, C:\rtl-sdr\bin\libusb-1.0.dll   # 应均为 True
```

### 3.3 rtl_test -t 握手

```powershell
C:\rtl-sdr\bin\rtl_test.exe -t
```

见到 `Found ... tuner`（本机 FC0012）与设备 SN 行 = 枚举与驱动 OK。同一时刻只允许一个
程序持有棒子：gqrx/SDR#/rtl_tcp 实例互相排斥——"rtl_test 说没设备但 Zadig 明明绑好了"
多半是别的程序正占着。

### 3.4 rtl_tcp 后台起服务（单客户端纪律）

```powershell
Start-Process -NoNewWindow C:\rtl-sdr\bin\rtl_tcp.exe -ArgumentList '-a','127.0.0.1','-p','1234'
```

rtl_tcp 是**单客户端协议**（listen backlog=1）：同一源同一时刻只允许一个连接，断开后需 **2-3s 冷却**才能重连——重连失败先等三秒，别先怪网络。

### 3.5 env 登记 → MCP 即可见

```powershell
$env:MBDSDR_RTL_TCP = "127.0.0.1:1234"   # 本进程生效；要全局用 setx
python -m mbdsdr_ai.core.mcp_server      # stdio MCP，agent 的接入面
```

登记后 `list_devices` 返回形如 `{"name": "rtl_tcp 127.0.0.1:1234", "driver": "rtltcp", ...}`，
`device_id` = `rtl_tcp_127.0.0.1_1234`。注意 **enumerate 不联网**：服务没起时 `connect` 由后端诚实报"连接失败"，绝不伪造 IQ。

## 4. Agent 遥控标准序列（MCP 工具调用）

```text
list_devices                                  -> 拿 device_id
connect(device_id="rtl_tcp_127.0.0.1_1234", arm_manual=true)
set_frequency(hz=435274000, arm_manual=true)
set_demod(mode="FM", arm_manual=true)
set_gain(db=20, arm_manual=true)
get_status                                     -> 回读（读工具不过门）
```

当晚实测回读（435.274MHz / FM / 20dB）：

```json
{"connected": true, "device": "rtl_tcp 127.0.0.1:1234",
 "frequency_hz": 435274000.0, "demod": "FM", "gain_db": 20.0}
```

不带 `arm_manual` 的写调用返回 `{"ok": false, "gated": true}`——这是**手动门拦截**，不是执行失败，也不代表设备有问题。

## 5. 天线测量卫生课（用户操作，agent 口述）

- **手持垂直、悬空、远离金属**：笔记本外壳不算"机箱"，也是金属，同样远离。
- 反例（真实案例）：拉杆天线**躺平**在桌面测，谐振整个测没——VSWR 从 2.1 恶化到
  3.3+ 全平。执行顺序：**先修姿势，再怪天线**。
- 拉杆天线长度↔谐振标定法（RH770，手持垂直实测）：

| 拉杆状态 | 谐振频率 |
|---|---|
| 8 节（缩） | < 380 MHz |
| 中长 | 408.2 MHz |
| 全拉出 | 419.5 MHz |

结论：该拉杆够不到 435 MHz。宁可换天线/加匹配，**不要**把 419.5 的谐振说成"435 段天线谐振"。

## 6. 诚实红线

- 无设备/无信号：工具必须空态（如 `aircraft=[]`）或明确报错；不得把空态说成"信号弱"、
  把报错说成"设备没问题"。
- 未校准数据标口径（见 2.5）：校准状态、手持/躺平等姿势都是测量条件，与频率、带宽
  一样必须随数据走。
- 排查次序：**姿势 → 馈线/接头 → 校准 → 天线本体**，最后才怀疑设备；`connect` 失败先看
  rtl_tcp 起没起、单客户端是否被占，等 2-3s 重试一次再下结论。
- ppm 未校（FC0012 晶振 20-50ppm）：绝对频率读数带约 ±22kHz@435 量级误差，引用须标
  "未做 ppm 标定"；标定后经 `device_args.ppm` 收紧。

## 7. 排障速查

| 现象 | 先做什么 |
|---|---|
| VNA 口枚举为空 | 换数据线/换 USB 口；Windows 查设备管理器 COM 口 |
| VNA 第二次连报 PermissionError | 这是已知 Windows 现象（2.2）：改单进程预开传输层，别做 close→reopen |
| rtl_test 找不到设备 | Zadig 绑 WinUSB（3.1）；确认无其他程序占用 |
| MCP `list_devices` 只有 debug | `MBDSDR_RTL_TCP` 未设置或设在别的进程；`connect` 不联网，失败即诚实报错 |
| 谐振"测没了" | 90% 是姿势/金属（第 5 节）：手持垂直悬空重测再判断 |

## 8. 交叉验证方法论

同一天线、同一馈线，**VNA 与 SDR 是两个独立观测**：VNA 看 S11/谐振/TDR（"天线本身在频域上是什么"），SDR 看 PSD/解调（"这段频谱里真有能量吗"），互相印证才下结论。当晚实例：VNA 测得 RH770 在 430 段谐振 419.5MHz（手持）↔ SDR 在 435.25 收到 9 个窄带载波、435.274 语音带 RMS 0.49（空频点噪声底 0.09）——两套链路一致指向"该天线此段可用、该频段有真实信号"。

## 交叉引用

- 自检口径/退出码/只读边界：`tools/hw_selfcheck/selfcheck.py` 与其 README；回传格式：`docs/learn/phase6/P2-hw-report-format.md`。
- NanoVNA 最小会话/协议序列：`docs/learn/nanovna/hardware-quickstart.md`；分析工具族与 Windows PermissionError 登记：`docs/learn/nanovna/feature-parity.md`。
- rtl_tcp 接入设计与已知边界：`docs/learn/sdr-rtltcp-agent-path.md`；Windows DLL 搜索顺序：`mbdsdr_ai/windows_setup.py`。
- 工具总览、MCP 运行方式、真机诚实声明：`README.md`、`docs/mcp_tools.md`。
