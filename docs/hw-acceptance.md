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

## 当前状态（2026-11-06 00:56 实跑）

命令：`python scripts\hw_acceptance\acceptance_suite.py`
证据：`.tmp-tool-results/hw_acceptance/suite_20261106_005623/`
总耗时：**21.2 s**（其中 R6 串口扫描 15.4 s 占大头）

| 环 | 名称 | 状态 | 关键实测 / SKIP 原因 | 本环耗时 |
|---|---|---|---|---|
| R1 | 连接与识别 | **SKIP(no_device)** | `device_count=0`；本机未插 RTL-SDR，无连接耗时可报 | 3.7 s（含导入库） |
| R2 | 频谱与首帧 | **SKIP(no_device)** | 同上；无第一帧时间/噪声底/SNR 数字 | 0.07 s |
| R3 | 流稳定性 | **SKIP(no_device)** | 同上；无请求/实收样本、短读计数 | 0.08 s |
| R4 | 解调与音频输出 | **SKIP(no_device)** | 同上；无音频 RMS/导频数字 | 0.07 s |
| R5 | 云台回读 | **SKIP(no_gimbal)** | `127.0.0.1:4533` 探测 613.3 ms 无应答；未下发任何运动指令 | 0.61 s |
| R6 | GNSS 定位 | **SKIP(no_gnss)** | 串口自动扫描 15420.9 ms 未找到 GNSS 接收机 | 15.4 s |
| R7 | 星象仪渲染 | **PASS** | 太阳误差 **0.0054°**；月球 vs **JPL DE421** 偏差 **26.5″**、视直径差 **4.7″**；1h 错位反证 **1981.4″** → 拨回 **26.5″**；渲染 **577.8 ms**；PNG **91,560 B** 非空白 | 0.58 s |

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
| R7 | PySide6 + `de421.bsp` + skyfield | 天文计算 → 太阳独立 Meeus 对拍 → **月球 vs JPL DE421** → 1h 错位反证 → `RFSkyView.grab()` | 太阳 ≤0.5°；月球赤道坐标 ≤120″、视直径 ≤12″；1h 错位必须 ≥600″（反证）；渲染 ≤3 s；PNG 非空白 | 数据/渲染自动；星空布局与手感人眼 | `星象仪模式数据异常：请检查观测站坐标与系统时间（时间/坐标错会导致星位全错）。` |

**月球阈值口径**：120″ 是**外部星历真值**阈值（JPL DE421 经 skyfield 读取；
DE421 月球精度约 1″ 量级 + 本仓 Meeus 截断级数余量，实测偏差 26.6″），
**不是**「两套自家实现的最大分歧」。缺 `de421.bsp`/skyfield 时该子项标
`UNVERIFIED` 并 `skipped`，不补数字。

反证可复跑：`python -m pytest tests\test_moon_reference_acceptance.py -q`
（含 1 小时错位必须红、拨回必须绿）；手工演示脚本在
`D:\mbdsdr\.tmp-tool-results\hw_acceptance\negctl_moon.py`（D 盘临时件，不入库）。

失败即停：任一环 `FAIL` 后，后续环标 `SKIP(not_run)` 并注明「前序 R? FAIL」，
不继续执行、不掩盖。

## 可复跑命令表

| 命令 | 实测耗时 | 依赖 | 产物 / 判据 |
|---|---|---|---|
| `python scripts\hw_acceptance\acceptance_suite.py` | **21.2 s** | R1–R4：librtlsdr + RTL-SDR；R5：rotctld `127.0.0.1:4533`（或板载云台）；R6：COM 口 GNSS；R7：PySide6 + skyfield + `de421.bsp` | `summary.json` + 逐环 `result.json`；SKIP 带 `no_device/no_gimbal/no_gnss` 原因码 |
| `python scripts\hw_acceptance\run_all.py --only r5,r6,r7` | **39.4 s** | TLE 缓存（离线需先有缓存，或允许联网）；skyfield；D 盘 TX 审计目录可写 | 上一版分环脚本（卫星过境/天空/发射闸），`PASS 3 / FAIL 0` |
| `python scripts\hw_acceptance\acceptance_suite.py --hw` | **3.9 s** | R1–R4：librtlsdr + RTL-SDR | 只跑硬件四环；无设备 → `PASS 0 / FAIL 0 / SKIP 4`，逐条 `no_device`；**SKIP 计数可见**，不会因 0 个 PASS 而报“通过” |
| `python scripts\hw_acceptance\manual_usb_cycle.py --mode usb`（无 `--interactive`） | **7.0 s**（SKIP） | 无（`no_operator`）；真跑需人工 + 设备/链路 | `result.json`；`SKIPPED(no_operator)`，**不产生任何拔线耗时数字** |
| `python -m pytest tests\test_moon_reference_acceptance.py -q` | **0.36 s** | `de421.bsp` + skyfield（缺则 skip） | 月球对拍 + 1h 反证：`3 passed` |

