# 频谱 / 瀑布显示层缺口调研（只读）

- 日期：2026-11-11
- 基线：`MBDSDR@b6bc856c`（工作区另有他线未提交改动，本次未触碰）
- 范围：`desktop/spectrum_widget.py`（4233 行）、`desktop/config_schema.py`、`desktop/settings_panel.py`、`desktop/main_window.py` 的显示接线，以及 `mbdsdr_ai` 侧对应 DSP。
- 性质：只读调研，未修改任何产品代码；本文件为唯一新增产物。

---

## 0. 结论（先报工具栏拥挤的量化结果）

| 指标 | 实测值 | 口径 |
| --- | --- | --- |
| 窄档可用宽（频谱面板） | **331 px** | 真实 `MainWindow` 最小窗口 1000×680，左栏 333，频谱页 331 |
| 当前工具栏常驻控件 | **1 个**（「更多」按钮 55×31 探针；真实主窗 51×31） | `spectrum_widget.py:2691-2706` |
| 当前工具栏最小宽 | **59 px**（真实主窗实测；独立探针 63） | 51 + 左右边距 4+4（`_build_toolbar:2547-2549`） |
| 全部控件常驻所需宽 | **1563 px** | 20 个控件布局 `minimumSize`；控件纯宽 1441 + spacing 114 + 边距 8 |
| 窄档缺口 | **1232 px**（1563 − 331） | 若按左栏绝对最小 260，缺口 1303 px |
| 被折叠的控件 | **14 个可交互控件 + 6 个标签** | 全部落在同一个「更多」菜单 |
| 菜单规模 | **10 行 / 310×358 px** | `QWidgetAction` 行，无分组、无滚动、无搜索 |

**结论**：拥挤没有被消除，只是被整体搬进了一个单点菜单。14 个控件里 DSP 参数（窗/FFT/平均/性能）与显示参数（配色/平滑/保持/标注）混排；7 个控件没有 tooltip；断连时全部置灰。

**「独立显示节」可行性（数字）**：

- 显示类 10 项（配色/平滑/峰值/Max/Min/书签/频段/自动量程/峰值门/调谐）若常驻行内：**985 px** ≈ 窄档 331 的 **3.0 倍**，不可行。
- 若做成常驻竖排抽屉：最宽行是「峰值门 label + spin」= 294 px（标签与 spin 并排），把标签与 spin 上下叠放后仍需 **171 px**；331 px 面板里绘图区会从 299 px 降到 **约 128 px（−57%）**，不可接受。
- 若只加第二个 55 px 的「显示」弹出按钮：工具栏最小宽 **120 px**（59 + 6 + 55），距 331 尚余 **211 px**，绘图区 299 px 不变。**这是唯一不撑大最小宽的迁移方式**。

---

## 1. 测量口径

- 平台：`windows`（不是 offscreen；本机 offscreen 字体库为 0，度量不可用）。
- 版本：CPython 3.14 / PySide6 6.12 / 主题 `dark_car` / `WA_DontShowOnScreen`。
- 单控件：`resize(sizeHint)` 后 `show()+processEvents()`，逐项断言 `actual == sizeHint`，**20/20 通过**。
- 面板：`resize(340, 303)`，断言 `actual == requested` **失败**：独立探针实际 `340×347`，真实主窗同主题最小高 **339**（Qt 按内容最小高拒绝 303），这本身就是溢出证据。
- 真实窗口：请求 860×700 被产品最小尺寸夹为 1000×691；请求 1000×700、1600×900 均精确命中。频谱页宽在最小窗口为 **331**，1600 宽时为 **643**。
- 以上为单次几何实测；几何测量无抖动，故未做多轮中位数。

---

## 2. 可调显示参数清单

> 「持久化」区分三态：**有键+启动回填** / **有键但启动不回填** / **无键**。
> 设置页入口统一在 `desktop/config_schema.py`（由 `settings_panel.py` 泛型渲染）。

