# 真机七环验收（可复跑 + 离线替代 + 人工验收卡）

入口与编号闸，职责分开：

```powershell
# 主口径：七环（文档里记作 R1–R7），每环独立子进程，汇总 PASS / FAIL / SKIP + 原因码
python scripts\hw_acceptance\run_all.py

# 设备链视角：文档里记作 A1–A7（A1–A4 硬件、A5 云台、A6 GNSS、A7 星象仪渲染）；失败即停
python scripts\hw_acceptance\acceptance_suite.py
python scripts\hw_acceptance\acceptance_suite.py --hw      # 只跑 A1–A4

# 反证：故意改坏判据，环必须 FAIL；否则说明判据形同虚设
python scripts\hw_acceptance\negative_control.py

# 编号闸：两套套件不许同号不同义（改坏编号必须判红）
python scripts\hw_acceptance\check_ring_labels.py
```

- **编号口径（两套套件各自有 R1–R7，含义不同）**：本文档统一用
  `R<n>` 指 `run_all.py` 七环，用 `A<n>` 指 `acceptance_suite.py` 设备链视角。
  两套运行时输出仍然是各自的 `R<n>`（`summary.json` 的 `ring` 字段原样），
  引用时请按下面的对照表加前缀，不要写成同一个 `R5`。

| 编号 | 环的语义 | 属于哪个套件 | 运行时输出 |
|---|---|---|---|
| R1 | 连接与识别 | run_all | `R1` |
| R2 | 频谱与瀑布 | run_all | `R2` |
| R3 | 解调与音频 | run_all | `R3` |
| R4 | 录音与回放 | run_all | `R4` |
| R5 | 卫星追踪与过境 | run_all | `R5` |
| R6 | 星象仪模式 | run_all | `R6` |
| R7 | 发射安全闸 | run_all | `R7` |
| A1 | 连接与识别 | acceptance_suite | `R1` |
| A2 | 频谱与首帧 | acceptance_suite | `R2` |
| A3 | 流稳定性 | acceptance_suite | `R3` |
| A4 | 解调与音频输出 | acceptance_suite | `R4` |
| A5 | 云台回读 | acceptance_suite | `R5` |
| A6 | GNSS 定位 | acceptance_suite | `R6` |
| A7 | 星象仪渲染 | acceptance_suite | `R7` |

> 最容易串的两处：**主口径的 5 号是卫星过境，设备链的 5 号是云台**；
> **主口径的 6 号是星象仪，设备链的 6 号是串口 GNSS**。
> `check_ring_labels.py` 会把「同号但语义不同」的写法直接判红，
> 例如把设备链的云台环写成主口径的编号。

- 退出码：任一环 `FAIL` → 1；`PASS` / `SKIP` → 0（**SKIP 不是通过**）。
- 证据全部落 D 盘：`.tmp-tool-results/hw_acceptance/`，可用 `--out` 指定。
- 环境变量（可选）：`MBDSDR_ROTCTLD_HOST/PORT`、`MBDSDR_GNSS_WAIT_S`、
  `MBDSDR_OBS_LAT/LON`、`MBDSDR_HW_ACCEPTANCE_DIR`。
- `MBDSDR_HW_FORCE_OFFLINE=1`：只把硬件环降级为 `SKIP + 离线替代`，用于演练
  SKIP 明细；**不可能**把硬件环判 PASS。

## 硬红线

1. 没有真实设备/后端 → `SKIP`，且原因码可见：
   `NO_HW` / `NO_DATA` / `NEEDS_OPERATOR` / `NEEDS_TIME_WINDOW`；
2. **严禁**用模拟/合成/`debug` 调试源数据把硬件环判 PASS；
3. 拿不到的数字不补：不输出，或标 `skipped` 并写明原因；
4. 全程静音（不打开声卡）、不下发云台运动指令、不产生射频；
5. 任何产物不得落 C 盘用户目录。

> 离线替代即使 PASS，也只能说明「数据面」成立，**不能**把硬件环判 PASS。
> 硬件环的真实状态始终是 `SKIP(NO_HW)` 或真实实测。

## 一、七环逐环：测什么 / 需要什么 / 能否离线替代 / 现状

状态取自 **2026-11-06 01:45 真机实跑**（`D:\mbdsdr\tmp\final_hw_014420`）与
**强制无硬件实跑**（`D:\mbdsdr\tmp\final_nohw_014552`）。两列都不合并、不取平均。

| 环 | 测的物理量 / 数据面 | 需要什么（设备/数据/时间窗） | 能否离线替代 | 真机现状 | 无硬件现状 / 原因码 |
|---|---|---|---|---|---|
| R1 连接与识别 | librtlsdr 枚举数、USB 三元组、tuner 型号、`connect_ms`/`disconnect_ms`、5 次开关是否泄漏句柄 | RTL-SDR + WinUSB/Zadig + 无他进程占用 | **不能**：USB 枚举、驱动绑定、开句柄耗时都是物理过程 | **PASS**：Generic RTL2832U / SN 77771111153705700，tuner=2(FC0012)，5/5 次开关成功（瞬时重试 0 次），断开后新句柄可开 | **SKIP** / `NO_HW`（未枚举到真实设备） |
| R2 频谱与瀑布 | 第一帧耗时、32 帧噪声底/SNR、噪声底漂移、NaN、瀑布矩阵 | RTL-SDR + 天线 + 88–108 MHz 已知强台 | **部分**：用真实 OTA 录音回放可验 DSP 数据面（SNR/漂移/NaN）；**不能**验实时流、第一帧、调谐精度 | **PASS**：94.30 MHz 台，SNR 22.02 dB、噪声底 −48.61 dB、漂移 4.79 dB、32/32 帧、NaN=0 | **SKIP** / `NO_HW`；离线替代 **PASS**：SNR 36.31 dB、漂移 3.68 dB |
| R3 解调与音频 | 音频帧数、声道、RMS、<15k/>15k 能量比、19 kHz 导频 | RTL-SDR + 强台；听感需声卡/人耳（脚本不出声） | **部分**：真实 OTA → 产品解调链可验数据面；**不能**验声卡、实时流、听感 | **PASS**：7680 帧/2 声道、RMS 0.6334、能量比 34.7；导频按**旧测法**读 19006 Hz/7.3 dB（旧法无抗混叠，读数不可信，见 §6.2）；修正后真机 98.0 MHz 实测 **19003.0 Hz / 37.7 dB** | **SKIP** / `NO_HW`；离线替代 **PASS**：RMS 0.2306、能量比 57.4、导频 19000 Hz/47.3 dB |
| R4 录音与回放 | 流式写盘字节增长、样本数、SigMF `integrity`、文件回放/实时 RMS 比、改采样率后 sidecar | RTL-SDR + D 盘可写 | **不能**：要真实实时流、写盘时钟与 SigMF 落盘 | **PASS**：2,359,296→4,718,592 B、598,016 样本、integrity=ok、回放比 0.46、改速后 1.2 MS/s 写入 sidecar | **SKIP** / `NO_HW`（无替代样本可证实时写盘） |
| R5 卫星追踪与过境 | 过境升起时刻/峰值仰角、TLE 完整性、跨午夜排序、仰角门 | TLE + skyfield（**不需要射频硬件**） | **可以**：纯计算，离线可复跑 | **PASS**：32 条过境；NOAA15/18/19/ISS 峰值仰角误差 0.00–0.05°，升起时刻差 0.3–5.3 s；TLE 校验和全部合法；排序升序 | **PASS**（离线纯算）；缺 TLE → `NO_DATA` |
| R6 星象仪模式 | 太阳 RA/Dec、月球 RA/Dec 与视直径、1 h 错位反证、offscreen 渲染字节/非空白 | skyfield + `de421.bsp`（**不需要射频硬件**） | **可以**：纯计算 + 离屏渲染，离线可复跑 | **PASS**：太阳误差 0.0054°；月球 vs JPL DE421 **26.2″**、视直径差 **4.5″**；1 h 反证 **1980.1″** → 拨回 26.2″；PNG 94,414 B | **PASS**（离线纯算）；缺星历 → `NO_DATA` |
| R7 发射（含安全闸） | 7 种门语义（手工 arm、伪造 token、一次性 token、动作不符、过期、撤销）、审计 JSONL | 纯软件；**不产生射频** | **可以**：门逻辑离线可复跑 | **PASS**：7 种判定全部符合预期，审计 11 条 JSONL，`rf_emitted=false` | **PASS**（离线纯算）；**真实发射**见「永久不可复跑」 |

