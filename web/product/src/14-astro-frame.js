/* MBDSDR 产品前端 · 14-astro-frame.js
 * 天文坐标框架：J2000(ICRS) → IAU2006 岁差 → IAU2000B 章动 → 本地恒星时 → 地平。
 *
 * 【事实源】mbdsdr_ai/celestial_geometry.py（仓库自有 MIT 实现，非 Stellarium 代码）。
 * 逐项对齐它的 gmst() / precession_matrix() / nutation_matrix() / obliquity() /
 * nutation_angles_2000b()，由 build/gen_astro_golden.py 生成跨语言金标准，
 * tests/astro_selftest.js 做数值对拍（不是"照着感觉写一遍"）。
 *
 * 【为什么不直接用仓库的 GroundStation 旋转矩阵】
 * 仓库的 vec_from_radec() 用的赤道基是 +y=北天极、-z=春分点（与其 docstring
 * 宣称的 +x=春分点/+z=北天极 不一致）；且其实测 GroundStation.east 与
 * 标准天文方位（东正）差一个符号。数值对拍：仓库链与前端标准链的 x/z 完全
 * 一致、y 恰好相反。因此这里只借 P/N/GMST，用基变换
 *     Q    : v_repo = Q · v_frontend      Q  = [0,1,0; 0,0,1; -1,0,0]
 *     Mpn  = Qᵀ · N · P · Q               （前端标准 ICRS 基下的 J2000→of-date）
 * 地平旋转仍用前端已验证的标准式（31-celestial.js 原 eqToHorizon 的矩阵形式）。
 * 这样既接上真实岁差/章动，又不把仓库的方位约定差异带进 UI。
 *
 * 【坐标约定】前端标准 J2000 赤道：x=春分点, y=赤经 6h, z=北天极；
 *            前端地平：x=北, y=东, z=天顶（方位角从北顺时针，东正）。
 * 【精度口径】章动是仓库的简化 6 主项模型，不是完整 IAU2000A/B 77 项；
 *            恒星自行按 HYG 实测 pmRA/pmDec 线性推进到当前历元（propagateProperMotion，
 *            忽略透视加速度/视差长期项，skyfield 严格空间运动对拍残差 ≤1.5″）；
 *            忽略 ΔT（UT1≈UTC）、不含大气折射、不含站址视差。
 *            够 UI 天球显示，不用于精确指向 / 定轨 / 过境结论。
 */
