/* MBDSDR 产品前端 · 31-celestial.js
 * 天球引擎（自研）：固定 J2000 星位 → IAU2006 岁差 + IAU2000B 章动
 *                  → 模拟时刻 GMST/LST → 地平 az/alt → 视图空间
 *                  → 投影到**全屏平面**（无圆形视场盘）。
 * 坐标框架在 14-astro-frame.js（事实源 mbdsdr_ai/celestial_geometry.py，
 * 跨语言金标准对拍）。太阳是 of-date 位置，走 dateToHorizon 只做 LST 旋转。
 *
 * 【怎么改的（2026-11-04，用户第二次点名后重做）】
 *  参考 D:/mbdsdr/reference/stellarium-web-engine 的真实渲染链（只学机制，
 *  不引代码，AGPL 不落进 MIT 仓库）：
 *    1. 相机在球心，一切先转到 **视图空间**：右 X / 上 Y / 前 Z。
 *    2. 投影只负责把球面方向变成平面坐标，**裁剪交给窗口矩形**——
 *       原项目就是 project_to_win() 把 NDC 映射到 window_size，屏幕是
 *       一个矩形，没有任何圆形遮罩。圆形视场盘是我上一版的臆造。
 *    3. 投影可插拔，同一套视图空间下二选一：
 *       - perspective    x = X/Z·f,  f = (min(W,H)/2)/tan(fov/2)   ← 针孔相机 / 人眼
 *       - stereographic  r = 2·tan(θ/2)                          ← 大 FOV 鱼眼
 *       原项目 proj_perspective.max_ui_fov=120°，proj_stereographic.max_ui_fov=185°，
 *       大视场切立体投影是它的既定策略，照做。
 *    4. **天空是球，屏幕是平面**：天圆地圆指的是球面几何（地平是大圆、
 *       地景绕 360° 方位铺满），不是把天空塞进一个圆圈里。
 *
 * 【科学口径 / 用户铁律】
 *  - 星星本来就在全天球固定位置，**不飞散、不平移、无独立角速度**。
 *  - 动的只有：视角（az/alt）、视场角（fov）、时间带来的天球刚性旋转（LST）。
 *  - 缩放 = 改 fov（改焦距），不做相机推拉。
 *  - 太阳位置用低精度解析式（Meeus 简化，±0.5°），不再用正弦假模型。
 *
 * 机制参考：mbdsdr_ai/celestial_geometry.py、mbdsdr_ai/new_spacetime.py、
 * desktop/rf_sky_view.py、mbdsdr_ai/skyengine/stars.py；
 * Stellarium Web Engine 的 project/painter/navigation 链路（笔记见
 * docs/08-engine-mechanism-notes.md）。
 */
