"""
MBDSDR APRS 报文解析器
======================
严格对照 direwolf 真实源码实现，把 AX.25 UI 帧的信息字段解析为结构化 dict。

来源对照（所有常量/公式均标注 file:line）：
- 数据类型标识符(DTI)表:   direwolf decode_aprs.c:327-465
- 未压缩位置 DDMM.hhN:     direwolf decode_aprs.c:3585-3700 (get_latitude_8 / get_longitude_9)
- 压缩位置 base-91:        direwolf decode_aprs.c:3518-3582 (decode_compressed_position)
- MIC-E 位置:              direwolf decode_aprs.c:1403-1656 (aprs_mic_e)
- 气象报告:                direwolf decode_aprs.c:2956-3200 (aprs_positionless_weather_report / weather_data)
- 消息:                    APRS 规范 ":ADDRESSEE(9):text{id}"

作者：MBDSDR Team (BI4MIB)
许可证：GPL-3.0
"""

from typing import Any, Dict, Optional
from mbdsdr_ai.ax25 import AX25Frame


# ------------------------------------------------------------
# 数据类型标识符 (Data Type Identifier)
# 来源: direwolf decode_aprs.c:327-465
# ------------------------------------------------------------
_DTI_TYPE = {
    '!': 'position',          # decode_aprs.c:338  位置(无时间戳,无消息)
    '=': 'position',         # decode_aprs.c:341  位置(无时间戳,有消息)
    '/': 'position',          # decode_aprs.c:386  位置(有时间戳,无消息)
    '@': 'position',          # decode_aprs.c:387  位置(有时间戳,有消息)
    "'": 'mic_e',             # decode_aprs.c:373  旧 Mic-E
    '`': 'mic_e',             # decode_aprs.c:374  当前 Mic-E
    ':': 'message',           # decode_aprs.c:394  消息/bulletin
    '_': 'weather',           # decode_aprs.c:459  无位置气象报告
    ';': 'object',            # decode_aprs.c:428
    ')': 'item',              # decode_aprs.c:380
    '>': 'status',            # decode_aprs.c:440
    'T': 'telemetry',         # decode_aprs.c:453
    '?': 'query',             # decode_aprs.c:447
    '{': 'userdef',           # decode_aprs.c:465
    '}': 'third_party',       # decode_aprs.c third-party
}


def _safe_float(s: str) -> Optional[float]:
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _decode_uncompressed_position(body: str, off: int = 0) -> Dict[str, Any]:
    """解析未压缩位置，body 为去掉 DTI（与可选时间戳）后的串。

    格式: DDMM.hhN [symtable] DDDMM.hhW [symcode] [course/speed] [/A=alt] [comment]
    来源: direwolf decode_aprs.c:3585-3700 (get_latitude_8/get_longitude_9)。
    纬度固定 8 字符 "DDMM.hhN"；符号表 1 字符；经度固定 9 字符 "DDDMM.hhW"；符号代码 1 字符。
    """
    out: Dict[str, Any] = {}
    p = off
    try:
        lat_field = body[p:p + 8]
        if len(lat_field) < 8:
            return out
        lat_deg = int(lat_field[0:2])
        lat_min = _safe_float(lat_field[2:7])
        if lat_min is None:
            return out
        lat = lat_deg + lat_min / 60.0
        if lat_field[7] == 'S':
            lat = -lat
        out['latitude'] = round(lat, 6)

        out['symbol_table'] = body[p + 8]
        lon_field = body[p + 9:p + 18]
        if len(lon_field) < 9:
            return out
        lon_deg = int(lon_field[0:3])
        lon_min = _safe_float(lon_field[3:8])
        if lon_min is None:
            return out
        lon = lon_deg + lon_min / 60.0
        if lon_field[8] == 'W':
            lon = -lon
        out['longitude'] = round(lon, 6)
        out['symbol_code'] = body[p + 18]

        # 尾部（符号代码之后原样返回）。普通位置的 course/speed 数据扩展由调用方
        # （非气象分支）解析；气象符号 '_' 时整个尾部交给 weather_data 自行识别 DDD/SSS。
        rest = body[p + 19:]

        # 高度 /A=ddddd (英尺)
        ai = rest.find('/A=')
        if ai >= 0:
            alt = _safe_float(rest[ai + 3:ai + 9])
            if alt is not None:
                out['altitude'] = alt
            rest = rest[:ai]

        out['comment'] = rest
    except (IndexError, ValueError):
        pass
    return out


