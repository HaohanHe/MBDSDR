/* 天文框架跨语言自检：14-astro-frame.js ↔ 仓库 celestial_geometry.py。
   金标准由 build/gen_astro_golden.py 生成；任何公式漂移都会在此报错。
   同时钉扎「章动必须是角秒量级」，防止 1e4 单位错误回潮。 */
const fs = require("fs");
const path = require("path");

const ROOT = path.join(__dirname, "..");
const golden = JSON.parse(fs.readFileSync(path.join(ROOT, "build/astro-golden.json"), "utf8"));
const src = fs.readFileSync(path.join(ROOT, "src/14-astro-frame.js"), "utf8");
global.window = { MBDSDR: {} };
(new Function("window", src))(global.window);
const A = global.window.MBDSDR.astro;
if (!A) throw new Error("14-astro-frame.js 未导出 MB.astro");

const EPS = 1e-11;          // 矩阵元素误差上限
const VEPS = 1e-10;         // 向量误差上限
const GEPS = 1e-8;          // GMST(度) 误差上限
let worst = 0, worstAt = "";

function cmp(label, a, b, tol) {
  const aa = Array.from(a), bb = Array.from(b);
  if (aa.length !== bb.length) throw new Error(`${label}: 长度 ${aa.length} != ${bb.length}`);
  for (let i = 0; i < aa.length; i++) {
    const e = Math.abs(aa[i] - bb[i]);
    if (e > worst) { worst = e; worstAt = `${label}[${i}]`; }
    if (!isFinite(e) || e > tol)
      throw new Error(`${label}[${i}] 偏差 ${e} > ${tol}: ${aa[i]} vs ${bb[i]}`);
  }
}

const seenJd = new Set();
for (const c of golden.cases) {
  const F = A.frameMatrices(c.jd, c.lat, c.lon);
  cmp(`gmst jd=${c.jd}`, [A.gmstDeg(c.jd)], [c.gmst], GEPS);
  cmp(`P jd=${c.jd}`, A.precessionMatrix(c.jd), c.P, EPS);
  cmp(`N jd=${c.jd}`, A.nutationMatrix(c.jd), c.N, EPS);
  cmp(`Mpn ${c.jd}/${c.lat}`, F.Mpn, c.Mpn, EPS);
  cmp(`Rf ${c.jd}/${c.lat}`, F.Rf, c.Rf, EPS);
  cmp(`M ${c.jd}/${c.lat}`, F.M, c.M, EPS);
  cmp(`Pinv jd=${c.jd}`, F.Pinv, c.Pinv, EPS);
  const nut = A.nutationAngles(c.jd);
  cmp(`dpsi jd=${c.jd}`, [nut.dpsi], [c.dpsiRad], EPS);
  cmp(`deps jd=${c.jd}`, [nut.deps], [c.depsRad], EPS);
  cmp(`eps jd=${c.jd}`, [A.obliquity(c.jd)], [c.epsRad], EPS);
  for (const v of c.vectors) {
    const h = A.matVec(F.M, v.v[0], v.v[1], v.v[2]);
    cmp(`horizon ${c.jd}/${c.lat} ra=${v.ra}`, [h.x, h.y, h.z], v.horizon, VEPS);
    const d = A.matVec(F.Rf, v.v[0], v.v[1], v.v[2]);
    cmp(`dateHorizon ${c.jd}/${c.lat} ra=${v.ra}`, [d.x, d.y, d.z], v.dateHorizon, VEPS);
    const o = A.matVec(F.Mpn, v.v[0], v.v[1], v.v[2]);
    cmp(`ofDate ${c.jd}/${c.lat} ra=${v.ra}`, [o.x, o.y, o.z], v.ofDate, VEPS);
    const j = A.matVec(F.Pinv, v.v[0], v.v[1], v.v[2]);
    cmp(`temeToJ2000 ${c.jd} ra=${v.ra}`, [j.x, j.y, j.z], v.temeToJ2000, VEPS);
  }
  if (!seenJd.has(c.jd)) {
    seenJd.add(c.jd);
    const arcsec = 206264.80624709636;
    const psi = Math.abs(A.nutationAngles(c.jd).dpsi * arcsec);
    if (!(psi > 0.1 && psi < 30))
      throw new Error(`jd=${c.jd}: |dpsi|=${psi.toFixed(6)}" 量级异常（章动单位又缩错了？）`);
  }
}

/* 正交性：总矩阵 M 应保长（纯旋转，不含缩放）。 */
for (const c of golden.cases.slice(0, 4)) {
  const F = A.frameMatrices(c.jd, c.lat, c.lon);
  for (const v of c.vectors) {
    const h = A.matVec(F.M, v.v[0], v.v[1], v.v[2]);
    const len = Math.hypot(h.x, h.y, h.z);
    if (Math.abs(len - 1) > 1e-10) throw new Error(`M 不保长: ${len}`);
  }
}

console.log(`astro-frame cross-language OK · ${golden.cases.length} cases / `
  + `${golden.cases.reduce((n, c) => n + c.vectors.length, 0)} vectors / `
  + `worst ${worst.toExponential(2)} (${worstAt})`);
