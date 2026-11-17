# GNSS 自诊断与「串口通了但没定位」排查

> 适用日期：2026-11-18 · 脚本：`scripts/gnss_diag.py` · 测试：`tests/test_gnss_diag.py`
> 适用设备：USB-TTL / USB-CDC 接入的 NMEA-0183 GNSS 模块（本次实测 CH340 @ COM4）。

## 1. 什么时候用

典型症状：

- 设备管理器里能看到 `USB-SERIAL CH340 (COMx)`，但软件一直「未连接」或「搜索中」；
- 串口能打开，却不知道有没有 NMEA、有没有定位、有没有完整 UTC；
- 定位时有时无，怀疑天线、馈线或波特率；
- 想区分「端口问题」「模块没吐语句」「没定位」「缺日期」四类原因。

`scripts/gnss_diag.py` 不解算、不画星图，只回答一个问题：**从串口打开到拿到
可用定位与完整 UTC，五段链路里哪一段断了**。它与 `scripts/sdr_diag.py` 对称：
SDR 脚本查样本通路，本脚本查 NMEA/定位通路。

## 2. 用法

```powershell
# 自动探测串口（按 --baud，默认 9600）
python scripts/gnss_diag.py

# 指定串口（Windows 常见 CH340 / CP2102）
python scripts/gnss_diag.py --port COM4

# 指定波特率与等待上限
python scripts/gnss_diag.py --port COM4 --baud 38400 --timeout 10

# 机器可读：stdout 只有 JSON
python scripts/gnss_diag.py --json
```

参数说明：

| 参数 | 默认 | 含义 |
|------|------|------|
| `--port` | 空（自动探测） | 串口名，如 `COM4`、`/dev/ttyUSB0` |
| `--baud` | `9600` | 波特率（8N1）；自动探测也只按这个波特率试 |
| `--timeout` | `10.0` | 「收到 NMEA / 有效定位 / 完整 UTC」每段各自的等待上限（秒） |
| `--json` | 关 | 输出 `stages[]` + `fix` + `antenna` + `verdict` |

说明：

- `--port` 为空时先枚举串口；一个都没有 → 退出码 1。有候选口则按 `--baud`
  调 `SerialGNSSReader.auto_detect()`，探测不到合法 NMEA 也记「无可用端口」
  （退出码 1），因为此时没有可继续诊断的已确认端口。
- 每段都有自己的计时；`--timeout` 不叠加成总时长。天线段额外最多等
  `min(--timeout, 3 s)` 捕捉 GPTXT，定位段最多补等 `min(--timeout, 2 s)`
  捕捉 GGA 统计，避免把「这一历元还没轮到」误报成缺失。
- 结束时 `reader.stop()` 关闭串口并 join 读线程；读线程是 daemon，
  **不残留进程**。

## 3. 五段诊断的含义

顺序固定，任何一段失败即停止，后续段不会伪造 PASS。

| # | `name` | 检查什么 | PASS 说明 | 失败说明 |
|---|--------|----------|-----------|----------|
| 1 | `open` | 枚举/自动探测 + `SerialGNSSReader.start(port, baud)` | 串口成功打开，回显实际口与波特率 | 没有候选串口、自动探测无 NMEA，或端口打不开 |
| 2 | `nmea` | `get_fix()["source"] == "real"` | 收到至少一条校验和正确的 NMEA 语句 | 窗口内没有任何合法 NMEA（波特率/接线/供电） |
| 3 | `fix` | 经纬度同时非空 | 有真实定位；尽量附 GGA 的星数/质量/HDOP | 有语句但坐标为空（天线、视野、冷启动） |
| 4 | `utc` | `utc_datetime` 非空 | 有日期（ZDA 或 RMC 的 ddmmyy），可锚定完整 UTC | 只有 hhmmss，没有 UTC 日期 |
| 5 | `antenna` | `fix["antenna"]`（来自 GPTXT） | 观测到 `open`/`short`/`ok`，或如实记 `unknown` | **永不失败**；只做观测与 warning |

关键边界：

- 第 2 段以 `source=real` 为准。`SerialGNSSReader` 只在解析到合法 NMEA 后
  才置 `real`；长期无数据时 `get_fix()` 回空 fix（`source=none`）。
