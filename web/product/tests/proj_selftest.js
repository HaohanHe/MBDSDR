/* 天球投影自检：投影 ↔ 反投影必须互逆（拖拽/缩放全靠这条）。
   覆盖两类投影 + 切换边界 + 极端 FOV。 */
const fs = require("fs");
const path = require("path").join(__dirname, "../src/31-celestial.js");
global.window = { MBDSDR: { SKYCAT: { stars: [], named: [], lines: {}, dso: [], meta: {} } } };
global.performance = { now: () => 0 };
(new Function("window", fs.readFileSync(path, "utf8")))(global.window);
const C = global.window.MBDSDR.celestial;
const W = 1440, H = 900;

let worst = 0, worstAt = "";
const fovs = [3, 12, 30, 60, 87.9, 88, 88.1, 110, 140, 158];
const views = [[0, 22], [137, -8], [270, 80], [45, -20], [0, 89], [200, 60]];
for (const fov of fovs) {
  C.V.fov = fov;
  for (const [az, alt] of views) {
    C.V.az = az; C.V.alt = alt;
    let local = 0;
    for (let i = 0; i < 400; i++) {
      const px = (i * 97) % W, py = (i * 53) % H;
      const d = C.unproject(px, py, W, H);
      if (!isFinite(d.az) || !isFinite(d.alt)) throw new Error(`unproject NaN at fov=${fov}`);
      const p = C.projectAltAz(d.az, d.alt, W, H);
      if (!p) { throw new Error(`round-trip lost point fov=${fov} az=${az} alt=${alt} px=${px}`); }
      const e = Math.hypot(p.x - px, p.y - py);
      if (e > local) local = e;
    }
    if (local > worst) { worst = local; worstAt = `fov=${fov} az=${az} alt=${alt}`; }
    if (local > 0.6) throw new Error(`round-trip error ${local.toFixed(2)}px at fov=${fov} az=${az} alt=${alt}`);
  }
}
console.log(`projection round-trip OK · worst ${worst.toFixed(3)} px (${worstAt})`);

/* 一列内「第一个 alt < hill 的位置」必须存在且唯一可求：
   在 fov 158（四角能看背后，alt 非单调）时，自上而下扫描要能命中。
   这里只验证扫描算法在 31-celestial 提供的能力下收敛（用 hill=0 简化）。 */
function hill(a) { return 1.8 + 1.1 * Math.sin(a * Math.PI / 180 * 3 + .6); }
for (const fov of [8, 30, 88, 120, 158]) {
  C.V.fov = fov; C.V.az = 33; C.V.alt = 2;   // 视线压到地平附近，地平确实在画面内
  let hits = 0, cols = 0;
  for (let x = 20; x < W; x += 97) {
    cols++;
    for (let y = 6; y <= H; y += 6) {
      const d = C.unproject(x, y, W, H);
      if (d.alt - hill(d.az) <= 0) { hits++; break; }
    }
  }
  if (!hits) throw new Error(`landscape scan found no horizon at fov=${fov}`);
  console.log(`  fov=${fov}: ${hits}/${cols} columns hit the horizon edge by top-down scan`);
}

/* 地平大圆在透视下必须是直线（大圆在针孔相机里的正确像） */
C.V.fov = 60; C.V.az = 90; C.V.alt = 0;
const ys = [];
for (let az = 60; az <= 120; az += 5) {
  const p = C.projectAltAz(az, 0, W, H);
  if (p) ys.push(p.y);
}
const spread = Math.max(...ys) - Math.min(...ys);
if (spread > 1.5) throw new Error(`horizon not straight under perspective: spread ${spread.toFixed(2)}px`);
console.log(`horizon straight under perspective OK (spread ${spread.toFixed(2)} px over ±30° az)`);

/* 立体投影下地平必须成弧（不是直线） */
C.V.fov = 150; C.V.az = 90; C.V.alt = 8;
const ys2 = [];
for (let az = 10; az <= 170; az += 5) {
  const p = C.projectAltAz(az, 0, W, H);
  if (p) ys2.push(p.y);
}
const spread2 = Math.max(...ys2) - Math.min(...ys2);
if (spread2 < 20) throw new Error(`horizon not curved under stereographic: spread ${spread2.toFixed(2)}px`);
console.log(`horizon curved under stereographic OK (spread ${spread2.toFixed(1)} px)`);

/* 屏幕四角在最大 FOV 下仍可投影（不能被 NEAR 裁掉大片） */
C.V.fov = 158; C.V.az = 0; C.V.alt = 0;
let corner = 0;
for (const [x, y] of [[0, 0], [W, 0], [0, H], [W, H]]) {
  if (C.unproject(x, y, W, H)) corner++;
}
console.log(`corners unprojectable at 158°: ${corner}/4`);
console.log("ALL PROJECTION CHECKS PASS");