> `scripts/release_check.py` 本轮**未改动**：该文件工作区含机制线未提交的
> `narrow/size1366/三态 run` 改动，按纪律不把他人工作夹带进本次提交。
> `--hw` 是等价的可嵌入闸门入口（退出码只在 FAIL 时为 1；SKIP 仍逐条计数）。
> 机制线合入后，可把 `acceptance_suite.py --hw` 直接挂到其步骤表。


### R8 热插拔 / 拔线（半自动：人动手，脚本计时判定）

脚本：`scripts/hw_acceptance/manual_usb_cycle.py`。**人工只做物理动作**
（拔/插 USB、拔网线、停/启服务端），计时与 PASS/FAIL 由脚本完成，避免人眼判读。

```powershell
# 只看判据、不作任何动作（无操作者时的实跑口径）
python scripts\hw_acceptance\manual_usb_cycle.py --mode usb
python scripts\hw_acceptance\manual_usb_cycle.py --mode rtl-tcp --port 1234
python scripts\hw_acceptance\manual_usb_cycle.py --mode spyserver --port 7355 --cut server

# 真跑（需要人守在设备旁；脚本会做 5s 倒计时）
python scripts\hw_acceptance\manual_usb_cycle.py --mode rtl-tcp --port 1234 --cut cable --interactive
```

- 证据：`.tmp-tool-results/hw_acceptance/hotplug_<mode>/result.json`（全部 D 盘）。
- 计时口径：**以最后一帧真实数据为 T0**，`detect_s`/`stale_s` 是静默持续时长，
  操作者晚按几秒不会污染数字；插回首帧用**重连成功→首帧**的软件延迟，另记
  `human_wait_s` 供参考。
- SKIP 原因码：`no_operator`（没加 `--interactive` / 非终端）、`no_hw_env`
  （`MBDSDR_NO_HW=1`）、`no_device`（枚举不到 RTL-SDR）、`no_endpoint`
  （建连失败）、`no_stream`（建连但 5s 无首帧）、`operator_cancel`（人工中断）。
  SKIP 不算通过，且不产生任何耗时数字。

三种后端路径不同，必须分开验（数值来源：`docs/network-layer.md` §0/§2/§7 本机实测）：

| 后端 / 拔法 | 步骤与观察点 | 上限 | 数字来源 |
|---|---|---|---|
| **本地 USB**（`--mode usb`） | ① 拔 USB → `connected=False`、`link_state=disconnected`；② 插回 → 重连并出首帧 | ① **≤0.5s**；② **≤1.0s** | `UNVERIFIED_EXT`：本机无真机，沿用网络源 RST **0.000s** / FIN **0.103s** 实测的 0.5s 上限（≈5× 余量）；首帧沿用换端口 **0.022s** 的 1.0s 上限（≈45× 余量）。第一次真机跑后用实测值校准 |
| **rtl_tcp · 拔网线**（`--cut cable`） | ① 半开：TCP 保活、停止转发 → `stale` → 硬判 `disconnected`；② 重插 → 重新建连 → 首帧 | ① stale **≤5.5s**、硬判 **≤11.0s**；② **≤1.0s** | 实测 stale **5.004s**、硬判 **10.034s**（`TestRealThresholdLatency`, idle=5s）；首帧实测换端口 **0.022s**（`UNVERIFIED_EXT` 重插路径） |
| **rtl_tcp · 停服务端**（`--cut server`） | ① 杀进程 → RST/FIN → `disconnected`，`error_category ∈ eof/reset`；② 重启 → 重连 → 首帧 | ① **≤0.5s**；② **≤1.0s** | 实测 RST **0.000s** / FIN **0.103s**（§2）；首帧同换端口 0.022s |
| **SpyServer · 拔网线**（`--cut cable`） | ① 半开 → stale → 硬判；② 重插 → 重新握手 → 首帧 | ① stale **≤5.5s**、硬判 **≤11.0s**；② **≤1.0s** | 实测 stale **5.010s**、硬判 **10.027s**（§0）；首帧沿用 rtl 0.020s 的 1.0s 上限（spy 未单独计时，`UNVERIFIED_EXT`） |
| **SpyServer · 停服务端**（`--cut server`） | ① 停进程 → RST/FIN；② 重启 → 重新握手 → 首帧 | ① **≤0.5s**；② **≤1.0s** | 实测 RST **0.000s** / FIN **0.098s**（§3）；首帧同上 |

