# MBDSDR 桌面端孤儿功能审计清单

> 审计时间：2026-09-27
> 审计范围：desktop/ 全部面板 × mbdsdr_ai/ 后端模块 × main_window.py 数据通路
> 审计方法：静态扫描面板类引用、数据馈入调用（feed_iq/feed_audio/update_iq/set_backend）、后端导入、硬编码颜色

---

## 一、前端孤儿（面板存在但从未收到真实数据）

| # | 面板 | 类名 | 问题 | 根因 |
|---|------|------|------|------|
| 1 | new_spacetime_panel.py | NewSpacetimePanel | 已创建并加入中央 Tab，但 `update_iq()` 从未被 main_window 调用 | main_window 数据循环只喂 spectrum/doppler/constellation/adsb，漏了 new_spacetime |
| 2 | aprs_panel.py | AprsPanel | 懒加载创建，有 `feed_audio()`，但主音频路径 `_demod_and_play()` 只写声卡，不 tap 给 APRS | 缺少音频 tap 分发机制 |
| 3 | satellite_image_panel.py | SatelliteImagePanel | 懒加载创建，有 `feed_audio()`，但从未被调用 | 同上，缺少音频 tap |
| 4 | modulation_panel.py | ModulationPanel | 构造函数签名 `__init__(self, backend, ...)`，但 main_window 调用 `ModulationPanel()` 无参数 → TypeError → 被 try/except 吞掉 → 永远是 None | 构造函数参数不匹配，面板从未成功实例化 |
| 5 | anr_panel.py | AnrPanel | UI 可交互，信号已连接，但 `_anr_enabled` 标志在音频路径中从未被读取；`_on_anr_strength()` 和 `_on_anr_learn_noise()` 都是 `pass` | ANR 降噪从未实际接入解调音频链 |

## 二、设计语言违规（硬编码颜色 / 未使用 tokens.py）

tokens.py 已完整提取 Figma CarWith 设计 token（颜色/圆角/字号/间距/比例），themes.py 的 CarDarkTheme 已委托 tokens 生成 QSS。但以下面板在代码内硬编码颜色，覆盖了主题 QSS：

| 面板 | 硬编码位置 | 问题 |
|------|-----------|------|
| constellation_panel.py | L98-138 多处 QColor(20,24,30)、QColor(80,200,120,110) 等 | 深色画布配色未走 token，散点绿色与主题 accent #919cac 不一致 |
| sat_track_panel.py | L62,66,132-143 整段 setStyleSheet 用 #F5F3EF/#5B7B8C/#C4845C | 用的是浅色 default 主题色，与 dark_car 主题冲突 |
| module_panel.py | L42-52 多处 QColor | 模块节点/连线颜色硬编码 |
| new_spacetime_panel.py | L73,104,122,126,164,175,193 QColor + #FFF | 图表配色硬编码 |
| rf_sky_view.py | L474-494+ 大量 QColor | 天空视图配色全部硬编码（2450行文件，影响最大） |
| spectrum_widget.py | L66-67 #000/#FFF，L371-379 QColor | 频谱/瀑布图画布颜色硬编码 |
| vfo_panel.py | L176-189 QColor | VFO 标记颜色硬编码 |
| adsb_map_panel.py | L57 QColor | 地图画布颜色硬编码 |

## 三、后端工具无前端面板（AI Agent / MCP 层）

tool_registry.py 注册约 400 个工具，主要服务于 AI 助手对话和 MCP 对外接口，不逐一需要桌面 UI。以下后端能力在桌面端完全没有对应面板或入口：

- FT8 解码/编码（ft8_decode/ft8_encode）
- ACARS 解码（acars_decoder）
- POCSAG 寻呼解码（pocsag_decoder）
- DMR/P25 数字语音（dmr_demod/dsdcc_lite）
- DAB+ 数字广播（dab_plus_lite）
- RDS 广播数据（rds_lite）
- VOR 伏尔解码（vor_decoder）
- SSTV 慢扫描电视（sstv_decoder）
- Codec2/FreeDV 数字语音（codec2_lite/freedv_modem）
- 各类 SDR 软件适配器（gqrx/sdrangel/sdr++/cubicsdr 等，约 15 个 adapter）

> 这些属于 AI 工具层，桌面端按需暴露即可，不强制全部做 UI。本轮优先接通已有面板壳但断数据的孤儿。

## 四、修复优先级

1. **P0 - 接通孤儿数据链路**：new_spacetime / aprs / satellite_image / modulation / anr
2. **P1 - 设计 token 对齐**：消除硬编码颜色，全部走 tokens.py / themes.py
3. **P2 - 后端工具 UI 暴露**：后续迭代按需添加