| 参数 | 当前入口（path:line） | 默认值 | 持久化 | tooltip |
| --- | --- | --- | --- | --- |
| 窗函数 | 更多菜单 `spectrum_widget.py:2568-2572`；设置 `config_schema.py:278` | `Nuttall` | 有键 `fft_window`，**启动不回填** | **无** |
| FFT 点数 | 更多菜单 `spectrum_widget.py:2575-2580`；设置 `config_schema.py:266` | 声明 2048；启动被性能档覆盖为 **1024** | 有键 `fft_size`，**启动不回填且被覆盖** | **无** |
| 平均帧数 | 更多菜单 `spectrum_widget.py:2583-2588`；设置 `config_schema.py:286` | `1`（1/4/8/16） | 有键 `fft_avg_frames`，**启动不回填** | **无** |
| 性能档（高/均衡/低） | 更多菜单 `spectrum_widget.py:2592-2596`；设置页无此键 | 面板默认「高」；启动 eco→「低」 | 间接：由 `perf_profile` 派生（`perf_profile.py:40-72`） | **无** |
| 调色板 | 更多菜单 `spectrum_widget.py:2600-2603`；设置 `config_schema.py:296` | `SDR++ 经典` | 有键 `waterfall_palette`，**启动不回填** | **无** |
| 瀑布时间平滑 | 更多菜单 `spectrum_widget.py:2606-2608`；设置 `config_schema.py:323` | `False` | 有键 `waterfall_smooth`，**启动不回填** | **无** |
| 标注峰值 | 更多菜单 `spectrum_widget.py:2610-2613`；设置 `config_schema.py:316` | `True` | 有键 `show_peaks`，**启动不回填** | **无** |
| Max 保持 | 更多菜单 `spectrum_widget.py:2616-2619` | `False` | **无键** | 有 |
| Min 保持 | 更多菜单 `spectrum_widget.py:2620-2623` | `False` | **无键** | 有 |
| 书签旗标 | 更多菜单 `spectrum_widget.py:2626-2632`；设置 `config_schema.py:330` | `True` | 有键 `show_bookmarks`，**启动不回填** | 有 |
| 频段彩带 | 更多菜单 `spectrum_widget.py:2635-2642`；设置 `config_schema.py:336` | `True` | 有键 `show_bandplan`，**启动不回填** | 有 |
| 自动量程 | 更多菜单 `spectrum_widget.py:2646-2651` | `True` | **无键** | 有 |
| 峰值判定阈值 | 更多菜单 `spectrum_widget.py:2666-2677`；设置 `config_schema.py:305` | `+6.0 dB`（相对本帧中位） | 有键 `peak_threshold_db`，**启动不回填** | 有 |
| 调谐模式（中心/VFO） | 更多菜单 `spectrum_widget.py:2680-2687` | 中心 | **无键** | 有 |
| 显示跨度（缩放） | 右侧竖滑杆 `spectrum_widget.py:2746-2753` | 全带宽 | **无键** | 有 |
| 显示量程上限 | 右侧竖滑杆 `spectrum_widget.py:2759-2775` | `-20 dBFS` | **无键** | 有（滑杆+标签） |
| 显示量程下限 | 右侧竖滑杆 `spectrum_widget.py:2781-2790` | `-100 dBFS` | **无键** | 有（滑杆+标签） |
| 瀑布历史深度 | **仅设置页** `settings_panel.py:1057-1060`；`config_schema.py:368` | `256` 行 | 有键 `waterfall_depth_rows`，**启动回填** | 设置页 help 有 |
| 显示瀑布图 | **仅设置页 / 视图菜单** `main_window.py:1408`、`13179-13205` | `True` | 有键 `show_waterfall`，**启动回填** | 菜单项文字 |
| 噪声底锚点（不可调） | 无 UI；`spectrum_widget.py:1628-1633` 常量 | 底噪落色带 10% | — | — |

### 2.1 持久化断链（关键）

`schema` 里 9 个频谱显示键确实会写盘，但启动时只回填 5 个键：

```python
# desktop/settings_panel.py:529-530
for key in ("bortle", "squelch_default_db", "tuning_step_hz",
            "waterfall_depth_rows", "sky_extinction_k"):
```

`fft_size / fft_window / fft_avg_frames / waterfall_palette / peak_threshold_db /
show_peaks / waterfall_smooth / show_bookmarks / show_bandplan` **不在启动回填列表**，
也没有其它启动路径消费它们。工具栏本身不写 settings（`spectrum_widget.py` 无
settings 引用），所以：

- 工具栏改的显示参数 → **只活到本次退出**；
- 设置页改的显示参数 → 写盘成功，但**重启后不生效**；
- `fft_size` 更糟：启动 `_apply_perf_profile`（`main_window.py:2114`、`4162-4214`）
  按默认 eco 档强制 `panel_level=2 → FFT 1024 / 256 bins / 100 ms`，而设置页读的是
  `DesktopSettings` 默认值 **2048**。于是设置页显示 2048、频谱实际跑 1024，用户选
  4096 也永远活不过一次重启。

---

## 3. 工具栏拥挤量化

### 3.1 单控件最小宽（dark_car，Windows）

| 控件 | 宽×高 | 控件 | 宽×高 |
| --- | --- | --- | --- |
| 窗 combo | 110×34 | 自动量程 | 77×18 |
| FFT combo | 83×34 | 峰值门 spin | 166×36（min 145） |
| 平均 combo | 64×34 | 调谐按钮 | 89×36 |
| 性能 combo | 77×34 | 更多按钮 | 55×31（探针）/ 51×31（真实主窗） |
| 调色板 combo | 123×34 | 标签「窗:/FFT:/平均:/性能:/调色板:」 | 16/27/29/29/42 |
| 瀑布平滑 / 峰值 / Max / Min | 77/51/77/74 | 标签「峰值门(相对本帧中位):」 | 128 |
| 书签 / 频段 | 51/51 | | |

汇总：控件纯宽 **1441 px**，19 个 6 px 间距 = **114 px**，左右边距 8 px，
布局 `minimumSize` = **1563×36 px**；窄档 331 px，溢出 **1232 px**。

### 3.2 被挤掉的项落在哪个菜单

全部 14 个可交互控件都在 `_more_menu`（`spectrum_widget.py:2700-2705`），10 行：

| 行 | 内容 |
| --- | --- |
| 1 | 窗 |
| 2 | FFT |
| 3 | 平均 |
| 4 | 性能 |
| 5 | 调色板 |
| 6 | 瀑布平滑 + 峰值 + Max保持（3 个/行） |
| 7 | Min保持 + 书签 + 频段（3 个/行） |
| 8 | 自动量程 |
| 9 | 峰值门 label + spin |
| 10 | 调谐:中心 |