> 原因码只允许这四个；`run_all.py` 的汇总行逐条打印
> `SKIP <环> → <原因码>：<原因>`，`summary.md` 也有独立的「SKIP 原因明细」表。

## 二、离线替代与参考值出处（不写出处 = 自己给自己判卷）

| 项 | 参考/夹具 | 出处 | 属于哪种 |
|---|---|---|---|
| R2/R3 数据面回放 | `tests/fixtures/real_fm_88m1/`（88.1 MHz 空口实录） | `provenance.json` 声明 `external-truth / real_ota`，含采集设备（RTL2832U+FC0012）、时间、sha256；每次运行重新校验 sha256 | **真实外部数据**（不是合成波形） |
| R6 月球绝对位置 | JPL DE421 | `de421.bsp`（仓库根，约 17 MB，**不入库**）经 skyfield 读取；DE421 月球星历精度约 1″ 量级 | **外部星历真值** |
| R6 太阳位置 | Meeus《Astronomical Algorithms》(1998) 第 25 章低精度太阳公式 | `ring6_sky_mode.py::_sun_reference` 独立实现 | **外部公开算法**，不是本仓同一实现 |
| R5 TLE 完整性 | NORAD TLE 校验和（数字相加、`-` 记 1、模 10） | 公开 TLE 格式规范；见 `_tle_checksum` | **外部标准** |
| R5 过境误差 | skyfield 对**同一份 TLE** 递推 | 与 `mbdsdr_ai.orbit` 共用 sgp4 内核 | **实现一致性，不是外部星历真值**；只证明两条代码路径一致 |
| R2/R3 指标 | 无绝对参考 | — | 只做「真实数据回归」：阈值是工程判据，不是计量真值 |

**月球阈值口径**：120″ 是**外部星历真值**阈值（DE421），不是「两套自家实现的最大分歧」。
缺 `de421.bsp`/skyfield 时，R6 返回 `SKIP(NO_DATA)`，太阳与渲染子项照跑但不据此判 PASS。

## 三、人工验收卡（无硬件/需人时照着做）

每张卡都可一页打印。**只填实测值；拿不到就写「未测」，不要补数。**

### 卡 R1 连接与识别（需设备）
前置：RTL-SDR 已插；关闭 SDR++/SDR#/rtl_tcp；已装 WinUSB/Zadig。
步骤：
1. `python scripts\hw_acceptance\ring1_connection.py`；
2. 拔插 USB 三次，每次重跑；
3. 打开另一个程序（如 SDR++）确认能接手设备，再关掉。

| 记录项 | 第 1 次 | 第 2 次 | 第 3 次 | 合格判据 |
|---|---|---|---|---|
| 枚举数 | | | | ≥1 |
| USB 三元组 | | | | 可读（允许为空字符串的老克隆棒） |
| tuner | | | | 非空 |
| connect_ms / disconnect_ms | | | | 只记录，不设上限 |
| 5 次开关成功率 | | | | 5/5 |
| 断开后新句柄可开 | | | | 是 |

### 卡 R2 频谱与瀑布（需设备 + 人眼；绝对频率精度需信号源）
前置：天线接入；已知强台（FM 88–108 MHz）。
步骤：
1. `python scripts\hw_acceptance\ring2_spectrum_waterfall.py`；
2. 观察瀑布 10 s：滚动是否连续、有无整屏跳变/撕裂；
3. （可选，校准）信号源输出 100.000 MHz / −60 dBm，记录峰值偏差。

| 记录项 | 实测 | 合格判据 |
|---|---|---|
| SNR | | ≥10 dB |
| 32 帧噪声底漂移 | | ≤8.5 dB（由同台 10 次分布定，见 §6.3） |
| 帧数 / NaN | | 32/32、0 |
| 瀑布观感 | 连续 / 跳变 / 撕裂 | 无整屏跳变 |
| （校准）100 MHz 峰偏 | | ≤5 kHz（仅在有信号源时判） |

> 绝对频率精度没有已知频率源就无法判；空口 FM 台的峰值会随调制漂移，不能当频率基准。

### 卡 R3 解调与音频（需设备 + 人耳）
步骤：
1. `python scripts\hw_acceptance\ring3_demod_audio.py`；
2. 打开产物 `wfm_audio.wav` 播放 30 s；
3. 记录是否有周期性爆音/金属声/静音段。

| 记录项 | 实测 | 合格判据 |
|---|---|---|
| 音频帧数 / 声道 | | ≥4096 / 1 或 2 |
| RMS | | 1e-4 – 1.0 |
| <15k/>15k 能量比 | | >2 |
| 19 kHz 导频 | | 有则 19000±100 Hz 且峰/邻域 >10 dB；无则记「单声道」 |
| 听感 | 清晰 / 爆音 / 金属声 | 可懂、无周期性爆音 |

### 卡 R4 录音与回放（需设备 + 人耳）
步骤：
1. `python scripts\hw_acceptance\ring4_recording_playback.py`；
2. 播放产物 `rec_2p4m.sigmf-data`（或同名 WAV 导出）；
3. 记录文件增长、完整性、回放声音。

| 记录项 | 实测 | 合格判据 |
|---|---|---|
| 写盘字节增长 | | 单调增长且 >0 |
| 样本数 | | >0 |
| SigMF `integrity` | | `ok` |
| 回放/实时 RMS 比 | | 0.2 – 0.8 |
| 改采样率后 sidecar | | 与新采样率一致 |
| 回放听感 | | 与实时一致，无断音 |

### 卡 R5 过境（离线纯算 + 真实接收另算）
离线部分（无需硬件）：
1. `python scripts\hw_acceptance\ring5_satellite_passes.py`；
2. 记录各星 `elevation_error_deg` / `rise_error_s` / TLE 校验和 / 是否升序。

| 记录项 | 实测 | 合格判据 |
|---|---|---|
| TLE 校验和 | | 全部合法 |
| 过境条数与排序 | | 升序（含跨午夜） |
| 峰值仰角误差 | | ≤1.0° |
| 升起时刻误差 | | ≤60 s |
| 仰角门 | | 全部 ≥ 设定门限 |

真实接收（需时间窗+天线，**半永久人工**）：按星历在过境窗口内守听，记录
实际首帧时刻与预测升起时刻差、实际峰值仰角（用云台回读或外部量角器）。

### 卡 R6 星象仪（离线纯算 + 观感）
步骤：
1. `python scripts\hw_acceptance\ring6_sky_mode.py`；
2. 打开 `sky_render.png`，检查星空布局、地平线、方位标注；
3. 若在桌面端，实际操作缩放/拖动/切换时间。

| 记录项 | 实测 | 合格判据 |
|---|---|---|
| 太阳误差 | | ≤0.5° |
| 月球 vs DE421 | | ≤120″ |
| 月球视直径差 | | ≤12″ |
| 1 h 错位反证 | | ≥600″，拨回 ≤120″ |
| 渲染耗时 / PNG 字节 | | ≤3 s / 非空白 |
| 观感 | 布局/方位/交互 | 方位正确、无错位（人眼判） |

### 卡 R7 发射安全闸（纯软件）与真实发射（永久人工授权）
门语义（离线）：`python scripts\hw_acceptance\ring7_tx_gate.py`；7 种判定必须
全部符合预期，审计 JSONL 可逐条解析，`rf_emitted=false`。

真实发射（**永久不可自动复跑**）：
1. 操作者持证并确认频率/功率/时间/地点合规；
2. 在界面由真人点击确认，生成一次性 token；
3. 记录：授权人、频率、功率、开始/结束时刻、SWR/驻波、实际发射时长；
4. 任何一项缺记录 → 该次发射不算验收。

## 四、可复跑命令表