(function (g) {
  "use strict";
  const MB = g.MBDSDR = g.MBDSDR || {};
  const D2R = Math.PI / 180;
  const J2000 = 2451545.0;
  const ARCSEC = D2R / 3600;                       // 1 角秒 -> rad
  const EPS0 = 84381.406 * ARCSEC;                // J2000 平黄赤交角
  const EPS_RATE = 46.836769 / 206264.806;        // IAU2006 一次项 rad/century

  /* ---------- 3×3 行主序矩阵 ---------- */
  function mul(A, B) {
    const o = new Float64Array(9);
    for (let r = 0; r < 3; r++) {
      const a = r * 3;
      for (let c = 0; c < 3; c++)
        o[a + c] = A[a] * B[c] + A[a + 1] * B[3 + c] + A[a + 2] * B[6 + c];
    }
    return o;
  }
  /* 与仓库 rot_x / rot_z 的定义逐字一致（注意它是"列向量约定"的负角形式）。 */
  function rotX(a) {
    const c = Math.cos(a), s = Math.sin(a);
    return new Float64Array([1, 0, 0, 0, c, s, 0, -s, c]);
  }
  function rotZ(a) {
    const c = Math.cos(a), s = Math.sin(a);
    return new Float64Array([c, s, 0, -s, c, 0, 0, 0, 1]);
  }
  function rotY(a) {
    const c = Math.cos(a), s = Math.sin(a);
    return new Float64Array([c, 0, -s, 0, 1, 0, s, 0, c]);
  }
  function matVec(m, x, y, z) {
    return { x: m[0] * x + m[1] * y + m[2] * z,
             y: m[3] * x + m[4] * y + m[5] * z,
             z: m[6] * x + m[7] * y + m[8] * z };
  }

  /* ---------- GMST (Capitaine, Wallace, Chapront 2003, A&A 412, 567, eq.43) ----------
   * 与仓库 gmst(jd_ut1) 同式：仓库令 jd_tt = jd_ut1（忽略 ΔT），此处照做。
   * 旧前端用的是 Meeus 多项式，2026 年与 Cap. 公式差约 0.057"，量级可忽略，
   * 但统一到仓库口径才能让跨语言对拍严格成立。 */
  function gmstDeg(jd) {
    const t = (jd - J2000) / 36525.0;
    const tu = t;
    const ut1Sec = (jd - Math.floor(jd) + 0.5) * 86400.0;
    let s = (((-0.000000002454 * t - 0.00000199708) * t
              - 0.0000002926) * t + 0.092772110) * t * t;
    s += (t - tu) * 307.4771013;
    s += 8640184.79447825 * tu + 24110.5493771 + ut1Sec;
    s *= 1.0 / 240.0;
    return ((s % 360) + 360) % 360;
  }

  /* ---------- IAU 2006 / P03 岁差（仓库同式多项式，角秒） ---------- */
  function precessionMatrix(jdTT) {
    const T = (jdTT - J2000) / 36525.0;
    const T2 = T * T, T3 = T2 * T, T4 = T3 * T, T5 = T4 * T;
    const zeta = (2.650545 + 2306.083227 * T + 0.2988499 * T2
                  + 0.01801828 * T3 - 0.000005971 * T4
                  - 0.0000003173 * T5) * ARCSEC;
    const z = (-2.650545 + 2306.077181 * T + 1.0927348 * T2
               + 0.01826898 * T3 - 0.000028596 * T4
               - 0.0000002904 * T5) * ARCSEC;
    const theta = (2004.191903 * T - 0.4269350 * T2 - 0.04183110 * T3
                   - 0.000006059 * T4 - 0.00000014543 * T5) * ARCSEC;
    /* 2026-11-06 修：与仓库 celestial_geometry.precession_matrix 同一处——
     * θ_A 是"赤道与黄道夹角"，在标准式里绕 **y** 轴（Ry），不是绕 x；
     * 且 ζ_A 的符号随本文件 rot_* 的负角约定一起翻转。总式：
     *   P_std = Rz_std(z_A) · Ry_std(−θ_A) · Rz_std(ζ_A)
     * 本文件 rotZ(a)=Rz_std(−a)、rotY(a)=Ry_std(−a)，代入后即下一行。
     * 【为什么还要再乘 Q/Qᵀ】本函数对外必须返回**仓库基**下的 P（和 Python 的
     * 同名字段逐元素对拍）；仓库赤道基是左手系(y=NCP, -z=春分点)，标准式算出来的
     * 是右手基下的矩阵，两者差一次基变换 P_mod = Sᵀ·P_std·S，而 Sᵀ 正是本文件的
     * Q、S 是 Qᵀ。漏掉这一步会让 P 与仓库差 3e-6 量级（对拍直接红），
     * 天狼星 of-date 赤经再偏 2349"。 */
    const P_std = mul(rotZ(-z), mul(rotY(theta), rotZ(-zeta)));
    return mul(Q, mul(P_std, QT));
  }

  function obliquity(jdTT) {
    return EPS0 - EPS_RATE * ((jdTT - J2000) / 36525.0);
  }

  /* ---------- IAU 2000B 简化主项章动（仓库同式 6 项，单位 = 角秒） ----------
   * 2026-11 修：仓库旧版把 arcsec 系数按 0.0001 arcsec 缩放，dpsi 只有
   * 0.0003"、N≈I（章动零生效）。产品与仓库现已同时按角秒换算。 */
  const NUT_TERMS = [
    [0, 0, 0, 0, 1, -17.2066, 0.0000],
    [0, 0, 2, 0, 2, -1.3170, 0.5774],
    [0, 0, 2, 0, 1, -0.2276, 0.0977],
    [0, 0, 2, 0, 0, 0.2074, 0.0000],
    [0, 1, 0, 0, 0, 0.1478, 0.0000],
    [1, 0, 0, 0, 0, 0.1299, 0.0000]
  ];
  function nutationAngles(jdTT) {
    const T = (jdTT - J2000) / 36525.0;
    const Om = (125.04452 - 1934.136261 * T) * D2R;
    const lS = (357.52910 + 35999.05030 * T) * D2R;
    const lM = (134.96340 + 477198.89108 * T) * D2R;
    const F = (93.27295 + 483202.017523 * T) * D2R;
    const D = (297.85036 + 445267.111480 * T) * D2R;
    let dp = 0, de = 0;
    for (const t of NUT_TERMS) {
      const arg = t[0] * lM + t[1] * lS + t[2] * F + t[3] * D + t[4] * Om;
      dp += t[5] * Math.sin(arg);
      de += t[6] * Math.cos(arg);
    }
    return { dpsi: dp * ARCSEC, deps: de * ARCSEC };
  }
  function nutationMatrix(jdTT) {
    const eps = obliquity(jdTT);
    const a = nutationAngles(jdTT);
    // N = Rx(eps_A) · Rz(-dpsi) · Rx(-eps_A - deps)
    return mul(rotX(eps), mul(rotZ(-a.dpsi), rotX(-eps - a.deps)));
  }

  /* ---------- 基变换：前端标准 ICRS <-> 仓库赤道基 ---------- */
  const Q = new Float64Array([0, 1, 0, 0, 0, 1, -1, 0, 0]);
  const QT = new Float64Array([0, 0, -1, 1, 0, 0, 0, 1, 0]);
  function transpose(m) {
    return new Float64Array([m[0], m[3], m[6], m[1], m[4], m[7], m[2], m[5], m[8]]);
  }

  /* ---------- 每帧一次：合成总矩阵 ----------
   * Rf = 前端标准赤道(of-date) → 地平(x=北,y=东,z=天顶)：
   *   [ -sinφ cosL, -sinφ sinL,  cosφ ]
   *   [ -sinL,       cosL,       0    ]
   *   [  cosφ cosL,  cosφ sinL,  sinφ ]      L = GMST + 东经
   * M = Rf · Qᵀ · N · P · Q   （J2000 标准向量一步到地平）
   * R = Rf                      （of-date 向量一步到地平，给太阳用）
   */
  function frameMatrices(jd, latDeg, lonDeg) {
    const P = precessionMatrix(jd), N = nutationMatrix(jd);
    const Mpn = mul(QT, mul(N, mul(P, Q)));
    const gmst = gmstDeg(jd);
    const L = (gmst + lonDeg) * D2R, phi = latDeg * D2R;
    const cL = Math.cos(L), sL = Math.sin(L);
    const cP = Math.cos(phi), sP = Math.sin(phi);
    const Rf = new Float64Array([
      -sP * cL, -sP * sL, cP,
      -sL, cL, 0,
      cP * cL, cP * sL, sP
    ]);
    return {
      jd, lat: latDeg, lon: lonDeg, gmst,
      lst: ((gmst + lonDeg) % 360 + 360) % 360,
      P, N, Mpn, Rf,
      M: mul(Rf, Mpn),
      // 前端基下的逆岁差 (Qᵀ P Q)⁻¹ = Qᵀ Pᵀ Q；TEME→J2000 用，金标准对拍
      Pinv: mul(QT, mul(transpose(P), Q))
    };
  }

  /* ---------- 恒星自行：J2000 catalog 位置线性推进到历元 t ----------
   * HYG 口径：pmra = μα·cosδ（mas/yr）、pmdec = μδ（mas/yr），ICRS/J2000 框架。
   * 标准星象仪显示近似（线性 RA/Dec 漂移）：
   *   δ(t) = δ0 + pmdec·Δt / 3.6e6
   *   α(t) = α0 + pmra·Δt / (3.6e6·cosδ0)
   * 近极保护：|cosδ0| < 1e-9 时 RA 不推进（cosδ→0 时 μα 发散；极区星 pmra 本就≈0）。
   * 忽略透视加速度/视差长期项（rv/parallax）；对拍 skyfield 1.54 严格空间运动
   * （_position_au + _velocity_au_per_d·Δt，含视差/视向速度/Doppler）：
   * 2026 历元残差 ≤0.35″、J2000→2051 最快星（Barnard）≤1.5″
   * （build/gen_proper_motion_golden.py + tests/proper_motion_selftest.js）。
   * 推进仍在 ICRS 框架，随后由 frameMatrices 的岁差 P 转到 of-date —— 不重复施加岁差。
   */
  const MAS_PER_DEG = 3600000;
  function propagateProperMotion(raDeg, decDeg, pmraMasYr, pmdecMasYr, dtYr) {
    let dec = decDeg + (pmdecMasYr * dtYr) / MAS_PER_DEG;
    const cosDec0 = Math.cos(decDeg * D2R);
    let ra = raDeg;
    if (Math.abs(cosDec0) > 1e-9)
      ra = raDeg + (pmraMasYr * dtYr) / (MAS_PER_DEG * cosDec0);
    dec = Math.max(-90, Math.min(90, dec));
    ra = ((ra % 360) + 360) % 360;
    return { ra, dec };
  }

  /* ---------- 极限星等模型（事实源：内核 skyengine，逐式镜像 + 跨语言钉扎） ----------
   * 三函数与 mbdsdr_ai/skyengine/stars.py::naked_eye_limit_mag 及
   * star_catalog.py::detail_gain_mag / limit_magnitude 逐字同构：
   *   nakedEyeLimitMag(b) = 6.5 - 0.35·max(0, trunc(b)-3)  （Stellarium StelSkyDrawer 同语义）
   *   detailGainMag(fov)  = clamp(0.5·log2(30/fov), 0, 1.5)（fov>=30 → 0，永不为负）
   *   limitMagnitude(fov,b,depthCap) = min(depthCap, nakedEye + detailGain)
   * detailGain 是**渲染 LOD 经验式**（放大后像素张角变小、暗星不再挤成一团，
   * 才逐层露出更暗的星），不是光度学模型、不是望远镜口径增益。
   * depthCap 默认 = 内核 CATALOG_DEPTH_MAG 9.0（其全星表深度）；前端随仓 HYG
   * 截断较浅（MAG_CAP=7.5），由调用方 31-celestial.js 传入自己的 cap。
   * 内核 int(bortle) 向零取整 → JS Math.trunc 同语义。
   * build/gen_limit_mag_golden.py 取内核真值，tests/limit_mag_selftest.js 紧钉扎
   * （分量 1e-12、合成 default-depth 与内核 limit_magnitude 逐点相等）。 */
  const DEFAULT_BORTLE = 3;
  const NAKED_EYE_LIMIT_SCALE = 6.5;
  const NAKED_EYE_BORTLE_SLOPE = 0.35;
  const DETAIL_REF_FOV_DEG = 30.0;
  const MAX_DETAIL_GAIN_MAG = 1.5;
  const CATALOG_DEPTH_MAG = 9.0;
  function nakedEyeLimitMag(bortle = DEFAULT_BORTLE) {
    return NAKED_EYE_LIMIT_SCALE
      - NAKED_EYE_BORTLE_SLOPE * Math.max(0, Math.trunc(bortle) - 3);
  }
  function detailGainMag(fovDeg) {
    const fov = Math.max(1e-6, Number(fovDeg));
    if (fov >= DETAIL_REF_FOV_DEG) return 0.0;
    const gain = 0.5 * Math.log2(DETAIL_REF_FOV_DEG / fov);
    return Math.min(MAX_DETAIL_GAIN_MAG, Math.max(0.0, gain));
  }
  function limitMagnitude(fovDeg, bortle = DEFAULT_BORTLE, depthCap = CATALOG_DEPTH_MAG) {
    return Math.min(depthCap, nakedEyeLimitMag(bortle) + detailGainMag(fovDeg));
  }

  /* ---------- 晨昏天空亮度 → 极限星等连续压制（事实源：内核 skyengine/twilight.py） ----------
   * 逐式镜像 mbdsdr_ai/skyengine/twilight.py：
   *   skyBrightnessMagArcsec2(sunAlt) = 分段线性天顶亮度表（V mag/arcsec²），太阳越高
   *     越亮（数值越小）；alt>=10° 夹到 4.0、alt<=-90° 夹到夜值 21.6；None/NaN → 夜值
   *     （太阳高度未知不假装白天）。插值 m_lo + t·(m_hi - m_lo)，t=(alt-a_lo)/(a_hi-a_lo)。
   *   twilightLimitPenaltyMag(sunAlt) = max(0, 21.6 - skyBrightness)：天空比无月夜亮多少等，
   *     极限星等就压低多少等（背景限定探测 1:1）。<=-18°（天文夜）惩罚精确为 0；白天压
   *     十几等，整层恒星自然消失（不靠布尔开关）。
   * 桌面 rf_sky_view.py:4246 已接线 limit = limit_magnitude(fov,bortle) - penalty(sun_alt)，
   * tests/test_sky_realism_batch.py::TestTwilightPenalty 钉扎；前端 31-celestial.js
   * effectiveLimit 同式合成（intro 淡入后再减惩罚）。build/gen_twilight_golden.py 取内核
   * 真值，tests/twilight_selftest.js 紧钉扎（分量/合成 1e-12）。
   * 【诚实口径】量级近似（Krisciunas & Schaefer 1991 / Patat et al. 2006 曲线量级），未实测
   * 标定，气溶胶/臭氧/纬度/方位带来 ±1 等差异；天顶等效值，无方位依赖、无月光。只用于显示层
   * “画不画、画多暗”，不参与指向/定轨/过境结论。表值口径以内核模块为准，不在别处再抄一份。 */
  const SKY_BRIGHTNESS_NIGHT_MAG_ARCSEC2 = 21.6;
  const SKY_BRIGHTNESS_TABLE = [
    [10.0, 4.0], [5.0, 4.8], [2.0, 6.0], [0.0, 8.0],
    [-2.0, 10.5], [-4.0, 13.0], [-6.0, 15.0], [-8.0, 16.5],
    [-10.0, 17.8], [-12.0, 19.0], [-15.0, 20.5],
    [-18.0, SKY_BRIGHTNESS_NIGHT_MAG_ARCSEC2], [-90.0, SKY_BRIGHTNESS_NIGHT_MAG_ARCSEC2]
  ];
  function skyBrightnessMagArcsec2(sunAltDeg) {
    if (sunAltDeg == null) return SKY_BRIGHTNESS_NIGHT_MAG_ARCSEC2;
    const alt = Number(sunAltDeg);
    if (!Number.isFinite(alt)) return SKY_BRIGHTNESS_NIGHT_MAG_ARCSEC2;
    const table = SKY_BRIGHTNESS_TABLE;
    if (alt >= table[0][0]) return table[0][1];
    if (alt <= table[table.length - 1][0]) return table[table.length - 1][1];
    for (let i = 0; i < table.length - 1; i++) {
      const aHi = table[i][0], mHi = table[i][1];
      const aLo = table[i + 1][0], mLo = table[i + 1][1];
      if (aLo <= alt && alt <= aHi) {
        const t = (alt - aLo) / (aHi - aLo);
        return mLo + t * (mHi - mLo);
      }
    }
    return SKY_BRIGHTNESS_NIGHT_MAG_ARCSEC2;
  }
  function twilightLimitPenaltyMag(sunAltDeg) {
    return Math.max(0.0, SKY_BRIGHTNESS_NIGHT_MAG_ARCSEC2
                       - skyBrightnessMagArcsec2(sunAltDeg));
  }

  MB.astro = {
    J2000, EPS0, ARCSEC, Q, QT, mul, transpose, matVec,
    gmstDeg, precessionMatrix, obliquity, nutationAngles, nutationMatrix,
    frameMatrices, propagateProperMotion, MAS_PER_DEG,
    nakedEyeLimitMag, detailGainMag, limitMagnitude,
    DEFAULT_BORTLE, NAKED_EYE_LIMIT_SCALE, NAKED_EYE_BORTLE_SLOPE,
    DETAIL_REF_FOV_DEG, MAX_DETAIL_GAIN_MAG, CATALOG_DEPTH_MAG,
    skyBrightnessMagArcsec2, twilightLimitPenaltyMag,
    SKY_BRIGHTNESS_NIGHT_MAG_ARCSEC2, SKY_BRIGHTNESS_TABLE,
    meta: {
      source: "mbdsdr_ai/celestial_geometry.py (MIT, repo-owned)",
      precession: "IAU 2006 / P03 (Vondrak-Capitaine 2011 polynomial)",
      nutation: "IAU 2000B abridged 6-term (NOT full 77-term; no mas claim)",
      time: "Capitaine-Wallace-Chapront 2003 GMST eq.43, UT1≈UTC (ΔT ignored)",
      basis: "frontend ICRS (x=vernal equinox, y=RA 6h, z=NCP); repo basis via Q",
      properMotion: "HYG v4.1 pmra/pmdec (mas/yr) linear RA/Dec drift; "
        + "cross-checked vs skyfield 1.54 rigorous space motion (<=0.35\" at 2026, <=1.5\" J2000->2051)",
      limitingMagnitude: "kernel fact source: skyengine/stars.py::naked_eye_limit_mag "
        + "(6.5-0.35*max(0,trunc(bortle)-3), Stellarium StelSkyDrawer semantics) + "
        + "star_catalog.py::detail_gain_mag (clamp(0.5*log2(30/fov),0,1.5), render LOD not photometry) "
        + "/ limit_magnitude (min(depthCap, sum)); cross-language pinned by "
        + "build/gen_limit_mag_golden.py + tests/limit_mag_selftest.js",
      twilight: "kernel fact source: skyengine/twilight.py::sky_brightness_mag_arcsec2 "
        + "(piecewise-linear zenith V mag/arcsec^2 table, night 21.6, None/NaN->night) + "
        + "twilight_limit_penalty_mag (max(0, 21.6 - brightness)); desktop rf_sky_view.py wires "
        + "limit = limit_magnitude(fov,bortle) - penalty(sun_alt), pinned by "
        + "tests/test_sky_realism_batch.py::TestTwilightPenalty; web effectiveLimit composes the same; "
        + "cross-language pinned by build/gen_twilight_golden.py + tests/twilight_selftest.js",
      notVerified: [
        "stellar proper motion uses linear RA/Dec approximation (ignores perspective acceleration / parallax secular terms); not for mas-level astrometry",
        "no atmospheric refraction, parallax or UT1-UTC correction",
        "repo precession polynomial leaves a ~5.3 arcsec constant rotation at J2000 (zeta_A/z_A constant terms + rot_z sign convention); not normalized",
        "nutation is a 6-term abridged model, no mas-level accuracy claim",
        "limiting magnitude is the kernel Bortle baseline + FOV render-LOD gain (NOT a full sky-brightness photometric model: moon phase / actual calibrated sky surface brightness still unmodeled, kernel limitations L24; the twilight penalty IS kernel-aligned, see twilight entry); the intro dark-adaptation fade is a web presentation layer, not kernel",
        "twilight penalty mirrors kernel skyengine/twilight.py: order-of-magnitude literature curve (Krisciunas & Schaefer 1991 / Patat et al. 2006), NOT radiometrically calibrated (aerosol/ozone/latitude/azimuth give ~+/-1 mag); zenith-equivalent, no azimuth dependence, no moonlight; display-layer only, never used for pointing/orbit/pass conclusions",
        "not suitable for precise pointing / pass prediction"
      ]
    }
  };
})(typeof window !== "undefined" ? window : globalThis);
