import 'package:flutter/material.dart';

// ============================================================================
// MBDSDR 移动端 —— 唯一的设计 token 来源
// ----------------------------------------------------------------------------
// 对齐 cpp/src/core/tokens.h 与 Apple/小米车机视觉气质：高端、克制、耐读。
// 硬性规则：
//   * 业务代码只允许引用本文件的常量/样式，禁止裸写 0xFFxxxxxx、禁止裸写像素；
//   * 弹性布局优先（Flexible/Expanded/LayoutBuilder/MediaQuery），不写死尺寸；
//   * 不堆颜色、不堆动效、不使用文化符号；
//   * 主题名称为「默认」，不使用其他主题命名，也不出现赛事类文案；
//   * 不内置 FM 电台频率、不内置地理位置、不内置任何密钥。
// ============================================================================
abstract final class AppTokens {
  // ---------------------------------------------------------------- 背景
  static const Color bgMain = Color(0xFF080A0C);
  static const Color bgBar = Color(0xFF000000);
  static const Color spectrumBg = Color(0xFF0A0C0E);

  /// 卡片 = 白色叠加层（4.5% / 7.5% / 10%），不是实心面板。
  static const Color card1 = Color(0x0CFFFFFF);
  static const Color card2 = Color(0x13FFFFFF);
  static const Color card3 = Color(0x1AFFFFFF);
  static const Color cardEdge = Color(0x0FFFFFFF);

  // ---------------------------------------------------------------- 文字
  /// 暖调近白，不用死灰。
  static const Color textPrimary = Color(0xFFECEAE6);
  static const Color textSecondary = Color(0xFFB9B6B1);

  static Color textAt(double opacity) => Colors.white.withValues(alpha: opacity);
  static const double textAlphaTertiary = 0.50;
  static const double textAlphaFaint = 0.23;

  // ---------------------------------------------------------------- 强调
  static const Color accent = Color(0xFF7CC4FF);
  static const Color accentHover = Color(0xFF9FD4FF);
  static const Color accentPress = Color(0xFF5AA8F0);
  static const Color success = Color(0xFF5FD08A);
  static const Color warning = Color(0xFFE0B35A);
  static const Color danger = Color(0xFFE74C3C);

  // ---------------------------------------------------------------- 圆角
  static const double radiusPanel = 24;
  static const double radiusCard = 10;
  static const double radiusSmall = 4;

  /// 胶囊（高的一半）：FilledButton / Chip / 步进按钮群全圆角。
  /// 手机触控控件用胶囊，不再用直角或半圆混用。
  static const double radiusPill = 999;

  // ---------------------------------------------------------------- 间距节奏（4pt 栅格）
  static const double spacingS = 4;
  static const double spacingM = 8;
  static const double spacingL = 16;

  /// 页面分块/卡片内大 padding（车机 64px 边距折到手机 ≈16–24）。
  static const double spacingXL = 24;

  /// 页面主边距/大分块之间。
  static const double spacingXXL = 32;

  // ---------------------------------------------------------------- 字重（语义化，禁裸写 FontWeight）
  /// 正文常规。
  static const FontWeight weightRegular = FontWeight.w400;

  /// 主力字重：标题/hero 读数/选中态文字（Figma 实测 500 占绝大多数）。
  static const FontWeight weightMedium = FontWeight.w500;

  /// 仅 app 标题等极少数位置，不通篇 600。
  static const FontWeight weightSemi = FontWeight.w600;

  // ---------------------------------------------------------------- 分隔与状态层
  /// 克制分隔线：1px 半透明白（≈6%）。面板之间优先留白+底色差，必须画线时用它。
  static const Color divider = Color(0x0FFFFFFF);

  /// 选中态填充：低饱和蓝灰（Figma #919cac @ 14%），
  /// 用于 SegmentedButton/Chip/列表选中块，不用亮 accent 铺满。
  static const Color selectedFill = Color(0x24919CAC);

  /// 选中态文字：近白，落在 [selectedFill] 上。
  static const Color selectedText = Color(0xFFECEAE6);

  /// 焦点环：accent 45%。键盘可达控件（Tab/输入）聚焦时的可见环。
  static const Color focusRing = Color(0x737CC4FF);

  /// 禁用控件底：白叠层 4%（现仅有文字禁用 alpha，补控件底）。
  static const Color disabledFill = Color(0x0AFFFFFF);

  // ---------------------------------------------------------------- 尺寸
  static const double touchMin = 44;
  static const double topBarH = 56;

  /// 空态/未配置插画图标尺寸（chat 未配置、EmptyState 共用）。
  static const double iconSizeEmpty = 48;

  /// 行内小图标（错误提示、列表 leading 等 14px 档）。
  static const double iconSizeInline = 14;

  /// 行内中图标（18px 档）。
  static const double iconSizeInlineLg = 18;

  /// 空态卡片最大宽度（居中约束）。
  static const double emptyStateMaxWidth = 360;

  // ---------------------------------------------------------------- 动效时长（ms）
  static const Duration animShort = Duration(milliseconds: 160);
  static const Duration animMedium = Duration(milliseconds: 220);
  static const Duration animLong = Duration(milliseconds: 350);