> **跑前自检（每次真机之前，禁止假定设备空闲）**：先查占用表/锁、进程、PnP 三项，再动手。
> 本机曾出现**非任何线启动**的 `MBDSDR`（pid 30740）占用设备；`Status OK` 只说明
> 枚举正常，不等于拿得到句柄。
>
> ```powershell
> python scripts\file_claim.py status                    # ① 占用表 / 活跃锁
> Get-Process python | Sort-Object CPU -Descending |
>   Select-Object -First 10 Id,ProcessName,CPU          # ② 重负载脚本
> Get-PnpDevice -InstanceId 'USB\VID_0BDA&PID_2832\77771111153705700' |
>   Select-Object Status                                 # ③ PnP
> ```
>
> 判据（三条缺一不得开跑；**空表 ≠ 无人占**，见 `COORDINATION.md` §6.1）：
> ① 占用表/claim 出现活跃行 → 等对方释放或以 `SKIP(NEEDS_OPERATOR)` 收场，不篡改别人的行；
> ② 进程命中 `deadscan.py` / `perf_probe_deep.py` / `capture_throughput.py` / `run_all.py` /
> `acceptance_suite.py` / `pytest`，或单核持续 ≥90% → **等它跑完，或以 `SKIP(NEEDS_OPERATOR)` 收场**，不 kill 他人进程；
> ③ PnP 非 `OK` → SKIP 并写原因码；
> 三项都干净才开跑，跑完再执行同样三条确认句柄已释放。长跑脚本必须「注册 + 跑完即退 + 有超时」。

| 命令 | 实测耗时 | 依赖（设备/数据/端口） | 产物 / 口径 |
|---|---|---|---|
| `python scripts\hw_acceptance\run_all.py` | **80.4 s**（真机 7/7 PASS）；**56.1 s**（强制无硬件：PASS 3 / SKIP 4） | R1–R4：RTL-SDR；R5：TLE 缓存 + skyfield；R6：`de421.bsp` + PySide6；R7：纯软件 | `summary.json` / `summary.md` / 逐环 `result.json`；SKIP 必带原因码 |
| `python scripts\hw_acceptance\run_all.py --only r5,r6,r7` | 约 **34 s**（本机实测 25.0+4.6+4.2，串行） | TLE + skyfield + `de421.bsp` + PySide6；无射频硬件 | 只跑三个离线纯算环；用于快速回归 |
| `python scripts\hw_acceptance\acceptance_suite.py` | **36.7 s**（真机：PASS 5 / FAIL 0 / SKIP 2）；无设备时全 SKIP（约 6 s） | A1–A4：RTL-SDR；A5：rotctld `127.0.0.1:4533`；A6：COM 口 GNSS；A7：PySide6 + `de421.bsp` | 设备链视角；失败即停；SKIP 带原因码 |
| `python scripts\hw_acceptance\acceptance_suite.py --hw` | **13–14 s**（连续两次 PASS 4/4） | A1–A4：librtlsdr + RTL-SDR | 只跑 A1–A4；无设备 → `PASS 0 / SKIP 4`，SKIP 计数可见 |
| `python scripts\hw_acceptance\negative_control.py` | **29.5 s**（R5 基线占 25 s） | TLE + skyfield + `de421.bsp` + 真实 OTA 夹具 | 4 个反证必须 `RED_AS_EXPECTED`；否则退出码 1 |
| `python -m pytest tests\test_moon_reference_acceptance.py -q` | **0.5 s**（3 passed） | `de421.bsp` + skyfield | 月球对拍 + 1h 反证回归 |
| `python -m pytest tests\test_hw_acceptance_contract.py -q` | **1.9 s**（18 passed） | 无（纯契约） | 锁四码、锁 `skip_result` 永不 PASS、锁 `UNVERIFIED→SKIP(NO_DATA)`、锁 R/A 编号不同义、锁导频测法与采集记账、锁噪声底阈值区间、锁占位序列号重试 |
| `python scripts\hw_acceptance\capture_throughput.py --seconds 6 --blocks 65536,262144,1048576 --modes sync,sync_only,thread,async` | 约 **3.5 min**（24 次 6 s 采集） | RTL-SDR；async 模式直连 pyrtlsdr | `throughput.json`：逐块读/转换耗时、captured/wall/吞吐/丢样；用于 §6.2.1 的归因 |
| `python scripts\hw_acceptance\capture_throughput.py --seconds 6 --blocks 16384 --modes product --mode-runs product=3` | 约 **20 s** | RTL-SDR；开的是产品类 `RTLSDRBackend`（ring 生产者），不是验收用的 OpenDevice | `product_path.json`：ring 供给、解调 consumed、各订阅者 dropped、Σ 订阅者 `dropped_total`、ring 积压、CPU/RSS；**非门限可选证据项，不计入七环 PASS/FAIL**（判据与口径见 §6.2.4） |
| `python scripts\hw_acceptance\check_ring_labels.py` | **0.1 s** | 无（纯文本） | 文档编号闸；`--selftest` 是它的反证（4 种改坏法必须红） |
| `python scripts\hw_acceptance\ring{1..7}_*.py --out D:\...` | R1 17.4 / R2 8.8 / R3 8.0 / R4 12.0 / R5 25.0 / R6 4.6 / R7 4.2 s | 见上表 | 单环排障；每环独立 `result.json` |

> 耗时口径：以上均为 **2026-11-06 01:44–02:00 本机实测**（RTL2832U + FC0012，
> 串行跑，未与其他采集进程并发）。设备被其他进程并发占用时，硬件环会
> `SKIP(NEEDS_OPERATOR)` 而不是 FAIL——串行跑多环时上一句柄的 USB 释放有
> 延迟，`common.transient_open_error()` 会做 4 次（≈1.8 s）瞬时重试，
> R1 的五次开关另记 `cycle_transient_retries`，重试次数不隐藏。

> `scripts/release_check.py` 本轮仍未改动（该文件工作区含机制线未提交改动）。
> 可用等价闸门 `acceptance_suite.py --hw`（或 `run_all.py`）挂到其步骤表：
> 退出码只在 FAIL 时为 1，SKIP 仍逐条计数。

## 五、永久不可复跑 / 半自动 / 条件复跑

| 项 | 类别 | 原因 |
|---|---|---|
| 真实射频发射 | **永久不可自动复跑** | 需持证设备、频率授权、真人一次性 token；脚本 R7 只验门语义 |
| 真实扬声器出声 | **永久人工** | 验收纪律要求静音；脚本只落 WAV |
| 云台机械指向精度 | **永久需人工授权** | 需物理转动 + 外部量角器；只读回读≠机械精度 |
| 星空/瀑布/音质观感 | **永久人眼/人耳** | 数据面可自动，观感不可断言 |
| 真实卫星接收/GNSS 首次定位 | **需时间窗/天空视野** | 过境窗口、室外天空；无 window → `NEEDS_TIME_WINDOW` |
| 拔线/驱动故障注入 | **半自动（按下节 R8）** | 人做物理拔插，脚本计时判定 |
| R1–R4 硬件链 | **条件复跑** | 接上 RTL-SDR、关掉占用进程即可复跑；当前设备可被其他进程间歇占用 → `NEEDS_OPERATOR` |

## 六、当前状态（诚实版）

基准时点：**2026-11-06 01:44–02:00**（RTL2832U / SN 77771111153705700，FC0012）。

- **真机（两次）**：
  - 01:45（`D:\mbdsdr\tmp\final_hw_014420`）：`PASS 7 / FAIL 0 / SKIP 0`，80.4 s；
  - 02:57（`.tmp-tool-results/hw_acceptance/20261106_025741`）：`PASS 7 / FAIL 0 / SKIP 0`；
  R1–R4 为真实设备实测，R5–R7 为离线纯算。七环全部跑过且判据成立。
- **无硬件（`MBDSDR_HW_FORCE_OFFLINE=1`）**：**`PASS 3 / FAIL 0 / SKIP 4`**，
  总耗时 **56.1 s**；4 个 SKIP 全部 `NO_HW`（R1/R2/R3/R4），
  R2/R3 的离线替代单独标 PASS。**不把 3 个 PASS 当七环通过。**
- **设备被占用时**：硬件环返回 `SKIP(NEEDS_OPERATOR)`，不是 FAIL、
  也不是 PASS；用户侧原文见 `common.USER_MSG["device_access_denied"]`。
- **反证**：`negative_control.py` 4 个注入全部 `RED_AS_EXPECTED`，29.5 s
  （R7 门放行、R5 仰角门 95°、R6 月球容差 −1″、R2 真实样本换全零）。
- **设备链视角**：`acceptance_suite.py` → `PASS 5 / FAIL 0 / SKIP 2`
  （A5 云台、A6 串口 GNSS 均 `NO_HW`），36.7 s；`--hw` → `PASS 4 / FAIL 0 / SKIP 0`
  （连续两次，13–14 s）。

