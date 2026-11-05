# SPDX-License-Identifier: MIT
"""
MBDSDR 桌面端设置单一事实源（desktop/config_schema.py）
=========================================================

本模块是**唯一**定义"设置项"的地方：键名、默认值、类型、取值范围、枚举选项、
分组、人话文案、是否需重启、存储后端。UI（settings_panel.py）只按 schema 渲染，
不许再写第二份默认值；settings.py 的 DEFAULTS 也从这里派生。

设计红线：
  * 默认值必须有出处：每条 ``source`` 写清取自哪个文件/哪一行/哪个上游约定，
    不允许"拍脑袋"。
  * 答不出"用户何时想改 / 默认依据 / 改错怎么办"的项，不进 exposed，
    在本文件底部 LOCKED 区登记原因（文档 docs/settings-inventory.md 同步）。
  * 越界/坏值绝不崩：数值夹到范围、未知枚举回默认，并让调用方能看见原因
    （:func:`validate_value` 返回 reason，导入流程据此汇报，不静默吞掉）。

存储后端（storage）：
  * ``settings``   → desktop_settings.json（DesktopSettings，原子写 + 防抖）
  * ``gui_config`` → ~/.mbdsdr/gui_config.json（观测站坐标，沿用既有权威源）
  * ``ai_config``  → ~/.mbdsdr/config.json（AI 参数，沿用 mbdsdr_ai.config）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# 静噪默认值/范围取自内核常量，schema 不再写第二份字面量（A5 交付说明）。
# 内核缺失（如裁剪打包）时退回同值，避免整个设置页签导入失败。
try:
    from mbdsdr_ai.squelch import (
        DEFAULT_SQUELCH_DBFS, SQUELCH_MAX_DBFS, SQUELCH_MIN_DBFS,
    )
except ImportError:  # pragma: no cover - 仅内核缺失时
    DEFAULT_SQUELCH_DBFS = -50.0
    SQUELCH_MAX_DBFS = -20.0
    SQUELCH_MIN_DBFS = -100.0

try:
    from mbdsdr_ai.skyengine.stars import EXTINCTION_K as _SKY_EXTINCTION_K
except ImportError:  # pragma: no cover - 仅内核缺失时
    _SKY_EXTINCTION_K = 0.2

# 采样率默认值的单一事实源是 desktop/constants.py（它又派生自
# mbdsdr_ai.sdr_backend.DEFAULT_SAMPLE_RATE_HZ=2.4 MS/s）；这里不写第二份字面量。
try:
    from desktop.constants import DEFAULT_SAMPLE_RATE_HZ as _DEFAULT_SAMPLE_RATE_HZ
except ImportError:  # desktop 目录直接上 sys.path（脚本/测试）时
    try:
        from constants import DEFAULT_SAMPLE_RATE_HZ as _DEFAULT_SAMPLE_RATE_HZ
    except ImportError:  # 常量模块也不可用时退回同值，保证 schema 仍可导入
        _DEFAULT_SAMPLE_RATE_HZ = 2_400_000

#: 配置导出格式版本（导出文件里写这个整数）。
#: 版本策略：**同主版本允许导入并迁移**（缺字段按默认补齐、多字段列出后忽略），
#: 只有跨主版本才拒绝——主版本变化意味着字段语义/取值范围可能重定义，
#: 按旧语义解释会写坏设置。
CONFIG_SCHEMA_VERSION = 1
CONFIG_SCHEMA_MAJOR = 1
CONFIG_SCHEMA_MINOR = 0

#: 分组展示顺序（设置面板页签）。未列分组不会显示。
GROUP_ORDER: Tuple[str, ...] = (
    "通用",
    "显示与主题",
    "星象仪",
    "频谱与解码",
    "卫星与多普勒",
    "AI",
    "存储与日志",
    "服务与远程访问",
)


@dataclass(frozen=True)
class SettingItem:
    """一个设置项的完整定义（默认值 + 校验 + 文案 + 生效方式）。"""

    key: str
    group: str
    label: str
    help: str
    kind: str                      # bool | int | float | enum | text
    default: Any
    source: str                    # 默认值出处（文件:行号 / 上游约定）
    choices: Tuple[Tuple[Any, str], ...] = ()
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    step: Optional[float] = None
    requires_restart: bool = False
    restart_note: str = ""
    storage: str = "settings"
    exposed: bool = True           # False = 内部键（不在面板出现，但仍参与导入导出）
    secret: bool = False           # True = 永不写入导出文件

    @property
    def choice_values(self) -> Tuple[Any, ...]:
        return tuple(v for v, _ in self.choices)

    @property
    def choice_labels(self) -> Tuple[str, ...]:
        return tuple(lbl for _, lbl in self.choices)


def _e(value: Any, label: str) -> Tuple[Any, str]:
    return (value, label)


# ---------------------------------------------------------------------------
# 设置项定义（全量）
# ---------------------------------------------------------------------------
ALL_ITEMS: Tuple[SettingItem, ...] = (
    # ---------------------------------------------------------------- 通用
    SettingItem(
        key="ui_language", group="通用", kind="enum",
        label="界面语言", default="zh",
        help="切换界面语言。译文未构建时保持简体中文，不会出现半坏翻译。",
        source="desktop/settings.py:59；源语言约定见 desktop/l10n.py:5",
        choices=(_e("zh", "简体中文"), _e("en", "English"), _e("ja", "日本語")),
        requires_restart=True,
        restart_note="Qt 翻译器必须在窗口创建前安装，切换后重启应用才生效。",
    ),
    SettingItem(
        key="touch_mode", group="通用", kind="enum",
        label="触屏适配", default="auto",
        help="自动检测触屏设备；强制触屏会放大按钮/滚动条，强制鼠标则保持桌面手感。",
        source="desktop/settings.py:49；检测语义见 desktop/touch_manager.py",
        choices=(_e("auto", "自动检测"), _e("on", "强制触屏"), _e("off", "强制鼠标")),
    ),

    # ---------------------------------------------------------------- 显示与主题
    SettingItem(
        key="theme", group="显示与主题", kind="enum",
        label="主题", default="default",
        help="整套配色与控件样式；频谱/瀑布会同步换色。",
        source="desktop/themes.py:545 DEFAULT_THEME=default",
        choices=(_e("default", "默认浅色"), _e("dark", "深色"),
                 _e("high_contrast", "高对比（无障碍）"),
                 _e("dark_car", "CarWith 深色"), _e("figma", "Figma 深色")),
    ),
    SettingItem(
        key="show_waterfall", group="显示与主题", kind="bool",
        label="显示瀑布图", default=True,
        help="关闭后只保留频谱曲线，可省一点绘制开销。",
        source="desktop/settings.py:45（show_waterfall=True）",
    ),

    # ---------------------------------------------------------------- 星象仪
    SettingItem(
        key="sky_pressure_hpa", group="星象仪", kind="float",
        label="当地大气压", default=1013.25, minimum=800.0, maximum=1100.0,
        step=1.0,
        help="用于地平线附近的大气折射修正；与当地气象站读数一致时高度角更准。",
        source="desktop/rf_sky_view.py:227 DISPLAY_REFRACTION_PRESSURE_HPA=1013.25",
    ),
    SettingItem(
        key="sky_temperature_c", group="星象仪", kind="float",
        label="当地气温", default=10.0, minimum=-50.0, maximum=50.0,
        step=1.0,
        help="与气压一起决定折射强度；极端高温/低温下地平线附近高度角会有差异。",
        source="desktop/rf_sky_view.py:228 DISPLAY_REFRACTION_TEMPERATURE_C=10.0",
    ),
    SettingItem(
        key="bortle", group="星象仪", kind="int",
        label="光害等级（Bortle）", default=3, minimum=1, maximum=9, step=1,
        help=("你头顶天空的光害程度：1=极暗乡村（银河照亮地面），"
              "5=郊外可见银河但无细节，9=市中心（几乎看不到银河）。"
              "等级越高可见星数越少——同一视场下 Bortle 3 的可见星数约为 "
              "Bortle 8 的 7 倍（本星表实测 1456 对 194 颗）。"
              "本模型对 1–3 档统一取裸眼极限 6.5 等，4 档起每档少约 0.35 等。"),
        source="mbdsdr_ai/skyengine/stars.py:155 DEFAULT_BORTLE=3",
    ),

    # ---------------------------------------------------------------- 频谱与解码
    SettingItem(
        key="fft_size", group="频谱与解码", kind="enum",
        label="频谱分辨率（FFT 点数）", default=2048,
        help="点数越大，频率分得越细但刷新越慢；2048 是速度与细节的平衡点。",
        source="desktop/spectrum_widget.py:151 默认 2048；档位同 SDR++ main_window.cpp:34",
        choices=(_e(1024, "1024（快）"), _e(2048, "2048（均衡）"),
                 _e(4096, "4096"), _e(8192, "8192"), _e(16384, "16384（最细）")),
    ),
    SettingItem(
        key="fft_window", group="频谱与解码", kind="enum",
        label="频谱窗函数", default="Nuttall",
        help="抑制频谱泄漏；Nuttall 旁瓣最低，Flattop 更适合测幅度。",
        source="desktop/spectrum_widget.py:150；上游 iq_frontend.h:17-21 / main_window.cpp:91",
        choices=tuple((w, w) for w in
                      ("Nuttall", "Hann", "Hamming", "Blackman", "Flattop", "None")),
    ),
    SettingItem(
        key="fft_avg_frames", group="频谱与解码", kind="enum",
        label="频谱平均帧数", default=1,
        help="多帧平均让底噪更平滑、弱信号更易读，但会拖慢响应。",
        source="desktop/spectrum_widget.py:152 默认 1；档位见同上 AVG_FRAMES",
        choices=(_e(1, "不平均"), _e(4, "4 帧"), _e(8, "8 帧"), _e(16, "16 帧")),
    ),
    SettingItem(
        key="waterfall_palette", group="频谱与解码", kind="enum",
        label="瀑布配色", default="default",
        help="默认低饱和不刺眼；经典 SDR++ 色带对弱信号层次更敏感。",
        source="desktop/spectrum_widget.py: _PlotState.wf_colors（默认第 0 项「默认」）",
        choices=(_e("default", "默认（低饱和）"), _e("sdrpp", "经典 SDR++")),
    ),
    SettingItem(
        key="peak_threshold_db", group="频谱与解码", kind="float",
        label="峰值判定阈值", default=6.0, minimum=1.0, maximum=30.0, step=1.0,
        help=("峰值显示门：高于「本帧全部有限 bin 的中位数」多少 dB 才算一个峰。"
              "这是相对余量，不是绝对 dBFS 电平（中位数随信号环境浮动，"
              "所以同一阈值在不同噪声下卡到的峰不同）。"
              "调大只标强信号，调小会标出更多弱峰。"),
        source="desktop/spectrum_widget.py: _PEAK_REL_DB_DEFAULT"
               "（峰值显示门，默认 6.0 dB，基准为全帧中位数）",
    ),
    SettingItem(
        key="show_peaks", group="频谱与解码", kind="bool",
        label="标注峰值", default=True,
        help="在谱面上画出超过阈值的峰点与频率标签。",
        source="desktop/spectrum_widget.py: _PlotState.show_peaks（默认 True）",
    ),
    SettingItem(
        key="waterfall_smooth", group="频谱与解码", kind="bool",
        label="瀑布时间平滑", default=False,
        help="对瀑布逐行做轻度 IIR 平滑，弱信号轨迹更连贯，但快速突发会被抹淡。",
        source="desktop/spectrum_widget.py:173 wf_smooth=False；UI 默认见 :1795",
    ),
    SettingItem(
        key="show_bookmarks", group="频谱与解码", kind="bool",
        label="显示书签旗标", default=True,
        help="在谱面顶部显示书签竖旗，点击可直接调到该频点。",
        source="desktop/spectrum_widget.py:852 show_bookmarks=True",
    ),
    SettingItem(
        key="show_bandplan", group="频谱与解码", kind="bool",
        label="显示频段彩带", default=True,
        help="按 bandplan.json 在谱面标注短波/航空/业余/卫星等频段。",
        source="desktop/spectrum_widget.py:855 show_bandplan=True",
    ),
    SettingItem(
        key="squelch_default_db", group="频谱与解码", kind="int",
        label="静噪门限默认值", default=int(DEFAULT_SQUELCH_DBFS),
        minimum=int(SQUELCH_MIN_DBFS), maximum=int(SQUELCH_MAX_DBFS), step=1,
        help=("静噪门限＝比该模式「噪声基线」高多少的余量（相对值，不是绝对音频 dBFS）。"
              "刻度映射：-100 ≈ 0 dB（几乎常开）、-50 ≈ 6 dB（默认）、"
              "-20 ≈ 16 dB（只放行强信号）。滑块数字沿用旧键名，但含义已是相对余量；"
              "噪声基线由解调后的谱峰/谱中位自动标定，切换模式或改变增益会重标。"
              "余量越高越容易切断弱信号，越低越容易放过底噪。"),
        source="mbdsdr_ai/squelch.py:54-62 SQUELCH_MIN/MAX/默认值与余量映射；"
               "margin_threshold_db() 为唯一换算入口",
    ),
    SettingItem(
        key="tuning_step_hz", group="频谱与解码", kind="enum",
        label="调谐步进", default=10_000,
        help=("滚轮/方向键与频谱吸附网格按这个步进走，与顶部「步进」下拉框同值。"
              "10 kHz 是默认平衡点；细调选 10/100 Hz，快速跨越选 1 MHz。"),
        source="desktop/spectrum_widget.py:921 snap_interval=10_000；"
               "档位同 desktop/control_panel.py:331-332",
        choices=(_e(10, "10 Hz"), _e(100, "100 Hz"), _e(1_000, "1 kHz"),
                 _e(10_000, "10 kHz"), _e(100_000, "100 kHz"),
                 _e(1_000_000, "1 MHz")),
    ),

    SettingItem(
        key="waterfall_depth_rows", group="频谱与解码", kind="enum",
        label="瀑布历史深度", default=256,
        help=("瀑布图保留多少行历史，决定能向上回溯多久。"
              "只提供六档（128/256/512/1024/2048/4096 行），与 C++ 界面和 AI "
              "工具同一枚举；非档位值（如 384）回落 256 并在状态栏说明原因。"
              "2048 行在 512 bin 下约 4 MB、2048 bin 下约 16 MB。"
              "深度只影响回看长度与内存，不拖慢每帧刷新；"
              "在瀑布窗口滚轮上滚即可回看，下滚回到最新。"),
        source="desktop/spectrum_widget.py WF_DEPTH_CHOICES / set_waterfall_depth()；"
               "cpp/src/core/tokens.h kWaterfallDepthChoices 同六档",
        choices=(_e(128, "128 行"), _e(256, "256 行"), _e(512, "512 行"),
                 _e(1024, "1024 行"), _e(2048, "2048 行"), _e(4096, "4096 行")),
    ),
    SettingItem(
        key="sky_extinction_k", group="星象仪", kind="float",
        label="大气消光系数 k", default=float(_SKY_EXTINCTION_K),
        minimum=0.0, maximum=0.5, step=0.01,
        help=("大气对星光的消光强度：Δm = k / sin(仰角)，逐星按各自高度计算。"
              "0=理想无消光，0.2=晴朗海平面（默认），0.5=雾霾/低空污染。"
              "它只压低低仰角天体，不改变 Bortle 决定的天顶极限星等，两者不重复计。"),
        source="mbdsdr_ai/skyengine/stars.py EXTINCTION_K=0.2；"
               "desktop/rf_sky_view.py set_extinction_k()",
    ),

    # ---------------------------------------------------------------- 存储与日志
    SettingItem(
        key="record_dir", group="存储与日志", kind="text",
        label="录音目录", default="",
        help=("录制的 IQ/音频文件存到这里。留空 = 程序目录旁的 "
              "recordings（本机即 D:\\mbdsdr\\recordings，默认不写 C 盘用户目录）。"
              "改目录只影响新录音，已有录音不动。"),
        source=("desktop/main_window.py:5536 旧写死 ~/mbdsdr_recordings；"
                "新唯一来源 desktop/settings.py:resolve_record_dir"),
    ),

    # ---------------------------------------------------------------- 卫星与多普勒
    SettingItem(
        key="observer_lat", group="卫星与多普勒", kind="float",
        label="地面站纬度", default=None, minimum=-90.0, maximum=90.0, step=0.000001,
        storage="gui_config",
        help="北纬为正。未设置时卫星过境/天空图保持空状态，不编造位置。",
        source="desktop/main_window.py:447 初始 None；:7768 从 gui_config.json 读回",
    ),
    SettingItem(
        key="observer_lon", group="卫星与多普勒", kind="float",
        label="地面站经度", default=None, minimum=-180.0, maximum=180.0, step=0.000001,
        storage="gui_config",
        help="东经为正。与纬度一起决定过境时刻与多普勒曲线。",
        source="desktop/main_window.py:447 初始 None；:7769 从 gui_config.json 读回",
    ),
    SettingItem(
        key="pass_min_elevation_deg", group="卫星与多普勒", kind="float",
        label="过境最低仰角", default=10.0, minimum=0.0, maximum=45.0, step=1.0,
        help=("过境列表只保留「峰值仰角」不低于该值的过境（度）。"
              "0=连贴地过境也列；45=只留头顶高仰角的好机会。"
              "打开「显示低仰角过境」时，下限让位到 5°。"),
        source="desktop/settings.py: effective_pass_min_elevation_deg()"
               "（消费点接线见 docs/settings-inventory.md §9.1）",
    ),
    SettingItem(
        key="pass_window_hours", group="卫星与多普勒", kind="int",
        label="过境搜索窗口", default=24, minimum=1, maximum=72, step=1,
        help=("向未来搜索多少小时的过境（1–72）。窗口越长列表越多、算得越久。"),
        source="desktop/settings.py: effective_pass_window_hours()"
               "（消费点接线见 docs/settings-inventory.md §9.1）",
    ),
    SettingItem(
        key="show_low_elevation_passes", group="卫星与多普勒", kind="bool",
        label="显示低仰角过境", default=False,
        help=("打开后把峰值仰角 5°–「过境最低仰角」之间的过境也列出来"
              "（适合追低轨/地平线附近的机会）；关闭只列达到最低仰角的过境。"),
        source="desktop/settings.py: effective_show_low_elevation_passes() / "
               "effective_pass_visibility_gate_deg()",
    ),

    # ---------------------------------------------------------------- AI
    SettingItem(
        key="ai_temperature", group="AI", kind="float",
        label="回答随机度（temperature）", default=0.7,
        minimum=0.0, maximum=2.0, step=0.05,
        storage="ai_config",
        help="越高越发散、越低越保守；无线电操作建议 0.2–0.8。",
        source="mbdsdr_ai/config.py:24 DEFAULT_CONFIG temperature=0.7",
    ),
    SettingItem(
        key="ai_top_p", group="AI", kind="float",
        label="采样截断（top_p）", default=0.9,
        minimum=0.0, maximum=1.0, step=0.05,
        storage="ai_config",
        help="只从累计概率前 top_p 的词里采样；与温度一起控制回答风格。",
        source="mbdsdr_ai/config.py:25 DEFAULT_CONFIG top_p=0.9",
    ),
    SettingItem(
        key="ai_max_output_tokens", group="AI", kind="int",
        label="单次回答长度上限", default=2048, minimum=128, maximum=16384,
        step=128, storage="ai_config",
        help="太小会截断长回答；太大更慢也更费额度。",
        source="mbdsdr_ai/config.py:21 max_output_tokens=2048",
    ),
    SettingItem(
        key="ai_max_context_tokens", group="AI", kind="int",
        label="上下文窗口", default=8192, minimum=1024, maximum=131072,
        step=1024, storage="ai_config",
        help="模型一次能记住的对话长度；超出后按压缩阈值自动摘要。",
        source="mbdsdr_ai/config.py:20 max_context_tokens=8192",
    ),
    SettingItem(
        key="ai_timeout", group="AI", kind="int",
        label="请求超时（秒）", default=60, minimum=5, maximum=300, step=5,
        storage="ai_config",
        help="本地小模型可调大；网络差时调大能减少超时失败。",
        source="mbdsdr_ai/config.py:26 timeout=60",
    ),

    # ---------------------------------------------------------------- 内部键（不暴露）
    SettingItem(key="frequency_hz", group="", kind="float", label="", help="",
                default=98_000_000.0, source="desktop/settings.py:29", exposed=False),
    SettingItem(key="gain_db", group="", kind="float", label="", help="",
                default=20.0, source="desktop/settings.py:30", exposed=False),
    SettingItem(key="demod_mode", group="", kind="text", label="", help="",
                default="FM", source="desktop/settings.py:31", exposed=False),
    SettingItem(key="bandwidth_hz", group="", kind="float", label="", help="",
                default=0.0, source="desktop/settings.py:32", exposed=False),
    SettingItem(key="sample_rate_hz", group="", kind="float", label="", help="",
                default=float(_DEFAULT_SAMPLE_RATE_HZ),
                source="desktop/constants.py: DEFAULT_SAMPLE_RATE_HZ"
                       "（派生自 mbdsdr_ai/sdr_backend.py:110，2.4 MS/s）",
                exposed=False),
    SettingItem(key="callsign", group="", kind="text", label="", help="",
                default="", source="desktop/settings.py:34", exposed=False),
    SettingItem(key="gnss_port", group="", kind="text", label="", help="",
                default="", source="desktop/settings.py:35", exposed=False),
    SettingItem(key="gnss_baudrate", group="", kind="int", label="", help="",
                default=9600, source="desktop/settings.py:36", exposed=False),
    SettingItem(key="agc_enabled", group="", kind="bool", label="", help="",
                default=True, source="desktop/settings.py:37", exposed=False),
    SettingItem(key="agc_speed", group="", kind="text", label="", help="",
                default="medium", source="desktop/settings.py:38", exposed=False),
    SettingItem(key="volume", group="", kind="int", label="", help="",
                default=48, source="desktop/settings.py:39", exposed=False),
    SettingItem(key="volume_scale_v2", group="", kind="bool", label="", help="",
                default=True, source="desktop/settings.py:44", exposed=False),
    SettingItem(key="app_mode", group="", kind="text", label="", help="",
                default="ide", source="desktop/settings.py:62", exposed=False),
    # 服务开关（既有面板，保留）
    SettingItem(key="rc_enabled", group="服务与远程访问", kind="bool",
                label="启用远程控制（TCP）", default=False,
                help="hamlib rigctld 风格；只监听 127.0.0.1，默认关闭。",
                source="desktop/settings.py:53"),
    SettingItem(key="rc_port", group="服务与远程访问", kind="int",
                label="远程控制端口", default=7356, minimum=1, maximum=65535,
                step=1, help="hamlib/rigctld 兼容端口；仅本机监听，改完需重启服务。",
                source="mbdsdr_ai/remote_control.py:40 DEFAULT_RC_PORT=7356"),
    SettingItem(key="web_enabled", group="服务与远程访问", kind="bool",
                label="启用 Web 服务", default=False,
                help="HTTP + WebSocket 频谱/音频流；只监听 127.0.0.1，默认关闭。",
                source="desktop/settings.py:55"),
    SettingItem(key="web_port", group="服务与远程访问", kind="int",
                label="Web 服务端口", default=8080, minimum=1, maximum=65535,
                step=1, help="浏览器访问 http://127.0.0.1:端口/ 的端口；仅本机监听。",
                source="git HEAD:desktop/settings_panel.py DEFAULT_WEB_PORT=8080"),
)

#: key -> item（单一事实源索引）
SCHEMA: Dict[str, SettingItem] = {it.key: it for it in ALL_ITEMS}

#: 面板里可见的项（按 GROUP_ORDER + 定义顺序）
EXPOSED_ITEMS: Tuple[SettingItem, ...] = tuple(it for it in ALL_ITEMS if it.exposed)

#: 设置面板不展示、但确实锁死的条目及原因（文档与 UI 提示共用）。
LOCKED: Tuple[Tuple[str, str], ...] = (
    ("星象仪图层（方位网格/赤道网格/罗盘/轨迹）",
     "星象仪底栏已有四个图层开关（rf_sky_view.py:6944-6957），"
     "设置里再开一套会出现两个不同步入口；持久化需坐标视图线在底栏直接写配置。"),
    ("Max/Min hold、dB 显示范围",
     "它们是测量态临时工具，不是长期偏好；重启清零是预期行为，持久化反而制造困惑。"),
    ("日志级别",
     "桌面端没有 logging.basicConfig/级别开关（仅分散的 getLogger），"
     "没有可改的消费点；先补日志基础设施再谈配置。"),
    ("采样率 / 带宽 / 增益 / 解调模式 / 音量",
     "控制面板与设备面板已有即时控件并已持久化（main_window.py:3939-4226），"
     "重复暴露会产生两个入口；保持单一交互入口。"),
    ("AI 的 provider / 模型 / API Key / Base URL",
     "AI 助手面板已有配置对话框（ai_panel.py:355-415，写入 ~/.mbdsdr/config.json）；"
     "设置里重复暴露会与密钥输入框形成双入口。本面板只补未暴露的采样参数。"),
    ("星象仪 FOV / 时间倍率",
     "星象仪底栏已有倍率与重置摄像机按钮，且它们是会话态视图操作，非长期偏好。"),
)


# ---------------------------------------------------------------------------
# 默认值 / 校验
# ---------------------------------------------------------------------------
def defaults(storage: Optional[str] = None) -> Dict[str, Any]:
    """按 storage 返回 {key: default}（settings.py 的 DEFAULTS 由此派生）。"""
    out: Dict[str, Any] = {}
    for it in ALL_ITEMS:
        if storage is not None and it.storage != storage:
            continue
        out[it.key] = it.default
    return out


def keys_by_storage(storage: str) -> List[str]:
    return [it.key for it in ALL_ITEMS if it.storage == storage]


def items_for_group(group: str) -> Tuple[SettingItem, ...]:
    """该分组全部暴露项（含 gui_config / ai_config 存储后端）。"""
    return tuple(it for it in EXPOSED_ITEMS if it.group == group)


def validate_value(item: SettingItem, value: Any) -> Tuple[Any, Optional[str]]:
    """把外部输入归一为合法值；返回 (归一值, 说明)。

    - 数值越界 → 夹到范围并说明（不崩）；
    - 未知枚举 → 回默认并说明；
    - 类型完全不可解析 → 回默认并说明；
    - 合法值 → 说明为 None。
    """
    reason: Optional[str] = None
    if item.kind == "bool":
        if isinstance(value, str):
            low = value.strip().lower()
            if low in ("1", "true", "yes", "on"):
                return True, None
            if low in ("0", "false", "no", "off"):
                return False, None
            return item.default, f"无法识别的布尔值 {value!r}，已回默认"
        return bool(value), None

    if item.kind in ("int", "float"):
        if value is None or (isinstance(value, str) and not value.strip()):
            return item.default, None
        try:
            num = float(value)
        except (TypeError, ValueError):
            return item.default, f"无法解析为数字 {value!r}，已回默认"
        if item.minimum is not None and num < item.minimum:
            num = item.minimum
            reason = f"低于下限 {item.minimum:g}，已夹到下限"
        if item.maximum is not None and num > item.maximum:
            num = item.maximum
            reason = f"高于上限 {item.maximum:g}，已夹到上限"
        return (int(round(num)) if item.kind == "int" else float(num)), reason

    if item.kind == "enum":
        if value in item.choice_values:
            return value, None
        # 容忍 "2048" 这类字符串数字（JSON/命令行常见）
        for v in item.choice_values:
            if isinstance(v, int) and str(value) == str(v):
                return v, None
        return item.default, f"不在可选值 {item.choice_values} 内，已回默认"

    # text：保持字符串，不接受 None
    if value is None:
        return item.default, None
    return str(value), None


def is_known(key: str) -> bool:
    return key in SCHEMA


# ---------------------------------------------------------------------------
# 导入 / 导出（纯函数，IO 由调用方负责）
# ---------------------------------------------------------------------------
@dataclass
class ImportResult:
    accepted: Dict[str, Any] = field(default_factory=dict)
    unknown: List[str] = field(default_factory=list)
    rejected: List[Tuple[str, str]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    #: 文件里缺失、按当前 schema 默认值补齐的键（同主版本迁移）。
    filled: List[str] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None

    def summary(self) -> str:
        """人话结果：导入 N 项 / 补齐 N 项 / 忽略 N 项 / 拒绝 N 项。"""
        parts = [f"已导入 {len(self.accepted)} 项",
                 f"已按默认补齐 {len(self.filled)} 项",
                 f"忽略未知 {len(self.unknown)} 项"]
        if self.rejected:
            parts.append(f"拒绝 {len(self.rejected)} 项")
        return "，".join(parts)


def build_export(settings_values: Dict[str, Any],
                 extra: Optional[Dict[str, Dict[str, Any]]] = None,
                 exported_at: str = "") -> Dict[str, Any]:
    """构造可读导出 JSON（含版本号；密钥类字段永不写入）。

    settings_values 只接受 settings 后端；extra 可带 ``{"gui_config": {...},
    "ai_config": {...}}``。
    """
    payload: Dict[str, Any] = {
        "app": "MBDSDR",
        "schema_version": CONFIG_SCHEMA_VERSION,
        "exported_at": exported_at,
        "settings": {},
        "gui_config": {},
        "ai_config": {},
    }
    for key, value in settings_values.items():
        item = SCHEMA.get(key)
        if item is None or item.storage != "settings" or item.secret:
            continue
        payload["settings"][key] = value
    if extra:
        for section in ("gui_config", "ai_config"):
            for key, value in (extra.get(section) or {}).items():
                item = SCHEMA.get(key)
                if item is None or item.storage != section or item.secret:
                    continue
                payload[section][key] = value
    return payload


def _parse_version(raw: Any) -> Tuple[int, int]:
    """把 1 / "1" / "1.0" / "1.2" 解析成 (主版本, 次版本)；非法抛 ValueError。"""
    if isinstance(raw, bool):
        raise ValueError("布尔值不是版本号")
    if isinstance(raw, int):
        major, minor = raw, 0
    elif isinstance(raw, float) and raw.is_integer():
        major, minor = int(raw), 0
    else:
        text = str(raw).strip()
        if not text:
            raise ValueError("空版本号")
        parts = text.split(".")
        major = int(parts[0])
        minor = int(parts[1]) if len(parts) > 1 and parts[1] else 0
    if major < 0 or minor < 0:
        raise ValueError("版本号不能为负")
    return major, minor


def parse_import(payload: Any) -> ImportResult:
    """解析导入 JSON：同主版本迁移，跨主版本拒绝；未知键**列出来**，不静默丢弃。

    迁移口径（用户拿旧导出文件回来不该被拒之门外）：
      * 同主版本、字段子集变化 → 缺的字段取当前 schema 默认（记入 ``filled``），
        多的字段列进 ``unknown`` 并忽略；
      * 跨主版本 → 拒绝，并说明原因与下一步该怎么办。
    """
    res = ImportResult()
    if not isinstance(payload, dict):
        res.error = "文件顶层不是 JSON 对象"
        return res
    if "schema_version" not in payload:
        res.error = "缺少 schema_version，无法确认版本兼容性"
        return res
    try:
        major, minor = _parse_version(payload["schema_version"])
    except (TypeError, ValueError):
        res.error = f"schema_version 不是合法版本号：{payload['schema_version']!r}"
        return res
    if major != CONFIG_SCHEMA_MAJOR:
        res.error = (
            f"配置版本 {major}.{minor} 的主版本与当前 {CONFIG_SCHEMA_MAJOR} 不同，"
            "已拒绝导入：跨主版本时字段语义/取值范围可能已重定义，"
            "按旧语义写入会把设置改坏。"
            f"怎么办：① 用当前版本重新导出一次作为新基线；"
            f"② 或手工把旧文件里想保留的少数项抄进新导出；"
            f"③ 或先用主版本 {major} 对应的程序版本导入。")
        return res
    if (major, minor) < (CONFIG_SCHEMA_MAJOR, CONFIG_SCHEMA_MINOR):
        res.warnings.append(
            f"导入文件版本 {major}.{minor} 较早（当前 "
            f"{CONFIG_SCHEMA_MAJOR}.{CONFIG_SCHEMA_MINOR}），按当前 schema 迁移")
    elif (major, minor) > (CONFIG_SCHEMA_MAJOR, CONFIG_SCHEMA_MINOR):
        res.warnings.append(
            f"导入文件版本 {major}.{minor} 较新（当前 "
            f"{CONFIG_SCHEMA_MAJOR}.{CONFIG_SCHEMA_MINOR}），"
            "不认识的字段会列在“忽略”里")

    seen: set = set()
    for section in ("settings", "gui_config", "ai_config"):
        seg = payload.get(section, {})
        if seg is None:
            continue
        if not isinstance(seg, dict):
            res.error = f"{section} 段不是对象"
            return res
        for key, value in seg.items():
            seen.add(key)
            item = SCHEMA.get(key)
            if item is None:
                res.unknown.append(f"{section}.{key}")
                continue
            if item.storage != section:
                res.rejected.append(
                    (key, f"属于 {item.storage} 存储，不应出现在 {section} 段"))
                continue
            norm, reason = validate_value(item, value)
            res.accepted[key] = norm
            if reason:
                res.warnings.append(f"{key}: {reason}")

    # 同主版本迁移：文件里没有的键按当前默认值补齐（密钥类不参与导入导出）。
    for item in ALL_ITEMS:
        if item.secret or item.key in seen:
            continue
        res.accepted[item.key] = item.default
        res.filled.append(item.key)
    if res.filled:
        shown = "、".join(res.filled[:12])
        more = f" 等 {len(res.filled)} 项" if len(res.filled) > 12 else ""
        res.warnings.append(f"已按默认补齐 {len(res.filled)} 项：{shown}{more}")
    return res
