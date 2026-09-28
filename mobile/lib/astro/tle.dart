/// TLE (Two-Line Element) 不可变模型与解析。
///
/// 列位置（0 基下标）严格对齐 Vallado / Space-Track 官方三行格式，
/// 与 python-sgp4 `sgp4/io.py` 的切片位置保持一致：
///   line1:  [2:7] 编号  [7] 分类  [9:17] 国际代号
///           [18:20] 两位年  [20:32] 年积日+小数
///           [33:43] 一阶导数  [44:50]+exp 二阶导数  [53:59]+exp BSTAR
///   line2:  [8:16] 倾角  [17:25] RAAN  [26:33] 偏心率
///           [34:42] 近地点幅角  [43:51] 平近点角  [52:63] 平均运动(rev/day)
///
/// 参考: Vallado, Crawford, Hujsak, Kelso (2006) "Revisiting Spacetrack Report #3".
library;

/// 解析 TLE 时格式错误抛出的异常。
class TleFormatException implements Exception {
  TleFormatException(this.message, [this.line]);

  final String message;
  final String? line;

  @override
  String toString() =>
      line == null ? 'TleFormatException: $message' : 'TleFormatException: $message\n  >> $line';
}

/// 不可变 TLE。所有角度字段以「度」存储（显示用），SGP4 传播所需的弧度/
/// 归一化数值由 [Sgp4] 在初始化时从这些字段换算，避免双份真值漂移。
class Tle {
  Tle._({
    required this.name,
    required this.line1,
    required this.line2,
    required this.catalogNumber,
    required this.epoch,
    required this.inclinationDeg,
    required this.raanDeg,
    required this.eccentricity,
    required this.argPerigeeDeg,
    required this.meanAnomalyDeg,
    required this.meanMotionRevDay,
    required this.bstar,
    required this.ndotRevDay2,
    required this.nddot,
  });

  final String name;
  final String line1;
  final String line2;

  /// NORAD 编目号（去掉前导零的整数）。
  final int catalogNumber;

  /// 历元时刻（UTC，由两位年 + 年积日换算）。
  final DateTime epoch;

  final double inclinationDeg;
  final double raanDeg;
  final double eccentricity;
  final double argPerigeeDeg;
  final double meanAnomalyDeg;

  /// 平均运动（圈/天，Kozai 意义）。
  final double meanMotionRevDay;

  /// 阻力系数 BSTAR（1/地球半径 单位）。
  final double bstar;

  /// 平均运动一阶导数（圈/天²）。
  final double ndotRevDay2;

  /// 平均运动二阶导数（已乘 10^exp，圈/天³）。
  final double nddot;

  // ------------------------------------------------------------------
  // 校验和
  // ------------------------------------------------------------------

  /// 计算一行（前 68 位）的校验和：数字按其值、负号按 1 求和后取模 10。
  static int computeChecksum(String line) {
    final span = line.length >= 68 ? line.substring(0, 68) : line;
    var sum = 0;
    for (final ch in span.codeUnits) {
      if (ch >= 48 && ch <= 57) {
        sum += ch - 48;
      } else if (ch == 45) {
        // '-'
        sum += 1;
      }
    }
    return sum % 10;
  }

  /// 该行末尾（第 69 位，下标 68）是否为合法校验和。
  static bool validateChecksum(String line) {
    if (line.length < 69) return false;
    final ch = line.codeUnitAt(68);
    if (ch < 48 || ch > 57) return false;
    return (ch - 48) == computeChecksum(line);
  }

  // ------------------------------------------------------------------
  // 单行解析
  // ------------------------------------------------------------------

  /// 由「名称行 + line1 + line2」三行构造。名称可省略。
  factory Tle.parseThreeLine(String name, String l1, String l2) {
    final line1 = l1.trimRight();
    final line2 = l2.trimRight();
    _checkLine1(line1);
    _checkLine2(line2);

    final catalog = int.parse(line1.substring(2, 7).trim());
    final twoDigitYear = int.parse(line1.substring(18, 20));
    final epochDay = double.parse(line1.substring(20, 32));
    final ndot = double.parse(line1.substring(33, 43));
    final nddotMantissa = double.parse(
      '${line1.substring(44, 45)}.${line1.substring(45, 50)}',
    );
    final nddotExp = int.parse(line1.substring(50, 52).trim());
    final bstarMantissa = double.parse(
      '${line1.substring(53, 54)}.${line1.substring(54, 59)}',
    );
    final bstarExp = int.parse(line1.substring(59, 61).trim());

    final inclinationDeg = double.parse(line2.substring(8, 16));
    final raanDeg = double.parse(line2.substring(17, 25));
    final eccRaw = line2.substring(26, 33).replaceAll(' ', '0');
    final eccentricity = double.parse('0.$eccRaw');
    final argPerigeeDeg = double.parse(line2.substring(34, 42));
    final meanAnomalyDeg = double.parse(line2.substring(43, 51));
    final meanMotionRevDay = double.parse(line2.substring(52, 63));

    if (line1.substring(2, 7) != line2.substring(2, 7)) {
      throw TleFormatException('两行的编目号不一致', line1);
    }

    final year = twoDigitYear < 57 ? twoDigitYear + 2000 : twoDigitYear + 1900;
    final epoch = _dayOfYearToDateTime(year, epochDay);

    return Tle._(
      name: name.trim().isEmpty ? 'NORAD $catalog' : name.trim(),
      line1: line1,
      line2: line2,
      catalogNumber: catalog,
      epoch: epoch,
      inclinationDeg: inclinationDeg,
      raanDeg: raanDeg,
      eccentricity: eccentricity,
      argPerigeeDeg: argPerigeeDeg,
      meanAnomalyDeg: meanAnomalyDeg,
      meanMotionRevDay: meanMotionRevDay,
      bstar: bstarMantissa * pow10(bstarExp),
      ndotRevDay2: ndot,
      nddot: nddotMantissa * pow10(nddotExp),
    );
  }