(function () {
  "use strict";
  const MB = window.MBDSDR;
  const D2R = Math.PI / 180, R2D = 180 / Math.PI;
  const JD_UNIX_EPOCH = 2440587.5, JD_J2000 = 2451545.0;
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

  /* 投影切换阈值：小于它用针孔透视（真实相机/人眼），大于它用立体投影（鱼眼）。
     选 88° 是因为两者在此附近画面几乎无差别，切换不跳。 */
  const PERSP_MAX_FOV = 88;
  /* 相机平面前向分量下限：Z<=NEAR 的点在相机背后或贴面，投影会爆到无穷远。
     0.02 ≈ 离 90° 视轴 1.1°，屏幕上早已出画，直接丢弃无视觉损失。 */
  const NEAR = 0.02;

  /* ---------- ViewState（对齐 sky_interaction.py：fov_deg / center_az / center_alt） ---------- */
  const V = {
    fov: 160, fovTarget: 160,      // 初始 = 全天球鱼眼，随后落到 100°（看得见地平弧）
    az: 0, azTarget: 0,            // 视图中心方位（北=0，东=90，顺时针）
    alt: 22, altTarget: 22,        // 视图中心高度角
    minFov: 1.5, maxFov: 160,
    introFov: 160,                 // 入场起始视场（收拢到 100°）
    bortle: 3,
    intro: 0                       // 0→1 入场（暗适应 + 视场收拢），星位一帧不动
  };

  /* ---------- 时间：唯一缺省是模拟时刻（TimeEngine 契约） ----------
   * 离线时以「演示起始时刻」为种子 + S.time.minutes 倍率推进，并在 HUD
   * 明写「未接内核 TimeEngine」。设定为站址当地夜间是为了让全天星场
   * 可见——这是**观测条件设定**，不是伪造观测数据。
   */
  const _today = new Date();
  const _start = Date.UTC(_today.getUTCFullYear(), _today.getUTCMonth(),
                          _today.getUTCDate(), 22, 0, 0) / 1000;   // 当日 UTC 22:00
  const TIME = { baseUnix: _start, kernelAttached: false, demoStart: true };
  const INTRO_MS = 1800;
  const _bornAt = (typeof performance !== "undefined" && performance.now)
    ? performance.now() : Date.now();
  function simUnix(state) {
    if (TIME.kernelAttached && MB.data && typeof MB.data.nowUnix === "function") {
      return MB.data.nowUnix();
    }
    return TIME.baseUnix + (state.time.minutes || 0) * 60;
  }
  function nowJd(state) { return simUnix(state) / 86400 + JD_UNIX_EPOCH; }
  /* GMST / 岁差 / 章动统一由 14-astro-frame.js 提供（事实源：
     仓库 mbdsdr_ai/celestial_geometry.py，跨语言金标准对拍 3.3e-16）。
     旧版这里用的是 Meeus 多项式且**完全没有岁差/章动**——2026 年距 J2000
     已有 26 年，岁差约 0.36°，窄视场下是肉眼可见的系统性偏移。 */
  const ASTRO = MB.astro;
  if (!ASTRO || typeof ASTRO.frameMatrices !== "function")
    throw new Error("31-celestial requires 14-astro-frame.js loaded first");
  function gmstDeg(jd) { return ASTRO.gmstDeg(jd); }
  function lstDeg(jd, lonDeg) { return (((gmstDeg(jd) + lonDeg) % 360) + 360) % 360; }

  /* ---------- 星表预计算 ---------- */
  const CAT = MB.SKYCAT || { stars: [], named: [], lines: {}, dso: [], meta: {} };
  const N = CAT.stars.length;
  const cosRA = new Float32Array(N), sinRA = new Float32Array(N);
  const sinDec = new Float32Array(N), cosDec = new Float32Array(N);
  /* 单位方向向量（标准 ICRS：x=春分点, y=赤经 6h, z=北天极）。
     eachStar 是每帧 25,792 颗的热点，预存后直接左乘总矩阵，
     省掉每颗星构造 (cD·cRA, cD·sRA, sD) 的 3 次乘法。
     这些三角表由 ensureEpoch 按当前历元（J2000 + HYG 实测自行）重建——
     不是固定 J2000；自行是几十年尺度漂移，按历元桶缓存，绝不逐帧重算。 */
  const VX = new Float64Array(N), VY = new Float64Array(N), VZ = new Float64Array(N);
  const MAG = new Float32Array(N), BV = new Float32Array(N);
  /* J2000 catalog 原值（度）+ HYG 实测自行（mas/yr，star[4]/star[5]，缺测=0）。
     ensureEpoch 据此把每颗星线性推进到当前模拟历元，再重建上面的三角表。 */
  const RA0 = new Float64Array(N), DEC0 = new Float64Array(N);
  const PMRA = new Float32Array(N), PMDEC = new Float32Array(N);
  for (let i = 0; i < N; i++) {
    const s = CAT.stars[i];
    RA0[i] = s[0] / 1000; DEC0[i] = s[1] / 1000;
    PMRA[i] = s[4] || 0; PMDEC[i] = s[5] || 0;
    MAG[i] = s[2] / 100; BV[i] = s[3] / 100;
  }
  const MAG_CAP = Number(CAT.meta.magMax || 7.5);

  /* 按星等升序的索引：extinction >= 0，所以 mag > 极限星等 L 的星**必然**
     不可见。按亮度排好后循环可以直接 break，不必为暗星做任何三角运算。
     L = 内核极限星等（裸眼基线 + FOV 渲染 LOD 增益，封顶星表深度）：广角
     （fov>=30）L 停在裸眼基线，星表暗尾（mag>L）整段跳过；窄视场 L 抬高、
      magnitude 门放宽，改由视锥早筛剔除画外星（体检 P1）。 */
  const ORDER = new (N > 65535 ? Uint32Array : Uint16Array)(N);
  {
    const idx = Array.from({ length: N }, (_, i) => i);
    idx.sort((a, b) => MAG[a] - MAG[b]);
    for (let i = 0; i < N; i++) ORDER[i] = idx[i];
  }

  const NAMED = (CAT.named || []).map(s => ({
    name: s.n, zh: s.zh || "", mag: s.m, bv: s.bv,
    ra0: s.ra, dec0: s.dec, pmra: s.pmra || 0, pmdec: s.pmdec || 0,
    ra: s.ra, dec: s.dec,
    cosRA: Math.cos(s.ra * D2R), sinRA: Math.sin(s.ra * D2R),
    sinDec: Math.sin(s.dec * D2R), cosDec: Math.cos(s.dec * D2R)
  }));
  /* DSO 的赤经赤纬是常量，三角值只算一次（体检 P4）。
     DSO 是河外/星云，无恒星自行，不推进（保持 J2000，岁差链照走）。 */
  const DSO_T = (CAT.dso || []).map(d => {
    const ra = d.ra * D2R, dec = d.dec * D2R;
    return { d, cRA: Math.cos(ra), sRA: Math.sin(ra),
             sD: Math.sin(dec), cD: Math.cos(dec) };
  });

  /* ---------- 恒星自行历元推进（按历元桶缓存，守住 P0 渲染性能） ----------
   * 自行是几十年尺度的缓慢漂移：演示历元≈2026（距 J2000 26 年），倍率最高 ×1800
   * 也只 ~0.5 模拟小时/真实秒，正常播放几乎不跨桶。漂移 > PM_BUCKET 年才重算一次
   * 全表三角（O(N) trig，~1-2ms）；每帧热点仍只做总矩阵乘法（与旧版逐帧成本持平）。
   * 推进在 ICRS 框架内（ASTRO.propagateProperMotion 线性 RA/Dec 漂移），随后由
   * frameMatrices 的岁差 P 转到 of-date —— 不重复施加岁差。命名亮星同步推进，
   * 标签与星场点不会因自行而错位（搜索锁定也走推进后的坐标）。 */
  const PM_BUCKET = 0.25;        // 年；桶内最大漂移误差 ≈ pm_max·0.25 ≈ 2.6″（Barnard），亚像素
  let _pmDtYr = null;
  function ensureEpoch(jd) {
    const dtYr = (jd - JD_J2000) / 365.25;
    if (_pmDtYr !== null && Math.abs(dtYr - _pmDtYr) < PM_BUCKET) return;
    _pmDtYr = dtYr;
    for (let i = 0; i < N; i++) {
      const p = ASTRO.propagateProperMotion(RA0[i], DEC0[i], PMRA[i], PMDEC[i], dtYr);
      const ra = p.ra * D2R, dec = p.dec * D2R;
      const cRA = Math.cos(ra), sRA = Math.sin(ra), sD = Math.sin(dec), cD = Math.cos(dec);
      cosRA[i] = cRA; sinRA[i] = sRA; sinDec[i] = sD; cosDec[i] = cD;
      VX[i] = cD * cRA; VY[i] = cD * sRA; VZ[i] = sD;
    }
    for (const s of NAMED) {
      const p = ASTRO.propagateProperMotion(s.ra0, s.dec0, s.pmra, s.pmdec, dtYr);
      s.ra = p.ra; s.dec = p.dec;
      const ra = p.ra * D2R, dec = p.dec * D2R;
      s.cosRA = Math.cos(ra); s.sinRA = Math.sin(ra);
      s.sinDec = Math.sin(dec); s.cosDec = Math.cos(dec);
    }
  }
  ensureEpoch(TIME.baseUnix / 86400 + JD_UNIX_EPOCH);   // 初始历元（演示起始时刻）

  /* ---------- 每帧一次的总矩阵（J2000 → 岁差 → 章动 → 本地恒星时 → 地平） ----------
   * 前端地平系：x=北, y=东, z=天顶。矩阵把「标准 ICRS 单位向量」一步送到地平，
   * 每颗星只剩 9 乘 6 加，与旧版逐星三角展开计算量持平，却补上了真实岁差/章动。
   * 缓存键必须含站址：旧版只按 jd 缓存，换站址时矩阵不会失效。 */
  let cache = { key: "", jd: -1, lst: 0, m: null, r: null,
                cL: 1, sL: 0, sPhi: 0, cPhi: 1 };
  function frame(state) {
    const jd = nowJd(state);
    ensureEpoch(jd);                 // 自行历元桶（命中即返回，跨桶才重算三角表）
    const lat = state.site.lat || 0, lon = state.site.lon || 0;
    const key = jd.toFixed(6) + "/" + lat + "/" + lon;
    if (key === cache.key) return cache;
    const F = ASTRO.frameMatrices(jd, lat, lon);
    const lr = F.lst * D2R, pr = lat * D2R;
    cache = { key, jd, lst: F.lst, m: F.M, r: F.Rf,
              cL: Math.cos(lr), sL: Math.sin(lr),
              sPhi: Math.sin(pr), cPhi: Math.cos(pr) };
    return cache;
  }
  /* J2000（HYG 星表、星座连线、DSO、J2000 赤道网格、已转 J2000 的卫星轨迹）。
     输入是 (cosRA, sinRA, sinDec, cosDec)，等价于标准 ICRS 向量
     (cosD·cosRA, cosD·sinRA, sinD)。 */
  function eqToHorizon(cRA, sRA, sD, cD, f) {
    const m = f.m, X = cD * cRA, Y = cD * sRA, Z = sD;
    return { x: m[0] * X + m[1] * Y + m[2] * Z,
             y: m[3] * X + m[4] * Y + m[5] * Z,
             z: m[6] * X + m[7] * Y + m[8] * Z };
  }
  /* of-date（太阳的 Meeus 低精度位置已用当年黄赤交角解出，本身是当前赤道系）。
     走这条链只做 LST 旋转，**不再施加岁差**，否则会重复修正约 0.36°。 */
  function dateToHorizon(cRA, sRA, sD, cD, f) {
    const r = f.r, X = cD * cRA, Y = cD * sRA, Z = sD;
    return { x: r[0] * X + r[1] * Y + r[2] * Z,
             y: r[3] * X + r[4] * Y + r[5] * Z,
             z: r[6] * X + r[7] * Y + r[8] * Z };
  }
  function altAzVector(azDeg, altDeg) {
    const a = altDeg * D2R, A = azDeg * D2R;
    return { x: Math.cos(a) * Math.cos(A), y: Math.cos(a) * Math.sin(A), z: Math.sin(a) };
  }
  function dirAltAz(v) {
    const l = Math.hypot(v.x, v.y, v.z) || 1;
    const alt = Math.asin(clamp(v.z / l, -1, 1)) * R2D;
    let az = Math.atan2(v.y / l, v.x / l) * R2D;
    return { az: ((az % 360) + 360) % 360, alt };
  }

  /* ---------- 相机基（视图空间：右 X / 上 Y / 前 Z） ----------
   * 与 Stellarium 的 ro2v 同构：视线方向 f 为 +Z，屏幕右为 +X，上为 +Y。
   * 只保留 yaw/pitch，roll 置 0（地平坐标系下天顶朝上，无需翻滚）。
   */
  function basis() {
    const a0 = V.alt * D2R, A0 = V.az * D2R;
    const f = { x: Math.cos(a0) * Math.cos(A0),
                y: Math.cos(a0) * Math.sin(A0), z: Math.sin(a0) };
    // 参考向量：默认世界天顶；接近天顶时退化 → 改用正北
    let ref = (Math.abs(f.z) > 0.9995) ? { x: 1, y: 0, z: 0 } : { x: 0, y: 0, z: 1 };
    let rx = ref.y * f.z - ref.z * f.y,
        ry = ref.z * f.x - ref.x * f.z,
        rz = ref.x * f.y - ref.y * f.x;
    const rl = Math.hypot(rx, ry, rz) || 1;
    rx /= rl; ry /= rl; rz /= rl;
    const ux = f.y * rz - f.z * ry,
          uy = f.z * rx - f.x * rz,
          uz = f.x * ry - f.y * rx;
    return { f, r: { x: rx, y: ry, z: rz }, u: { x: ux, y: uy, z: uz } };
  }
  let _basis = null, _basisKey = "";
  function getBasis() {
    const key = V.az.toFixed(4) + "/" + V.alt.toFixed(4);
    if (key !== _basisKey) { _basis = basis(); _basisKey = key; }
    return _basis;
  }
  function projMode(fovDeg) {
    return (fovDeg === undefined ? V.fov : fovDeg) <= PERSP_MAX_FOV
      ? "perspective" : "stereographic";
  }
  /* 焦距标定：以屏幕**短边**半宽对应 tan(fov/2)。
     这样 fov 就是最小视场角，宽屏水平自然更宽（与针孔相机一致）。 */
  function focalOf(W, H, fovDeg) {
    const fv = fovDeg === undefined ? V.fov : fovDeg;
    return (Math.min(W, H) / 2) / Math.tan(fv * D2R / 2);
  }

  /* 可见性阈值（关键，且两种投影不同）：
   *  - 透视：必须 Z > 0 才在相机前方，否则投影分母为负/为零，屏幕上是假的；
   *  - 立体投影：映射的是**整个球面**（背极点除外），屏幕四角本来就会落到
   *    相机背后（θ>90°）——这是鱼眼的固有性质，不是错误。只有 Z→−1 的
   *    背极点是奇点（ρ=2tan(θ/2)→∞），必须排除。
   *  上一版对立体投影也套用 Z>NEAR，导致广角下四角的星和地景被整片丢掉。 */
  const STEREO_POLE = -0.995;      // cos 174.3°，ρ=45 远在屏幕外
  function zCut() {
    return V.fov <= PERSP_MAX_FOV ? NEAR : STEREO_POLE;
  }

  /* ---------- 投影：球面方向 → 屏幕像素 ----------
   * 透视：sx = cx + (X/Z)·f      sy = cy − (Y/Z)·f
   * 立体：k = 2/(1+Z)；sx = cx + X·k·ppr，ppr 使 θ=fov/2 时正好落在短边半宽
   * 二者都只做"球→平面"，裁剪交给画布矩形（和原项目 window_size 一致）。
   */
  function dotF(v, b) { return v.x * b.f.x + v.y * b.f.y + v.z * b.f.z; }
  /* 纯投影，不做可见性判定。projectSegment 已经把端点裁到可见侧；
     若这里再判一次 Z，球面插值 + 归一化带来的微小回落会把**整段**丢掉
     （体检 B6：鱼眼边界的网格断口）。唯一必须挡的是立体投影背极点 Z=-1
     造成的除零；靠近它的点投影半径极大，本来就在窗口外，画布会裁掉。 */
  function projectUnchecked(v, W, H, b, Z) {
    const X = v.x * b.r.x + v.y * b.r.y + v.z * b.r.z;
    const Y = v.x * b.u.x + v.y * b.u.y + v.z * b.u.z;
    let k;
    if (V.fov <= PERSP_MAX_FOV) {
      k = focalOf(W, H) / Z;
    } else {
      // 靠近背极点时把分母钳到下限，而不是丢掉整段：这类点投影半径极大，
      // 本来就在窗口外，画布矩形裁剪会处理；丢段才会留下可见的断口。
      const denom = Math.max(1e-6, 1 + Z);
      k = 2 / denom * ((Math.min(W, H) / 2) / (2 * Math.tan(V.fov * D2R / 4)));
    }
    return { x: W / 2 + X * k, y: H / 2 - Y * k, z: Z };
  }
  function toScreen(v, W, H) {
    const b = getBasis();
    const Z = dotF(v, b);
    if (Z <= zCut()) return null;
    return projectUnchecked(v, W, H, b, Z);
  }
  function projectAltAz(azDeg, altDeg, W, H) {
    return toScreen(altAzVector(azDeg, altDeg), W, H);
  }
  /* 线段裁剪：把 a→b 截到可见阈值平面上，返回 [pa, pb] 或 null。
     没有这一步，跨越相机平面（或立体投影背极点）的线会在屏幕上拉出横穿全屏的假线。 */
  function projectSegment(a, b, W, H) {
    const bs = getBasis();
    const zc = zCut();
    const za = a.x * bs.f.x + a.y * bs.f.y + a.z * bs.f.z;
    const zb = b.x * bs.f.x + b.y * bs.f.y + b.z * bs.f.z;
    if (za <= zc && zb <= zc) return null;
    // 插值到「阈值 + 余量」而不是阈值本身：端点是球面单位向量，插值后
    // 归一化会让 Z 略微回落，若正好落在 zc 上，toScreen 的二次 Z 判定会
    // 把它判成不可见 → 整段被丢，鱼眼边界出现断口（体检 B6）。
    const zSafe = zc + 1e-6;
    let A = a, B = b;
    if (za <= zc) {
      const t = (zSafe - za) / (zb - za);
      A = norm({ x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t, z: a.z + (b.z - a.z) * t });
    } else if (zb <= zc) {
      const t = (zSafe - zb) / (za - zb);
      B = norm({ x: b.x + (a.x - b.x) * t, y: b.y + (a.y - b.y) * t, z: b.z + (a.z - b.z) * t });
    }
    const pa = projectUnchecked(A, W, H, bs, dotF(A, bs)),
          pb = projectUnchecked(B, W, H, bs, dotF(B, bs));
    if (!pa || !pb) return null;
    return [pa, pb];
  }
  function norm(v) {
    const l = Math.hypot(v.x, v.y, v.z) || 1;
    return { x: v.x / l, y: v.y / l, z: v.z / l };
  }

  /* 屏幕 → 天球方向（grab-the-sky / zoom-to-cursor 反投影） */
  function unproject(px, py, W, H) {
    const b = getBasis();
    const Xp = px - W / 2, Yp = -(py - H / 2);
    let vx, vy, vz;
    if (V.fov <= PERSP_MAX_FOV) {
      const f = focalOf(W, H);
      vx = b.f.x + b.r.x * (Xp / f) + b.u.x * (Yp / f);
      vy = b.f.y + b.r.y * (Xp / f) + b.u.y * (Yp / f);
      vz = b.f.z + b.r.z * (Xp / f) + b.u.z * (Yp / f);
    } else {
      const ppr = (Math.min(W, H) / 2) / (2 * Math.tan(V.fov * D2R / 4));
      const ax = Xp / ppr, ay = Yp / ppr;
      const rho = Math.hypot(ax, ay);
      if (rho < 1e-9) return dirAltAz(b.f);
      const th = 2 * Math.atan(rho / 2), c = Math.cos(th), s = Math.sin(th);
      const ux = ax / rho, uy = ay / rho;
      vx = b.f.x * c + (b.r.x * ux + b.u.x * uy) * s;
      vy = b.f.y * c + (b.r.y * ux + b.u.y * uy) * s;
      vz = b.f.z * c + (b.r.z * ux + b.u.z * uy) * s;
    }
    return dirAltAz(norm({ x: vx, y: vy, z: vz }));
  }

  /* ---------- 太阳位置（低精度解析式，Meeus 简化：±0.5°） ----------
   * 上一版用 sin(localHour) 造了一个假太阳高度，属于"编数据"，撤掉。
   * 这里给的是真几何：平黄经 → 黄赤交角 → 赤经赤纬 → 地平。
   * 精度够判昼夜/晨昏蒙影；不用于日食、日出时刻表（not_verified）。
   */
  let _sunCache = { jd: NaN, val: null };
  function sunAltAz(state) {
    const jd = nowJd(state);
    // 同一模拟时刻：天空底色与 HUD 会各要一次，命中即复用（体检 P4）。
    if (_sunCache.val && Math.abs(jd - _sunCache.jd) < 1e-12) return _sunCache.val;
    const n = jd - JD_J2000;
    const L = (280.460 + 0.9856474 * n) % 360;
    const g = ((357.528 + 0.9856003 * n) % 360) * D2R;
    const lam = (L + 1.915 * Math.sin(g) + 0.020 * Math.sin(2 * g)) * D2R;
    const eps = (23.439 - 0.0000004 * n) * D2R;
    const ra = Math.atan2(Math.cos(eps) * Math.sin(lam), Math.cos(lam));
    const dec = Math.asin(Math.sin(eps) * Math.sin(lam));
    // 太阳的 RA/Dec 是 of-date（用当年黄赤交角解出），不能当 J2000 再岁差一次。
    const v = dateToHorizon(Math.cos(ra), Math.sin(ra), Math.sin(dec), Math.cos(dec), frame(state));
    const d = dirAltAz(v);
    const val = { az: d.az, alt: d.alt,
                  ra: ((ra * R2D) % 360 + 360) % 360, dec: dec * R2D };
    _sunCache = { jd, val };
    return val;
  }

  /* ---------- 可见判据 ---------- */
  /* 消光常量与内核 stars.py 同口径（k=0.2 晴朗海平面、Δm 上限 3.5、仰角下限 0.5°）。 */
  const LM = { kExt: 0.2, maxExt: 3.5 };
  /* 极限星等 = 内核事实源（14-astro-frame.limitMagnitude，逐式镜像
     skyengine.stars.naked_eye_limit_mag + star_catalog.detail_gain_mag/limit_magnitude，
     跨语言钉扎 build/gen_limit_mag_golden.py + tests/limit_mag_selftest.js）。
     Bortle 裸眼基线 6.5-0.35·max(0,b-3)（Stellarium 同语义）+ FOV 渲染 LOD 增益
     clamp(0.5·log2(30/fov),0,1.5)：广角（fov>=30）增益为 0、保持裸眼基线，
     窄视场才逐层露暗星。旧版自研 1.85·log10(60/fov) 在 fov>60 时变负、凭空压低
     极限（广角比裸眼还少星），窄视场增益又过猛——现已统一到内核口径。
     前端随仓 HYG 截断 MAG_CAP（=CAT.meta.magMax，7.5 等）作为 depthCap 传入；
     内核默认 cap 是其全星表深度 9.0。这不是望远镜口径增益，也不是完整天空亮度
     光度学模型（月相/晨昏/实际天光表面亮度仍未建模，内核 limitations L24）。 */
  function limitMag(fovDeg, bortle) {
    return ASTRO.limitMagnitude(fovDeg, bortle, MAG_CAP);
  }
  /* sin(asin(z)) === z：eachStar 已经算出了单位向量的 z 分量（= sin alt），
     直接传进来即可，不必 asin 求角度再 sin 回去（体检 P1）。 */
  function extinctionFromSin(sinAlt) {
    if (sinAlt >= 1) return 0;
    return Math.min(LM.maxExt, LM.kExt / Math.max(Math.sin(0.5 * D2R), sinAlt));
  }
  function extinction(altDeg) {
    return extinctionFromSin(Math.sin(altDeg * D2R));
  }
  /* 入场暗适应淡入是 Web 呈现层（不是内核）：intro 0→1 时显示门限由 2 等
     放开到当前 FOV 极限星等，星按真实星等依次浮现。 */
  function effectiveLimit(fovDeg, bortle) {
    const L = limitMag(fovDeg, bortle);
    return 2.0 + (L - 2.0) * V.intro;
  }
  function daylightHidden(sunAltDeg) { return sunAltDeg > -0.833; }
  function starStyle(mag, bv, L, sinAlt) {
    const eff = mag + extinctionFromSin(sinAlt);
    if (eff > L) return null;
    const depth = L - eff;
    const r = clamp(0.60 + depth * 0.55, 0.55, 4.6);
    const a = clamp(0.42 + depth * 0.15, 0.36, 1);
    const warm = clamp((bv - 0.55) * 0.16, -0.09, 0.09);
    return { r, a, warm };
  }

  /* ---------- 批量绘制：每帧只算一次基与焦距 ---------- */
  function eachStar(state, W, H, cb) {
    const f = frame(state), L = effectiveLimit(V.fov, V.bortle);
    const b = getBasis();
    const fx = b.f.x, fy = b.f.y, fz = b.f.z,
          rx = b.r.x, ry = b.r.y, rz = b.r.z,
          ux = b.u.x, uy = b.u.y, uz = b.u.z;
    const persp = V.fov <= PERSP_MAX_FOV;
    const zc = persp ? NEAR : STEREO_POLE;
    const kBase = persp ? focalOf(W, H)
                        : (Math.min(W, H) / 2) / (2 * Math.tan(V.fov * D2R / 4));
    const cx0 = W / 2, cy0 = H / 2;
    /* 视场早筛：Z = cos(与视轴夹角)。屏幕矩形外接圆半径对应的最小 Z，
       小于它的星连 X/Y/k 都不用算。窄视场可剔除 95%+ 的 25,792 颗，
       广角时阈值自然落到背极点附近，等于不启作用（数学上不误删）。 */
    const maxR = Math.hypot(W / 2, H / 2) + 8;
    let zViewMin;
    if (persp) {
      const q = maxR / kBase;
      zViewMin = 1 / Math.sqrt(1 + q * q);
    } else {
      const q = maxR / (2 * kBase);
      zViewMin = (1 - q * q) / (1 + q * q);
    }
    if (zViewMin < zc) zViewMin = zc;
    const horizonSin = Math.sin(-1.2 * D2R);
    const m = f.m;
    const m0 = m[0], m1 = m[1], m2 = m[2], m3 = m[3], m4 = m[4],
          m5 = m[5], m6 = m[6], m7 = m[7], m8 = m[8];
    let shown = 0;
    for (let j = 0; j < N; j++) {
      const i = ORDER[j];
      if (MAG[i] > L) break;          // 消光只会更暗，后面全不可见
      /* 与 eqToHorizon 同一条总矩阵链（J2000→岁差→章动→LST→地平），
         这里内联展开避免 25,792 次函数调用。上一版内联的是旧 LST 展开式，
         绕过岁差/章动 —— 由 verify_product.py 的 [astro] 守卫钉死。 */
      const x = VX[i], y = VY[i], z = VZ[i];
      const vx = m0 * x + m1 * y + m2 * z;
      const vy = m3 * x + m4 * y + m5 * z;
      const vz = m6 * x + m7 * y + m8 * z;
      const Z = vx * fx + vy * fy + vz * fz;
      if (Z <= zc || Z < zViewMin) continue;
      const X = vx * rx + vy * ry + vz * rz, Y = vx * ux + vy * uy + vz * uz;
      const k = persp ? kBase / Z : kBase * 2 / (1 + Z);
      const sx = cx0 + X * k, sy = cy0 - Y * k;
      if (sx < -8 || sx > W + 8 || sy < -8 || sy > H + 8) continue;   // 窗口矩形裁剪
      const sinAlt = clamp(vz, -1, 1);
      if (sinAlt < horizonSin) continue;                               // 地平门
      const st = starStyle(MAG[i], BV[i], L, sinAlt);
      if (!st) continue;
      cb(sx, sy, st, MAG[i], BV[i], Math.asin(sinAlt) * R2D, i);
      shown++;
    }
    return { shown, L };
  }
  function namedScreen(state, W, H) {
    const f = frame(state), out = {};
    for (const s of NAMED) {
      const v = eqToHorizon(s.cosRA, s.sinRA, s.sinDec, s.cosDec, f);
      const p = toScreen(v, W, H);
      if (p && v.z > -0.02) out[s.name] = { p, alt: Math.asin(clamp(v.z, -1, 1)) * R2D, s };
    }
    return out;
  }
  /* 搜索锁定用：命名星的当前历元地平向量（与星点走同一套自行推进后的三角表）。 */
  function namedVector(name, state) {
    const s = NAMED.find(x => x.name === name);
    if (!s) return null;
    const f = frame(state);   // 先推进历元再读三角表：参数左→右求值会先读到旧历元值
    return eqToHorizon(s.cosRA, s.sinRA, s.sinDec, s.cosDec, f);
  }
  function dsoScreen(state, W, H, L) {
    const f = frame(state), out = [];
    for (const e of DSO_T) {
      const d = e.d;
      const v = eqToHorizon(e.cRA, e.sRA, e.sD, e.cD, f);
      const p = toScreen(v, W, H);
      if (!p || v.z < -0.02) continue;
      const alt = Math.asin(clamp(v.z, -1, 1)) * R2D;
      // 仓库 dso_catalog.json 允许星等/角径为 null（源目录缺数据）。
      // 这种情况下**不参与亮度门**（不知道亮度就不能按亮度筛），
      // 只在前端画一个类型符号；绝不拿 0/99 冒充实测值。
      if (d.m != null && d.m + extinction(alt) > L + 1.5) continue;
      out.push({ p, alt, d });
    }
    return out;
  }

  /* ---------- 每帧推进：只有视角/FOV/入场状态在变，星位不动 ---------- */
  function step(dt, state) {
    const now = (typeof performance !== "undefined" && performance.now)
      ? performance.now() : Date.now();
    const t = clamp((now - _bornAt) / INTRO_MS, 0, 1);
    V.intro = t;
    // 入场：视场从 160° 收拢到 100°（视野收束，不是星在飞）。
    // 只在用户/夹具还没改过 fovTarget 时覆盖，避免把外部设定顶掉。
    if (t > 0 && t < 1 && V.fovTarget === V.introFov) V.fovTarget = 100;
    const e = 1 - Math.pow(0.0025, Math.min(dt, 60));
    V.fov += (V.fovTarget - V.fov) * e;
    let dAz = V.azTarget - V.az;
    if (dAz > 180) dAz -= 360; else if (dAz < -180) dAz += 360;
    V.az = ((V.az + dAz * e) % 360 + 360) % 360;
    V.alt = clamp(V.alt + (V.altTarget - V.alt) * e, -25, 89);
  }
  function zoom(factor, px, py, W, H) {
    const before = unproject(px, py, W, H);
    V.fovTarget = clamp(V.fovTarget * factor, V.minFov, V.maxFov);
    V.fov = V.fovTarget;
    const after = unproject(px, py, W, H);
    let dAz = before.az - after.az;
    if (dAz > 180) dAz -= 360; else if (dAz < -180) dAz += 360;
    V.azTarget = V.az + dAz;
    V.altTarget = clamp(V.alt + (before.alt - after.alt), -25, 89);
    V.az = ((V.azTarget % 360) + 360) % 360; V.alt = V.altTarget;
  }
  function panTo(az, alt) { V.azTarget = az; V.altTarget = clamp(alt, -25, 89); }

  MB.celestial = {
    V, CAT, N, MAG_CAP, LM, TIME, NEAR, PERSP_MAX_FOV, astro: ASTRO,
    simUnix, nowJd, gmstDeg, lstDeg, frame, eqToHorizon, dateToHorizon, dirAltAz,
    altAzVector, basis, getBasis, projMode, focalOf,
    toScreen, projectAltAz, projectSegment, unproject, sunAltAz,
    limitMag, effectiveLimit, extinction, starStyle, daylightHidden,
    eachStar, namedScreen, namedVector, dsoScreen,
    step, zoom, panTo,
    hud(state) {
      const jd = nowJd(state), jdDate = new Date((jd - JD_UNIX_EPOCH) * 86400000);
      const mode = projMode();
      return {
        jd, unix: simUnix(state),
        utc: jdDate.toISOString().slice(0, 19).replace("T", " "),
        lst: lstDeg(jd, state.site.lon || 0),
        fov: V.fov, az: V.az, alt: V.alt,
        limit: effectiveLimit(V.fov, V.bortle),
        kernel: TIME.kernelAttached,
        starCount: N, magCap: MAG_CAP,
        projMode: mode,
        projection: mode === "perspective" ? "Perspective (pinhole)" : "Stereographic (2 tan θ/2)",
        sunAlt: sunAltAz(state).alt
      };
    }
  };
})(window);