菜单实测 **310×358 px**；`QMenu` 不提供滚动条，也没有 DSP / 显示分组标题。
「更多」按钮的 tooltip（`:2695-2697`）列了 12 项，但**未列**「显示瀑布图」与
「瀑布历史深度」，这两项只能在设置页/视图菜单找到。

### 3.3 迁移到「显示」节的三种形态

| 形态 | 宽度代价 | 窄档 331 下的结果 |
| --- | --- | --- |
| 行内常驻 10 项显示参数 | +985 px | 溢出 654 px，不可行 |
| 常驻竖排抽屉（171 px） | +171 px | 绘图区 299→约 128 px（−57%），不可接受 |
| 第二个 55 px「显示」弹出按钮 | +61 px（含间距） | 工具栏 120 px，绘图区不变，**可行** |

### 3.4 第二个「显示」弹出按钮方案（2026-11-11 已落地，见 7.3）

**形态**：与「更多」同款的 `QToolButton`（`InstantPopup`，55×31，文字「显示」），紧跟在「更多」右侧。
工具栏最小宽 **59 + 6 + 55 = 120 px**；窄档 331 px 下余 **211 px**，绘图区宽度不变。

**分工原则**：按"改数据"还是"改看法"切。

| 菜单 | 归属 | 行数 | 控件 |
| --- | --- | --- | --- |
| 「更多」= DSP / 操作 | 改数据链路 | 5 行 | 窗函数、FFT 点数、平均帧数、性能档、调谐模式（中心/VFO） |
| 「显示」= 外观 / 标注 / 量程 | 只改怎么看 | 5 行内容 + 3 个分组标题 | 见下表 |

**「显示」菜单分组明细（3 组 / 8 个控件 / 5 行）**：

| 分组标题 | 行 | 控件 | 备注 |
| --- | --- | --- | --- |
| 色彩 | 1 | 调色板 combo（123 px） | 100% 下最宽行之一 |
| 瀑布 | 2 | 瀑布平滑 · 显示瀑布图 · 历史深度 ▸ | 3 个/行；深度为 6 档子菜单（128…4096），把 G6 收回面板 |
| 标注 | 3 | 峰值门 label + spin | 最宽行 ≈ 294 px，沿用现布局 |
| 标注（续） | 4 | 标注峰值 · 书签旗标 · 频段彩带 | 3 个/行 |
| 量程与包络 | 5 | 自动量程 · Max 保持 · Min 保持 | 3 个/行 |

**尺寸与滚动**：

- 高度估算按当前菜单实测换算（10 行 = 358 px → 约 36 px/行；分组标题约 22 px）：
  `5×36 + 3×22 + 菜单边距 ≈ 250–270 px`，**低于现状 358 px**，100% 缩放下无需滚动。
- 125% ≈ 315–340 px、150% ≈ 375–405 px；产品最小窗口高 680，锚点在工具栏（y≈40），三种缩放均不触底。
- 结论：**不需要滚动**；`QMenu` 本身也不支持滚动条，所以设计上强制「每组 ≤3 控件/≤2 行」，以后新增项必须进子菜单，不能无限加行。
- 宽度取整后约 **300 px**（受峰值门行 294 与调色板 123 约束），与当前 310 px 相当。

**其它配套**：

- 每个菜单行补 tooltip（当前 7 项缺失），「更多」tooltip 也补上瀑布两项，消除 G4 的发现性问题。
- 「显示」组全部为纯显示项，断连时**保持可用**（修 G12）；「更多」组里只置灰窗/FFT/平均/性能。
- FFT 行若暂不联动 `num_bins`，菜单文字直接写「FFT 点数（计算 4096 / 显示 512）」，满足"必须让用户知情"（G3 的 S 级处置）。
- 不做成常驻竖排抽屉：那会让绘图区从 299 px 降到约 128 px（−57%），见 3.3。

---

## 4. 缺口明细

> 严重度：P1 = 用户可见的功能失效/误导；P2 = 体验、发现性、维护性。
> 成本：S < 0.5 人日；M 0.5–2 人日；L > 2 人日。

### G1（P1，M）显示参数持久化断链 + 设置页与实际值不一致

- 位置：`desktop/settings_panel.py:524-530`；`desktop/main_window.py:2114`、`4162-4214`；`desktop/config_schema.py:266-340`。
- 现象：9 个有键参数启动不回填；`fft_size` 被 eco 档覆盖；设置页显示 2048、实际 1024；设置页 help 还写着「2048 是速度与细节的平衡点」（`config_schema.py:267-272`），与实际默认 1024 矛盾。
- 影响：用户每次重启都要重配配色/窗/平均/峰值/书签/频段；设置页给出与事实不符的数字，属于"假控件"同类问题。
- 成本：M（补启动回填 + 让设置页读取实际生效值，或明确性能档与 FFT 的优先级并写进 UI）。

### G2（P1，S–M）右侧滑杆列在低高度下溢出：末根量程下限滑杆被裁 44 px

