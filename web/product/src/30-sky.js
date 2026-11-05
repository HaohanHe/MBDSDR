/* MBDSDR 产品前端 · 30-sky.js
 * 上层：星象仪模式（时空主导）——全天球 + 摄像机语义 + 信息卡。
 *
 * 【本轮重做（2026-11-04）】
 *  撤掉上一版的「圆形视场盘 + 圆边框 + 屏幕径向渐变」——那是把天空塞进一个
 *  球里，不是天圆地圆。改成照 Stellarium Web Engine 的真实链路：
 *    - 相机在球心，投影铺满**整个屏幕矩形**，裁剪只靠窗口边界；
 *    - 小 FOV 用针孔透视（地平线就是直线，那是大圆在相机里的正确像），
 *      大 FOV 用立体投影（鱼眼，地平线弯成大圆弧）；
 *    - 地景绕 **360° 方位**铺满地平以下，转身看背后也在；不随时间转。
 *
 * 【用户铁律 / 科学口径】
 *  - 星星本来就在全天球固定位置：**不飞散、不平移、无独立角速度**。
 *    数据来自 HYG v4.1 全天星表（25,792 颗）+ 仓库 141 命名亮星。
 *  - 动的只有：视角（az/alt）、视场角（fov）、时间带来的天球刚性旋转（LST）。
 *  - 昼夜用真实太阳高度（低精度解析式），不是假正弦。
 *  - 摄像机锁定后卫星相对画面静止、天球在背后动；定轨在此层零入口。
 *  - 银河：无数据·不提供；地景：示意轮廓（无实时全景数据），都如实标。
 *
 * 数学与坐标链全部在 31-celestial.js；本文件只负责绘制与交互。
 */
