# 真机七环验收报告（2026-11-03）

> **整份为 2026-11-03 单次真机跑、产物未入库、R4 测法已作废，不作为本轮判据。**
> 2026-11-12 裁定：20 条数字断言中 R4 占 12 条随旧导频测法作废，R1/R2/R3/R5/R7
> 仅 5 条可作历史线索（E3，不可复跑），**无效多于有效 → 整份标作废**（未验证）；
> 不把产物补拷进 `docs/perf-evidence/`，避免让已知有错的报告获得 E3 证据身份。

> 本篇结论未经 commit 取证，引用前须复核（判据见 `scripts/verify_claims.py`；补齐四元组 `<结论> — <hash> — <path>:<line> — <改动后字面量>` 后可摘除本行）。

> 执行脚本：[scripts/real_hw_acceptance.py](../scripts/real_hw_acceptance.py)
> 硬件：RTL-SDR（RTL2832U + **Fitipower FC0012**，序列 77771111153705700，调谐范围约 22–948.6 MHz）
> 环境：Windows 11 + Python 3.14 + pyrtlsdr（无 SoapySDR）+ rtl-sdr 官方工具集（`C:\rtl-sdr\bin`）
> 纪律落实：全程静音（不打开声卡；C++ 侧 `MBDSDR_NULL_AUDIO=1`）、无窗口（`CREATE_NO_WINDOW`）、
> 每环独立超时、单环失败不拖死全局、结束强制释放设备并有末检。
> 证据目录：`.tmp-tool-results/hw_acceptance/20261103_191805/`（results.json + 录音 + 快照，未入库）

## 「七环」口径说明

仓库内无既定「七环」清单（`docs/learn/phase13/P4-realdevice-checklist.md` 是六项重点，
且其中 3 项当时标记 P1/P2 在途）。本报告采用任务书给定的「真实硬件链路端到端七个面」口径，
Phase13 清单中可映射项（#1 Tune 重试写回读、#2 带宽/增益行为）吸收进环 2。

## 逐环结果

| 环 | 项目 | 结果 | 关键证据 |
|----|------|------|----------|
| R1 | 设备枚举/打开/释放 | **PASS（历史，E3 不可复跑）** —— 仍有效 | pyrtlsdr 10/10 + SDRController 5/5 反复开关成功；末次新句柄可开（无泄漏、不抢占）；产物未入库，不可复核 |
| R2 | 调谐与增益 | **PASS（历史，E3 不可复跑）** —— 仍有效 | 5 频点（94.0/137.5/144.64/433.92/470.0 MHz）写回读精确一致；FC0012 五档增益（-9.9/-4.0/7.1/17.9/19.2 dB）写读一致；AGC 可切；对 98.0 MHz 台估 ppm≈+33（**历史值 / 不可复核**：当次证据目录未入库，且是粗估，需同一支 RTL-SDR 重测才能复现） |
| R3 | 频谱正确性（对拍） | **PASS（历史，E3 不可复跑）** —— 仍有效 | 频轴平移：调 ref/ref±400 kHz，质心峰偏 -7.3/+399.4/-404.9 kHz（容差 ±40k）；MBDSDR `read_spectrum` 中位质心峰偏 -423 Hz、高于局部 36.3 dB；rtl_fm 独立代码路径解调同频出音频 RMS 4301（int16）；产物未入库，不可复核 |
| R4 | 解调与音频链路（静音） | **作废（随 R4 测法）** | 信号级 MPX 旧测法读数：**19 kHz 立体声导频**（19006 Hz，+12.1 dB）**已作废**——无抗混叠抽取会把 181159 Hz 杂散折到 18.8 kHz 附近并压低幅度，见 `docs/hw-acceptance.md` §6.2；修正后同台 98.0 MHz 为 19003.0 Hz / 37.7 dB。本行其余数字（2 ch、RMS 0.225、能量 <15 kHz 占比 80.9×）**也不作判据**（未验证，不抢救） |
| R5 | 录音落盘 | **PASS（历史，E3 不可复跑）** —— 仍有效 | 录制中流式增长 2,883,584 → 5,898,240 B；stop 后 745,472 样本；两次采样率 sidecar `core:sample_rate` 分别为 2,400,000 / 1,200,000（变更正确跟随）；产物未入库，不可复核 |
| R6 | 数字模式解码 | **部分作废** | ADS-B：FC0012 可用上限 948.5 MHz（948.6 MHz 起失败）< 1090 MHz，本机物理不可达，已单列结案（见下节）；APRS 144.64 MHz 实收 31.5 s 无有效帧属时间窗问题（**未验证**，解码链只在真实 OTA 上跑通无崩溃）；NOAA/Meteor 过境窗口等不到 |
| R7 | 便携包真机启动 | **PASS（历史，E3 不可复跑）** —— 仍有效 | `cpp/build-local/dist/.../bin/mbdsdr.exe --snapshot`（干净 PATH=仅 System32、offscreen、NULL_AUDIO）2.9 s 退出码 0，PNG 13,733 B；包内含 librtlsdr.dll（真机非 stub 构建）与 qoffscreen 插件；PNG 未入库，不可复核 |