- 位置：`desktop/spectrum_widget.py:2749`、`2770`、`2785`（`_span_slider` / `_db_max_slider` / `_db_min_slider` 的 `setMinimumHeight(90/80/80)`）；`2475-2476`（谱面/瀑布最小高 120/60）。
- 具体控件（面板被压到 331×303 时的实测几何）：

  | 控件 | `setMinimumHeight` | 自身 `minimumSizeHint` | 303 档几何（x,y,w,h） | 底边 |
  | --- | --- | --- | --- | --- |
  | `_span_slider`（缩放） | 90 | **16** | 2,20,16,90 | 110 |
  | `_db_max_slider`（上限） | 80 | **16** | 2,132,16,80 | 212 |
  | `_db_min_slider`（下限） | 80 | **16** | 2,234,16,80 | **314** |

  列 `dbSliderCol` 自身 `minimumSizeHint = 30×314`（工具栏 31 + 列 314 + 边距）；面板最小高因此是 **347**（独立探针）/ **339**（真实主窗 dark_car 主题）。
- 起始高度档：请求高 **< 347**（独立探针）或 **< 339**（真实主窗）即开始裁切。303 档时被裁的是**末根 `_db_min_slider` 的下半段**：其底边 314 超出可视 body 270，**裁掉 44 px**（347−303）；「最小」标签 y=214..232 仍在可视区，**先出界的是滑杆轨道下半段与手柄行程，不是标签**。这与已知单「6 px」不符，当前 HEAD 复测为 36–44 px。
- 定性：**不是内容真的放不下，是最小高被 fixed 尺寸撑住**。`QSlider` 自身 `minimumSizeHint` 只有 16 px；三根滑杆的真实需求 = 3×16 + 3×18（标签）+ 间距 10 ≈ **112 px**。
- 反证（同一探针，逐档放松 `setMinimumHeight` 后重新 `invalidate()` 再测）：

  | 滑杆最小高 | 列 minHint | 面板最小高 | 请求 303 的实际结果 |
  | --- | --- | --- | --- |
  | 90/80/80（现状） | 314 | 347 | **347（裁 44 px）** |
  | 50/50/50 | 214 | 247 | **303**（三根约 83/84/83，末根底边 270，刚好放下） |
  | 0/0/0 | 112 | 217 | **303** |

  即：放松到 50 就能容纳 303，证明溢出由 `setMinimumHeight` 硬约束造成，而不是内容本身需要 314 px。
- 影响：低高度下量程下限滑杆只剩上半段可点，用户难以拖到低位；同时违反"请求尺寸 == 实际尺寸"，父容器被裁 36–44 px。
- 成本：S–M（把三根滑杆最小高降到约 50，或按可用高度等比分配；高度低于阈值时转入 `set_compact` 的隐藏滑杆形态）。
- 补充事实：产品最小窗口 1000×680 下频谱页实测 **331×436**，比面板最小高 347 多 **89 px**，不溢出；缺陷只在用户拖分隔条、更小屏幕、星象仪槽位等低高度场景触发。

### G3（P1，S–M）FFT 档位只改计算量、不改善显示分辨率（端到端实测，送绘恒 512 根）

- 完整链路（从上到下的 `path:line`，截断发生在第 5 步）：

  1. `desktop/spectrum_widget.py:2578-2579` — `_fft_combo.currentTextChanged` → `lambda t: self.generator.set_fft_size(int(t))`。
  2. `desktop/spectrum_widget.py:395-412` — `set_fft_size` **只写 `self.fft_size`**，全程不触碰 `self.num_bins`。
  3. `desktop/spectrum_widget.py:528` — `push_iq` 取 `N = self.fft_size`；`:538` `np.fft.fft(x * win)`；`:560` `power_db` 长度 = N。
  4. `desktop/spectrum_widget.py:563` — `self.spectrum = self._resample_max(power_db)`。
  5. `desktop/spectrum_widget.py:619-631` — `_resample_max` 输出长度**恒为 `self.num_bins`**（等长直接返回；整除时 `reshape(num_bins, -1).max(axis=1)`）。**N→512 的合并就发生在这一行。**
  6. `num_bins` 只有两个来源：构造默认 512（`:232-233`）与 `set_performance_mode` → `reconfigure(fft_size, num_bins)`（`:2906-2937`、`:424-432`；`_PERF_LEVELS` `:2895-2904` = 高 2048/512/50 ms、均衡 1024/384/66 ms、低 1024/256/100 ms）。
  7. 绘制：`_draw_spectrum` `:1687-1692` 直接 `spec = gen.spectrum; n = len(spec)`，**绘制层不再重采样**；瀑布列数同样由 `num_bins` 决定。

- 端到端实测（真实 `SpectrumPanel`：工具栏下拉切档 → `push_iq` 真实重算 → 在 `_resample_max` 出口与 `_draw_spectrum` 入口分别取数组长度）：

  | 下拉选择 | `generator.fft_size` | `num_bins` | `len(spectrum)` | 瀑布列数 | 实际送绘 `drawn_n` |
  | --- | --- | --- | --- | --- | --- |
  | 4096 | 4096 | 512 | 512 | 512 | **512** |
  | 2048 | 2048 | 512 | 512 | 512 | **512** |
  | 1024 | 1024 | 512 | 512 | 512 | **512** |
  | 性能=低 + FFT 4096 | 4096 | 256 | 256 | 256 | **256** |

  反证成立：4096→2048→1024 三档，实际送绘长度**恒为 512**，说明 FFT 档位对显示谱线数完全无效；看到的 512 不是别的路径，就是 `num_bins`。只有切性能档才会变（256/384/512）。