### 6.1 两次真机复跑对照（01:45 vs 02:57）

同机同棒（RTL2832U / SN 77771111153705700，FC0012），两次都是真实设备。

| 环 | 检查项 | 01:45 | 02:57 | 判据 | 结论 |
|---|---|---|---|---|---|
| R1 | 枚举 / tuner / 5 次开关 / 释放 | 1 台 / 2(FC0012) / 5/5 / 是 | 同左 | ≥1 / 非空 / 5/5 / 是 | 两次一致，可复现 |
| R1 | 单环耗时 | 17.4 s | 29.6 s | 无上限 | 差异来自 USB 时序，不作判据 |
| R2 | 目标台 | 94.30 MHz | 93.25 MHz | 自动选最强台 | 不同台，不可逐点比 |
| R2 | SNR | 22.02 dB | 33.37 dB | ≥10 dB | 两次都远高于阈值 |
| R2 | 噪声底漂移 | 4.79 dB | 2.27 dB | ≤8.5 dB（§6.3 分布定阈） | 4.79 占旧 6 dB 的 79%，正是「单次贴近阈值」；同台 10 次实测 4.22–6.81 dB（mean+3σ=8.14），旧阈值会 2/10 假红 |
| R2 | 峰值偏移（仅信息） | 87,782 Hz | 143,772 Hz | 不判 | **不能当频率精度** |
| R3 | RMS / 能量比 | 0.6334 / 34.7 | 0.2023 / 22.9 | 1e-4–1.0 / >2 | 两次都过 |
| R3 | 19 kHz 导频 | 19006 Hz / 7.3 dB | 18841 Hz / 8.9 dB | >10 dB 才算 | 两次都 `skipped`。**旧法读数不作数**：无抗混叠抽取把 181159 Hz 的杂散折到 18841 Hz，幅度也被折叠底压低（§6.2）；修正后同台 98.0 MHz 为 19003 Hz / 37.7 dB → `PASS` |
| R4 | 写盘 / 样本 / integrity | +2.36 MB / 598,016 / ok | +2.49 MB / 589,824 / ok | 增长>0 / >0 / ok | 两次一致 |
| R4 | 回放比 | 0.46 | 0.61 | 0.2–0.8 | 均通过；02:57 更靠近上限 |
| R5 | 过境条数 / 升起误差 | 32 / 0.3–5.3 s | 33 / 2.2–3.2 s | ≤60 s | 两次一致（时段不同故条数不同） |
| R5 | 仰角误差 | 0.00–0.05° | 0.01–0.05° | ≤1.0° | 两次一致 |
| R6 | 月球 vs DE421 / 视直径 | 26.2″ / 4.5″ | 25.8″ / 4.2″ | ≤120″ / ≤12″ | 两次一致 |
| R6 | 1 h 反证 | 1980.1″ | 1978.2″ | ≥600″ | 两次都红得预期 |
| R7 | 7 种门语义 / `rf_emitted` | 全符合 / false | 全符合 / false | 全符合 / false | 两次一致 |

**因此可以升级的结论**

- R1–R4 从「单次真机实测」升级为「两次独立真机复跑、同机同棒、判据两次都成立」；
  R1 的枚举/tuner/5 次开关、R4 的落盘完整性在两次之间完全一致。
  注意：R2/R3 的 SNR、RMS 绝对值随所选电台变化（22 vs 33 dB），
  不构成「数值可重复」，只保证判据两次成立。
- R6 的月球绝对位置两次都落在 DE421 的 26″ 量级，说明不是偶然抖动。

**仍不能当硬件证据的结论**

- R3 的**立体声/19 kHz 导频**：旧测法的两次读数（19006 / 18841 Hz，7.3 / 8.9 dB）
  不可信（理由见 §6.2）。修正测法后 98.0 MHz 真机 `PASS`（19003 Hz / 37.7 dB），
  可作「单台单次」的导频证据；仍**不能**证明立体声 L/R 分离正确（那需听感 +
  差信号测试，见人工卡 R3）。
- R2 的**绝对频率精度**：峰值偏移两次差 56 kHz，空口 FM 无频率基准，不能判。
- R5 的「误差」是和**同一份 TLE + 同一个 sgp4 内核**比，属实现一致性，
  不是外部星历真值；R5/R6/R7 本身也不需要射频硬件，不能算「硬件链证据」。
- R1 的单环耗时、R2 的目标台频率都随环境变化，只做记录，不做判据。

本轮实测发现并修掉的三处**判据口径问题**（都属于「量错了」而不是「放宽」）：

1. `acceptance_suite` A2 的 `centroid_offset_le_40khz`：空口 FM 的峰值随调制/邻台
   漂移，不是频率精度指标（与 `ring2` 同一理由）。改为信息字段 + 明确注明
   「绝对频率精度需已知频率源」，与人工卡 R2 对齐。
2. `acceptance_suite` A3 用 16384 样本/次读流：同样 2.4 MS/s 下实测
   16384/块 **1.74 MS/s**、65536/块 **2.15 MS/s**、262144/块 **2.28 MS/s**——
   小块低吞吐是 **Python 每次调用开销**，不是设备流。改为 262144 样本/块后
   吞吐 2.27 MS/s，判据才在量设备而不是量解释器。
3. R1 五次开关与跨环串行打开：上一句柄 USB 释放有延迟，会瞬时「枚举不到设备」。
   新增 `common.transient_open_error()`（device-not-found / access-denied 统一按瞬时处理），
   最多 4 次 ≈1.8 s 重试；重试次数记入 `cycle_transient_retries` 与注释，
   **不是**把失败藏起来——真拔线时重试耗尽仍会 FAIL/SKIP。

### 6.2 19 kHz 导频：同台重复性实验（判「台侧差异」还是「我们侧误差」）

两次 R3 读到 19006 Hz/7.3 dB 与 18841 Hz/8.9 dB，都 `skipped`；第二次偏
19000±100 Hz 规格 159 Hz。但两次测的是**不同电台**，无法区分「台侧偏移」、
「我们侧重采样比错」、「测法不稳」。测法（`scripts/hw_acceptance/station_stability.py pilot`）：
2.4 MS/s IQ → 正交鉴频 → `resample_poly` **带抗混叠**降到 200 kHz →
Welch（65536 点/Hann，不重叠）→ 18.5–19.5 kHz 取峰 + 抛物线插值（bin 3.05 Hz）。

| 实验 | 台 | 段数 / 每段 | 逐段导频频率 | 段间极差 | 峰-邻域 dB |
|---|---|---|---|---|---|
| 同台重复 | 98.0 MHz | 5 × 30 s | 19000.22 / 19000.68 / 19000.25 / 19000.40 / 19000.52 | **0.46 Hz** | 37.06–38.19 |
| 同台重复 | 91.24375 MHz（此前报 18841 的台） | 3 × 12 s | 19000.37 / 18999.60 / 19000.19 | **0.77 Hz** | 12.05–13.46 |
| 换采样率 | 98.0 MHz | 1 × 8 s / 速率 | 1.024 MS/s→19000.01；2.048→19000.53；2.400→19001.40 | 1.39 Hz | 37.62–39.39 |

> 本轮 `scan` 实测最强台是 93.305 MHz（33.4 dB），98.0 MHz 不是最强；
> 按「固定同一强台」的要求仍取 98.0 MHz（它有 19 kHz 导频，37.9 dB）。

判定（三选一里落第二条，但真因在第三条的「测法」上）：

1. **不是我们侧的频率标定/重采样比错**：同台段间极差 0.46–0.77 Hz，
   绝对值 19000 ±0.7 Hz；若重采样比错 1%，19000 Hz 会被读成 19190 Hz。
   换三个请求速率读数一致（1.39 Hz 内），说明速率标定与抽取比都没问题。
2. **不是台侧差异**：此前报 18841 Hz 的 91.24375 MHz 台，用带抗混叠的测法
   读回 19000 Hz——那 159 Hz 是折叠假峰，不是该台导频偏离规格。
3. **真因是 ring3 旧测法**：`mpx[::dec]`（dec=12）无抗混叠直接抽取，2.4 MS/s 下
   200 kHz·k 附近的杂散会折回通带；`181159 Hz` 折到 `200000 − 181159 = 18841 Hz`，
   正好落在 18.5–19.5 kHz 搜索带里且幅度压过真导频。再叠加单段 131072 点 FFT、
   无平均、无插值 → 幅度也被折叠底压低（旧法 7.3–8.9 dB vs 新法同台 12.0–38.2 dB）。