def _decode_compressed_position(body: str, off: int = 0) -> Dict[str, Any]:
    """解析压缩位置。

    格式: sym_table yyyy xxxx sym_code c s ...（13 字节）
    来源: direwolf decode_aprs.c:3518-3582
      lat = 90 - ((y0-33)*91^3 + (y1-33)*91^2 + (y2-33)*91 + (y3-33)) / 380926.0   (line 3522)
      lon = -180 + ((x0-33)*91^3 + (x1-33)*91^2 + (x2-33)*91 + (x3-33)) / 190463.0  (line 3535)
      altitude = 1.002^((c-33)*91 + (s-33))   (line 3569)
    """
    out: Dict[str, Any] = {}
    try:
        y = body[off + 1:off + 5]
        x = body[off + 5:off + 9]
        if len(y) < 4 or len(x) < 4:
            return out
        lat = 90 - ((ord(y[0]) - 33) * 91 ** 3 + (ord(y[1]) - 33) * 91 ** 2 +
                    (ord(y[2]) - 33) * 91 + (ord(y[3]) - 33)) / 380926.0
        lon = -180 + ((ord(x[0]) - 33) * 91 ** 3 + (ord(x[1]) - 33) * 91 ** 2 +
                      (ord(x[2]) - 33) * 91 + (ord(x[3]) - 33)) / 190463.0
        out['latitude'] = round(lat, 6)
        out['longitude'] = round(lon, 6)
        out['symbol_table'] = body[off]
        out['symbol_code'] = body[off + 9]

        c = body[off + 10]
        s = body[off + 11]
        # 高度: (t-33)&0x18 == 0x10，来源 decode_aprs.c:3568
        if (ord(s) - 33) & 0x18 == 0x10:
            out['altitude'] = round(1.002 ** ((ord(c) - 33) * 91 + (ord(s) - 33)), 1)
        elif '!' <= c <= 'z':
            # 课程/速度: course=(c-33)*4; speed_knots=1.08^(s-33)-1  (line 3578-3579)
            out['course'] = (ord(c) - 33) * 4
            out['speed'] = round(1.08 ** (ord(s) - 33) - 1.0, 1)
        out['comment'] = body[off + 12:].strip()
    except (IndexError, ValueError):
        pass
    return out


def _mic_e_digit(ch: str) -> int:
    """MIC-E 目的地址字符 -> 数字。来源: direwolf decode_aprs.c:1359-1400。"""
    if '0' <= ch <= '9':
        return ord(ch) - ord('0')
    if 'A' <= ch <= 'J':
        return ord(ch) - ord('A')
    if 'P' <= ch <= 'Y':
        return ord(ch) - ord('P')
    if ch in ('K', 'L', 'Z'):
        return 0
    return 0


def _decode_mic_e(frame: AX25Frame, info: str) -> Dict[str, Any]:
    """解析 MIC-E 位置（位置编进目的地址 + 信息字段）。

    来源: direwolf decode_aprs.c:1403-1656 (aprs_mic_e)。
    """
    out: Dict[str, Any] = {}
    dest = (frame.destination or '').upper().ljust(6)
    try:
        d = [_mic_e_digit(dest[i]) for i in range(6)]
        # 纬度: dd + mm.mm/100，line 1440-1445
        lat = d[0] * 10 + d[1] + (d[2] * 1000 + d[3] * 100 + d[4] * 10 + d[5]) / 6000.0
        # N/S: 第4字符为数字或'L' -> 南(line 1450)；P..Z -> 北(line 1454)
        if dest[3].isdigit() or dest[3] == 'L':
            lat = -lat
        out['latitude'] = round(lat, 6)

        # 经度偏移: 第5字符数字/L -> 0；P..Z -> 1，line 1470-1477
        offset = 0 if (dest[4].isdigit() or dest[4] == 'L') else 1

        # info[0]=DTI, info[1:4]=经度(度/分/百分分)，line 1493-1576
        c0 = ord(info[1]); c1 = ord(info[2]); c2 = ord(info[3])
        if offset and 118 <= c0 <= 127:
            lon = c0 - 118
        elif not offset and 38 <= c0 <= 127:
            lon = (c0 - 38) + 10
        elif offset and 108 <= c0 <= 117:
            lon = (c0 - 108) + 100
        elif offset and 38 <= c0 <= 107:
            lon = (c0 - 38) + 110
        else:
            lon = None
        if lon is not None:
            if 88 <= c1 <= 97:
                lon += (c1 - 88) / 60.0
            elif 38 <= c1 <= 87:
                lon += ((c1 - 38) + 10) / 60.0
            if 28 <= c2 <= 127:
                lon += (c2 - 28) / 6000.0
            # E/W: 第6字符数字/L -> 东；P..Z -> 西(负)，line 1590-1598
            if dest[5] >= 'P' and dest[5] <= 'Z':
                lon = -lon
            out['longitude'] = round(lon, 6)

        # 符号: info[7]=symbol_code, info[8]=sym_table_id（MIC-E 顺序相反），line 1610-1611
        out['symbol_code'] = info[7]
        out['symbol_table'] = info[8]

        # 速度(节)/航向，line 1640-1656
        sc0 = ord(info[4]); sc1 = ord(info[5]); sc2 = ord(info[6])
        n = (sc0 - 28) * 10 + (sc1 - 28) // 10
        if n >= 800:
            n -= 800
        out['speed'] = n  # 节
        n = ((sc1 - 28) % 10) * 100 + (sc2 - 28)
        if n >= 400:
            n -= 400
        out['course'] = 0 if n == 360 else n
        out['comment'] = info[9:].strip()
    except (IndexError, ValueError):
        pass
    return out


