# 待落库补丁：QSS 字号钉死 10pt（无障碍「假支持」）

> 提出：2026-11-11 夜；来源：裁定①（本轮优先级最高）
> **状态：已落地**（2026-11-11 深夜，裁定①「选 A + 自动降档」后）。
> 落地文件：`desktop/themes.py`（`BASE_FONT_PT` / `font_scale()` /
> `qss_font_size()` / 9 处 QSS 字号）、`desktop/tokens.py`（`_px_to_pt` 乘倍率）、
> `desktop/main.py`（show 前调用 `fit_scale_to_screen`）、
> `desktop/settings_panel.py`（降档说明行）、`desktop/control_panel.py`（②竖排）。
> 下方 `.patch` 为落地前版本，自动降档未包含；以工作树代码为准。
> 验证工作树：`d:\mbdsdr\output\wt-fs`（detached @ `6e90e93b`）
> 基线报告：`docs/research/2026-11-11-gap-layout.md` §2.5 / §3.9 / §3.10

## 〇、自动降档（裁定①新增，已落地）

`themes.fit_scale_to_screen(window, app, theme)` 在主窗 `show()` 前量
`minimumSizeHint().width()`：

1. 屏幕可用宽的 90% 为上限；`150%` 最小宽 988 → 1024 屏（上限 921）放不下，
   逐档降到 **125%**（865，余 56px）；1280 屏保留 150%。
2. 降档后重算 QSS（`theme.apply`）与 `QFont`，否则只改了一半。
3. 150% 档把 `right_tab.setMinimumWidth(353)`（书签组竖排后需 330，基准视口
   310 会裁字）；125% 及以下置 0，避免把 1024 屏的 125% 顶到 940。
4. 降档说明写进设置页「外观与语言」组：
   「当前屏幕不足以按 150% 渲染，已按 125% 显示。」
5. 剩余 63 处局部 `font-size`（ai_panel 23、main_window 12、location_panel 6 等）
   不在本补丁范围，列为后续项；本次反证覆盖的是主题 QSS 选中的按钮/下拉/
   输入/分组框。

## 一、钉死的范围（全仓 78 处 / 14 文件）

`font-size:` 在 `desktop/` 下共 **78 处**，按文件（行号：值）：

| 文件 | 处数 | 本补丁 | 说明 |
| --- | --- | --- | --- |
| `ai_panel.py` | 23 | 否 | 372:11pt、379:8pt、387:8pt、401:10pt、421:8pt、430:8pt、445:9pt、457:8pt … |
| `main_window.py` | 12 | 否 | 1593:14pt、2045:9pt、2519:8.5pt、2529:9pt、2559:9pt、2576:13px、2623:8.5pt … |
| `themes.py` | 9（+1 文档串误命中） | **是** | 全局 QSS：sectionTitle 11pt / statusValue 11pt / freqDisplay 28pt / QPushButton 10pt / QComboBox 10pt / QLineEdit 10pt / QTextEdit 9pt / QGroupBox 11pt / QSpinBox 10pt |
| `location_panel.py` | 6 | 否 | 435:11px、446:14px、447:11px、448:12px、467:12px、556:11px |
| `tokens.py` | 6 | **是** | 465/470/484/492/557/664：经 `_px_to_pt()` 的 `FONT['*']` |
| `sat_track_panel.py` | 4 | 否 | 170:11pt、176/251/352:8.5pt |
| `new_spacetime_panel.py` | 3 | 否 | 380/535/545:20pt |
| `rf_sky_view.py` | 3 | 否 | 1977:9pt、8327:9pt、8329:9pt |
| `status_panel.py` | 3 | 否 | 321:16pt、921/927:12pt |
| `time_machine.py` | 3 | 否 | 139:15px、141:12px、144:13px |
| `capability_drawer.py` | 2 | 否 | 46:11px、49:10px |
| `floating_panel.py` / `gain_panel.py` / `weather_panel.py` | 各 1 | 否 | 112:12px / 67:8pt / 545:11px |

**本补丁修的是「共享 QSS 那一批」**：按钮 / 下拉 / 单行输入 / 数值框 / 分组框 / 多行输入 / 分区标题 / 频率大数字 / 状态值。这 9 个选择器覆盖右栏与顶栏的绝大部分可点控件，也是用户说的「这批控件」。

剩余 **62 处 / 12 文件**是各面板的内联样式（多为 8～9pt 小标签），改法相同（调用 `themes.qss_font_size()`），但文件分别归频谱显示线 / 卫星线 / 设置线，需各自占用后施工。**不许再用裸 pt/px。**

## 二、改法（机制）

Qt 的 QSS **不认 `em` / `%` / `rem`**，`font-size` 只接受 `pt` / `px` 这类绝对值。
所以「相对字号」在 Qt 里的唯一正确形态是：**在生成 QSS 时把设计稿 pt 按当前应用字体倍率换算成字面量**。

`themes.py` 新增：

```python
BASE_FONT_PT = 10.0


def font_scale() -> float:
    """当前应用字体相对设计基准（BASE_FONT_PT）的倍率。"""
    pt = float(get_app_font().pointSizeF())
    return pt / BASE_FONT_PT if pt > 0 else 1.0


def qss_font_size(pt: float) -> str:
    """把设计稿 pt 换算成当前缩放下的 QSS font-size 字面量（如 12.5pt）。"""
    return f"{round(float(pt) * font_scale(), 2):g}pt"
```

所有 QSS 字号写成 `font-size: {qss_font_size(10)};`。

