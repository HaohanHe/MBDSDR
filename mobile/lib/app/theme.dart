import 'package:flutter/material.dart';

import 'tokens.dart';

// ============================================================================
// 应用主题（唯一主题：深色「默认」）
// ----------------------------------------------------------------------------
// 基底：ThemeData.dark + useMaterial3；全部颜色只取 AppTokens，
// AppBar 透明无阴影，卡片/控件圆角 radiusCard，涟漪克制。
// ============================================================================

abstract final class AppTheme {
  /// 对外主题名（设置页外观分组只有这一项）。
  static const String name = '默认';

  static ThemeData get data {
    final base = ThemeData.dark(useMaterial3: true);
    const scheme = ColorScheme.dark(
      primary: AppTokens.accent,
      onPrimary: AppTokens.bgMain,
      secondary: AppTokens.accentHover,
      surface: AppTokens.bgMain,
      onSurface: AppTokens.textPrimary,
      onSecondary: AppTokens.bgMain,
      error: AppTokens.danger,
      onError: AppTokens.bgMain,
    );

    return base.copyWith(
      scaffoldBackgroundColor: AppTokens.bgMain,
      colorScheme: scheme,
      appBarTheme: const AppBarTheme(
        backgroundColor: Colors.transparent,
        surfaceTintColor: Colors.transparent,
        elevation: 0,
        scrolledUnderElevation: 0,
        centerTitle: false,
        titleTextStyle: AppTokens.appTitle,
      ),
      dividerTheme: const DividerThemeData(
        color: AppTokens.cardEdge,
        thickness: 1,
        space: 1,
      ),
      cardTheme: CardThemeData(
        color: AppTokens.card1,
        surfaceTintColor: Colors.transparent,
        elevation: 0,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(AppTokens.radiusCard),
          side: const BorderSide(color: AppTokens.cardEdge),
        ),
      ),
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(AppTokens.radiusCard),
          ),
        ),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(AppTokens.radiusCard),
          ),
        ),
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: AppTokens.card1,
        hintStyle: AppTokens.auxiliary,
        labelStyle: AppTokens.auxiliary,
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(AppTokens.radiusCard),
          borderSide: const BorderSide(color: AppTokens.cardEdge),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(AppTokens.radiusCard),
          borderSide: const BorderSide(color: AppTokens.cardEdge),
        ),
      ),
      textTheme: base.textTheme.apply(
        bodyColor: AppTokens.textPrimary,
        displayColor: AppTokens.textPrimary,
      ),
      // 涟漪克制：低透明度淡入，不做高亮强闪。
      splashColor: AppTokens.accent.withValues(alpha: 0.08),
      highlightColor: AppTokens.accent.withValues(alpha: 0.04),
      // 焦点环：键盘/可达控件聚焦时的可见反馈（低饱和 accent 环）。
      focusColor: AppTokens.focusRing,
      hoverColor: AppTokens.card2.withValues(alpha: 0.5),
    );
  }
}