def _decode_weather(info: str) -> Dict[str, Any]:
    """解析气象字段（位置报告尾部或无位置 '_' 报告）。

    字段标签: c=风向 s=风速 g=阵风 t=温度F r=1h雨 p=24h雨 P=午夜起雨 h=湿度 b=气压
    来源: direwolf decode_aprs.c:3130-3197 (weather_data)。
    """
    w: Dict[str, Any] = {}
    i = 0
    n = len(info)

    def take(label: str, width: int):
        nonlocal i
        if i + 1 + width <= n and info[i] == label:
            seg = info[i + 1:i + 1 + width]
            i += 1 + width
            if set(seg) <= set('0123456789.-') and seg.strip():
                v = _safe_float(seg)
                if v is not None:
                    return v
        return None

    # 位置报告内嵌气象: "DDD/SSS" 数据扩展格式（line 3075-3089）
    if n >= 7 and info[3] == '/' and info[:3].isdigit() and info[4:7].isdigit():
        w['wind_direction'] = int(info[:3])
        w['wind_speed'] = int(info[4:7])  # 节
        i = 7

    # 无位置气象: cDDD sSSS 标签形式（line 3093-3104）
    cdir = take('c', 3)
    if cdir is not None:
        w['wind_direction'] = int(cdir)
    wspd = take('s', 3)
    if wspd is not None:
        w['wind_speed'] = int(wspd)  # 节
    gust = take('g', 3)
    if gust is not None:
        w['wind_gust'] = gust
    temp = take('t', 3)
    if temp is not None:
        w['temperature_f'] = temp
    r1 = take('r', 3)
    if r1 is not None:
        w['rain_1h_inch'] = r1 / 100.0
    p24 = take('p', 3)
    if p24 is not None:
        w['rain_24h_inch'] = p24 / 100.0
    pmid = take('P', 3)
    if pmid is not None:
        w['rain_midnight_inch'] = pmid / 100.0
    hum = take('h', 2)
    if hum is not None:
        w['humidity'] = hum
    baro = take('b', 5)
    if baro is not None:
        w['pressure_mbar'] = baro / 10.0  # b 单位 1/10 hPa
    return w


def _decode_message(info: str) -> Dict[str, Any]:
    """解析 APRS 消息。格式 ":ADDRESSEE(9):text{id}"。来源: decode_aprs.c:394。"""
    out: Dict[str, Any] = {}
    try:
        addressee = info[1:10].strip()
        msg = info[11:] if len(info) > 11 else ''
        msg_id = None
        if '{' in msg:
            msg, tail = msg.rsplit('{', 1)
            msg_id = tail.strip()
        out['addressee'] = addressee
        out['message'] = msg
        out['message_id'] = msg_id
    except IndexError:
        pass
    return out