  /// 缓动：统一 OutCubic（先快后慢），反馈与转场都用它，不混用花哨曲线。
  static const Curve easingStandard = Curves.easeOutCubic;

  /// 退场缓动：InCubic，退场比入场快一半。
  static const Curve easingExit = Curves.easeInCubic;

  // ---------------------------------------------------------------- 瀑布色板（与 cpp kWaterfallStops 一致）
  static const List<Color> waterfallStops = <Color>[
    Color(0xFF000000),
    Color(0xFF00005A),
    Color(0xFF0028C8),
    Color(0xFF00C8EB),
    Color(0xFF28DC5A),
    Color(0xFFFFEB3C),
    Color(0xFFFF7800),
    Color(0xFFFF281E),
  ];

  // ---------------------------------------------------------------- 频谱 dB 显示范围
  static const double dbLowerDefault = -100;
  static const double dbUpperDefault = 0;
  static const double dbGridStep = 20;

  // ---------------------------------------------------------------- RTL-SDR 硬件范围
  static const double freqMinHz = 24e6;
  static const double freqMaxHz = 1700e6;
  static const double freqStepHz = 100e3;
  static const double gainMinDb = 0;
  static const double gainMaxDb = 49.6;
  static const double gainStepDb = 0.6;
  static const List<double> sampleRatesHz = <double>[
    1.024e6,
    2.048e6,
    2.4e6,
    3.2e6,
  ];

  // ---------------------------------------------------------------- 静噪门限（dBFS，相对解调后音频 RMS）
  // 与桌面端 cpp/src/dsp/squelch.h 对齐：门限越接近 0 越严（只有强信号才开门）。
  /// 门限滑杆下限（dBFS）。
  static const double squelchThresholdMinDb = -100;

  /// 门限滑杆上限（dBFS）。
  static const double squelchThresholdMaxDb = -20;

  /// 安静信道上解调后音频的噪声底估计（dBFS）。默认门限设在它之上。
  static const double squelchNoiseFloorDb = -60;

  /// 默认门限高于噪声底的裕量（dB）：默认门限 = 噪声底 + 裕量。
  static const double squelchDefaultAboveNoiseDb = 10;

  /// 默认门限（dBFS）= [squelchNoiseFloorDb] + [squelchDefaultAboveNoiseDb]。
  static double get squelchDefaultThresholdDb =>
      squelchNoiseFloorDb + squelchDefaultAboveNoiseDb;

  // ---------------------------------------------------------------- 字体
  static const List<String> monoFallback = <String>[
    'JetBrains Mono',
    'Menlo',
    'Consolas',
    'Roboto Mono',
    'monospace',
  ];
  static const List<String> sansFallback = <String>[
    'MiSans',
    'PingFang SC',
    'Microsoft YaHei',
    'Roboto',
    'sans-serif',
  ];

  // ---------------------------------------------------------------- 文本样式
  static const TextStyle appTitle = TextStyle(
    fontSize: 17,
    fontWeight: weightSemi,
    color: textPrimary,
    height: 1.25,
  );

  static const TextStyle sectionTitle = TextStyle(
    fontSize: 14,
    fontWeight: weightMedium,
    color: textPrimary,
    height: 1.3,
  );

  static const TextStyle body = TextStyle(
    fontSize: 13.5,
    fontWeight: weightRegular,
    color: textPrimary,
    height: 1.45,
  );

  static const TextStyle auxiliary = TextStyle(
    fontSize: 12,
    fontWeight: weightRegular,
    color: textSecondary,
    height: 1.4,
  );

  static const TextStyle mono = TextStyle(
    fontSize: 12.5,
    fontWeight: weightMedium,
    color: textSecondary,
    fontFamilyFallback: monoFallback,
    height: 1.35,
  );

  /// 仪器微标注字号：dB gutter / 频率刻度 / 测量读数盒 / NF 标注等画布内小字。
  /// 画布 CustomPainter 内统一引用此常量，避免散落裸 fontSize。
  static const double annotationFontSize = 10.0;

  /// 频谱页大频率读数（中央 MHz 显示）。Figma hero 大数字用 500，不通篇粗黑。
  static const TextStyle freqReadout = TextStyle(
    fontSize: 26,
    fontWeight: weightMedium,
    color: textPrimary,
    fontFamilyFallback: monoFallback,
    height: 1.1,
  );

  /// 白色叠加卡片装饰（car-HMI surface）。
  static BoxDecoration cardDecoration({
    double radius = radiusCard,
    Color color = card1,
    bool edge = true,
  }) {
    return BoxDecoration(
      color: color,
      borderRadius: BorderRadius.circular(radius),
      border: edge ? Border.all(color: cardEdge) : null,
    );
  }

  /// 把 dBFS 值（[-100,0]）映射到瀑布色板颜色。
  static Color waterfallColorFor(double dbfs) {
    final t = ((dbfs - dbLowerDefault) / (dbUpperDefault - dbLowerDefault))
        .clamp(0.0, 1.0);
    final scaled = t * (waterfallStops.length - 1);
    final i = scaled.floor();
    if (i >= waterfallStops.length - 1) return waterfallStops.last;
    final f = scaled - i;
    return Color.lerp(waterfallStops[i], waterfallStops[i + 1], f)!;
  }
}
