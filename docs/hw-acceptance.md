# 真机七环验收套件（hw-acceptance）

把原「手工七环」固化为可重复执行的验收套件。脚本：`scripts/hw_acceptance/`。

## 硬红线（写进脚本与每条输出）

1. **没有真实 SDR 设备时，硬件环一律 `SKIPPED(no_device)`**，不产生任何数字；
2. **严禁**用模拟数据、合成信号、`debug` 调试源把硬件环判为 PASS；
3. 拿不到的字段标 `UNVERIFIED` 并写明原因，不拿推断当结论；
4. 本套件全程静音（不打开声卡）、无窗口（`CREATE_NO_WINDOW`）、每环独立超时；
5. 产物一律落 D 盘 `.tmp-tool-results/hw_acceptance/<时间戳>/`。

## 运行方式

```powershell
# 全跑（七环，约 30–60 秒；无设备时 R1–R4 直接 SKIPPED）
python scripts/hw_acceptance/run_all.py

# 只跑指定环
python scripts/hw_acceptance/run_all.py --only r1,r3

# 单环直跑（证据目录可指定）
python scripts/hw_acceptance/ring3_demod_audio.py --freq 98.0e6 --out D:\mbdsdr\tmp\hw
```

退出码：任一环 `FAIL` → 1；`PASS/SKIPPED/UNVERIFIED` → 0。
每环产物：`rN_*/result.json`；总汇总：`summary.json` + `summary.md`。

## 七环总表（2026-11-05 实际运行）

| 环 | 名称 | 状态 | 关键实测 | 复跑 |
|---|---|---|---|---|
| R1 | 连接与识别 | **SKIPPED(no_device)** | `device_count=0`；未产生数字 | `run_all.py --only r1` |
| R2 | 频谱与瀑布 | **SKIPPED(no_device)** | 未产生数字 | `run_all.py --only r2` |
| R3 | 解调与音频 | **SKIPPED(no_device)** | 未产生数字 | `run_all.py --only r3` |
| R4 | 录音与回放 | **SKIPPED(no_device)** | 未产生数字 | `run_all.py --only r4` |
| R5 | 卫星追踪与过境 | **PASS** | 仰角误差 0.03–0.04°，升起误差 1.5–4.5 s（4 颗） | `run_all.py --only r5` |
| R6 | 天空模式 | **PASS** | 太阳误差 0.005°；离屏渲染 87 KB 非空白 | `run_all.py --only r6` |
| R7 | 发射（含安全闸） | **PASS** | 7 种门判定全部符合预期；审计 11 条可解析 | `run_all.py --only r7` |

> 本次运行证据：`.tmp-tool-results/hw_acceptance/run_20261105/`。
> **R1–R4 需要插上 RTL-SDR**；当前机器未枚举到设备，按红线跳过，绝不补数字。

## 逐环判据

### R1 连接与识别 — `ring1_connection.py`

| 项 | 内容 |
|---|---|
| 前置 | RTL-SDR 已插入；无 SDR++/SDR#/rtl_tcp 占用 |
| 动作 | librtlsdr 枚举 → 读三元组 → connect → 5 次开关 → 释放终检 |
| 期望（可量化） | `device_count>=1`；三元组可读；5/5 开关成功；循环后新句柄可开 |
| 自动断言 | 全部 |
| 人眼 | 无 |
| 失败时用户看到 | 无设备：`未检测到 SDR 设备。请插入 RTL-SDR 后点「重新枚举」…`<br>被占用：`设备被其他程序占用。请关闭 SDR++ / SDR# / rtl_tcp…`<br>缺驱动：`找不到 SDR 运行库（librtlsdr）…` |

### R2 频谱与瀑布 — `ring2_spectrum_waterfall.py`

| 项 | 内容 |
|---|---|
| 前置 | 设备可用；天线接入 |
| 动作 | 自动扫 88–108 MHz 选最强台 → 32 帧 `read_spectrum(1024)` 组成瀑布 |
| 期望（可量化） | 峰底比 ≥ 10 dB；质心峰偏 ≤ 40 kHz；32 帧噪声底极差 ≤ 6 dB；0 NaN |
| 自动断言 | 全部（瀑布矩阵存 `waterfall.npz`） |
| 人眼 | 瀑布滚动是否顺滑、配色可读性 |
| 失败时用户看到 | `当前频点没有足够强的信号。请换已知强台（FM 广播）或接天线后重试。` |

### R3 解调与音频 — `ring3_demod_audio.py`

| 项 | 内容 |
|---|---|
| 前置 | 设备可用；有 FM 强台 |
| 动作 | WFM 解调 → `read_audio(96000)` → 能量分布；信号级 MPX 找 19 kHz 导频；音频写 WAV（不经过声卡） |
| 期望（可量化） | ≥4096 帧；RMS ∈ (1e-4, 1.0)；<15k/>15k 能量比 > 2；立体声台导频 19000±300 Hz 且高于邻域 10 dB |
| 自动断言 | 数据面全部 |
| 人眼 | 音质/爆音/底噪听感（需真实出声，脚本**永不开声卡**） |
| 失败时用户看到 | `当前频点没有足够强的信号…`；无声卡相关错误不会出现（静音设计） |

