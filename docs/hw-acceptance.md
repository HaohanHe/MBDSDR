# 真机七环验收（单命令可复跑）

一条命令跑完七环：逐环执行、逐环 PASS/FAIL/SKIP、**失败即停**、末尾汇总
`PASS n / FAIL n / SKIP n` 并逐条列出 SKIP 原因。

```powershell
python scripts\hw_acceptance\acceptance_suite.py
python scripts\hw_acceptance\acceptance_suite.py --out D:\mbdsdr\tmp\hw_suite
```

- 退出码：任一环 `FAIL` → 1；`PASS`/`SKIP` → 0。
- 证据：`D:\mbdsdr\MBDSDR\.tmp-tool-results\hw_acceptance\suite_<时间戳>\`
  每环 `result.json` + `summary.json` + 渲染/音频产物。**全部落 D 盘。**
- 环境变量（可选）：`MBDSDR_ROTCTLD_HOST/PORT`、`MBDSDR_GNSS_WAIT_S`、
  `MBDSDR_OBS_LAT/LON`、`MBDSDR_HW_ACCEPTANCE_DIR`。

## 硬红线（写进脚本启动横幅与每条结果）

1. 没有真实设备/后端 → `SKIP(no_device | no_gimbal | no_gnss)`，并写明原因；
2. **严禁**用模拟/合成/`debug` 调试源数据判 PASS（`debug` 源不计入真机）；
3. 拿不到的数字不补，标 `UNVERIFIED` 或直接不输出；
4. 全程静音（不打开声卡）、不下发云台运动指令、不产生射频；
5. 任何产物不得落 C 盘用户目录。

## 当前状态（2026-11-05 23:59 实跑）

命令：`python scripts\hw_acceptance\acceptance_suite.py`
证据：`.tmp-tool-results/hw_acceptance/suite_20261105_235914/`

| 环 | 名称 | 状态 | 关键实测 / SKIP 原因 |
|---|---|---|---|
| R1 | 连接与识别 | **SKIP(no_device)** | `device_count=0`；本机未插 RTL-SDR，无连接耗时可报 |
| R2 | 频谱与首帧 | **SKIP(no_device)** | 同上；无第一帧时间/噪声底/SNR 数字 |
| R3 | 流稳定性 | **SKIP(no_device)** | 同上；无请求/实收样本、短读计数 |
| R4 | 解调与音频输出 | **SKIP(no_device)** | 同上；无音频 RMS/导频数字 |
| R5 | 云台回读 | **SKIP(no_gimbal)** | `127.0.0.1:4533` 探测 609.4 ms 无应答；未下发任何运动指令 |
| R6 | GNSS 定位 | **SKIP(no_gnss)** | 串口自动扫描 15.7 s 未找到 GNSS 接收机 |
| R7 | 星象仪渲染 | **PASS** | 太阳位置误差 0.0054°（独立 Meeus）；渲染总耗时 374.1 ms；PNG 87,055 B 非空白 |

> 汇总：`PASS 1 / FAIL 0 / SKIP 6`。R1–R6 属**本机不可验**（缺真机/云台/GNSS），
> 不是产品失败；没有用任何模拟数据顶替。

## 七环判据与实测口径

| 环 | 前置 | 动作 | 期望（可量化） | 自动 / 人眼 | 失败时用户看到 |
|---|---|---|---|---|---|
| R1 | RTL-SDR 已插、无他进程占用 | librtlsdr 枚举 → `SDRController.connect` → 释放 | 枚举 ≥1；三元组可读；tuner 可识别；打印 `connect_ms`/`disconnect_ms` | 全自动 | 无设备：`未检测到 SDR 设备。请插入 RTL-SDR 后点「重新枚举」…`；占用：`设备被其他程序占用。请关闭 SDR++ / SDR# / rtl_tcp…`；缺库：`找不到 SDR 运行库（librtlsdr）…` |
| R2 | 设备可用；天线接入 | 连接后立即读首帧 → 扫 88–108 MHz 找强台 → 连读 32 帧 | `first_frame_ms` 实测；SNR ≥10 dB；质心峰偏 ≤40 kHz；噪声底漂移 ≤6 dB；0 NaN | 帧率/数字全自动；瀑布观感人眼 | `当前频点没有足够强的信号。请换已知强台（FM 广播）或接天线后重试。` |
| R3 | 设备可用 | 2.4 MS/s 连续读 IQ 3 s，统计请求/实收/短读/None | 吞吐比 ≥0.90；有效率 2.4±0.24 MS/s；短读 0；None 读 0 | 全自动 | 同上；另：`pyrtlsdr 无 overflow 计数，丢样本只能由吞吐比间接体现，不伪造数字` |
| R4 | 设备可用；有 FM 强台 | WFM 解调 → `read_audio` → 写 WAV（**不进声卡**） | ≥4096 帧；RMS ∈ (1e-4, 1.0)；<15k/>15k 能量比 >2；打印声道数/WAV 字节 | 数据面自动；音质人眼/人耳 | `当前频点没有足够强的信号…`；脚本永不打开声卡，无声卡报错 |
| R5 | rotctld 已启动或板载云台在线 | **只读**：TCP 探测 → `get_position()` 两次 | 打印 `connect_ms`/`read_ms`、方位/仰角、两次回读差 ≤1° | 全自动；机械精度另需授权运动测试 | `云台无应答。请确认 rotctld 已启动（默认 127.0.0.1:4533）或板载云台通道已连接。` |
| R6 | 串口 GNSS 接收机 | `RealGNSSMonitor.start()` → 等待 fix | 打印首次定位耗时 `ttff_s`、经纬度、星数、HDOP；坐标来自真实 NMEA | 全自动 | `未获得 GNSS 定位。请检查 GNSS 接收机串口/天线，或在户外重试。` |
| R7 | PySide6 + offscreen | 天文计算 → 独立 Meeus 对拍 → `RFSkyView.grab()` | 太阳误差 ≤0.5°；渲染总耗时 ≤3 s；PNG 非空白；打印字节数 | 数据/渲染自动；星空布局与手感人眼 | `星象仪模式数据异常：请检查观测站坐标与系统时间（时间/坐标错会导致星位全错）。` |