已修：`ring3._pilot` 改为 `resample_poly` 抗混叠 + Welch 平均 + 抛物线插值 —— `a19f506e` — `scripts/hw_acceptance/ring3_demod_audio.py:48` — `resample_poly`。
同时把采集改成连读约 6 s IQ（读线程修复后实得 5.7–5.9 s，约 17–18 段）而不是单段 131072 点。真机 98.0 MHz 复跑 →
`PASS`，导频 **19003.0 Hz / 37.7 dB**；91.24375 MHz 复测 12.05–13.46 dB
仍过 10 dB 门限。

**段数对幅度读数的影响（同一台 93.305 MHz；下面的段数是旧采集速率下的实验值）**：5 段 10.4 dB、14 段 15.2 dB、
22–26 段 16.7–20.1 dB——短采集的估计方差会把一台稳定立体声台压到 10 dB
门限附近（10.4 只差 0.4 dB）。所以采集时长从 2 s 提到 6 s，不是调门限。
**幅度口径**：37.7 dB 是「峰/邻域中位数」，与产品链 `pilot_snr_db`
（导频 RMS / MPX 总 RMS，典型 −20 dB）不是一个口径，不可互比；
10 dB 门限只对前者成立。

> 采集时长本身「实得多少」由丢样决定，见 §6.2.1：修复前 6 s 只采到 5.0–5.2 s，
> 段数因此是 15；读线程修好后回到 17–18 段。门限未动。

#### 6.2.1 采集链丢样：请求 6 s 实得 5.0–5.2 s → 读线程修复后 1.7–3.5%

