# 串口 GNSS NMEA 解析 + NTRIP 接入笔记

> 状态：**骨架已就位，等真硬件插上后据实调试**。本次只打通软件链路，未实测真实模块。
> 许可：GPL-3.0。本批代码为从零实现 NMEA-0183 标准解析，未拷贝任何 GPL 不兼容代码。

## 学到了什么

1. **NMEA-0183 语句结构**：`$<talker><type>,...*<XOR校验和>\r\n`。
   校验和是 `$` 与 `*` 之间所有字符逐字节 XOR，取低 8 位，两位十六进制。
   解析前必须先验校验和，否则会把串口噪声当定位。
2. **多星座 talker 前缀**（参考 direwolf `repos/direwolf/src/dwgpsnmea.c:38-42`）：
   | 前缀 | 系统 |
   |---|---|
   | GP | GPS (美国) |
   | GL | GLONASS (俄) |
   | GA | Galileo (欧) |
   | GB / BD | 北斗（BD 为部分厂商私有前缀） |
   | GN | 多系统联合输出 |
   北斗模块常出 `$BDGGA` / `$GBGSV`，必须把这两个前缀也放行，否则北斗定位被丢掉。
3. **经纬度字段是 ddmm.mmmm（纬度）/ dddmm.mmmm（经度）**，不是十进制度；
   要 `度 + 分/60` 转换，南纬 S / 西经 W 取负。
4. **GGA 才有高度/卫星数/HDOP/fix_quality；RMC 才有速度/航向/日期；VTG 补充速度；
   GSA 给参与定位的卫星号和 PDOP；GSV 给可见卫星；ZDA 给高精度授时**。
   单一语句信息不全，必须后台线程持续读、多语句合并出最新 fix。
5. **auto_detect 策略**：遍历候选串口 × 波特率 `4800/9600/38400/57600/115200`，
   每个口短超时读几行，出现“`$` 开头且校验和通过”的 NMEA 即锁定。无设备时安静返回 None。
6. **NTRIP client**（参考 RTKLIB str2str / `src/stream.c`）：本质是 HTTP GET
   `/<mountpoint>` + Basic Auth，caster 回 `200` 后整条 TCP 连接就是 RTCM3 字节流。
   本批只把原始字节回调出去，**不解算 RTCM**（解算要 RTKLIB，后续再议）。

## 移植/对照

| 来源 | 用到的东西 | 我们的文件 |
|---|---|---|
| direwolf `dwgpsnmea.c:38-42` | talker ID 含义与多星座前缀 | `mbdsdr_ai/serial_gnss.py` `TALKER_IDS`；`mbdsdr_ai/new_spacetime.py` `_NMEA_TALKERS` |
| NMEA-0183 standard | GGA/RMC/GSA/GSV/VTG/ZDA 字段顺序、XOR 校验和 | `mbdsdr_ai/serial_gnss.py` `NMEAParser`；`mbdsdr_ai/new_spacetime.py` `parse_gnss_*` |
| RTKLIB str2str `src/stream.c` | NTRIP HTTP GET + Basic Auth 收 RTCM3 | `mbdsdr_ai/serial_gnss.py` `NTRIPClient` |
| 已有项目 SDR 干扰监测 | 保留不动 | `mbdsdr_ai/gnss_monitor.py` 上半部分 |

## 本批新增/改动文件

- 新增 `mbdsdr_ai/serial_gnss.py`：`NMEAParser` / `SerialGNSSReader` / `NTRIPClient`（骨架）。
- 改动 `mbdsdr_ai/new_spacetime.py`：新增 `parse_gnss_gga/gsa/gsv/vtg/zda` + `parse_nmea_sentence` 分发，扩展 RMC 多星座前缀。
- 改动 `mbdsdr_ai/gnss_monitor.py`：末尾追加 `GNSSPosition` + `RealGNSSMonitor`（封装 reader，无 fix 返回 `source="none"`）。
- 改动 `desktop/status_panel.py`：`StatusPanel.update_gnss(fix_dict)`（real 绿 `#5B8C5A` / none 灰）。
- 改动 `desktop/rf_sky_view.py`：`RFSkyView.set_gnss_position(fix_dict)`（real 记录观测站坐标 / none 空状态）。
- 新增 `scripts/serial_gnss_probe.py`：独立自检，无设备 exit 0。

## 红线（继续遵守）

- 无真实定位时一律 `source="none"`、坐标 `None`，UI 显示“未连接/无数据”，**绝不造假坐标**。
- GPL-3.0，不引入不兼容许可代码。
- 真硬件插上后再据实调波特率/语句频率/字段口径；本批未联调 GUI 与卫星追踪刷新。