- 第 3 段不拿 `(0,0)` 或上一段旧坐标冒充定位；RMC 状态为 `V` 时坐标为 None。
- 第 4 段把「只有时间、没有日期」判为不完整。GGA 只有 hhmmss，不能拼当天
  日期冒充 UTC；需要 ZDA 或 RMC 的有效 `ddmmyy`。
- 第 5 段是观测项：`open`/`short` 只写 warning，**退出码仍是 0**。
  `unknown` 表示没收到明确的 `ANTENNA OPEN/SHORT/OK`，不等于天线正常。

## 4. 退出码与 verdict

| 退出码 | `verdict` | 含义 |
|--------|-----------|------|
| `0` | `ok` | 五段全部通过（天线告警不影响退出码） |
| `1` | `no_port` | 没有候选串口，或自动探测未找到可用 GNSS 口 |
| `2` | `open_failed` | 端口存在但打不开（占用/无权限/设备异常） |
| `3` | `no_nmea` | 串口已打开，但收不到合法 NMEA |
| `4` | `no_fix` | 有 NMEA，但没有有效定位 |
| `5` | `no_utc` | 有定位，但没有完整 UTC 日期 |

## 5. `--json` 输出结构

```json
{
  "port": "COM4",
  "baud": 9600,
  "stages": [
    {"name": "open",    "ok": true, "detail": "已打开 COM4 @ 9600 8N1", "elapsed_ms": 44.7},
    {"name": "nmea",    "ok": true, "detail": "已收到合法 NMEA（校验和通过），fix.source=real", "elapsed_ms": 201.9},
    {"name": "fix",     "ok": true, "detail": "已定位 …，卫星 16，fix_quality=1，hdop=0.8", "elapsed_ms": 0.0},
    {"name": "utc",     "ok": true, "detail": "完整 UTC：…", "elapsed_ms": 654.7},
    {"name": "antenna", "ok": true, "detail": "GPTXT 告警 ANTENNA OPEN…", "elapsed_ms": 100.7}
  ],
  "fix": {
    "source": "real",
    "latitude": 0.0,
    "longitude": 0.0,
    "altitude_m": 0.0,
    "satellites": 16,
    "hdop": 0.8,
    "fix_quality": 1,
    "utc_time": "000000.000",
    "utc_datetime": "1970-01-01T00:00:00+00:00",
    "date": "010170"
  },
  "antenna": {"status": "open", "text": "01,01,01,ANTENNA OPEN", "warning": true},
  "verdict": "ok",
  "exit_code": 0
}
```

> 上面 JSON 里的坐标/时间是为了展示字段结构而填的占位值，不是实测记录；
> 实测坐标不落盘（见第 6 节）。

- `stages` 按固定顺序排列，**只包含实际执行过的段**；上游失败时后续段不出现。
- `fix` 是最后一次成功读取的快照（坐标、星数、质量、UTC）。
- `antenna`：`status` 为 `open`/`short`/`ok`/`unknown`，`warning` 仅在
  `open`/`short` 时为 `true`。
- `detail` 是给人看的证据文本，不保证跨版本逐字稳定；`name`/`verdict`/
  退出码/`antenna.status` 才是机器可依赖的契约。

## 6. 本次真机证据（2026-11-18）

设备与配置：

- `COM4`，描述 `USB-SERIAL CH340 (COM4)`，厂商 `wch.cn`，`USB VID:PID=1A86:7523`
- 9600 8N1，`--timeout 10`

实跑结果（人读输出一次，另以 `--json` 复跑一次，结论一致）：

| 段 | 结果 | 证据 |
|----|------|------|
| 端口打开 | PASS | 已打开 `COM4 @ 9600 8N1`（44.7 ms） |
| 收到 NMEA | PASS | 收到校验和正确的 `GNGGA/GNGLL/GNGSA/GPGSV/BDGSV/GNRMC/GNVTG/GNZDA/GPTXT`（201.9 ms） |
| 有效定位 | PASS | 已定位（坐标按仓库身份红线不落盘），卫星 16，`fix_quality=1`，`hdop=0.8` |
| 时间完整性 | PASS | `utc_datetime=2026-11-17T20:31:00+00:00`，`date=171126`（654.7 ms） |
| 天线状态 | PASS | `GPTXT` 原文 `01,01,01,ANTENNA OPEN` → `status=open`，`warning=true`（100.7 ms） |