def _decode_telemetry(info: str) -> Dict[str, Any]:
    """解析遥测数据报告 "T#aaa,b,b,b,b,b,bits"。来源: decode_aprs.c:453。"""
    out: Dict[str, Any] = {}
    try:
        body = info[1:]
        parts = body.split(',')
        seq = parts[0]
        out['sequence'] = seq
        vals = []
        for p in parts[1:6]:
            v = _safe_float(p.strip())
            vals.append(v)
        out['analog'] = vals[:5]
        if len(parts) > 6:
            out['digital'] = parts[6].strip()
    except (IndexError, ValueError):
        pass
    return out


def parse_aprs_frame(frame: AX25Frame) -> Dict[str, Any]:
    """把一个 AX.25 UI 帧解析为结构化 APRS dict。

    返回字段: type, source, source_ssid, destination, digipeaters,
              latitude, longitude, altitude, speed, course, symbol,
              weather(dict), telemetry(dict), message(dict), comment, raw.
    """
    result: Dict[str, Any] = {
        'type': 'unknown',
        'source': frame.source,
        'source_ssid': frame.source_ssid,
        'destination': frame.destination,
        'destination_ssid': frame.dest_ssid,
        'digipeaters': [f"{c}-{s}" + ('*' if r else '') for c, s, r in frame.digipeaters],
        'latitude': None, 'longitude': None, 'altitude': None,
        'speed': None, 'course': None,
        'symbol': None, 'weather': None, 'telemetry': None,
        'message': None, 'comment': '', 'raw': '',
    }
    if frame.control not in (0x03,):  # 仅 UI 帧
        return result
    try:
        info = frame.info.decode('latin-1')
    except Exception:
        return result
    if not info:
        return result

    result['raw'] = info
    dti = info[0]
    result['type'] = _DTI_TYPE.get(dti, 'unknown')

    try:
        if dti in ("'", '`'):
            result.update(_decode_mic_e(frame, info))
            if result.get('symbol_code'):
                result['symbol'] = result['symbol_table'] + result['symbol_code']

        elif dti == ':':
            result['message'] = _decode_message(info)

        elif dti == 'T':
            result['telemetry'] = _decode_telemetry(info)

        elif dti == '_':
            # 无位置气象: '_' + 8字节时间戳(MDHM) + 气象字段，line 2959-2975
            result['weather'] = _decode_weather(info[9:])

        elif dti in ('!', '=', '/', '@'):
            body = info
            off = 0
            # 带时间戳: '/' 或 '@' 后 7 字节 DDHHMMz（line 386-387）
            if dti in ('/', '@') and len(body) >= 8:
                result['timestamp'] = body[1:8]
                off = 8
            else:
                off = 1
            rest = body[off:]
            # 压缩 vs 未压缩判定: 位置首字符是数字 -> 未压缩(可读)，否则 -> 压缩。
            # 来源: direwolf decode_aprs.c:926 (isdigit(lat[0]) 可读) / :970 (else 压缩)。
            if rest and rest[0].isdigit():
                pos = _decode_uncompressed_position(rest, 0)
            else:
                pos = _decode_compressed_position(rest, 0)
            result['latitude'] = pos.get('latitude')
            result['longitude'] = pos.get('longitude')
            result['altitude'] = pos.get('altitude')
            result['speed'] = pos.get('speed')
            result['course'] = pos.get('course')
            sym_code = pos.get('symbol_code')
            tail = pos.get('comment', '') or ''
            if pos.get('symbol_table') and sym_code:
                result['symbol'] = pos['symbol_table'] + sym_code
            # 符号代码 '_' 表示气象报告，尾部即气象数据（decode_aprs.c:930,974）
            if sym_code == '_':
                result['weather'] = _decode_weather(tail)
                result['comment'] = ''
                result['speed'] = result['weather'].get('wind_speed')
                result['course'] = result['weather'].get('wind_direction')
            else:
                # 普通位置: 数据扩展 course/speed 形如 ccc/sss（节）
                if len(tail) >= 7 and tail[3] == '/' and tail[:3].isdigit() and tail[4:7].isdigit():
                    result['course'] = int(tail[0:3])
                    result['speed'] = int(tail[4:7])
                    tail = tail[7:]
                result['comment'] = tail.strip()
                # 普通位置评论里也可能内嵌 '_...' 气象段
                wi = tail.find('_')
                if wi >= 0:
                    result['weather'] = _decode_weather(tail[wi + 1:])
                    result['comment'] = tail[:wi].strip()
    except Exception:
        pass

    return result
