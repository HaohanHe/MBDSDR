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
 *            忽略 ΔT（UT1≈UTC）、不含恒星自行、不含大气折射、不含站址视差。
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
    // P = Rz(z_A) · Rx(-theta_A) · Rz(-zeta_A)
    return mul(rotZ(z), mul(rotX(-theta), rotZ(-zeta)));
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

  MB.astro = {
    J2000, EPS0, ARCSEC, Q, QT, mul, transpose, matVec,
    gmstDeg, precessionMatrix, obliquity, nutationAngles, nutationMatrix,
    frameMatrices,
    meta: {
      source: "mbdsdr_ai/celestial_geometry.py (MIT, repo-owned)",
      precession: "IAU 2006 / P03 (Vondrak-Capitaine 2011 polynomial)",
      nutation: "IAU 2000B abridged 6-term (NOT full 77-term; no mas claim)",
      time: "Capitaine-Wallace-Chapront 2003 GMST eq.43, UT1≈UTC (ΔT ignored)",
      basis: "frontend ICRS (x=vernal equinox, y=RA 6h, z=NCP); repo basis via Q",
      notVerified: [
        "stellar proper motion not applied (HYG J2000 positions used verbatim)",
        "no atmospheric refraction, parallax or UT1-UTC correction",
        "repo precession polynomial leaves a ~5.3 arcsec constant rotation at J2000 (zeta_A/z_A constant terms + rot_z sign convention); not normalized",
        "nutation is a 6-term abridged model, no mas-level accuracy claim",
        "not suitable for precise pointing / pass prediction"
      ]
    }
  };
})(typeof window !== "undefined" ? window : globalThis);