结论：`verdict=ok`，退出码 `0`。天线告警单独打印为 warning：

```
警告：天线状态 OPEN——定位可用性可能受影响（不影响本次退出码）
```

补充观测：

- 模块每个历元稳定输出 `$GPTXT,01,01,01,ANTENNA OPEN*25`，同时仍能输出
  16 颗卫星、`fix_quality=1` 的有效定位并给出完整 UTC/ZDA；
- 原始流还存在一条上电/对齐期的半截语句（首行缺失 `$GNZDA` 头），解析器
  按非法行丢弃，不影响后续历元；
- 两次运行结束后端口可被下一次运行立即重新打开，说明串口已正常关闭、
  无残留进程占用。

## 7. 天线告警说明

`SerialGNSSReader` 只从 GPTXT 文本里提取三种明确状态：

| `status` | GPTXT 关键词 | 含义 | 是否 warning |
|----------|--------------|------|--------------|
| `open` | `ANTENNA OPEN` | 模块自检认为天线端口开路（未接天线、馈线断/接头松） | 是 |
| `short` | `ANTENNA SHORT` | 模块自检认为天线端口短路 | 是 |
| `ok` | `ANTENNA OK` | 模块明确报天线正常 | 否 |
| `unknown` | 无上述词 | 没收到明确状态；**不等于正常** | 否（但见下） |

为什么天线告警不改变退出码：

- 本脚本判的是「数据链路是否可用」，不是「射频链路是否最优」。定位是否
  有效由第 3 段独立判定；天线状态是观测，不能反过来掩盖或改写定位结论。
- 本次 `ANTENNA OPEN` 与 16 星有效定位同时存在：告警说明模块认为天线端口
  异常，但接收链路仍能拿到信号（例如馈线/接头本身充当了天线，或有其他
  耦合路径）。这类状态应视为**需要修的硬件问题**，不能因为「还能定位」
  就当成正常。
- `unknown` 也不等于「没问题」：有些模块默认不发天线自检，或只发厂商文本；
  这时脚本如实记录「未观测到」，而不是伪造 `ok`。

## 8. 常见修复

1. **换天线/馈线**：先检查 SMA/IPEX 接头是否拧紧、馈线是否被压断；
   换一根已知良好的天线复测。`ANTENNA OPEN` 优先查开路，`SHORT` 查短路。
2. **确认波特率**：模块出厂常见 9600；RTK/高通模块可能是 38400 或
   115200。用 `--baud` 显式指定后重跑；仍无 NMEA 时用串口助手以 8N1
   确认能否看到 `$GNGGA` 文本。
3. **排除其它占用串口的程序**：串口助手、Arduino IDE、另一个 MBDSDR、
   地图/调试工具都可能独占 COM 口。关闭后重跑；退出码 2 且提示「被其它
   程序占用或无权限」时优先查这一项。
4. **冷启动与视野**：首次定位可能需要 30 s 到几分钟；把天线放到室外或
   窗边，观察 `satellites` 是否增长。长期 0 星且无天线告警时，再查模块
   供电与射频前端。
5. **补发日期语句**：退出码 5 时确认模块是否输出 ZDA，或 RMC 是否带
   `ddmmyy`；可在模块配置工具中打开后重跑。

## 9. 边界与不做什么

- 不做信号质量、频谱、干扰判断（SDR 侧见 `scripts/sdr_diag.py` /
  `scripts/rtl_selfcheck.py`）；
- 不配置模块、不修改波特率、不发配置指令；
- 不联网、不下载星历、不加第三方依赖（仅用已有的 `pyserial`）；
- 不自动抢占被其它程序占用的串口，也不清理别人的占用；
- 不替代示波器/矢网对天线与馈线的物理检测。

## 10. 自动化测试

```powershell
python -m pytest tests/test_gnss_diag.py -q
```

使用假 reader 与假端口枚举，不触碰硬件；覆盖：五段全过退出 0、无端口
退出 1（含自动探测失败）、打不开退出 2（返回 False 与抛异常两种）、
无 NMEA 退出 3、无定位退出 4、无完整 UTC 退出 5、天线 `open` 仍退出 0
且带 warning、`--json` 字段与段顺序。
