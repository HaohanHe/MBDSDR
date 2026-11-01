# SDR 前端操作逻辑对标差距清单（SDR++ / gqrx / SDR# → MBDSDR desktop）

> 对标报告全文见调研记录（SDR++ 官方手册 / gqrx 源码 plotter.cpp·freqctrl.cpp·
> kbd-shortcuts.txt / rtl-sdr.com SDRSharp Users Guide）。
> 本文只列**经我方代码核验**的差距（file:line 为证），分 P0/P1/P2 施工序。
> 核验日期：2026-11-01。已对齐项同样列出，防止重复施工。

## 0. 已对齐（勿重复造）

| 事实标准 | 我方实现 |
|---|---|
| 点击频谱调谐 | `desktop/spectrum_widget.py` 单击/双击跳频 + snap 对齐 |
| 拖拽平移调谐 / Ctrl 拖=RF shift 视图平移 | 同上 `mouseMoveEvent` |
| Ctrl+滚轮=光标锚点缩放 | 同上 `wheelEvent`（照 SDR++ waterfall.cpp 数学） |
| 滚轮=按 snap 步进调谐，Shift×10 / Alt×0.1 | 同上 |
| 方向键步进 / 上下箭头细调 | 同上 `keyPressEvent` |
| VFO 色带：整拖移动 / 拖边调带宽 / Shift 橡皮筋新建 / 右键删 / 边沿滚轮调带宽 | 同上 `_begin_vfo_drag` 等 |
| snap 步进选择器 | `control_panel.py` `step_changed` → `set_snap_interval` |
| dB Min/Max 滑条 + FFT size/window/色图/平滑 | `desktop/spectrum_widget.py` |
| 硬件 AGC 开关 + 分档增益滑条（真实下发 GainStager） | `desktop/gain_panel.py` |
| 静噪（噪声门）+ ANR | `desktop/anr_panel.py` |
| 书签双击调谐 | `desktop/bookmark_panel.py` `itemDoubleClicked` |
| 峰值检测标注 | `desktop/spectrum_widget.py` `find_peaks` |
| 瀑布图/频谱主体 + 触屏捏合 | `desktop/spectrum_widget.py` / `touch_manager.py` |

## P0 操作核心缺口（施工第一批）

1. **数字频率显示三件套**。现状：只读大屏 `freq_display` + 键入框 `freq_input`
   （`control_panel.py:263-270`）。缺：悬停某位滚轮增减该位、点击数字上半 +1/
   下半 −1（七段式手感）。三件套全部程序的事实标准。
2. **调谐模式 Center/Normal 切换**。现状：点击谱面永远重调硬件中心频率
   （`spectrum_widget.py:1737-1743`）。缺 Normal 模式——点击只移 VFO、画面不动，
   瀑布保留多信号轨迹（SDR++ 调谐模式按钮 / gqrx 中键 / SDR# Center 同原语）。
3. **滚轮手势分层（gqrx 密度）**。现状：VFO 边沿滚轮=带宽。缺：Ctrl+滚轮=
   对称调带宽、Shift+滚轮=整体搬移通带（谱面任意位置）。
4. **键盘单键切解调 + 滤波宽窄**。gqrx：A/N/W/S/C 切模式，</./> 切滤波宽窄。
   现模式切换只在面板 combo，无快捷键。

## P1 工程深度缺口（施工第二批）

5. **解调级 AGC 档位**（Fast/Medium/Slow/Off 下拉，gqrx 式）。现仅有硬件 AGC
   布尔开关（`gain_panel.py:153`）与状态显示；解调级 AGC 档与
   attack/decay 参数面未见。施工前先查 `mbdsdr_ai` 接收链是否已有 AGC DSP 可挂。
6. **切模式自动套滤波预设 + 模式相关选项就地展开**。现模式切换不联动带宽
   （`modulation_panel.py` 是调制识别卡，非模式选择器）。SDR++/gqrx/SDR# 均：
   WFM→150k、AM→10k、USB→2.8k，切模式即套预设。
7. **Max/Min hold 与 SNR 表**。峰值检测已有，hold 线（FFT Hold）与顶栏常驻
   SNR 表（SDR++ 招牌）缺。
8. **书签频谱标签叠加**。三件套均支持把书签画在频谱上缘、点击标签直接调谐；
   现有 markers（右键加标记）与书签两套未打通。

## P2 锦上添花（第三批）

9. **频率轴滚轮缩放**：滚轮悬停在底部频率刻度轴上=以光标为中心缩放（现滚轮
   全局=调谐，轴区未分层）。
10. **FFT 仪器化**：RBW/重叠率实时显示、瀑布时间跨度（gqrx 把瀑布当测量仪器）。
11. **TCP socket 远程控制**：gqrx 有；我方已有 MCP worker + AI 工具面，
    属于差异化优势，**不追**，避免双头遥控。

## 差异化（我方有、三件套无）

点星即听（天空图→sat_links→多普勒调谐）、星象仪形态 + goto 缓动、AI 胶囊
命令栏、卫星跟踪闭环 + 云台跟随、VNA 实测链、MCP/Agent 工具面。

## 施工顺序建议

P0-1 → P0-2 → P0-3/4（一次手势批）→ P1-6（预设联动，改动小收益大）→
P1-5（先查内核 DSP）→ P1-7/8 → P2。每项带 offscreen 交互测试，
沿用既有 token/样式体系。