- 定性：**降采样本身是性能取舍，FFT 下拉不联动是缺陷**。
  - 性能取舍（有意）：`_PERF_LEVELS` 明确按性能档定 `num_bins`（512/384/256），max 抽取用于控制每帧像素数与重绘成本。这部分不是 bug。
  - 缺陷（取值用错变量 / 半接线）：FFT 下拉只改 `fft_size`，不联动 `num_bins`。于是 4096 点比 2048 多付约 2.2 倍 FFT 时间，送绘仍是 512 根；`config_schema.py:267-272` 的 help 还写着「2048 是速度与细节的平衡点 / 点数越大分得越细」，与行为矛盾；工具栏下拉与设置页各改一半，属双入口半接线。
- 影响：2.4 MHz 跨度下显示与峰值定位分辨率固定为 `span/512 ≈ 4.7 kHz`（4096 点理论约 0.59 kHz）；峰值跟踪（`_PEAK_MATCH_BINS=5`）、`value_at_freq`、瀑布列全部按 512 栅格，密集信号会合并相邻峰。用户为"更细"付了算力却拿不到分辨率。
- 处置（按用户裁决必须让用户知情，二者可并存）：
  - 立即（S）：菜单行与悬停读数标注真实口径「计算 4096 / 显示 512」，并改掉 schema help 的「越细」表述。
  - 后续（M）：让 `num_bins = min(fft_size, 画布宽, 性能档上限)` 随 FFT 派生，性能档只保留上限；补回归门 `len(gen.spectrum)` 与 `_draw_spectrum` 入口长度断言。
- 成本：S（标注 + 文案）／M（联动 `num_bins` + 回归测试）。

### G4（P1，M）工具栏单点菜单：14 控件、10 行、无分组、7 项无 tooltip

- 位置：`desktop/spectrum_widget.py:2546-2706`。
- 现象：DSP 参数与显示参数混排；无分组标题；7 项（窗/FFT/平均/性能/调色板/瀑布平滑/峰值）无 tooltip；菜单 310×358；断连时 14 项全置灰（`:2881-2887`）而「更多」按钮仍可点。
- 影响：新用户找不到调色板/瀑布平滑；想只看显示设置要逐行扫；未连接时无法预先配置。
- 成本：M（拆成「DSP」「显示」两个 55 px 弹出菜单 + 分组标题 + 补齐 tooltip；`_set_controls_enabled` 只置灰真正依赖数据的项）。具体分组/行数/滚动与分工见 **3.4**。

### G5（P2，S）瀑布刷新固定 100 ms，不随性能档

- 位置：`desktop/spectrum_widget.py:4158-4160`（WaterfallWidget 定时器写死 100 ms）；`2906-2937`（`set_performance_mode` 只改 `self._plot._timer`）。
- 现象：性能档把谱面刷新改成 50/66/100 ms，瀑布始终 100 ms。
- 影响：高档下瀑布行率与谱面不同步；`wf_row_rate_hz()`/`wf_span_seconds()` 显示的真实行率与所选档位的预期不一致。
- 成本：S（把瀑布定时器纳入同一档位表；注意瀑布按数据行增量绘制，不应盲目提高重绘频率）。

### G6（P2，S）「显示瀑布图」「瀑布深度」不在面板内

- 位置：设置 `desktop/config_schema.py:228`、`368`；消费 `desktop/main_window.py:13179-13205`、`settings_panel.py:1057-1060`；面板菜单 `spectrum_widget.py:2700-2705` 未包含。
- 现象：面板里只能调瀑布外观，不能开关瀑布或改历史深度；「更多」tooltip 也未提这两项。
- 影响：用户以为面板没有瀑布深度设置，需要离开频谱页去设置页。
- 成本：S（作为两个菜单行加入「显示」菜单）。

### G7（P2，M–L）三套 FFT/DSP 口径不一致

- 位置：
  - 显示链路 `desktop/spectrum_widget.py:517-582`（Nuttall 默认、dBFS 按窗相干增益归一、20 分位噪声底、max 抽取）；
  - AI 工具 `mbdsdr_ai/spectrum_processor.py:74-133`（Hann 默认、`10log10(|FFT|²)` 未做 dBFS、最低 10% 均值噪声底）；
  - AI 工具 `mbdsdr_ai/signal_spectrum.py:35-121`（Welch 50% 重叠、median 噪声底）。
- 现象：同一段 IQ，屏幕、`spectrum_analyze_iq`、`SpectrumProcessor` 三条路径的窗、平均、dB 参考、噪声底定义均不同；`spectrum_processor.py:245-288` 的 zoom/pan 只存状态、`compute_waterfall` 零调用。
- 影响：AI 报出的峰/底噪与用户屏幕对不上；绝对门限（如 -60 dB）在不同路径含义不同。
- 成本：M–L（先统一"噪声底/峰值门"口径并加跨实现对拍；zoom/waterfall 占位代码要么接线要么删除）。

### G8（P2，S–M）AI 无法调整显示参数

- 位置：`mbdsdr_ai/agent.py:901-933`（仅注册 `spectrum_analyze_iq`，入参是用户自带 IQ）；`desktop/spectrum_widget.py` 无 AI 可调用入口。
- 现象：AI 能分析 IQ，但不能改调色板/FFT/量程/显示项。
- 影响：语音/智能体场景下"把瀑布调成低饱和""量程收紧"无法执行。
- 成本：S–M（复用 `_apply_spectrum` 的 setter，注册一个受限的显示控制工具）。

