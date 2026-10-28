# 上游 SDR UI 操作逻辑学习笔记（ui_operation_logic_real）

> SPDX-License-Identifier: MIT
>
> 目标：从上游 SDR GUI（SDR++ / SDRangel / gqrx / CubicSDR）提炼**操作逻辑**
> （不是架构——架构见 `sdr_ui_architecture.md` / `sdrpp_deep.md`）：
> 频谱/瀑布交互、快捷键可发现性、空态与错误呈现、面板身份与布局落盘。
> 产出直接对照 MBDSDR 的 `desktop/`，给出「已对齐 / 待对齐」清单。
>
> 标注纪律：**〔实测〕**= 本机跑过或仓内源码核过 file:line；**〔推断〕**=
> 机制推断/公开文档口径，未在本机验证。GPL 代码只学机制不搬运。

---

## 1. 频谱/瀑布：调谐与量程的控件归属

| 上游 | 机制 | MBDSDR 对齐情况 |
|---|---|---|
| SDR++ | 频谱画布滚轮 = 调谐中心频率；Shift/Alt 改步进倍率；瀑布区拖拽 = 平移视图；dB 量程在频谱顶部窄条（上/下沿各一拖柄）〔推断，见 sdrpp_deep.md §2 画布右缘竖排滑条——phase84 已按此做 edge-rail〕 | 〔实测〕`desktop/spectrum_widget.py` wheelEvent：Shift=10×、Alt=0.1×、snap_interval 对齐（SDR++ roundl 语义）已有；滚轮瞬时 tooltip（步进倍率+目标频率）phase85-A6 补齐 |
| gqrx | 频谱点击 = 跳频；拖拽 = 改带宽（对 VFO 框边缘）〔推断〕 | 〔实测〕MBDSDR 多 VFO 点选/拖拽在 spectrum_multi_vfo 测试有钉扎；带宽拖拽未见 |
| CubicSDR | 拖数字调谐（drag-tune）〔实测，见 sdr_ui_architecture.md §0〕 | 〔实测〕control_panel 数字输入 + wheel 分档均在 |

**操作逻辑结论**：调谐入口必须「滚轮（微调）+ 数字（直达）+ 点击（跳频）」三路并存，
且滚轮必须有瞬时可见反馈（步进多少 → 到多少），否则用户在 1568px 宽谱上盲调。
MBDSDR 已三路齐全；反馈由 phase85 tooltip 收口。

## 2. 快捷键可发现性

| 上游 | 机制 |
|---|---|
| SDR++ | 无内置快捷键表，靠 README；社区常抱怨不可发现〔推断〕 |
| SDRangel | 每个 dock/面板自带右键菜单，动作在菜单里可见，快捷键作为菜单加速键显示〔推断〕 |
| gqrx | 传统菜单栏加速键（Alt 带下划线）〔推断〕 |

**操作逻辑结论**：菜单里挂 QAction = 快捷键自动可发现（菜单渲染加速键文本）。
MBDSDR C++ 侧 `shortcuts_catalog.h` 是显式目录，审计口径：目录里有但 main_window
没连 = 死快捷键（幽灵），连了但目录没有 = 不可发现——两者都算 bug。
〔实测〕phase85 C++ 审计按此口径查过（本机不可编译，只审计）；
Python 侧 `QAction` 全挂菜单栏，加速键随菜单可见，无死绑定（A3 另修了
Ctrl+C 焦点冲突——文本框获焦时全局动作必须让位系统惯例复制）。

## 3. 空态与错误呈现（本仓第一红线的上游对照）

| 上游 | 无设备时的行为 |
|---|---|
| SDR++ | 设备下拉显示 "None"，频谱画布显示噪声底以下的空线 + 状态栏 "No device"〔推断〕 |
| gqrx | 弹窗报错一次，主界面频谱冻结在最后一帧〔推断——冻结最后一帧正是本仓禁止的「陈旧数据冒充实时」〕 |
| SDRangel | 每个 device 面板显式灰显 + tooltip 说明缺什么〔推断〕 |

**操作逻辑结论**：空态 = 显式（灰显/空态文案/错误信封），**绝不冻结最后一帧**。
〔实测〕MBDSDR `device_panel.py` 空态文案 + 连接键禁用；`rtltcp_client.read_samples`
读不够返回 None 不返回零数组；weather_panel 解不出图如实报帧数与原因
（phase85-P0 收口：compose_visible_image 失败路径只回 None + 日志原因）。

## 4. 面板身份与布局落盘

上游三家都不承诺跨语言/跨版本布局兼容（dock 标题即身份，改翻译就丢布局）
〔推断〕。MBDSDR 走得更前：panel id 稳定身份（`panel_registry.py` + tabBar().tabData），
落盘存 id、恢复时 id ∪ 译文别名双索引。〔实测〕phase85 A1：
`_raise_center_tab` 译文残留已清；C++ 侧 7d3fa3f 同类修复早已落地。

## 5. 待对齐清单（下一轮 UI 候选，按影响排序）

1. **dB 量程拖柄**（SDR++ 窄条双拖柄）：MBDSDR 有 spinbox 但无画布直拖——高频操作路径少一跳。
2. **VFO 带宽边缘拖拽**（gqrx 式）：多 VFO 已有，带宽只能数字改。
3. **右键菜单统一动作出口**（SDRangel 式）：面板右键 = 动作可发现性兜底，快捷键作加速键。
4. **失联瞬时横幅**：设备拔出时画布顶部红条 + 原因，而不是只在日志（上游三家都弱在这，可以做得比上游好）。

---

## 证据索引

- 仓内：`sdr_ui_architecture.md`（四项目范式对照）、`sdrpp_deep.md`（模块图/设备 UI 移植）、
  `stellarium_interaction.md`（交互模型参考）。
- MBDSDR 对齐改动：phase83（设置治理）、phase84（edge-rail）、phase85（A1-A5/B1-B7/P0 合成）。
- 本文件所有「〔推断〕」项未在本机跑过上游 GUI；如需转〔实测〕，需在本机装 SDR++ 对照录屏。
