import 'package:flutter/material.dart';

// MBDSDR 日式低饱和主题（与桌面端 themes.py "日式浅色" 一致）
class AppTheme {
  static const Color bg = Color(0xFFF5F3EF);        // 米白暖灰
  static const Color bgAlt = Color(0xFFEDEBE6);     // 稍深
  static const Color card = Color(0xFFFFFFFF);       // 纯白卡片
  static const Color text = Color(0xFF2C2C2C);       // 深灰文字
  static const Color textSecondary = Color(0xFF6B6B6B);
  static const Color border = Color(0xFFE0DEDA);     // 浅灰边框
  static const Color primary = Color(0xFF5B7B8C);    // 低饱和蓝灰
  static const Color accent = Color(0xFFC4845C);     // 低饱和橙
  static const Color accentHover = Color(0xFFB0754E);
  static const Color danger = Color(0xFFB85C5C);     // 低饱和红
  static const Color success = Color(0xFF6B8E6B);    // 低饱和绿

  static ThemeData get light {
    final base = ThemeData(
      useMaterial3: true,
      scaffoldBackgroundColor: bg,
      colorScheme: ColorScheme.fromSeed(
        seedColor: primary,
        primary: primary,
        secondary: accent,
        surface: card,
        onSurface: text,
      ),
      fontFamily: 'MiSans',
    );
    return base.copyWith(
      appBarTheme: const AppBarTheme(
        backgroundColor: bg,
        foregroundColor: text,
        elevation: 0,
        centerTitle: false,
      ),
      cardTheme: CardThemeData(
        color: card,
        elevation: 0,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(12),
          side: const BorderSide(color: border),
        ),
      ),
      elevatedButtonTheme: ElevatedButtonThemeData(
        style: ElevatedButton.styleFrom(
          backgroundColor: primary,
          foregroundColor: Colors.white,
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(8),
          ),
        ),
      ),
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          backgroundColor: accent,
          foregroundColor: Colors.white,
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(8),
          ),
        ),
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: card,
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(8),
          borderSide: const BorderSide(color: border),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(8),
          borderSide: const BorderSide(color: border),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(8),
          borderSide: const BorderSide(color: primary, width: 1.5),
        ),
      ),
      bottomNavigationBarTheme: const BottomNavigationBarThemeData(
        backgroundColor: card,
        selectedItemColor: accent,
        unselectedItemColor: textSecondary,
        type: BottomNavigationBarType.fixed,
        elevation: 0,
        showUnselectedLabels: true,
      ),
      dividerTheme: const DividerThemeData(color: border, thickness: 0.5),
    );
  }
}