### G9（P2，M）无瀑布对比度 / gamma / 独立色标

- 位置：`desktop/spectrum_widget.py:2940-2946`（只有两套固定色带）、`2225-2280`（LUT 由 dB 量程映射）。
- 现象：瀑布与谱线共用 `db_min/db_max`；没有 gamma/对比度/独立瀑布量程。
- 影响：弱信号层次只能靠全局量程硬调，强信号与底噪不能兼顾。
- 成本：M（LUT 增加 gamma 参数 + 一个滑杆/菜单项；需保持与 SDR++ 观感一致）。

### G10（P2，S）自动量程常量写死、噪声底不可见

- 位置：`desktop/spectrum_widget.py:1628`（`_AUTO_DB_NOISE_POS=0.10`）、`1632-1633`（40–80 dB 跨度）、`1636-1674`（20 分位噪声底、1663 的 3 dB 滞回）。
- 现象：锚点/跨度/滞回不可调；无"当前噪声底"读数；手动拖量程即永久关闭自动，仅复选框同步取消，无原因提示。
- 影响：不同噪声环境下自动量程观感不可控；用户不知道为什么自动量程"自己关了"。
- 成本：S（状态行提示 + 悬停显示噪声底；常量提到可配置层）。

### G11（P2，S）平均帧实现为"攒够 N 帧再输出"

- 位置：`desktop/spectrum_widget.py:546-560`。
- 现象：`_avg_count < avg_frames` 时直接 `return`，不更新谱面；N=16 时首帧延迟 16 个数据帧。
- 影响：切到 16 帧平均后画面先"冻住"一段时间，无"正在平均"提示；用户误以为卡死。
- 成本：S（滑动平均或显示进度提示）。

### G12（P2，S）断连时显示参数全部置灰

- 位置：`desktop/spectrum_widget.py:2881-2887`。
- 现象：`_set_controls_enabled(False)` 把调色板/平滑/峰值/书签/频段/量程等纯显示项一并禁用。
- 影响：未连接硬件时无法预先配置显示；「更多」按钮仍可打开一个全灰菜单。
- 成本：S（只禁用 DSP 与依赖数据的项；纯显示项始终可用）。

### G13（P2，S）QMenu 无滚动，高 DPI 下有裁切风险

- 位置：`desktop/spectrum_widget.py:2700-2705`。
- 现象：10 行菜单实测 310×358 px；`QMenu` 不滚动。
- 影响：100% 缩放下 680 高窗口可容纳；125%/150% 缩放按比例约 448/537 px，最小窗口下会被屏幕下缘裁切（**推断，未在高 DPI 实测**）。
- 成本：S（分组后每菜单 ≤5 行；或改用可滚动弹出面板）。

### G14（P2，S）跨度滑杆无数字读数

- 位置：`desktop/spectrum_widget.py:2741-2753`、`2795-2830`。
- 现象：标签只写「缩放」，数值靠谱面刻度推断；主界面已按用户裁决移除实时读数。
- 影响：用户无法确认当前 span（例如 48 kHz 还是 2.4 MHz），只能读刻度。
- 成本：S（滑杆 tooltip 或悬停读数带"跨度"前缀与单位，遵守既有"裸数字禁止"规则）。

### G15（P2，S）悬浮读数只有频率 + 电平

- 位置：`desktop/spectrum_widget.py:2116-2134`。
- 现象：悬停只显示 `MHz + dBFS`；SNR/相对本底只在峰值 tooltip 出现（`:2110-2113`）。
- 影响：用户想读某点 SNR/噪声底时没有入口。
- 成本：S（悬停行追加"SNR（相对本帧中位）"，注意刷新频率与可读性）。

### G16（P2，S）OpenGL 入口与旧分区口径遗留

- 位置：`desktop/spectrum_widget.py:2425`（`prefer_opengl` 接收后未使用）、`4223-4233`（docstring 声称"优先 OpenGL"）、`3239-3242`（`_spectrum_height` 仍按 55% 估算）、`1221-1224`（注释仍写"底噪锚在 ~22%"，实际常量已是 0.10）。
- 现象：实际始终 QPainter；`_spectrum_height` 的 55% 分支只因 `SpectrumCurveWidget` 覆写才未生效；自动量程注释与常量口径漂移。
- 影响：文档/参数与实际不符；后续复用 mixin 时可能误判水位。
- 成本：S（删除死参数/死分支或明确标注保留原因；GL 加速另立）。

### G17（P2，S）测试缺口

- 位置：`tests/` 无对应用例（全仓 grep 无 `_apply_initial_state` 的频谱键断言、无滑杆溢出、无菜单项完整性测试）。
- 现象：G1/G2/G4/G6 均可回归而无人拦。
- 影响：同类问题反复出现（本次已发现已知单的 6 px 与实测 36–44 px 不一致）。
- 成本：S（补 4 条行为测试：启动回填、331×436 与 331×303 两档几何、菜单项集合、断连可用性）。

### G18（P2，S）档位常量双份维护