### R4 录音与回放 — `ring4_recording_playback.py`

| 项 | 内容 |
|---|---|
| 前置 | 设备可用；D 盘可写 |
| 动作 | 录 IQ 1.2 s → 观察字节增长 → 停录 → `inspect_recording` → `load_iq` 回放鉴频 → 改采样率再录 |
| 期望（可量化） | 字节数递增；样本 >0；integrity=`complete`；文件回放/实时 RMS 比 0.3–3；sidecar 采样率 2.4M/1.2M 跟随 |
| 自动断言 | 数据面全部 |
| 人眼 | 回放声音听感 |
| 失败时用户看到 | `录制失败：请检查 D 盘剩余空间与写入权限…` |

### R5 卫星追踪与过境 — `ring5_satellite_passes.py`

| 项 | 内容 |
|---|---|
| 前置 | 能取 TLE（缓存/在线）；无需射频硬件 |
| 动作 | 取 NOAA-15/18/19 + ISS 的 TLE → `predict_passes(24h, ≥5°)` → skyfield 用**同一份 TLE** 独立递推对拍 |
| 期望（可量化） | 峰值仰角误差 ≤ 1.0°；升起时刻误差 ≤ 60 s；列表门限/排序检查 |
| 自动断言 | 全部（观测站坐标为**参数输入**，不是实测） |
| 人眼 | 无（面板视觉属 R6） |
| 失败时用户看到 | TLE 不可用：`TLE 数据不可用：请联网刷新…（不使用内置近似值冒充实测过境。）` |
| 备注 | 无 TLE → `UNVERIFIED(no_tle)`；无 skyfield → 独立参考子项 `UNVERIFIED` |

### R6 天空模式 — `ring6_sky_mode.py`

| 项 | 内容 |
|---|---|
| 前置 | PySide6 + offscreen 平台 |
| 动作 | 太阳/月亮/行星位置计算 → 与脚本内独立 Meeus 算法对拍 → 无站点诚实空态 → RFSkyView 离屏渲染 |
| 期望（可量化） | 太阳误差 ≤ 0.5°；月球 RA/Dec 合法且地月距 0.0020–0.0030 AU；未设站点禁止过境；PNG 像素起伏 >10 |
| 自动断言 | 数据面与渲染冒烟 |
| 人眼 | 星空布局、卫星标记、交互手感 |
| 失败时用户看到 | `天空模式数据异常：请检查观测站坐标与系统时间…` |
| 备注 | 月球**绝对位置**未独立验证（缺星历文件），子项标 `UNVERIFIED` |

### R7 发射（含安全闸） — `ring7_tx_gate.py`

| 项 | 内容 |
|---|---|
| 前置 | 无需发射硬件；本环**不调用任何 TX 后端、不发波** |
| 动作 | 7 种门场景：模型自报 arm_manual / 伪造 token / 正确 token / 复用 / 动作不符 / TTL 过期 / 撤销；检查审计 JSONL |
| 期望（可量化） | 拒绝码精确匹配 `gate_arm_manual_untrusted`、`gate_unknown_token`、`gate_token_used`、`gate_action_mismatch`、`gate_expired`、`gate_revoked`；放行仅一次；审计 ≥4 条且可解析 |
| 自动断言 | 全部 |
| 人眼/外部 | **真实射频发射未验证**（需持证发射设备与频率授权）；RTL-SDR 只能收不能发 |
| 失败时用户看到 | `发射未授权。所有发射必须由真人在界面确认（一次性 token）…` |

## 已知缺陷（验收套件发现，本轮未改产品代码）

1. **过境列表跨午夜排序失真（一般）**：`mbdsdr_ai/orbit.py` 中 `passes.sort(key=lambda p: p["rise_time"])`
   以 `HH:MM:SS` 字符串排序，导致「明天 05:34」排在「今晚 20:48」之前。
   R5 将其记录为 `known_issue`（检查项 SKIP，不掩盖）。建议改为按 `t_rise` 数值排序并补测试。
2. **离屏截图 CJK 呈方框（观感）**：Qt offscreen 无系统字体回退，仅影响无头截图。

## 未验证项（如实列出）

- **R1–R4**：本轮无设备，全部 `SKIPPED(no_device)`，无任何实测数字；
- **R6 月球绝对位置**：缺星历文件，未独立验证；
- **R7 真实射频发射**：未验证（无发射设备/授权，且套件设计为不发波）；
- **人耳听感**：R3/R4 的音质与回放听感需操作者放行出声后手动验。

## 与旧脚本的关系

`scripts/real_hw_acceptance.py`（2026-11-03 单体脚本）保留可复跑；本套件按新口径
（连接/频谱/解调/录音/卫星/天空/发射）拆分，`docs/hw-acceptance.md` 为唯一状态口径。
