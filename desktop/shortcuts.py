# SPDX-License-Identifier: MIT
"""
桌面端快捷键登记表（唯一事实源）
================================

main_window 只从 ``bind(id)`` 取键序列，不在各构建函数里散落字符串；
``tests/test_shortcuts_guard.py`` 反向核对窗口里实际绑定的 QAction /
QShortcut 与本表是否一致——新增按钮忘了登记会直接红。

约定：
  * ``key=None`` 表示该动作**故意不配快捷键**，必须在 ``note`` 里写原因；
  * ``scope`` 区分生效范围：window=主窗恒生效，planetarium=仅星象仪形态，
    spectrum=仅频谱控件自身获焦时生效；
  * id 命名 ``域.动作``，域与菜单/顶栏分区一致。

键位归属规则：**焦点在哪，局部优先**。
-------------------------------------
同一键序列允许在不同 scope 各登记一条（例如 ``A`` 同时是
``view.all_sky``（window）和 ``spectrum.mode.am``（spectrum））：

  * 频谱控件自身获焦 → A 归频谱的 AM 解调，主窗「全天模式」不抢键；
  * 频谱没有焦点（焦点在天空视图/主窗其他区域）→ A 归主窗，进入全天模式。

实现靠 Qt 的上下文优先级，不靠 keyPressEvent 拦截：
频谱侧的局部键装成 ``QShortcut`` + ``Qt.WidgetShortcut``（只在父控件自身
获焦时匹配，是 Qt 里最具体的上下文）；主窗侧的全天模式仍是
``WidgetWithChildrenShortcut``，并在槽内跳过「焦点属于频谱局部键作用域」
的情形兜底。这样输入框里按 A 只会输入字母，频谱里按 A 只切 AM。
"""

from typing import NamedTuple, Optional, Tuple


class Shortcut(NamedTuple):
    id: str
    key: Optional[str]
    label: str
    scope: str = "window"
    note: str = ""