- 位置：`desktop/spectrum_widget.py:225-230`（`WINDOWS/FFT_SIZES/AVG_FRAMES` 硬编码）vs `desktop/config_schema.py:266-294`（同一批允许值再声明一次）。
- 现象：`:226` 注释写"唯一事实源 = config_schema.py 的 fft_size 三档"，但代码仍维护一份字面量元组；`config_schema.py:273` 的 `source` 仍指向 `spectrum_widget.py:151`，实际默认已移到 `:243`。
- 影响：改档位时两处必须同步；漏改会出现"设置允许 4096、面板下拉没有"或反之。
- 成本：S（面板从 schema 元信息派生选项；或给 schema 常量加守卫测试）。

---

## 5. 按严重度排序（最多 20 行）

| # | 严重度 | 缺口 | 关键位置 | 影响 | 成本 |
| --- | --- | --- | --- | --- | --- |
| 1 | P1 | 显示参数启动不回填（9 键）；FFT 被 eco 覆盖；设置页 2048 vs 实际 1024 | `settings_panel.py:524-530`、`main_window.py:2114/4162-4214` | 每次重启重配、设置页数字失真 | M |
| 2 | P1 | 面板 303 高时末根量程下限滑杆被裁 44 px；阈值 <347（探针）/<339（主窗），`setMinimumHeight(90/80/80)` 硬撑而非内容不足（**已修复，见 §7.3**） | `spectrum_widget.py:2749/2770/2785` | 下限滑杆下半段不可操作 | S–M |
| 3 | P1 | 实测 4096/2048/1024 送绘恒 512 根（低档 256）；降采样是性能取舍，FFT 下拉不联动 `num_bins` 是缺陷（**已修复，见 §7.1/§7.2**） | `:2578-2579 → :395-412 → :528/:560 → :563/:619-631 → :1690-1692` | 大 FFT 只涨算力、schema help 与行为矛盾 | S（标注）/M（联动） |
| 4 | P1 | 单一「更多」菜单承载 14 控件/10 行，无分组，7 项无 tooltip | `spectrum_widget.py:2546-2706` | 发现性差、DSP 与显示混排 | M |
| 5 | P2 | 瀑布定时器固定 100 ms，不随性能档 | `spectrum_widget.py:4158-4160/2906-2937` | 高档瀑布与谱面不同步 | S |
| 6 | P2 | 瀑布开关/深度不在面板菜单 | `config_schema.py:228/368`、`spectrum_widget.py:2700-2705` | 用户找不到深度设置 | S |
| 7 | P2 | 三条 FFT 链路窗/平均/dB/噪声底口径不一；zoom/waterfall 占位 | `spectrum_widget.py:517-582`、`spectrum_processor.py:74-133/245-288`、`signal_spectrum.py:35-121` | AI 结论与屏幕不一致 | M–L |
| 8 | P2 | AI 无显示控制工具 | `agent.py:901-933` | 智能体无法改显示参数 | S–M |
| 9 | P2 | 无瀑布对比度/gamma/独立色标 | `spectrum_widget.py:2940-2946/2225-2280` | 弱信号层次不可调 | M |
| 10 | P2 | 自动量程常量写死、噪声底不可见、手动后无原因提示 | `spectrum_widget.py:1628-1674` | 自动量程观感不可控 | S |
| 11 | P2 | 平均 N 帧"攒够再输出"，无进度提示 | `spectrum_widget.py:546-560` | 高平均档画面先冻住 | S |
| 12 | P2 | 断连时显示参数全置灰 | `spectrum_widget.py:2881-2887` | 无法预先配置显示 | S |
| 13 | P2 | QMenu 无滚动，高 DPI 裁切风险（推断） | `spectrum_widget.py:2700-2705` | 小屏/高缩放菜单出界 | S |
| 14 | P2 | 跨度滑杆无数字读数 | `spectrum_widget.py:2741-2753` | 无法确认当前跨度 | S |
| 15 | P2 | 悬浮读数缺 SNR/噪声底 | `spectrum_widget.py:2116-2134` | 读数入口缺失 | S |
| 16 | P2 | `prefer_opengl` 无效、`_spectrum_height` 旧 55% 口径 | `spectrum_widget.py:2425/4223-4233/3239-3242` | 文档与实现不符、复用陷阱 | S |
| 17 | P2 | 上述缺口无回归测试 | `tests/` | 问题可无声复发 | S |
| 18 | P2 | 档位常量双份维护（面板元组 vs schema） | `spectrum_widget.py:225-230`、`config_schema.py:266-294` | 改档漏改即 UI/配置错位 | S |

---

## 6. 未核实 / 边界

- 高 DPI（125%/150%）菜单裁切为**推断**，本次只在 100% 缩放实测。
- 产品最小窗口在真实 Windows 屏下是 **1000×680**（`main_window.py:842-852`：`min(1000, 94% 屏宽)`、`min(680, 94% 屏高)`）；`tests/test_ui_layout_locks.py:311` 的 860 断言来自 offscreen 800×600 虚拟屏（94%→752，`max(860, …)`），不代表真实桌面。本次窄档口径取真实 Windows 的 1000×680。
- 未在真机硬件在线的状态下测量显存/CPU 占用；本文只做几何与代码路径取证。

---

## 7. 修复落地（2026-11-11，按用户指令顺序执行）

### 7.1 把「4096 对显示无效」说清楚（S）