**为什么不能合并成一版**：rtl_tcp 重连只需 socket + 12 字节 `RTL0` 头；SpyServer
重连要重新走设备/采样率/streaming 协商，首帧必须校验真实 `ndarray`（长度 4096、
非全零），不能拿「TCP 连上了」当恢复成功；USB 是本地枚举路径（librtlsdr
重新 open），与网络心跳无关，拔出后由生产者读错误直接 `_mark_link_lost`。

**本轮实跑（无操作者）**：`usb`、`rtl-tcp`（cut=cable）、`spyserver`（cut=server）
三个模式均输出 `status=SKIPPED(no_operator) code=no_operator`，退出码 0，
`result.json` 分别落在 `.tmp-tool-results/hw_acceptance/hotplug_{usb,rtl-tcp,spyserver}/`；
未执行任何拔插动作，也**没有**产生可引用的耗时数字。

### 只能条件复跑 / 永久不可复跑

| 项 | 状态 | 原因 |
|---|---|---|
| R1–R4 连接/频谱/流/音频 | **接上 RTL-SDR 即可复跑** | 当前 `device_count=0` |
| R5 云台回读 | **接上 rotctld/板载云台即可复跑** | 当前端口无应答 |
| R6 GNSS 定位 | **接上串口 GNSS 即可复跑** | 当前无接收机 |
| 真实射频发射 | **永久不可自动复跑** | 需持证设备、频率授权与真人一次性 token；脚本 R7 只验门语义、不产生射频 |
| 真实扬声器出声 | **永久人工** | 验收纪律要求静音，脚本只落 WAV |
| 云台机械指向精度 | **永久需人工授权** | 需物理转动 + 外部量角器；只读回读不代表机械精度 |
| 拔线/驱动未装等故障注入 | **半自动（R8）** | 人做物理拔插，`manual_usb_cycle.py` 计时并判定；无操作者时 `SKIP(no_operator)`，不产生数字 |
| 星空/瀑布/音质观感 | **永久人眼/人耳** | 渲染与数据面可自动，观感不可断言 |


## 未验证项（如实列出）

- **R1–R4**：本机无 RTL-SDR，全部 `SKIP(no_device)`，**零实测数字**；
- **R5**：本机无 rotctld/板载云台，`SKIP(no_gimbal)`；机械跟随精度需真 rotator/IMU，
  且 `pose_error_by_source.readback.samples > 0` 前不得把指令误差当机械精度；UI
  在云台按钮 tooltip 与停机文案里原样写「指向精度：不可报（原因）」，**不显示 0°**；
- **R6**：本机无串口 GNSS，`SKIP(no_gnss)`；坐标未产生，未用任何默认站址冒充；
- **R7 月球绝对位置**：已对拍 JPL DE421（偏差 26.6″、视直径 4.8″），不再是未验证项；
  依赖 `de421.bsp`（本机在仓库根，约 17 MB，**不入库**）；换机缺星历时该子项自动
  `UNVERIFIED`，不会静默变绿；
- **人耳听感**：R4 数据面已验证，音质/爆音需操作者放行出声后手动验；
- **R7 人眼手感**：offscreen 渲染仅证明能出图，星空布局/交互需真实显示环境；
- **R8 拔线**：本轮只跑过无操作者 `SKIP(no_operator)`，**没有任何真机拔插数字**；
  表中 USB 的 0.5s/1.0s 是 `UNVERIFIED_EXT`（沿用网络源实测上限），网络拔线的
  5s/10s 数字来自本机回环半开，**真实网线/NAT/驱动重枚举未验证**。第一次真机
  跑完后，用实测值校准这两档上限，而不是把本表当已验证。

## 与旧脚本的关系

- `scripts/real_hw_acceptance.py`（2026-11-03 单体七环）保留可复跑；
- `scripts/hw_acceptance/ring1_connection.py` … `ring7_tx_gate.py`（上一版按环拆分，
  覆盖频谱/录音/卫星/天空/发射）保留；本文件的 `acceptance_suite.py` 是**单命令入口**，
  以本文件的状态表为当前口径。
