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
| R3 解调与音频 | 音频帧数、声道、RMS、<15k/>15k 能量比、19 kHz 导频 | RTL-SDR + 强台；听感需声卡/人耳（脚本不出声） | **部分**：真实 OTA → 产品解调链可验数据面；**不能**验声卡、实时流、听感 | **PASS**：7680 帧/2 声道、RMS 0.6334、能量比 34.7；导频 19006 Hz/7.3 dB 过弱 → 记 `skipped`（单声道台合法） | **SKIP** / `NO_HW`；离线替代 **PASS**：RMS 0.2306、能量比 57.4、导频 19000 Hz/31.8 dB |
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
| 32 帧噪声底漂移 | | ≤6 dB |
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
| 19 kHz 导频 | | 有则 19000±100 Hz；无则记「单声道」 |
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

| 命令 | 实测耗时 | 依赖（设备/数据/端口） | 产物 / 口径 |
|---|---|---|---|
| `python scripts\hw_acceptance\run_all.py` | **80.4 s**（真机 7/7 PASS）；**56.1 s**（强制无硬件：PASS 3 / SKIP 4） | R1–R4：RTL-SDR；R5：TLE 缓存 + skyfield；R6：`de421.bsp` + PySide6；R7：纯软件 | `summary.json` / `summary.md` / 逐环 `result.json`；SKIP 必带原因码 |
| `python scripts\hw_acceptance\run_all.py --only r5,r6,r7` | 约 **34 s**（本机实测 25.0+4.6+4.2，串行） | TLE + skyfield + `de421.bsp` + PySide6；无射频硬件 | 只跑三个离线纯算环；用于快速回归 |
| `python scripts\hw_acceptance\acceptance_suite.py` | **36.7 s**（真机：PASS 5 / FAIL 0 / SKIP 2）；无设备时全 SKIP（约 6 s） | A1–A4：RTL-SDR；A5：rotctld `127.0.0.1:4533`；A6：COM 口 GNSS；A7：PySide6 + `de421.bsp` | 设备链视角；失败即停；SKIP 带原因码 |
| `python scripts\hw_acceptance\acceptance_suite.py --hw` | **13–14 s**（连续两次 PASS 4/4） | A1–A4：librtlsdr + RTL-SDR | 只跑 A1–A4；无设备 → `PASS 0 / SKIP 4`，SKIP 计数可见 |
| `python scripts\hw_acceptance\negative_control.py` | **29.5 s**（R5 基线占 25 s） | TLE + skyfield + `de421.bsp` + 真实 OTA 夹具 | 4 个反证必须 `RED_AS_EXPECTED`；否则退出码 1 |
| `python -m pytest tests\test_moon_reference_acceptance.py -q` | **0.5 s**（3 passed） | `de421.bsp` + skyfield | 月球对拍 + 1h 反证回归 |
| `python -m pytest tests\test_hw_acceptance_contract.py -q` | **0.5 s**（11 passed） | 无（纯契约） | 锁四码、锁 `skip_result` 永不 PASS、锁 `UNVERIFIED→SKIP(NO_DATA)`、锁 R/A 编号不同义 |
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
| R2 | 噪声底漂移 | 4.79 dB | 2.27 dB | ≤6 dB | 01:45 更贴近阈值（79%），02:57 宽松 |
| R2 | 峰值偏移（仅信息） | 87,782 Hz | 143,772 Hz | 不判 | **不能当频率精度** |
| R3 | RMS / 能量比 | 0.6334 / 34.7 | 0.2023 / 22.9 | 1e-4–1.0 / >2 | 两次都过 |
| R3 | 19 kHz 导频 | 19006 Hz / 7.3 dB | 18841 Hz / 8.9 dB | >10 dB 才算 | **两次都 `skipped`**：02:57 差 1.1 dB 没到门限，且频率偏 19000±100 Hz 规格 159 Hz；只能记「单声道合法」，**不能当立体声证据** |
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

- R3 的**立体声/19 kHz 导频**：两次导频都弱到 `skipped`，只能证明「单声道合法」，
  不能证明立体声解调正确。
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