SHORTCUTS: Tuple[Shortcut, ...] = (
    # ---- 文件菜单 ----
    Shortcut("file.connect", "Ctrl+C", "连接设备...", note="焦点在文本框时放行复制"),
    Shortcut("file.disconnect", "Ctrl+D", "断开连接"),
    Shortcut("file.quit", "Ctrl+Q", "退出"),
    # ---- 视图菜单 ----
    Shortcut("view.waterfall", "Ctrl+Shift+W", "显示瀑布图"),
    Shortcut("view.fullscreen", "F11", "全屏"),
    Shortcut("view.reset_position", None, "重置窗口位置",
             note="窗口跑到屏幕外时的救援入口，低频操作，只从「视图」菜单点击"),
    Shortcut("view.all_sky", "A", "全天模式（160°）",
             note="焦点在频谱时该键归频谱的 A=AM（局部优先）；频谱无焦点时"
                  "才进入全天模式。规则见本文件顶部「焦点在哪，局部优先」"),
    Shortcut("view.planetarium", "Ctrl+Shift+P", "星象仪模式"),
    Shortcut("view.planetarium_exit", "Esc", "星象仪形态退出", scope="planetarium",
             note="只在该形态启用；先关 AI 回复卡再退出形态"),
    # ---- 工具菜单 ----
    Shortcut("tools.sweep", "Ctrl+Shift+F", "FM 扫频找台"),
    Shortcut("tools.record", "Ctrl+R", "开始/停止录音"),
    Shortcut("tools.ntrip", "Ctrl+Shift+N", "NTRIP 设置"),
    Shortcut("tools.gnss_connect", "Ctrl+Shift+G", "连接 GNSS..."),
    Shortcut("tools.gnss_disconnect", None, "断开 GNSS",
             note="与连接成对，重复配键会与连接抢同一次按键"),
    Shortcut("tools.sat_start", None, "开始跟踪选中卫星", note="需先选卫星，低频操作"),
    Shortcut("tools.sat_stop", None, "停止卫星跟踪", note="与开始跟踪成对"),
    Shortcut("tools.sat_tle", None, "更新卫星 TLE (Celestrak)",
             note="联网操作，避免误触"),
    Shortcut("tools.about", "F1", "关于 MBDSDR"),
    # ---- 帮助菜单 ----
    # 可发现性入口：内容由本表与注册表实时生成，不另抄一份键位/能力清单。
    # 不配键是刻意的——F1 已绑「关于」，避免抢占系统帮助键的既有语义。
    Shortcut("help.quick_start", None, "三步上手",
             note="帮助菜单常驻入口；低频操作，不占系统帮助键 F1"),
    Shortcut("help.catalog", None, "能力目录",
             note="帮助菜单常驻入口；从注册表实时生成，低频操作"),
    Shortcut("help.shortcuts", None, "快捷键一览",
             note="帮助菜单常驻入口；内容由本表生成，低频操作"),
    # ---- 面板菜单 ----
    Shortcut("panel.reset_layout", None, "恢复出厂布局", note="破坏性操作，要求点击确认"),
    # ---- 顶栏 ----
    Shortcut("layout.save", "Ctrl+Shift+S", "保存"),
    Shortcut("layout.delete", None, "删除", note="破坏性操作，内置布局本就置灰"),
    Shortcut("vfo.new", "Ctrl+Alt+V", "+ VFO", note="避开输入框里的 Ctrl+Shift+V 粘贴"),
    Shortcut("vfo.cycle", "Ctrl+Tab", "切换主听"),
    Shortcut("vfo.mute", "Ctrl+M", "静音"),
    Shortcut("vfo.replay", "Ctrl+Shift+R", "回放"),
    # ---- 频谱视图（焦点内局部键，scope=spectrum）----
    # 只在频谱控件自身获焦时生效（QShortcut + Qt.WidgetShortcut）。这些键
    # 不进主窗 QAction，避免焦点在输入框/其他面板时误触。同一键位可与
    # window 条目重复（见本文件顶部「焦点在哪，局部优先」）。
    Shortcut("spectrum.mode.am", "A", "AM 解调", scope="spectrum",
             note="频谱获焦时 A=AM；焦点不在频谱时 A 归「全天模式」"),
    Shortcut("spectrum.mode.nfm", "N", "NFM 解调", scope="spectrum",
             note="频谱获焦时生效"),
    Shortcut("spectrum.mode.fm", "F", "FM 解调", scope="spectrum",
             note="频谱获焦时生效"),
    Shortcut("spectrum.mode.wfm", "W", "WFM 解调", scope="spectrum",
             note="频谱获焦时生效"),
    Shortcut("spectrum.mode.cw", "C", "CW 解调", scope="spectrum",
             note="频谱获焦时生效"),
    Shortcut("spectrum.sideband.lsb", "S", "LSB 解调", scope="spectrum",
             note="频谱获焦时生效"),
    Shortcut("spectrum.sideband.usb", "Shift+S", "USB 解调", scope="spectrum",
             note="频谱获焦时生效"),
    Shortcut("spectrum.tune.left", "Left", "频率 −1 格（Shift ×10 / Alt ×0.1）",
             scope="spectrum", note="频谱获焦时生效；按网格步进向下调谐"),
    Shortcut("spectrum.tune.right", "Right", "频率 +1 格（Shift ×10 / Alt ×0.1）",
             scope="spectrum", note="频谱获焦时生效；按网格步进向上调谐"),
    Shortcut("spectrum.tune.up", "Up", "频率细调 +（1/10 格）", scope="spectrum",
             note="频谱获焦时生效"),
    Shortcut("spectrum.tune.down", "Down", "频率细调 −（1/10 格）",
             scope="spectrum", note="频谱获焦时生效"),
    Shortcut("spectrum.filter.narrow", ",", "滤波收窄一档", scope="spectrum",
             note="频谱获焦时生效；按当前模式的带宽预设表步进"),
    Shortcut("spectrum.filter.wide", ".", "滤波放宽一档", scope="spectrum",
             note="频谱获焦时生效；按当前模式的带宽预设表步进"),
)

#: id -> Shortcut（构建期按 id 取键）
BY_ID = {s.id: s for s in SHORTCUTS}


def bind(shortcut_id: str) -> Optional[str]:
    """按登记 id 取键序列；未登记的 id 直接抛 KeyError（构建期就暴露）。"""
    return BY_ID[shortcut_id].key


def registered_keys() -> Tuple[str, ...]:
    """所有已配置的键序列（不含 key=None 的条目）。"""
    return tuple(s.key for s in SHORTCUTS if s.key)