同时补上根因：`get_app_font()` 原来 `setPointSize(10)` 写死，等于连 QFont 一起钉死，
系统字号调大也读不到。改为：

```python
font.setPointSizeF(max(BASE_FONT_PT, _platform_font_pt()))
```

`_platform_font_pt()` 只读一次 `QApplication.font().pointSizeF()`（下限 10pt，**只允许上调**），
避免主题反复 apply 时把上一轮的放大值当新基准（棘轮）。
本机 Qt 默认字体两档指纹都是 **9pt**，`max(10, 9) = 10`，所以**默认外观零变化**。

`tokens.py` 的 `_px_to_pt()` 同样乘 `font_scale()`（延迟导入避免 themes→tokens 成环），
覆盖 light/figma 两套 token 主题的 6 处。

## 三、反证（同一台机、同一脚本、只改代码）

脚本：`d:\mbdsdr\output\tmp\fontscale_probe.py`（不进库）。
造 7 个控件（主题已 apply 后再建），量 `height()`；字体倍率用闸门同一套包法（包 `themes.get_app_font`）。

DPR1.0（行高 27）：

| 控件 | 改前 100/125/150 | 改后 100/125/150 | 150/100 |
| --- | --- | --- | --- |
| `QPushButton` | 45 / 45 / 45 | **45 / 51 / 57** | 1.267 |
| `QComboBox` | 43 / 43 / 43 | **43 / 49 / 55** | 1.279 |
| `QLineEdit` | 45 / 45 / 45 | **45 / 51 / 57** | 1.267 |
| `QSpinBox` | 45 / 45 / 45 | **45 / 51 / 57** | 1.267 |
| `QGroupBox` | 75 / 75 / 75 | **75 / 83 / 89** | 1.187 |
| `QLabel#sectionTitle` | 37 / 37 / 37 | **37 / 45 / 51** | 1.378 |
| `QCheckBox`（对照，未被 QSS 选中） | 27 / 33 / 39 | 27 / 33 / 39 | 1.444 |

DPR1.5（行高 18）：改前 36 / 36 / 36（按钮），改后 **36 / 41 / 45**。

结论：

1. **改前 125% 与 100% 逐像素相同**（45/45/45），这就是「假装支持无障碍」；
2. 改后 125% ≠ 100%，150% 高度真实增长 26.7%（按钮），字号从 10pt → 12.5pt / 15pt；
3. 对照组 `QCheckBox` 两版一致（27/33/39），说明差异只来自 QSS 而不是测量口径。

真实控件（右栏 `tune_button` / `mode_combo` / `sample_rate_combo` / `freq_input` / `ppm_spin`）
在改前同样逐档不变，改后逐档增长，与合成控件一致。

## 四、代价（必须一起看）

修好之后 125% / 150% 是**真的变大**，右栏内容最小宽随之上升——这不是回归，是此前被钉死掩盖的真实需求：

| 右栏 body 最小宽（default 态，DPR1.0） | 100% | 125% | 150% |
| --- | --- | --- | --- |
| 改前（QSS 钉死） | 349 | **349** | **349** |
| 改后 | 349 | 399 | 449 |

改前「150% 和 100% 一模一样」本身就是 bug 的证据。改后必然更宽，右栏宽度问题详见
`docs/pending-patches/2026-11-11-right-column-width-margin.md` §七（当前瓶颈已换成「收藏」组，
不是本补丁引入）。

## 五、补丁（`git apply`）

```diff
diff --git a/desktop/tokens.py b/desktop/tokens.py
--- a/desktop/tokens.py
+++ b/desktop/tokens.py
@@ -385,11 +385,22 @@ class DesignTokens:
 
     @staticmethod
     def _px_to_pt(px_str: str) -> float:
-        """'39px' -> 29.25pt（QSS font-size 用 pt）。"""
+        """'39px' -> 29.25pt（QSS font-size 用 pt），并按界面字号倍率缩放。
+
+        QSS 不认 em/%，写死的 pt 会让控件无视用户的字号设置。倍率取
+        ``themes.font_scale()``（应用字体 / 设计基准），延迟导入避免与
+        themes→tokens 的模块级依赖成环。
+        """
         try:
-            return round(float(px_str.replace("px", "")) * 0.75, 1)
+            pt = float(px_str.replace("px", "")) * 0.75
         except Exception:
             return 10.0
+        try:
+            from themes import font_scale
+            pt *= font_scale()
+        except Exception:
+            pass
+        return round(pt, 1)
 
     # ------------------------------------------------------------------
     # QSS 生成
```

`themes.py` 的完整差异（新增 4 个符号 + 9 处 `font-size`）见同目录
`2026-11-11-qss-font-scale.patch`（含 tokens.py），或按第二节手工改。

应用与复核：

```powershell
git apply docs/pending-patches/2026-11-11-qss-font-scale.patch
python -m pytest tests/test_theme_switch.py tests/test_ui_layout_locks.py -q
# 反证：125% 必须与 100% 不同
$env:MBDSDR_GATE_FONT_SCALE="1.25"; python scripts/ui_narrow_breakdown.py --json
```

## 六、验收判据

1. 100% 档 7 个控件高度与改前逐像素相同（默认外观零变化）；
2. 125% / 150% 档，上述控件 `height()` 严格大于 100% 档（不是「一样」）；
3. `tests/test_ui_layout_locks.py` 全绿（100% 基线不动）；
4. 主题切换（default / dark / high_contrast / light）无 QSS 解析告警；
5. 剩余 62 处必须在各线施工时同步改，禁止新增裸 `font-size: <数字>pt|px`。