失败即停：任一环 `FAIL` 后，后续环标 `SKIP(not_run)` 并注明「前序 R? FAIL」，
不继续执行、不掩盖。

## 未验证项（如实列出）

- **R1–R4**：本机无 RTL-SDR，全部 `SKIP(no_device)`，**零实测数字**；
- **R5**：本机无 rotctld/板载云台，`SKIP(no_gimbal)`；机械跟随精度需真 rotator/IMU，
  且 `pose_error_by_source.readback.samples > 0` 前不得把指令误差当机械精度；UI
  在云台按钮 tooltip 与停机文案里原样写「指向精度：不可报（原因）」，**不显示 0°**；
- **R6**：本机无串口 GNSS，`SKIP(no_gnss)`；坐标未产生，未用任何默认站址冒充；
- **R7 月球绝对位置**：缺星历文件，未独立验证（太阳误差已量化）；
- **人耳听感**：R4 数据面已验证，音质/爆音需操作者放行出声后手动验；
- **R7 人眼手感**：offscreen 渲染仅证明能出图，星空布局/交互需真实显示环境。

## 与旧脚本的关系

- `scripts/real_hw_acceptance.py`（2026-11-03 单体七环）保留可复跑；
- `scripts/hw_acceptance/ring1_connection.py` … `ring7_tx_gate.py`（上一版按环拆分，
  覆盖频谱/录音/卫星/天空/发射）保留；本文件的 `acceptance_suite.py` 是**单命令入口**，
  以本文件的状态表为当前口径。