(function () {
  "use strict";
  const MB = window.MBDSDR, S = MB.state, C = MB.celestial;
  const $ = MB.$, t = (k, v) => MB.i18n.t(k, v);
  const clamp = MB.math.clamp;

  const cv = $("#skyCanvas"), cx = cv.getContext("2d");
  const view = { w: 0, h: 0, dpr: Math.min(2, window.devicePixelRatio || 1) };
  let frameT = 0, dragState = null, dragMoved = false;
  const STARS = (C.CAT && C.CAT.stars) || [];

  /* ---------- 卫星天球轨迹（真实 SGP4，不是示意折线） ----------
   * 事实源：build/sync_sat_tracks.py 从仓库 _BUILTIN_TLES 用 sgp4 传播成
   * TEME 地心 RA/Dec，按 90s 采样。前端与恒星共用同一套 eqToHorizon →
   * 投影链，所以「天动星静」与卫星自身轨道运动同时成立。
   *
   * 诚实口径：这是**地心方向**，未做 TEME→J2000 岁差/章动、未做站址视差；
   * 站址 trusted=false，绝不据此给升落 / 过境结论。无数据或模拟时刻跑出
   * 采样窗口时返回 null —— 不画点、不伪造指向（not_verified 见 HUD）。
   */
  const TRACKS = (MB.SATTRACKS && MB.SATTRACKS.tracks) || null;
  const TMETA = (MB.SATTRACKS && MB.SATTRACKS.meta) || null;
  function trackOf(s) { return TRACKS ? TRACKS[String(s.norad)] : null; }
  function wrapDelta(a, b) { return ((b - a + 540) % 360 + 360) % 360 - 180; }
  function satRaDec(s, unix) {
    const tr = trackOf(s);
    if (!tr || !TMETA) return null;
    const k = (unix - TMETA.epochUnix) / TMETA.stepSec + TMETA.halfSteps;
    if (!isFinite(k) || k < 0 || k >= TMETA.count - 1) return null;
    const i = Math.floor(k), f = k - i;
    const ra0 = tr[i * 2] / 100, de0 = tr[i * 2 + 1] / 100;
    const ra1 = tr[(i + 1) * 2] / 100, de1 = tr[(i + 1) * 2 + 1] / 100;
    return { ra: (ra0 + wrapDelta(ra0, ra1) * f + 360) % 360,
             dec: de0 + (de1 - de0) * f };
  }
  function satVector(s, unix, fr) {
    const rd = satRaDec(s, unix);
    if (!rd) return null;
    const ra = rd.ra * Math.PI / 180, dec = rd.dec * Math.PI / 180;
    return C.eqToHorizon(Math.cos(ra), Math.sin(ra), Math.sin(dec), Math.cos(dec), fr);
  }
  function satAltAz(s) {
    const v = satVector(s, C.simUnix(S), C.frame(S));
    return v ? C.dirAltAz(v) : null;      // {az, alt} 度，或诚实 null
  }
  function satScreen(s, W, H) {
    const v = satVector(s, C.simUnix(S), C.frame(S));
    // 地平以下不画：地景在后处理里铺满，卫星不应透过地面显示
    return (v && v.z > -0.02) ? C.toScreen(v, W, H) : null;
  }
  /* 球面折线：相邻样本用 projectSegment 裁剪，鱼眼边缘不会拉出横穿全屏的假线。 */
  function drawTracks(W, H, sky) {
    if (!TRACKS || !TMETA || sky.orbits === false) return;
    const unix = C.simUnix(S), fr = C.frame(S);
    const center = Math.floor((unix - TMETA.epochUnix) / TMETA.stepSec + TMETA.halfSteps);
    const half = 34;                       // 前后各约 51 分钟
    cx.save(); cx.lineWidth = 1; cx.setLineDash([3, 4]);
    for (const s of MB.SATS) {
      const tr = trackOf(s);
      if (!tr) continue;
      cx.strokeStyle = S.cam.locked === s
        ? "rgba(255,255,255,.55)" : "rgba(255,255,255,.16)";
      let prev = null;
      const i0 = Math.max(0, center - half), i1 = Math.min(TMETA.count - 1, center + half);
      for (let i = i0; i <= i1; i++) {
        const ra = tr[i * 2] / 100 * Math.PI / 180,
              dec = tr[i * 2 + 1] / 100 * Math.PI / 180;
        const v = C.eqToHorizon(Math.cos(ra), Math.sin(ra),
                                Math.sin(dec), Math.cos(dec), fr);
        if (prev && (prev.z > -0.02 || v.z > -0.02)) {
          const seg = C.projectSegment(prev, v, W, H);
          if (seg) {
            cx.beginPath(); cx.moveTo(seg[0].x, seg[0].y);
            cx.lineTo(seg[1].x, seg[1].y); cx.stroke();
          }
        }
        prev = v;
      }
    }
    cx.restore();
  }

  function resize() {
    view.w = cv.clientWidth; view.h = cv.clientHeight;
    cv.width = Math.max(1, view.w * view.dpr);
    cv.height = Math.max(1, view.h * view.dpr);
    cx.setTransform(view.dpr, 0, 0, view.dpr, 0, 0);
  }
  window.addEventListener("resize", resize);

  /* ---------- 地平轮廓：方位高度场（示意，不是实测地形） ----------
   * 逐列扫描会调用它几万次/帧，改为 0.5° 查找表 + 线性插值：
   * 轮廓本身是 ≤13 次谐波的低频曲线，插值误差远小于 1 像素（体检 P2）。 */
  const HILL_N = 720;
  const HILL = new Float32Array(HILL_N + 1);
  for (let i = 0; i <= HILL_N; i++) {
    const a = (i * 360 / HILL_N) * Math.PI / 180;
    HILL[i] = 1.8 + 1.1 * Math.sin(a * 3 + 0.6) + .8 * Math.sin(a * 7 + 2.1)
            + .45 * Math.sin(a * 13 + 4.2);
  }
  function hillAlt(az) {
    const u = (((az % 360) + 360) % 360) * (HILL_N / 360);
    const i = u | 0, f = u - i;
    return HILL[i] * (1 - f) + HILL[i + 1] * f;
  }

  /* 赤道网格的三角值是常量，只算一次（每帧原本 546 点 × 2 次）。 */
  const EQ_TRIG = [], EQ_DEC_TRIG = [];
  for (let ra = 0; ra <= 360; ra += 4) {
    const rd = ra * Math.PI / 180;
    EQ_TRIG.push([Math.cos(rd), Math.sin(rd)]);
  }
  for (let dec = -60; dec <= 60; dec += 30) {
    const dd = dec * Math.PI / 180;
    EQ_DEC_TRIG.push([Math.sin(dd), Math.cos(dd)]);
  }
  /* 方位窗口：fov<160 时只画视野附近的方位，省 2/3 的投影开销。
     立体投影下四角能看很远，所以窗口给到 fov*1.05 + 20°。 */
  function azVisible(az) {
    if (C.V.fov >= 155) return true;
    let d = Math.abs(((az - C.V.az + 540) % 360) - 180);
    return d < C.V.fov * 1.05 + 20;
  }

  /* 球面环采样（返回世界方向向量，闭合） */
  function ring(altFn, stepDeg) {
    const pts = [], st = stepDeg || 2;
    for (let az = 0; az <= 360; az += st) pts.push(C.altAzVector(az, altFn ? altFn(az) : 0));
    return pts;
  }
  /* 沿球面环描边：逐段过 Z=NEAR 裁剪，绝不让线段横穿画布 */
  function strokeRing(pts, W, H) {
    let any = false;
    cx.beginPath();
    for (let i = 0; i < pts.length - 1; i++) {
      const seg = C.projectSegment(pts[i], pts[i + 1], W, H);
      if (!seg) continue;
      cx.moveTo(seg[0].x, seg[0].y); cx.lineTo(seg[1].x, seg[1].y);
      any = true;
    }
    if (any) cx.stroke();
    return any;
  }
  /* 地景：逐屏幕列求**天空区间**（上沿 top / 下沿 bot），再整体填「天空以外」。
   *
   * 走过的两条错路，都留在这当路标：
   *  ① 按「方位×高度分块」拼多边形 → 地平以下的球冠跨过相机平面（天底 Z≈0），
   *    块被裁掉就在画面上留阶梯空洞；
   *  ② 逐列只找一个上沿「之上是天、之下是地」 → 相机朝天顶时地平线在屏幕上
   *    是一个**闭合大圆**（中间是天、四周是地），一列会是「地-天-地」两段，
   *    只找上沿就把整列判成地，画面被拉出竖直硬边（用户 11-04 23:01 截图实证）。
   * 现在求完整天空区间：一列最多两段（进入天空 top、离开天空 bot），
   * 用 rect(全屏) + 天空环 + evenodd 填充 = 「全屏异或天空」= 地面。
   */
  const EDGE_STEP = 3;      // 屏幕列步长（px）
  const EDGE_SCAN = 10;     // 回退细扫步长（px），正常路径不用
  const COARSE = 12;        // 两阶段采样：每列 13 个判据点
  let _edgeCalls = 0;
  function altMinusHill(px, py, W, H) {
    _edgeCalls++;                 // 真实单帧反投影次数，供 perf 回归门读取
    const d = C.unproject(px, py, W, H);
    return d.alt - hillAlt(d.az);
  }
  /* 整屏预判：省掉抬头/俯视时的大段扫描 */
  function allSkyOrAllGround(W, H) {
    let sky = 0, ground = 0;
    for (let k = 0; k <= 8; k++) {
      const x = W * k / 8;
      if (altMinusHill(x, 1, W, H) > 0) sky++; else ground++;
      if (altMinusHill(x, H - 1, W, H) > 0) sky++; else ground++;
    }
    if (sky === 18) return "sky";
    if (ground === 18) return "ground";
    return null;
  }
  function bisectAlt(x, y0, y1, W, H, skyAbove) {
    let lo = y0, hi = y1;
    for (let k = 0; k < 7; k++) {
      const mid = (lo + hi) / 2;
      if ((altMinusHill(x, mid, W, H) > 0) === skyAbove) lo = mid; else hi = mid;
    }
    return (lo + hi) / 2;
  }
  /* 边界只在视角/FOV/画布变化时重算：静止时命中缓存，拖动时每帧一次 */
  let _edgeCache = { key: "", val: null };
  function landscapeEdge(W, H) {
    const key = W + "x" + H + "|" + C.V.az.toFixed(2) + "|" +
                C.V.alt.toFixed(2) + "|" + C.V.fov.toFixed(2);
    if (_edgeCache.key === key && _edgeCache.val) return _edgeCache.val;
    _edgeCalls = 0;               // 只统计「缓存失效的全量重算」这一帧
    const st = EDGE_STEP, n = Math.floor(W / st) + 1;
    const top = new Float32Array(n), bot = new Float32Array(n);
    const pre = allSkyOrAllGround(W, H);
    if (pre === "sky") { top.fill(0); bot.fill(H); }
    else if (pre === "ground") { top.fill(H); bot.fill(H); }
    else {
      const ys = new Float32Array(COARSE + 1);
      const flags = new Uint8Array(COARSE + 1);
      for (let i = 0; i < n; i++) {
        const x = Math.min(W, i * st);
        /* 阶段一：13 点判「这一列有几段天空」。
           绝大多数列 alt(y) 单调 → 0 或 1 次翻转；抬头广角时是
           「地-天-地」→ 2 次翻转。两者都能用分段二分精确定位。 */
        for (let k = 0; k <= COARSE; k++) {
          ys[k] = H * k / COARSE;
          flags[k] = altMinusHill(x, ys[k], W, H) > 0 ? 1 : 0;
        }
        let flips = 0;
        for (let k = 1; k <= COARSE; k++) if (flags[k] !== flags[k - 1]) flips++;
        let tp = -1, bt = -1;
        if (flips <= 2) {
          if (flags[0]) tp = 0;
          for (let k = 1; k <= COARSE; k++) {
            if (flags[k] === flags[k - 1]) continue;
            const cut = bisectAlt(x, ys[k - 1], ys[k], W, H, flags[k - 1] === 1);
            if (flags[k] === 1) tp = cut;   // 地→天：天空上沿
            else bt = cut;                  // 天→地：天空下沿
          }
          if (tp >= 0 && bt < 0) bt = H;    // 一路到底都是天
          if (tp < 0) { tp = H; bt = H; }   // 整列无天
        } else {
          // 极少数复杂结构：回退逐点细扫，保持原语义不猜
          let prevSky = flags[0] === 1, prevY = 0;
          if (prevSky) tp = 0;
          for (let y = EDGE_SCAN; y <= H; y += EDGE_SCAN) {
            const sky = altMinusHill(x, y, W, H) > 0;
            if (sky !== prevSky) {
              const cut = bisectAlt(x, prevY, y, W, H, prevSky);
              if (sky) tp = cut;
              else { bt = cut; if (tp >= 0) break; }
            }
            prevSky = sky; prevY = y;
          }
          if (tp >= 0 && bt < 0) bt = H;
          if (tp < 0) { tp = H; bt = H; }
        }
        top[i] = tp; bot[i] = bt;
      }
    }
    const val = { top, bot, st, n };
    _edgeCache = { key, val };
    return val;
  }
  function fillLandscape(W, H) {
    const edge = landscapeEdge(W, H);
    const { top, bot, st, n } = edge;
    let ymin = H;
    for (let i = 0; i < n; i++) if (top[i] < ymin) ymin = top[i];
    // 极轻的纵向渐变：剪影贴地平处稍亮、往下沉到纯黑，
    // 给「地圆」一点纵深；不做照片式纹理（无数据不冒充实拍）。
    const g = cx.createLinearGradient(0, ymin, 0, H);
    g.addColorStop(0, "#0a0e16");
    g.addColorStop(.28, "#04060b");
    g.addColorStop(1, "#000000");
    cx.beginPath();
    cx.rect(0, 0, W, H);                       // 全屏
    cx.moveTo(0, top[0]);                      // 天空区间的上沿（左→右）
    for (let i = 1; i < n; i++) cx.lineTo(Math.min(W, i * st), top[i]);
    for (let i = n - 1; i >= 0; i--) {         // 下沿（右→左）
      cx.lineTo(Math.min(W, i * st), bot[i]);
    }
    cx.closePath();
    cx.fillStyle = g;
    cx.fill("evenodd");                        // 全屏 ⊕ 天空 = 地面
    return edge;
  }
  /* 天际线描边：只在「真正穿越屏幕」的列之间连线，屏外列自然断段 */
  function strokeEdgeLine(ys, st, W, H) {
    cx.beginPath();
    let started = false;
    for (let i = 0; i < ys.length; i++) {
      const y = ys[i];
      if (y <= 0.5 || y >= H - 0.5) { started = false; continue; }
      const x = Math.min(W, i * st);
      if (started) cx.lineTo(x, y); else { cx.moveTo(x, y); started = true; }
    }
    cx.stroke();
  }
  /* ---------- 每帧 ---------- */
  function draw(dt) {
    const sky = S.sky, W = view.w, H = view.h;
    if (!W || !H) return;
    frameT += dt;
    if (S.form === "sky") {
      S.time.angle += dt * 0.000008 * Math.min(S.time.rate, 60);
      S.time.minutes += dt / 60000 * S.time.rate;
    }
    C.step(dt, S);
    /* 锁星跟随并入主循环（原实现另起一条 rAF 链，且每帧把镜头钉在
       同一个**静态假方位**上）。现在卫星按真实 SGP4 轨迹随模拟时刻移动，
       镜头跟它转、天球在背后刚性旋转 —— 摄像机语义才真正成立。 */
    if (S.cam.locked) {
      const a = satAltAz(S.cam.locked);
      if (a) C.panTo(a.az, a.alt);
    }

    const sun = C.sunAltAz(S);
    const sunAlt = sun.alt;
    const day = clamp(sunAlt / 12, 0, 1);              // 0=夜 1=白天
    const twilight = clamp(1 - Math.abs(sunAlt) / 16, 0, 1);
    const hidden = C.daylightHidden(sunAlt);

    cx.save();
    cx.fillStyle = "#000"; cx.fillRect(0, 0, W, H);

    /* --- 天空底色：天顶暗、地平亮。沿屏幕纵轴做渐变（滚转为 0，纵向≈高度角），
       不是以屏幕中心为圆心的径向渐变——那个才是「看进一个球」的元凶。 --- */
    const edge = sky.landscape ? landscapeEdge(W, H) : null;
    let hzY = null;
    if (edge) {
      const mid = [];
      for (let i = 0; i < edge.n; i++) {
        const y = edge.top[i];
        if (y > 0.5 && y < H - 0.5) mid.push(y);
      }
      if (mid.length) { mid.sort((a, b) => a - b); hzY = mid[Math.floor(mid.length / 2)]; }
    }
    if (hzY === null) hzY = C.V.alt >= 0 ? H : 0;
    {
      const top = clamp(hzY, -H, H * 2);
      const g = cx.createLinearGradient(0, top, 0, top - Math.max(H, 1) * 1.1);
      const lo = 10 + day * 96, mid2 = 7 + day * 78, hi = 4 + day * 52;
      g.addColorStop(0, `rgb(${lo},${lo + 4},${lo + 12})`);
      g.addColorStop(.45, `rgb(${mid2},${mid2 + 3},${mid2 + 10})`);
      g.addColorStop(1, `rgb(${hi},${hi + 2},${hi + 7})`);
      cx.fillStyle = g;
      cx.fillRect(0, 0, W, H);
    }

    /* --- 坐标网格：球面等高圈 / 方位子午线，都是三维里的曲线 --- */
    if (sky.grid) {
      cx.strokeStyle = "rgba(255,255,255,.085)"; cx.lineWidth = 1;
      for (let alt = 15; alt <= 75; alt += 15) strokeRing(ring(() => alt, 3), W, H);
      for (let az = 0; az < 360; az += 30) {
        const pts = [];
        for (let alt = -5; alt <= 88; alt += 3) pts.push(C.altAzVector(az, alt));
        strokeRing(pts, W, H);
      }
    }
    if (sky.eqgrid) {
      cx.strokeStyle = "rgba(255,255,255,.075)"; cx.lineWidth = 1;
      const fr = C.frame(S);
      for (const [sD, cD] of EQ_DEC_TRIG) {
        const pts = [];
        for (const [cRA, sRA] of EQ_TRIG)
          pts.push(C.eqToHorizon(cRA, sRA, sD, cD, fr));
        strokeRing(pts, W, H);
      }
      cx.strokeStyle = "rgba(255,255,255,.12)";
      const eq = [];
      for (const [cRA, sRA] of EQ_TRIG) eq.push(C.eqToHorizon(cRA, sRA, 0, 1, fr));
      strokeRing(eq, W, H);
    }

    /* --- 全天星：固定 J2000 星位，每帧仅按 LST 重投影 --- */
    const L = C.effectiveLimit(C.V.fov, C.V.bortle);
    let shownStars = 0;
    if (!hidden) {
      const info = C.eachStar(S, W, H, (x, y, st) => {
        cx.globalAlpha = st.a * (1 - .94 * day) * C.V.intro;
        const w = st.warm;
        const l = Math.round(clamp(214 + w * 255, 150, 255));
        cx.fillStyle = `rgb(${l},${l},${Math.min(255, l + (w < 0 ? 8 : 0))})`;
        cx.beginPath(); cx.arc(x, y, st.r, 0, Math.PI * 2); cx.fill();
        if (st.r > 2.6) {
          cx.globalAlpha *= .20;
          cx.beginPath(); cx.arc(x, y, st.r * 2.7, 0, Math.PI * 2); cx.fill();
        }
      });
      shownStars = info.shown;
      cx.globalAlpha = 1;
    } else {
      cx.globalAlpha = .62; cx.fillStyle = "#fff";
      cx.font = "13px MiSans, sans-serif"; cx.textAlign = "center";
      cx.fillText(t("sky.dayHidden"), W / 2, H / 2 - 8);
      cx.font = "11px MiSans, sans-serif";
      cx.fillText(t("sky.dayHint"), W / 2, H / 2 + 14);
      cx.textAlign = "left"; cx.globalAlpha = 1;
    }

    /* --- 星座连线 --- */
    const named = C.namedScreen(S, W, H);
    if (sky.constellations !== false && !hidden) {
      cx.strokeStyle = "rgba(255,255,255,.22)"; cx.lineWidth = 1;
      cx.setLineDash([2, 5]);
      for (const abbr in C.CAT.lines) {
        for (const [a, b] of C.CAT.lines[abbr]) {
          const pa = named[a], pb = named[b];
          if (!pa || !pb) continue;
          cx.beginPath(); cx.moveTo(pa.p.x, pa.p.y); cx.lineTo(pb.p.x, pb.p.y); cx.stroke();
        }
      }
      cx.setLineDash([]);
      if (C.V.fov >= 55 && sky.labels) {
        cx.font = "10px MiSans, sans-serif"; cx.fillStyle = "rgba(255,255,255,.42)";
        for (const abbr in C.CAT.lines) {
          const pts = C.CAT.lines[abbr].map(p => named[p[0]]).filter(Boolean);
          if (!pts.length) continue;
          const mx = pts.reduce((s, q) => s + q.p.x, 0) / pts.length;
          const my = pts.reduce((s, q) => s + q.p.y, 0) / pts.length;
          const info = C.CAT.constellations[abbr] || {};
          const label = info[S.locale === "zh-CN" ? "zh" : "en"] || abbr;
          cx.fillText(label, mx, my);
        }
      }
    }

    /* --- DSO：OpenNGC 派生目录的类型符号（形态/表面亮度不还原，L26） --- */
    if (sky.dso && !hidden) {
      for (const q of C.dsoScreen(S, W, H, L)) {
        const d = q.d, unknown = d.m == null || d.size == null;
        cx.strokeStyle = unknown ? "rgba(255,255,255,.28)" : "rgba(255,255,255,.45)";
        cx.lineWidth = 1;
        if (unknown) cx.setLineDash([2, 3]);   // 未知量 = 虚线符号，不冒充实测
        const rr = d.size == null ? 3 : clamp(d.size / 24, 2, 7);
        if (d.t === "galaxy") {
          const ar = d.axis == null ? .7 : clamp(d.axis, .25, 1);
          cx.beginPath(); cx.ellipse(q.p.x, q.p.y, rr * 1.6, rr * .7 * ar, 0, 0, 7); cx.stroke();
        } else if (d.t === "cluster" || d.t === "cluster_nebula") {
          cx.beginPath(); cx.arc(q.p.x, q.p.y, rr, 0, 7); cx.stroke();
        } else if (d.t === "double_star") {
          cx.beginPath(); cx.arc(q.p.x - 2, q.p.y, 1.4, 0, 7); cx.stroke();
          cx.beginPath(); cx.arc(q.p.x + 2, q.p.y, 1.4, 0, 7); cx.stroke();
        } else {
          cx.strokeRect(q.p.x - rr, q.p.y - rr, rr * 2, rr * 2);
        }
        cx.setLineDash([]);
        if (sky.labels && d.zh && d.m != null && d.m <= 6) {
          cx.font = "9px MiSans, sans-serif";
          cx.fillStyle = "rgba(255,255,255,.42)";
          cx.fillText(S.locale === "zh-CN" ? d.zh : (d.en || d.n), q.p.x + rr + 4, q.p.y + 3);
        }
      }
    }

    /* --- 中文星官标签 --- */
    if (sky.labels && !hidden) {
      cx.font = "10px MiSans, sans-serif";
      for (const name in named) {
        const q = named[name];
        if (q.s.mag > 2.0 || !q.s.zh) continue;
        cx.fillStyle = "rgba(255,255,255,.72)";
        cx.fillText(S.locale === "zh-CN" ? q.s.zh : name, q.p.x + 7, q.p.y - 5);
      }
    }

    /* --- 地平辉光：沿地平弧逐点铺小径向渐变（加法混合），
       所以它永远贴着那条曲线，无论视角怎么转、地平线是直是弯。 --- */
    if (sky.atmosphere) {
      const warm = twilight > .05;
      const rgb = warm ? [255, 118, 52] : [78, 118, 198];
      const amp = warm ? .10 + .20 * twilight + .14 * day : .035 + .17 * day;
      const rad = Math.min(W, H) * .22;
      cx.save(); cx.globalCompositeOperation = "lighter";
      for (let az = 0; az < 360; az += 8) {
        if (!azVisible(az)) continue;
        const p = C.projectAltAz(az, 0, W, H);
        if (!p) continue;
        if (p.x < -rad || p.x > W + rad || p.y < -rad || p.y > H + rad) continue;
        const g = cx.createRadialGradient(p.x, p.y, 0, p.x, p.y, rad);
        g.addColorStop(0, `rgba(${rgb[0]},${rgb[1]},${rgb[2]},${Math.min(.5, amp)})`);
        g.addColorStop(.45, `rgba(${rgb[0]},${rgb[1]},${rgb[2]},${Math.min(.5, amp) * .32})`);
        g.addColorStop(1, "rgba(0,0,0,0)");
        cx.fillStyle = g;
        cx.beginPath(); cx.arc(p.x, p.y, rad, 0, 7); cx.fill();
      }
      cx.restore();
      cx.globalAlpha = 1;
    }

    /* --- 地景：绕 360° 方位的地平高度场，铺满地平以下；随视角动，不随时间转 --- */
    if (edge) {
      cx.globalAlpha = C.V.fov < 6 ? Math.max(.35, C.V.fov / 6) : 1;
      fillLandscape(W, H);
      cx.globalAlpha = 1;
    }

    /* --- 地平大圆：球面几何，不是平面横条 --- */
    {
      cx.strokeStyle = "rgba(255,255,255,.20)"; cx.lineWidth = 1.2;
      strokeRing(ring(null, 2), W, H);
      if (edge) {
        cx.strokeStyle = "rgba(255,255,255,.13)"; cx.lineWidth = 1;
        strokeEdgeLine(edge.top, edge.st, W, H);
        strokeEdgeLine(edge.bot, edge.st, W, H);
      }
    }

    /* --- 方位字：锚在世界方位的地平大圆上 --- */
    if (sky.labels) {
      for (const [ch, az] of [["N", 0], ["E", 90], ["S", 180], ["W", 270]]) {
        const p = C.projectAltAz(az, 0, W, H);
        if (!p || p.x < 20 || p.x > W - 20 || p.y < 20 || p.y > H - 20) continue;
        cx.fillStyle = "#d2542c";
        cx.beginPath(); cx.arc(p.x, p.y + 11, 2.6, 0, 7); cx.fill();
        cx.font = "bold 16px MiSans, sans-serif"; cx.fillText(ch, p.x - 6, p.y - 4);
      }
    }

    /* --- SGP4 天球轨迹（真实轨道传播，不是示意折线） --- */
    drawTracks(W, H, sky);

    /* --- 卫星 --- */
    if (sky.sats) {
      for (const s of MB.SATS) {
        const p = satScreen(s, W, H);
        if (!p) { s._screen = null; continue; }
        const locked = S.cam.locked === s;
        const halo = cx.createRadialGradient(p.x, p.y, 0, p.x, p.y, 13);
        halo.addColorStop(0, "#ffffff" + (locked ? "bb" : "55"));
        halo.addColorStop(1, "#ffffff00");
        cx.fillStyle = halo; cx.beginPath(); cx.arc(p.x, p.y, 13, 0, 7); cx.fill();
        cx.strokeStyle = "#fff"; cx.lineWidth = 1; cx.globalAlpha = .55;
        cx.beginPath(); cx.arc(p.x, p.y, (s.size || 2) + 3.4, 0, 7); cx.stroke();
        cx.globalAlpha = 1; cx.fillStyle = "#fff";
        cx.beginPath(); cx.arc(p.x, p.y, (s.size || 2) + (locked ? 1.4 : 0), 0, 7); cx.fill();
        if (sky.labels) {
          cx.font = "11px MiSans, sans-serif";
          cx.fillStyle = locked ? "#fff" : "rgba(255,255,255,.6)";
          cx.fillText(s.id, p.x + 13, p.y + 3.5);
        }
        if (locked) {
          const breathe = Math.sin(frameT * Math.PI / 1000) * 1.2;
          cx.strokeStyle = "rgba(255,255,255,.85)"; cx.lineWidth = 1;
          cx.setLineDash([4, 4]);
          cx.beginPath(); cx.arc(p.x, p.y, 17 + breathe, 0, 7); cx.stroke();
          cx.setLineDash([]);
          cx.beginPath();
          cx.moveTo(p.x - 26, p.y); cx.lineTo(p.x - 20, p.y);
          cx.moveTo(p.x + 20, p.y); cx.lineTo(p.x + 26, p.y);
          cx.moveTo(p.x, p.y - 26); cx.lineTo(p.x, p.y - 20);
          cx.moveTo(p.x, p.y + 20); cx.lineTo(p.x, p.y + 26);
          cx.stroke();
        }
        s._screen = p;
      }
    }

    if (sky.night) {
      cx.fillStyle = "rgba(120,0,0,.42)"; cx.fillRect(0, 0, W, H);
      cx.fillStyle = "rgba(255,40,20,.10)"; cx.fillRect(0, 0, W, H);
    }
    cx.restore();

    /* --- 诚实空态：银河无数据 / 地景是示意图 --- */
    if (sky.hud) {
      cx.font = "10px MiSans, sans-serif"; cx.fillStyle = "rgba(255,255,255,.26)";
      cx.fillText(t("sky.galaxyNone"), 18, H - 116);
      if (sky.landscape) cx.fillText(t("sky.landscapeSketch"), 18, H - 100);
    }

    if (sky.hud && $("#skyHud")) renderHud(shownStars);
  }

  let _hudText = "";
  function renderHud(shownStars) {
    const h = C.hud(S);
    const lines = [
      t("hud.spacetime"),
      `${t("hud.utc")} ${h.utc} · ${t("hud.jd")} ${h.jd.toFixed(5)}`,
      `${t("hud.lst")} ${h.lst.toFixed(2)}° · ${t("hud.fov")} ${h.fov.toFixed(1)}°`,
      `${t("hud.view")} Az ${h.az.toFixed(1)}° / Alt ${h.alt.toFixed(1)}°`,
      `${t("hud.limit")} ${h.limit.toFixed(2)} ${t("hud.mag")} · ${t("hud.visible")} ${shownStars.toLocaleString()}`,
      `${t("hud.projection")} ${h.projection}`,
      `${t("hud.sunAlt")} ${h.sunAlt.toFixed(1)}°`,
      `${t("hud.catalog")} ${h.starCount.toLocaleString()} · ${t("hud.magCap")} ${h.magCap}`,
      `${t("hud.timeSource")} ${h.kernel ? t("hud.kernelLive") : t("hud.kernelOffline")}`,
      C.TIME.demoStart && !h.kernel ? t("hud.demoStart") : "",
      t("sky.galaxyNone"),
      t("hud.satTrack"),
      t("hud.limitNote")
    ].filter(Boolean);
    const text = lines.join("\n");
    if (text !== _hudText) { _hudText = text; $("#skyHud").textContent = text; }
  }

  /* ---------- 摄像机：锁定后天动星静 ---------- */
  function lockSat(s, textOnly) {
    if (!textOnly) {
      S.cam.locked = s;
      C.V.fovTarget = 26;
      const a = satAltAz(s);
      if (a) C.panTo(a.az, a.alt);      // 无轨迹数据不伪造指向
    }
    $("#scName").textContent = t("card.norad") + " " + s.norad;
    $("#scSub").textContent = t(s.typeKey) + " · " + s.id;
    $("#scAlias").innerHTML = t("card.alias") + " " + s.alias.map(x => `<b>${x}</b>`).join(" ");
    $("#scKv").innerHTML = s.kv.map(item => {
      const labelKey = item.k === "field.riseSet" ? "field.visible" : item.k;
      let val = item.v;
      if (val && typeof val === "object") val = val[S.locale] || val["zh-CN"] || "";
      if (item.k === "field.riseSet") val = t(item.k, { rise: item.rise, set: item.set });
      else if (typeof val === "string" && val.indexOf("field.") === 0) val = t(val);
      const hl = /field.downlink|field.scheme|field.tleEpoch|field.dataPolicy/.test(item.k) ? "hl" : "";
      return `<dt>${t(labelKey)}</dt><dd class="${hl}">${val}</dd>`;
    }).join("");
    $("#scDesc").innerHTML = t(s.descKey) + `　<a href="${s.wiki}" target="_blank" rel="noopener">${t("card.moreWiki")}</a>`;
    $("#scLock").textContent = t("card.lock") + (S.site.trusted ? "" : " · " + t("site.untrusted"));
    $("#satCard").classList.add("open");
    $("#zoomFabs").style.display = "flex";
    $("#zoomFabs").style.top = ""; $("#zoomFabs").style.bottom = "86px";
    if (!textOnly) MB.emit("sat:locked", s);   // 跟随在 draw() 主循环里
  }
  function unlock() {
    S.cam.locked = null;
    C.V.fovTarget = 100;
    $("#satCard").classList.remove("open");
    $("#zoomFabs").style.display = "none";
    MB.emit("sat:unlocked");
  }

  /* ---------- 交互：grab-the-sky / zoom-to-cursor / 点击选星 ---------- */
  cv.addEventListener("mousedown", e => {
    dragMoved = false;
    const r = cv.getBoundingClientRect();
    const sx = e.clientX - r.left, sy = e.clientY - r.top;
    dragState = { sx, sy, sky: C.unproject(sx, sy, view.w, view.h), az: C.V.az, alt: C.V.alt };
    if (S.cam.locked) unlock();
  });
  window.addEventListener("mousemove", e => {
    if (!dragState) return;
    const r = cv.getBoundingClientRect();
    const px = e.clientX - r.left, py = e.clientY - r.top;
    if (Math.abs(px - dragState.sx) > 5 || Math.abs(py - dragState.sy) > 5) dragMoved = true;
    const cur = C.unproject(px, py, view.w, view.h);
    // 钉点式：按下时的天球方向始终跟着光标（grab-the-sky）
    let dAz = dragState.sky.az - cur.az;
    if (dAz > 180) dAz -= 360; else if (dAz < -180) dAz += 360;
    C.V.azTarget = C.V.az = ((dragState.az + dAz) % 360 + 360) % 360;
    C.V.altTarget = C.V.alt = clamp(dragState.alt + (dragState.sky.alt - cur.alt), -25, 89);
  });
  window.addEventListener("mouseup", () => { dragState = null; });
  cv.addEventListener("wheel", e => {
    e.preventDefault();
    const r = cv.getBoundingClientRect();
    C.zoom(e.deltaY < 0 ? 1 / 1.12 : 1.12, e.clientX - r.left, e.clientY - r.top, view.w, view.h);
  }, { passive: false });
  cv.addEventListener("click", e => {
    if (dragMoved) return;
    const r = cv.getBoundingClientRect();
    const mx = e.clientX - r.left, my = e.clientY - r.top;
    let best = null, bd = 26 * 26;
    for (const s of MB.SATS) {
      if (!s._screen) continue;
      const d = (s._screen.x - mx) ** 2 + (s._screen.y - my) ** 2;
      if (d < bd) { bd = d; best = s; }
    }
    if (best) lockSat(best);
  });

  $("#scClose").addEventListener("click", unlock);
  $("#satCard").addEventListener("click", e => {
    const b = e.target.closest("[data-act]"); if (!b) return;
    MB.dialog.open(b.dataset.act, S.cam.locked);
  });
  $("#zoomIn").addEventListener("click", () => {
    C.V.fovTarget = clamp(C.V.fovTarget / 1.35, C.V.minFov, C.V.maxFov);
  });
  $("#zoomOut").addEventListener("click", () => {
    C.V.fovTarget = clamp(C.V.fovTarget * 1.35, C.V.minFov, C.V.maxFov);
  });
  $("#searchInput").addEventListener("keydown", e => {
    if (e.key !== "Enter") return;
    const q = e.target.value.trim().toLowerCase();
    if (!q) return;
    const hit = MB.SATS.find(s => [s.id, s.norad, t(s.typeKey), ...s.alias]
      .join(" ").toLowerCase().includes(q));
    if (hit) { lockSat(hit); return; }
    const star = (C.CAT.named || []).find(s =>
      s.n.toLowerCase().includes(q) || (s.zh || "").includes(q));
    if (star) {
      const v = C.eqToHorizon(Math.cos(star.ra * Math.PI / 180),
                              Math.sin(star.ra * Math.PI / 180),
                              Math.sin(star.dec * Math.PI / 180),
                              Math.cos(star.dec * Math.PI / 180), C.frame(S));
      const d = C.dirAltAz(v);
      C.panTo(d.az, d.alt); C.V.fovTarget = 20;
      MB.emit("star:goto", star.n);
    }
  });

  MB.sky = {
    draw, resize, lockSat, unlock, view, STARS,
    makeSky() { /* 星表由 12-sky-catalog.js 提供；此处不再造任何星 */ },
    now: () => frameT,
    edgeStat: () => _edgeCalls,
    retranslate() { if (S.cam.locked) lockSat(S.cam.locked, true); }
  };
})(window);