- `config_schema.py`：`fft_size` 的 label 改为「FFT 点数（计算分辨率）」，help 重写为
  「点数决定计算分辨率（RBW/峰值测量精度），显示分辨率由画布像素宽与性能档决定，
  实际「计算 N / 显示 M」见工具栏」；删除「点数越大分得越细」「细节的平衡点」等假承诺。
- `spectrum_widget.py`：FFT 菜单行新增 `_fft_bin_label`，实时显示
  `计算 {fft_size} / 显示 {num_bins}`，并补 tooltip；FFT 下拉改走
  `_on_fft_size_changed`，切档后同步标签。
- 守卫：`tests/test_spectrum_fft_display_truth.py`
  - `test_help_drops_fake_promise`（帮助文本不得再出现假承诺）
  - `test_help_has_two_resolutions` / `test_label_no_longer_claims_spectrum_resolution`
  - `test_display_number_equals_drawn_array_length`（标签里的 M == 实际送绘数组长度）

### 7.2 `num_bins = min(fft_size, 画布像素宽, 档位上限)`（M）

- 新增 `SpectrumPanel._bins_for_width` / `_apply_render_bins`；在 `resizeEvent`、
  FFT 切档、性能档切换时重算；`_PERF_LEVELS` 第二列由固定值改为**上限**
  （高 2048 / 均衡 512 / 省电 256）。
- 取值为能整除 FFT 点数的 2 的幂中与画布像素宽**比值最接近**的一档，
  以保住 `_resample_max` 的 `reshape+max` 快速路径。
- 实测送绘长度：
  | 面板宽 | 画布宽 | FFT 4096 时的 bins | 折线点数变化 |
  | --- | --- | --- | --- |
  | 331（最小窗口） | 299 | **256** | 512 → 256（更省） |
  | 1400 | 1368 | **1024** | 512 → 1024 |
  | 省电档任意宽 | — | **256** | 与旧值一致 |
- 性能预算（本机 CPython 3.14 实测，单次）：
  - `_resample_max` 4096→512 **0.011 ms**、→1024 **0.019 ms**、→2048 **0.035 ms**；
  - `QPolygonF` 512 点 **0.020 ms**、1024 点 **0.033 ms**、2048 点 **0.071 ms**；
  - 瀑布行映射按像素宽度 O(w) 走，与 bins 无关。
  → 最坏增量 < 0.1 ms/帧，相对 50 ms 刷新预算可忽略；省电档预算不变（上限仍 256）。
- 守卫：`tests/test_spectrum_fft_display_truth.py::TestBinsFollowCanvasWidth`
  - 宽/窄画布送绘长度必须不同（1024 vs 256），写死 512 会红；
  - 性能档上限生效；bins 必须整除 FFT（快速路径）。

### 7.3 「显示」弹出层（M，已落地 2026-11-11）

按 §3.4 只做「三根量程滑杆进弹层」这一刀（「更多」的 DSP/显示分组重排不在本笔）：

| | 改前 | 改后 |
| --- | --- | --- |
| 滑杆位置 | 面板右侧常驻竖排（占宽 30 px） | 工具栏「显示」弹出层内三行横滑杆 |
| 工具栏最小宽 | 59（只有「更多」） | **122**（「更多」56 + 间距 6 + 「显示」56 + 边距 4） |
| 面板最小高 | 247（含滑杆列 214） | **205**（offscreen 实测；滑杆列不再参与） |
| 滑杆最小尺寸 | `setMinimumHeight(50)` ×3 | `setMinimumWidth(120)` ×3（`_SLIDER_MIN_W`） |
| 方向契约 | 竖排 `invertedAppearance(True)`（顶=最小） | 横排默认外观（左=最小、右=最大） |
| 步数 | 直接拖 = 1 步 | 点「显示」弹出 + 拖 = 2 步 |

- 滑杆对象与 `valueChanged` 接线不变（`_span_slider` / `_db_max_slider` /
  `_db_min_slider`、`apply_db_range` 仍是唯一状态源），只换了父容器；
  `_db_slider_col` 改挂在「显示」`QMenu` 的 `QWidgetAction` 上。
- 工具栏改为独立 `QWidget`（`_toolbar`）：布局对象没有 `minimumSizeHint`/
  `geometry`，「不裁切」只能靠控件几何反证。
- 守卫：`tests/test_spectrum_slider_overflow.py`
  - 反证一：331×303 请求精确命中、两个入口右边缘 ≤ 工具栏宽；缩到面板最小宽
    （offscreen 126 px）时工具栏实际宽 == `minimumSizeHint().width()`；
  - 反证二：push 真 IQ 后改 dB 量程，FFT 画布像素与逐行分布都必须变化；
    拖动「下限」滑杆同样要改到状态与画面；
  - 反证三：`InstantPopup` + 菜单只有 1 个 action + 无二级子菜单 + 三根滑杆
    首屏可见（2 步，不是 3 步）。
- 连带改的旧钉子：`test_ui_hard_defects`（竖排 `invertedAppearance` → 横排
  方向契约）、`test_planetarium_entry_visibility`（「紧凑收起滑杆列」不再成立，
  改为断言滑杆列不在面板里）。
- 已知未收口：`scripts/ui_narrow_breakdown.py` 仍按「常驻 `dbSliderCol`」量
  滑杆底边，弹层化后该段读数失去意义（属 layout-gap 线的脚本，未在本笔改）。
