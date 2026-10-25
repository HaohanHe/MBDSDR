# experiments/satellite_rx — 卫星接收工具链

> 2026-10-25 ARISS/CUTE/NOAA 战役产物。**数据口径：ota（真实空口）**。
> 所有脚本零屏幕依赖（大工具形态：一条命令出结果，不经过人的眼睛和手）。

## 背景

首次真机卫星接收战役（RTL-SDR + 馈线 + 拉杆天线，长春站 GNSS 实测
43.774156N, 125.397813E, 230.8m）。ISS ARISS 过境因塔吊手台违规占用
435–438MHz 段（437.7315MHz，PTT 间歇发射，SNR 62–66dB，已取证）+ 屋内
接收条件不足而未收到信号；但完整工具链在过程中成型并自检通过。

## 工具链（scripts/）

| 脚本 | 作用 | 状态 |
|---|---|---|
| `sat_radar.py` | 机会雷达：未来 N 小时所有可收机会，按"值得打"排序 | 实测 |
| `sat_catch.py` | **总入口**：算过境→等 AOS→录音(SigMF)→多普勒→解码→判决，五模式（apt/lrpt/nfm/wfm/iq） | dry+冒烟 |
| `apt_decode.py` | NOAA APT 解码（多普勒补偿+AM 域转换+仓库 noaa_apt_lite） | selftest 59 行 |
| `waterfall.py` | **多普勒轨迹判决**：r→1 收到 / r→0 噪声 | 实测判决正确 |
| `pass_recorder.py` | 纯计时录音器（sat_catch 内核） | ISS 尾巴实测 |
| `mbdsdr_ctl.py` | MBDSDR 桌面端统一遥控（ControlHub 50732，屏蔽参数名坑+重试） | 实测 |
| `stack_up.py` | **守护式起栈**：rtl_tcp+mbdsdr+连接；卡死自愈+状态恢复 | 实测自愈 |
| `doppler_fm.py` | 多普勒补偿 + NFM/WFM 解调 → WAV | 实测 |
| `passes_leo.py` | 全量过境表（LEO 过滤） | 实测 |
| `osm_rfi.py` | OSM 周边干扰源方位图（真北基准） | 实测 |

## 快速上手

```bash
cd experiments/satellite_rx/scripts
# 1. 看有什么机会
python sat_radar.py --hours 24 --min-alt 40
# 2. 起接收栈（UI 可选，只当显示器）
python stack_up.py --freq 137.1 --rate 1024000 --mode AM
# 3. 收一颗（四种模式：apt/lrpt/nfm/wfm/iq）
python sat_catch.py --sat "NOAA 19" --freq 137100000 --demod apt
# 4. 遥控桌面端
python mbdsdr_ctl.py status / tune 137.1 / mode am / record start
```

## 站址/硬件

- GNSS 实测站址（勿改）：43.774156N, 125.397813E, 230.8m（长春，COM10 CH340 9600）
- RTL-SDR（RTL2832U + Fitipower FC0012，FC0012 忽略手动增益）
- 采样率固定 **1.024 MS/s**（2.048 会 USB 崩 `Library error -5`）
- TLE：Celestrak（amateur/cubesat/weather 组，CATNR/NAME/GROUP 查询，
  不支持 SEARCH）；批量拉取后必须校验每行以 `1 `/`2 ` 开头（会混入
  `Invalid query` 错误行污染文件）

## 诚实口径

- 解码出的图/数据 = **ota**（真实空口），可对外
- 合成/测试数据 = **synthetic**，必须标注
- 未收到就写未收到 + 多普勒判决为噪声，不伪造（waterfall.py 的判决是标准）

## 已知坑（血泪，详见 /d/mbdsdr/output/ariss/PASS_PLAYBOOK.md）

1. `nohup &` 在 Git Bash 下子进程被回收 → 用 run_in_background
2. 2.048 MS/s USB 崩 → 1.024
3. **Windows DETACHED_PROCESS 启动 GUI 剥夺渲染上下文**（瀑布黑，数据在）→
   守护模式不 detach
4. 引擎帧宽随块大小跳变 → bins_ 重分配、ringCount_ 归零、瀑布画空
   （已在 spectrum_engine.cpp 修复：固定 useN=fftSize_）
5. 8bit RTL 噪底 -97dBFS → dB floor 需 -130（已在 tokens.h 修复）
6. 磁罗盘现场失效（塔吊/钢筋）→ 太阳定向/信号寻星
7. ControlHub 参数名：tune=freq_hz / rate=rate_hz / mute=muted