**结论：7 环中 6 环通过，1 环（数字模式 OTA 解码）如实判无法验。** 无阻断级缺陷。
（本结论随整份判废，仅作历史记录，不作本轮判据。）

### ADS-B 1090 MHz：本机物理不可达（结案，不是待办）

本机 RTL2832U + Fitipower FC0012 实测可用上限 **948.5 MHz**（**948.6 MHz 起即失败**），
低于 ADS-B 的 **1090 MHz**，**本机物理不可达——当前棒子频率上限不足，需 R820T 系**
（R820T/R820T2）才能验。该项从「待真机」移出、单列结案：**不是待办**，不等设备、不挂账
（2026-11-06 本机实测记录见 `docs/pending-patches/2026-11-06-limitation-adsb-1090-fc0012.md`，
探针原件未入库，E3 不可复跑）。

## 复跑方法

```powershell
# 插上 RTL-SDR、确认无其他 SDR 软件占用后，仓库根目录：
python scripts/real_hw_acceptance.py                 # 七环全跑（约 4-5 分钟）
python scripts/real_hw_acceptance.py --ring 3        # 单环
python scripts/real_hw_acceptance.py --aprs-seconds 120   # 延长 APRS 监听窗
```

退出码：任一环 FAIL → 1；全部 PASS/UNVERIFIABLE → 0。每环判定标准见脚本 docstring 与各环注释。

## 发现的问题（分级）

### 阻断：无

### 一般
1. **过境预报结果不透出 TLE 来源与时龄**：`get_satellite_pass` 返回字典无 source/epoch
   字段，调用方无法分辨是 celestrak 实时值还是内置回退近似值（内置 TLE 元素为凑整值，
   过境时刻误差可达数十分钟）。建议在 `predict_passes`/`get_satellite_pass` 返回中增加
   `tle_source`（celestrak / builtin-fallback）与 `tle_epoch`。**需改代码**（mbdsdr_ai/decoders.py
   + controller.py），不属本轮真机缺陷修复范围，登记待办。
2. **pyrtlsdr API 单位陷阱**：`get_gains()` 返回 0.1 dB 整数而 `set_gain()` 收 dB 浮点——
   首轮环 2 误判 FAIL 即源于此。**第三方库习性**，脚本已按正确单位实现并留注释，提醒后续使用者。

### 观感
1. **offscreen 快照 CJK 字符呈方框**：Qt offscreen 平台无系统字体回退，仅影响无头截图，
   真实显示环境不受影响。不需要修；如需无头截图留档美观，可在 CI 镜像装 Noto Sans CJK。
2. **rtl_power 在本机增益行为反直觉**（`-g 192` 读数低于 `-g 0`，且 bin 有空洞 NaN）——
   独立基准工具的环境行为，非本仓库代码问题；验收脚本已改为不依赖 rtl_power
   （频轴平移自洽 + rtl_fm 互证，更硬）。

## 不可验声明（不拿推断当结论）

- **ADS-B（结案，不是待办）**：FC0012 实测可用上限 948.5 MHz（948.6 MHz 起失败）< 1090 MHz，
  本机物理不可达；需 R820T 系棒子另验（2026-11-06 实测记录见
  `docs/pending-patches/2026-11-06-limitation-adsb-1090-fc0012.md`，原件未入库，E3 不可复跑）。
- **APRS / NOAA-APT / Meteor LRPT OTA 解码**：会话窗口（19:00–19:20 本地）内无可用
  过境与 APRS 台活动；解码链路的 golden/文件级正确性由仓内既有测试钉扎，
  真机 OTA 出报文留待有过境/有台活动时用 `--ring 6` 复跑。
- **听感项**（Phase13 #3 链内 AGC 听感、#5 长时音高漂移）：本轮纪律禁止出声，
  数据面（导频/音频 RMS/频谱含量）已验证；人耳听感留待用户手动放行后单独验。

## 观察到的他人在途（未触碰）

- `cpp/CMakeLists.txt`（M）、`cpp/tests/test_lrpt_real_cross.cpp`（??）——
  非本会话所有物，按台账纪律未动，已在 COORDINATION.md 登记。