  /// 仅由两行构造（名称取编目号）。
  factory Tle.fromLines(String l1, String l2) => Tle.parseThreeLine('', l1, l2);

  /// 解析 Celestrak 风格的多组三行文本：跳过注释(#)与空行，
  /// 遇到非数字开头的行视为名称行，其后紧跟 line1 / line2。
  static List<Tle> parseBatch(String text) {
    final lines = text.split('\n').map((l) => l.trimRight()).toList();
    final result = <Tle>[];
    var i = 0;
    while (i < lines.length) {
      final line = lines[i];
      if (line.trim().isEmpty) {
        i++;
        continue;
      }
      final first = line.trimLeft();
      if (first.startsWith('#')) {
        i++;
        continue;
      }
      if (first.startsWith('1 ')) {
        // 无名称行：紧跟 line2。
        if (i + 1 >= lines.length || !lines[i + 1].trimLeft().startsWith('2 ')) {
          throw TleFormatException('line1 后缺少 line2', line);
        }
        result.add(Tle.fromLines(line, lines[i + 1]));
        i += 2;
      } else if (first.startsWith('2 ')) {
        throw TleFormatException('孤立的 line2（缺少 line1）', line);
      } else {
        // 名称行。
        final name = line.trim();
        if (i + 2 >= lines.length) {
          throw TleFormatException('名称行后缺少完整两行', line);
        }
        final l1 = lines[i + 1];
        final l2 = lines[i + 2];
        if (!l1.trimLeft().startsWith('1 ') || !l2.trimLeft().startsWith('2 ')) {
          throw TleFormatException('名称行后应为 line1 + line2', line);
        }
        result.add(Tle.parseThreeLine(name, l1, l2));
        i += 3;
      }
    }
    return result;
  }

  // ------------------------------------------------------------------
  // 内部校验
  // ------------------------------------------------------------------

  static void _checkLine1(String line) {
    if (line.length < 64 ||
        !line.startsWith('1 ') ||
        line.length <= 23 ||
        line[8] != ' ' ||
        line[23] != '.' ||
        line.length <= 34 ||
        line[32] != ' ' ||
        line[34] != '.' ||
        line.length <= 63 ||
        line[43] != ' ' ||
        line[52] != ' ' ||
        line[61] != ' ' ||
        line[63] != ' ') {
      throw TleFormatException('line1 不符合 TLE 列格式', line);
    }
  }

  static void _checkLine2(String line) {
    if (line.length < 68 ||
        !line.startsWith('2 ') ||
        line[7] != ' ' ||
        line[11] != '.' ||
        line[16] != ' ' ||
        line[20] != '.' ||
        line[25] != ' ' ||
        line[33] != ' ' ||
        line[37] != '.' ||
        line[42] != ' ' ||
        line[46] != '.' ||
        line[51] != ' ') {
      throw TleFormatException('line2 不符合 TLE 列格式', line);
    }
  }

  /// 年积日（含小数天数）-> UTC DateTime。
  static DateTime _dayOfYearToDateTime(int year, double dayOfYear) {
    final start = DateTime.utc(year, 1, 1);
    final daysWhole = dayOfYear.floor() - 1; // 第 1 天就是 1 月 1 日
    final fractional = dayOfYear - dayOfYear.floor();
    final totalMicros = (daysWhole * 86400 + fractional * 86400) * 1000000;
    return start.add(Duration(microseconds: totalMicros.round()));
  }

  static double pow10(int exp) {
    var result = 1.0;
    if (exp >= 0) {
      for (var i = 0; i < exp; i++) {
        result *= 10.0;
      }
    } else {
      for (var i = 0; i < -exp; i++) {
        result /= 10.0;
      }
    }
    return result;
  }
}