仪器：[capture_throughput.py](file:///d:/mbdsdr/MBDSDR/scripts/hw_acceptance/capture_throughput.py)
（同台同参数逐块记录「读耗时 / 转换耗时」，四种读法对照）。
数据源：[ring3_demod_audio.py](file:///d:/mbdsdr/MBDSDR/scripts/hw_acceptance/ring3_demod_audio.py)
的 `pilot_*` 字段。

**修复前（旧 ring3：读一块 → 立刻转一块 → 再读）**：93.305 MHz / 2.4 MS/s /
请求 6.0 s / 块 262144，5 次：

| # | captured_s | wall_s | mpx_points | 吞吐 MS/s | 丢样 | Welch 段数 | 导频 Hz | 高于邻域 dB |
|---|---|---|---|---|---|---|---|---|
| 1 | 5.243 | 6.105 | 1,048,608 | 2.061 | 12.6% | 16 | 19002.1 | 13.1 |
| 2 | 5.134 | 6.077 | 1,026,762 | 2.043 | 14.4% | 15 | 18999.5 | 15.2 |
| 3 | 5.134 | 6.058 | 1,026,762 | 2.034 | 14.4% | 15 | 19001.9 | 13.4 |
| 4 | 5.134 | 6.032 | 1,026,762 | 2.043 | 14.4% | 15 | 19000.3 | 12.5 |
| 5 | 5.024 | 6.011 | 1,004,916 | 2.006 | 16.3% | 15 | 19000.2 | 16.2 |

均值 14.4%。注意「15×65536 = 4.92 s」只是 Welch 用掉的整段部分，不是实际
采集时长；真实值看 `pilot_mpx_points`（5.13 s）。

**归因（每格 3 次，块 65536 / 262144 / 1048576，请求 6 s，丢样取三次范围）**：

| 读法 | 65536 | 262144 | 1048576 | 每块读耗时中位 | 每块转换耗时中位 |
|---|---|---|---|---|---|
| sync+conv（旧 ring3） | 14.9 / 15.3 / 15.3% | 16.3 / 14.4 / 14.4% | 12.6 / 12.6 / 12.6% | 27.7 / 112.9 / 451 ms | 4.4 / 14.9 / 54 ms |
| sync_only（只读不转） | 1.2% | 1.7% | 0.0%（跨窗多算 1 块） | 同上 | — |
| thread+queue（现 ring3） | 0.3 / 0.8 / 0.3% | 1.7 / 3.5 / 3.5% | 5.3 / 5.3 / 5.3% | 同上 | 3.4 / 14.9 / 62 ms |
| async+queue（librtlsdr 异步） | 0.0% | 0.0% | 0.0% | — | 4.1 / 15.1 / 54 ms |

三条判据：

1. **不是 USB 带宽不够**：异步读吞吐 **2.370–2.398 MS/s**（请求的 98.8–99.9%），
   丢样 0；纯同步读也有 **2.317–2.361 MS/s**。
2. **主体是「转换占住读循环」**：每格转换总耗时 0.63–0.92 s / 6 s（占 10.5–15.3%），
   与 sync+conv 的丢样 12.6–16.3% 同阶——转换期间没有任何在途传输，设备 FIFO
   溢出。块越大单次空档越长，但每 6 s 的转换总量不变，所以三种块大小的丢样
   都在 12.6–16.3%。
3. **同步块读另有 1.3–3% 传输间死区**：262144 时读耗时 112.9 ms ≈ 109.2 ms
   信号 + 3.7 ms 固定开销；异步读（多缓冲在途）能补掉这部分。

**修复（已落地）**：`_pilot_from_ctrl` 改为**独立读线程 + 有界队列**（6 块 ≈ 12 MB，满则读线程阻塞，内存有界；`26fc39fc` — `scripts/hw_acceptance/ring3_demod_audio.py:110` — `queue.Queue`）。
主线程只做鉴频/抗混叠；窗口前预热 0.7 s（scipy 首次滤波设计），不把一次性成本算成丢样。同台同参数 4 次有效：

| # | captured_s | wall_s | 吞吐 MS/s | 丢样 | Welch 段数 | 导频 Hz / dB |
|---|---|---|---|---|---|---|
| 1 | 5.898 | 6.195 | 2.285 | 1.7% | 18 | 19000.6 / 15.7 |
| 2 | 5.789 | 6.115 | 2.272 | 3.5% | 17 | 19000.2 / 15.4 |
| 3 | 5.898 | 6.195 | 2.285 | 1.7% | 18 | 18999.2 / 14.8 |
| 4 | 5.789 | 6.114 | 2.272 | 3.5% | 17 | 19000.3 / 14.4 |

（1.7% 与 3.5% 交替来自块粒度：53 或 54 个整块，1 块 = 1.8%。）

残差不是修法没生效：① 同步块读 2.27–2.32 MS/s < 请求 2.4，6 s 墙钟最多拿到
约 5.9 s；② 块粒度 1 块 = 1.8%，窗口末尚未转换的 1 块不计入 captured，
所以 3.5% 是上界。**这是本机 USB/驱动边界**（异步读可达 2.37–2.40 MS/s，
但要绕开 controller 直接调 pyrtlsdr，rtl_tcp 源不可用，故不采用）。
块 65536 实测 0.3–0.8% 更低，但队列弹性只有 0.16 s（262144 是 0.65 s）且
读调用多 4 倍，取 262144。

未选更激进的口径：读满 `fs×seconds` 个样本（captured 恒等于 6.0、drop 恒为 0）
会把墙钟拖到约 6.3 s，掩盖「请求 6 s」的真实供给差；因此保留「墙钟窗口 +
如实记账」。

复跑：

```powershell
python scripts\hw_acceptance\capture_throughput.py --seconds 6 `
  --blocks 65536,262144,1048576 --modes sync,sync_only,thread,async `
  --mode-runs sync=3,sync_only=1,thread=3,async=1 --out D:\mbdsdr\tmp\throughput.json
python scripts\hw_acceptance\ring3_demod_audio.py --freq 93.305e6
# 看 result.json 的 pilot_captured_s / pilot_drop_pct / pilot_mpx_points / pilot_welch_segments
```

#### 6.2.2 产品主路径：读与处理本来就不在同一线程（同仪器 0.0–1.7%，不是 12–16%）

上一节修的是**验收脚本**的串行读法。产品桌面走的是另一条路，用同一仪器
（`capture_throughput.py --modes product`）实测：

- `RTLSDRBackend.connect()` 启动 `_ThreadedRingReader` 生产者线程
  （`mbdsdr_ai/sdr_backend.py` 的 `_produce_loop`，8192 样点/块、ring 1,000,000 样点
  ≈ 0.42 s），USB bulk 读只发生在这条线程；
- 桌面 `ReceivePipeline` 的 `IqReaderThread` 从 ring 订阅连续读 16384 块，经
  ping-pong 分发给 `FftWorker` / `DemodWorker` / `RecordTap`
  （`desktop/receive_pipeline.py`）；
- 探针开的就是产品类 `RTLSDRBackend`，三个订阅者分别模拟：解调（桌面
  `DemodWorker` 用的同一个 `mbdsdr_ai.dsp.WFMReceiver`，不含声卡与 UI）、FFT、
  20 fps 频谱快照。

同台（93.305 MHz / 2.4 MS/s / 请求 6 s / 消费块 16384），3 个独立进程各 1 次 +
同一进程连跑 5 次：

| 批 | # | ring 供给 produced_s（MS/s） | 解调消费 captured_s | 丢样 | 解调每块中位 | audio / fft 订阅者 dropped | ring 积压 max | CPU s | RSS 峰值 |
|---|---|---|---|---|---|---|---|---|---|
| 独立进程 | 1 | 6.001（2.400） | 6.007 | 0.0% | 1.91 ms | 0.0 / 0.0 s | 3.4 ms | 2.77 | 127.4 MB |
| 独立进程 | 2 | 5.990（2.396） | 6.001 | 0.0% | 1.95 ms | 0.0 / 0.0 s | 6.8 ms | 2.61 | 127.4 MB |
| 独立进程 | 3 | 5.888（2.355） | 5.898 | 1.7% | 2.08 ms | 0.0 / 0.0 s | 6.8 ms | 2.89 | 126.5 MB |
| 同进程连跑 | 1–5 | 5.967–5.997（2.386–2.399） | 5.973–6.001 | 0.0–0.4% | 1.8–2.1 ms | 0.0 / 0.0 s | ≤6.8 ms | 2.64–3.25 | 127.2–158.0 MB |

> 连跑批的 RSS 每次约 +7.7 MB（≈ ring 的 8 MB），但换成独立进程每次都是
> 126.5–127.4 MB——属 Windows 堆保留而非单连接泄漏；产品正常一次连接只建一个
> ring。该增长未改产品代码，仅记录观测量。

判据：

1. **处理没有占住读循环**：audio 与 fft 订阅者 `dropped` 都是 **0.0 s**；WFM 解调
   每 16384 块 1.8–2.1 ms（对应信号 6.83 ms，约占 30% 单核），ring 积压 ≤6.8 ms
   ——读线程与处理线程互不阻塞。若按旧 ring3 的串行读法，转换期间没有在途 bulk
   传输，丢样会回到 12–16%。
2. **残留 0.0–1.7% 是 USB 供给差，不是处理丢样**：ring 生产者 6 s 实际写入
   5.888–6.001 s（2.355–2.403 MS/s），与 §6.2.1 的 sync_only 同一边界；解调消费
   与供给差 ≤10 ms。`drop_pct` 沿用 ring3 的 `max(0, 1 − captured/6)`，把供给差
   如实记账，不掩盖成 0。
3. **20 fps 频谱快照只覆盖 13.6–13.8% 是有意为之**：每 50 ms 取最新 16384 点做
   一帧快照，本来就不承诺连续消费；跳过记在它自己的 `sub_dropped`，不参与
   音频/录制路径。**不能把这 14% 读成「产品丢样」**。
4. **`ring.dropped_total()` 按订阅者记账，可以当产品丢样指标**：口径是
   「Σ 各订阅者 `dropped`」（`mbdsdr_ai/sdr_backend.py::dropped_total()`），
   写侧覆盖旧区域前给每个落后订阅者独立跳游标、只记它自己跳过的样点；
   `SDRStatus.dropped_samples` 读的就是这个合计，`clear()` 不清历史。
   **旧口径的假数字**（`_count` 只跟默认 `read()` 记账）：product 探针第一版
   在 2 s 窗口报 `ring_dropped_total_s=1.908 s`，而解调实际消费 1.959 s、
   三个订阅者 dropped 全 0。换成 6 s 窗口更好算：按请求 2.4 MS/s 满速写作
   14.4 M 样点、默认 `read()` 一次没调时，`_count` 涨满 1 M 之后其余
   **13.4 M 样点（≈5.6 s，占窗口约 93%）全被记成丢弃**；本机 USB 实际供给
   5.62–6.00 s（13.5–14.4 M）时，旧口径会报 **12.5–13.4 M（≈5.2–5.6 s，
   87–93%）**，新口径下同一场景为 0
   （`tests/test_fake_rtl_chain.py::test_following_subscriber_not_counted_as_dropped`
   与 `test_two_subscribers_each_read_half_no_drop` 钉住）。
   注意合计口径的边界：同一样点被 N 个订阅者各跳过一次会计 N 次，它回答
   「订阅者视角丢了多少」，不是「设备少产了多少」——供给差看 `produced_s`。

结论：**产品没有这个病**。验收脚本会踩，是因为它用 `DeviceManager.open()` 返回的
`OpenDevice`（裸 pyrtlsdr，一次调用一次同步 bulk 读，没有常驻读线程），旧
`_pilot_from_ctrl` 又在这个循环里串行做 13–17 ms 的 MPX 转换；产品用 ring 生产者
+ 独立 worker，转换不占 USB 读线程。**这次 ring3 的修复是验收仪器的修复，不能当
产品性能证据**。本探针覆盖解调/频谱消费；`RecordTap` 的落盘吞吐不在表内（它和
音频一样由同一读线程每块分发，但磁盘写另计）。

#### 6.2.3 异步读：做 / 不做 / 什么条件下做（架构决策记录）

背景数字：异步读 2.370–2.398 MS/s、丢样 0；同步块读 2.27–2.36。差 2–5%。

| 维度 | 绕开 controller 直连 pyrtlsdr 异步读 | 现同步读线程 / ring |
|---|---|---|
| rtl_tcp / 远程源 | `RtlSdr.read_bytes_async` 只对本地 USB 有效；`RTLSDRBackend(host=...)` 的 TCP 源没有对应接口，只能另写一套或在 host 模式回退同步，同一后端出现两条语义 | 本地与 rtl_tcp 共用 `read_fn`，行为一致 |
| 热插拔 | 回调线程里出错或静默停止；拔出后 `cancel_read_async` 可能挂在失效句柄上，必须外加看门狗并把错误送回主线程 | 每次 `read_fn` 同步抛错，生产者循环 `logger.warning` + 10 ms 重试 |
| 设备生命周期 | 回调可能在 `cancel` 之后仍被调用；`close()`/重开顺序错会 use-after-free；重连还要重新枚举（本机还有占位序列号 `rtl-0` 的坑） | 句柄只在生产者线程里用；断开 = 停线程 + close，路径短 |
| 错误恢复 | 回调内异常若被吞，主线程只看到「没数据」；要自带计数（callback / queue-full / 短回调）才能区分「设备停」与「消费慢」 | `None`/空块有明确分支，异常与欠读可计数 |
| 背压 / 内存 | librtlsdr 内部多缓冲不感知 Python 消费速度；回调里排队满只能丢，必须自己记账，否则又是静默丢样 | 有界队列满则读线程阻塞，内存有界（ring3 已用） |
| 收益 | 供给 2.37–2.40 vs 2.27–2.36；6 s 丢样 0 vs 1.7–3.5%（验收）/ 4.1–4.9%（产品） | — |

**决策：不做**（默认路径保持同步读线程 / ring）。理由：12.6–16.3% 的病根是串行
转换，已由读线程消除；剩下的 1.7–3.5%（验收）/ 4.1–4.9%（产品）是 USB 供给差，
收益 <5%，却要新增回调错误通道、热插拔与生命周期处理，并把 rtl_tcp 与本地 USB
拆成两条路径。

**什么条件下做**（任一）：

1. 出现「必须样点完整」的用户可见需求（例如 IQ 录制逐样点对齐、长时无缺口），
   且 4–5% 的供给差会破坏该需求；
2. 异步适配器只替换 `_ThreadedRingReader` 的 `read_fn`，ring / 消费者 / 线程模型
   不变，同时补齐：回调异常通道、queue-full 计数、`cancel_read_async` + 3 s
   join、拔出/重插测试；`host`（rtl_tcp）模式自动回退同步；
3. `mbdsdr_ai/sdr_backend.py` 的 claim owner 同意并承担回归。

在此之前，不得把异步读的 2.37–2.40 MS/s 写进产品指标；它只是本机「带宽够」的证据。

#### 6.2.4 `product` 模式：非门限的可选证据项（**不计入七环 PASS/FAIL**）

`capture_throughput.py --modes product` 是**可选证据**，不是验收门限。七环
（R1–R7）的 PASS/FAIL 完全由 `run_all.py` 判定；本条不参与、不加分、不卡合并，
失败只写进证据文件，不改变任何环的状态。

跑它只有一个目的：以后有人改 `_ThreadedRingReader`、ring 的 `read_fn` 或桌面
读循环（`desktop/receive_pipeline.py::IqReaderThread`）时，能在约 20 s 内确认
「读与处理仍不在同一线程、订阅者没有被迫丢样」。

命令（先按 §四 的跑前自检确认设备空闲）：

> **干净窗口三条件（与 `COORDINATION.md` §6.1 同口径，缺一不得开跑）**：
> ① 占用表无活跃行且 `python scripts\file_claim.py status` 无活跃锁；
> ② 进程列表无 `deadscan.py` / `perf_probe_deep.py` / `capture_throughput.py` /
> `run_all.py` / `acceptance_suite.py` / `pytest`，或单核持续 ≥90%；
> ③ RTL-SDR PnP `Status OK`。只满足「表里没人写」不算干净窗口：2026-11-06 的
> 一组 product 数据就是在他线 `deadscan.py`（pid 43832，单核 100%，累计 CPU 1132 s）
> 常驻时作废的（详见 §6.2.4 表 E 组与 `COORDINATION.md` §6.1）。

```powershell
python scripts\hw_acceptance\capture_throughput.py --seconds 6 --blocks 16384 `
  --modes product --mode-runs product=3 --out D:\mbdsdr\tmp\product_path.json
```

参数：`--seconds 6` 采集窗口；`--blocks 16384` 消费块大小（与桌面读线程一致）；
`--mode-runs product=3` 独立采集 3 次；`--freq/--gain` 用默认值（93.305 MHz /
17.9 dB，与 §6.2.2 同台同源）。约 **20 s** 完成，产物字段：`produced_s` /
`produced_msps`、`captured_s` / `drop_pct`、`sub_dropped_s`（按订阅者分项）、
`ring_dropped_total_s`（Σ 订阅者口径）、`backlog`（`max_ms`）、`cpu_s`、
`rss_peak_mb`。

判据（三条同时成立才算「证据成立」；不成立只记「不成立」，不改环状态）：

1. **解调丢样 ≤1.7%**：`drop_pct`，沿用 `max(0, 1 − captured/6)`，把 USB
   供给差如实记账，不掩盖成 0；
2. **订阅者 dropped = 0**：`sub_dropped_s["probe-audio"]` 与
   `sub_dropped_s["probe-fft"]` 都必须是 0.0 s；`probe-gui`（20 fps 快照）按
   设计本就只读 13–14%，它自己的 dropped **不计入**本条；
3. **ring 积压 ≤10 ms**：`backlog.max_ms`，即解调订阅者落后生产者不超过 10 ms。

复跑口径：3 次里任一次满足即可。`ring_dropped_total_s` 应与 `sub_dropped_s`
三项之和一致；不一致说明丢样记账又被改坏或有未登记订阅者——按缺陷处理，
**不要把它当成性能门限数字**。

**前置：独占窗口要三合一证据，且「空表 ≠ 无人占」。** `COORDINATION.md §6.1`
的占用表只有模板行，**不等于没人占**——2026-11-06 06:10 的实测就是表里
`_（空）_`、实际有人常驻 `deadscan.py`（单核持续 100%，E 组因此作废）。
开跑前必须同时给出并留时间戳：① 占用表无活跃行；② 进程列表无 `deadscan.py` /
`perf_probe_deep` / `ui_narrow_breakdown` / `hw_acceptance`；③ 设备 PnP `OK`。
判读顺序永远是**先看 `produced_msps`、再看 `drop_pct`**：单核满载的耗时扫描会
把供给打掉，此时 product 数据只能作废重跑，不是代码退化。

`drop_pct` 跟着 USB 供给走，不只看代码。下表是 2026-11-06 05:30–06:23 本机
**同台同源**（93.305 MHz / 17.9 dB）、六组窗口各 3 次 × 6 s 的并列实测。
**组别先按「能不能当判据」分开**，不要混着引用：

| 组 | 窗口条件 | 用途 | 供给 MS/s | `captured_s`（按次） | `drop_pct`（按次） | `backlog.max_ms` | `underruns` | `cpu_s` | `rss_peak_mb` |
|---|---|---|---|---|---|---|---|---|---|
| A | 并行 `perf_probe_deep.py` | **被污染，仅方向判断** | 2.246–2.302 | 5.670 / 5.755 / 5.615 | 5.3 / 4.0 / 6.4 | 3.41 | 0 | 3.08 / 3.02 / 3.64 | 127.6 / 135.3 / 142.9 |
| B | 无并发探针 ① | **可当判据** | 2.294–2.384 | 5.734 / 5.960 / 5.891 | 4.4 / **0.7** / 1.8 | 3.41–6.83 | 0 | 2.97–3.31 | 127.3–135.2 |
| C | 无并发探针 ② | **可当判据** | 2.195–2.305 | 5.495 / 5.762 / 5.639 | 8.4 / 4.0 / 6.0 | 3.41–6.83 | 0 | 2.92–3.20 | 127.4–142.9 |
| D | 无并发探针 ③ | **可当判据** | 2.310–2.375 | 5.939 / 5.912 / 5.775 | **1.0 / 1.5** / 3.7 | 3.41 | 0 | 2.92–3.14 | 127.2 / 134.8 / 40.0⚠ |
| E | 他线常驻 `deadscan.py`（单核持续满载） | **被污染，仅方向判断** | 2.337–2.378 | 5.946 / 5.844 / 5.939 | 0.9 / 2.6 / 1.0 | **23.89** / 3.41 / 3.41⚠ | 0 | 3.06–3.23 | 127.5–143.2 |
| F | 独占窗口（`deadscan.py` 已退出，06:22:55–06:23:20；`product_final_clean.json`） | **可当判据（真·独占）** | 2.382–2.391 | 5.987 / 5.973 / 5.967 | **0.2 / 0.4 / 0.6** | 3.41 | 0 | 2.89–3.17 | 127.7 / 135.3 / 143.4 |

- 可判据组（B/C/D/F）开跑前逐项确认：无 `perf_probe_deep` / `ui_narrow_breakdown` /
  `deadscan.py` / `pytest` / 其它 `capture_throughput` 进程，设备 PnP `OK`。
  **其中只有 F 满足「三合一独占证据 + 时间戳」**（占用表无活跃行、进程列表干净、
  PnP `OK`，06:22:55 开跑、06:23:20 结束），是最可信的一组；B/C/D 虽无探针，
  但宿主仍有 IDE/浏览器等常态负载，供给只到 2.19–2.38 MS/s。
  被污染组（A/E）**数字不代表代码能力，不得引用为判据**：A 是被并行压测探针
  挤占；E 是他线常驻 `deadscan.py`（pid 43832，单核持续 100%，本线无权终止，
  只能如实记录，E 组整体作废）。
- **判据 2 在 18/18 次全部成立**：`probe-audio`/`probe-fft` 的 dropped 均为
  0.0 s，`ring_dropped_total_s` 与分项之和逐次严格相等；`probe-gui` 只读
  13.6–14.7%（其自身 dropped 不计入判据 2）。
- **判据 3 在可判据组 12/12 成立**（≤6.83 ms）；被污染组 E 的 1 次出现 **23.89 ms**
  单点尖峰（同次 p95 = 0.0、中位 0.0，属瞬态），按「不成立」记，不掩盖。
- **判据 1 的真值在独占窗口**：F 组 3/3 成立，`drop_pct` **0.2 / 0.4 / 0.6%**
  （供给 2.382–2.391 MS/s）。可判据组 12 次里 6 次 ≤1.7%（0.7 / 1.0 / 1.5 /
  0.2 / 0.4 / 0.6），其余 1.8–8.4%；全部 18 次里 8 次成立。`drop_pct ≈
  1 − produced_s/6`，主要由 USB 供给缺口决定，与读块耗时无关：读中位 4.2–4.9 ms，
  仍低于 16384 点在 2.4 MS/s 下的供给周期 6.83 ms。
- **判读顺序固定**：先看 `produced_msps`——≥2.35 且 `drop_pct` 仍 >1.7% 才怀疑
  代码退化；供给掉到 2.2–2.3 时第 1 条按「不成立」记，**不改阈值、不改代码**。
  F 组在 2.382–2.391 下拿到 0.2–0.6%，说明历史 8 次 0.0–1.7%（§6.2.2 表）在
  真正独占时是可复现的；唯一一次「供给 ≥2.35 但 drop 略超」是 B-run3
  （2.357 MS/s / 1.8%，超 0.1 个百分点），单次不作退化结论。
- ⚠ D-run3 的 `rss_peak_mb` 40.0 与同组其余两次（127–135）不成比例，未查到
  解释，按异常读数原样记录，不作为内存结论。

重采样比算式（音频链，`decode_stereo → _resample_to`）：
`gcd(2048000, 48000) = 16000` → `up = 3`、`down = 128` → 有效比 128/3 = 42.6667，
`48000 × 128/3 = 2,048,000` 精确可逆；请求 2.4 MS/s 时 `gcd = 48000` →
`up=1/down=50`（同样精确）。实际生效的是 **1/50**：R3 固定 `FS = 2.4e6`，
3/128 只在应用侧用默认 2.048 MS/s 时生效。

### 6.3 R2 噪声底漂移：同台同增益 10 次分布 → 阈值重定（6 → 8.5 dB）

01:45 那次 drift 4.79 dB 占旧阈值 6 dB 的 80%，是「单次贴近阈值」的观感问题。
口径与 `ring2` **完全一致**（2.4 MS/s、1024 点、±700 kHz、读 35 帧丢前 3 帧、
帧间隔 20 ms），同一台 **98.0 MHz**、同一增益 **17.9 dB**，连接-断开 10 次：

| # | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
|---|---|---|---|---|---|---|---|---|---|---|
| drift (dB) | 6.49 | 6.81 | 5.89 | 5.35 | 6.29 | 4.86 | 5.55 | 6.28 | 4.22 | 5.01 |

- mean **5.67**、σ **0.82**；单次极差 4.22–6.81 dB，10 次之间极差 2.59 dB。
- 旧阈值 6 dB 低于这个统计量本身的自然范围：10 次里 **2 次**（6.81、6.49）
  会假红——说明 6 dB 是从单次观测拍的，不是从分布来的。
- 按分布重定：**mean + 3σ = 8.14 → 取 8.5 dB**（覆盖实测最大值 6.81，余量 1.7 dB）。
- **不采用**「1.5×实测最大极差 = 10.2 dB」这个更松的口径；取更严的 8.5，
  不存在为了让数字好看而放宽。
- 反证（测试锁定）：回到 6.0 时同一批分布里 2 次观测 FAIL；阈值必须
  `> 6.81` 且 `< 10.2`（=1.5×6.81 取到 0.1 dB）。A2 与 R2 直接用同一个常量
  `FLOOR_DRIFT_MAX_DB`。
- 另有一台 93.3 MHz 同法实测 drift **1.87 dB**（`ring2` 真机复跑）——漂移随台/功率
  变化，阈值按最坏台（98.0）的分布取，不能按最舒服的台取。
- 说明：另有两批（帧间未加 20 ms 间隔）观测到 4.34–7.59 dB；口径与 `ring2`
  不同，**不作为定阈依据**，仅记录。

**这条结论是被测试锁住的，不是文档里的一句声明**：

```powershell
# 契约测试：阈值必须落在 (6.81, 10.2) 内且等于 8.5（3 条一起跑）
python -m pytest tests\test_hw_acceptance_contract.py -q -k "floor and threshold"

# 反证 A：把 ring2.FLOOR_DRIFT_MAX_DB 临时改成 6.0 → 必须红
#   AssertionError: 阈值 6.0 dB 没覆盖 10 次实测的最大极差 6.81 dB，正常观测会假红
# 反证 B：临时改成 10.2 → 必须红
#   AssertionError: 阈值 10.2 dB ≥ 1.5×实测最大极差 10.2 dB，属于为好看放宽
#   两次实测均 1 failed（0.17 s），改回 8.5 后 16 passed
```

> **单笔 checkout 的已知不自洽（历史已推送、不改写）**：`a19f506e` 引入
> `station_stability.py` 时已 import `ring2.FLOOR_DRIFT_MAX_DB`，而该常量是
> 下一笔 `b2f95ac6` 才加的。所以**必须按序 checkout `a19f506e → b2f95ac6`**
> （或任何之后的提交）。单独 checkout `a19f506e` 时实测：
> `ImportError: cannot import name 'FLOOR_DRIFT_MAX_DB' from 'ring2_spectrum_waterfall'`
> （exit 1）。从当前 HEAD 起该依赖改为**使用点延迟导入**：常量缺失时给
> 「请按序 checkout a19f506e → b2f95ac6」的可读 `SystemExit`，不再是裸 `ImportError`。

## 七、R8 热插拔 / 拔线（半自动：人动手，脚本计时判定）

脚本：`scripts/hw_acceptance/manual_usb_cycle.py`。**人工只做物理动作**
（拔/插 USB、拔网线、停/启服务端），计时与 PASS/FAIL 由脚本完成。

```powershell
# 只看判据、不作任何动作（无操作者时的实跑口径）
python scripts\hw_acceptance\manual_usb_cycle.py --mode usb
python scripts\hw_acceptance\manual_usb_cycle.py --mode rtl-tcp --port 1234
python scripts\hw_acceptance\manual_usb_cycle.py --mode spyserver --port 7355 --cut server

# 真跑（需要人守在设备旁；脚本会做 5s 倒计时）
python scripts\hw_acceptance\manual_usb_cycle.py --mode rtl-tcp --port 1234 --cut cable --interactive
```

- 证据：`.tmp-tool-results/hw_acceptance/hotplug_<mode>/result.json`（全部 D 盘）。
- 计时口径：**以最后一帧真实数据为 T0**，`detect_s`/`stale_s` 是静默持续时长；
  插回首帧用**重连成功→首帧**的软件延迟，另记 `human_wait_s` 供参考。
- SKIP 原因码（R8 专用，不进七环汇总）：`no_operator` / `no_hw_env` / `no_device` /
  `no_endpoint` / `no_stream` / `operator_cancel`。SKIP 不产生任何耗时数字。

三种后端路径不同，必须分开验（数值来源：`docs/network-layer.md` §0/§2/§7 本机实测）：

| 后端 / 拔法 | 步骤与观察点 | 上限 | 数字来源 |
|---|---|---|---|
| **本地 USB**（`--mode usb`） | ① 拔 USB → `connected=False`、`link_state=disconnected`；② 插回 → 重连并出首帧 | ① **≤0.5s**；② **≤1.0s** | `UNVERIFIED_EXT`：沿用网络源 RST **0.000s** / FIN **0.103s** 实测的 0.5s 上限；首帧沿用换端口 **0.022s** 的 1.0s 上限 |
| **rtl_tcp · 拔网线**（`--cut cable`） | ① 半开 → `stale` → 硬判 `disconnected`；② 重插 → 重新建连 → 首帧 | ① stale **≤5.5s**、硬判 **≤11.0s**；② **≤1.0s** | 实测 stale **5.004s**、硬判 **10.034s**（`TestRealThresholdLatency`, idle=5s） |
| **rtl_tcp · 停服务端**（`--cut server`） | ① 杀进程 → RST/FIN → `disconnected`；② 重启 → 重连 → 首帧 | ① **≤0.5s**；② **≤1.0s** | 实测 RST **0.000s** / FIN **0.103s**（§2） |
| **SpyServer · 拔网线**（`--cut cable`） | ① 半开 → stale → 硬判；② 重插 → 重新握手 → 首帧 | ① stale **≤5.5s**、硬判 **≤11.0s**；② **≤1.0s** | 实测 stale **5.010s**、硬判 **10.027s**（§0） |
| **SpyServer · 停服务端**（`--cut server`） | ① 停进程 → RST/FIN；② 重启 → 重新握手 → 首帧 | ① **≤0.5s**；② **≤1.0s** | 实测 RST **0.000s** / FIN **0.098s**（§3） |
