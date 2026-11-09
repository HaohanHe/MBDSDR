/* 恒星自行跨语言自检：14-astro-frame.js propagateProperMotion ↔ 金标准。
   金标准由 build/gen_proper_motion_golden.py 生成（skyfield 严格空间运动 +
   同构 Python 线性转录）。两层钉扎：
     1. 紧钉扎 1e-9°：JS 线性公式 ↔ Python 线性转录（防公式/单位漂移）。
     2. 显示级 2″：JS 线性结果 ↔ skyfield 严格运动（量近似误差，亚像素）。
   另钉扎极区守卫（cosδ0→0 时 RA 不漂移）与 dec 钳位。 */
const fs = require("fs");
const path = require("path");

const ROOT = path.join(__dirname, "..");
const golden = JSON.parse(fs.readFileSync(path.join(ROOT, "build/proper-motion-golden.json"), "utf8"));
const src = fs.readFileSync(path.join(ROOT, "src/14-astro-frame.js"), "utf8");
global.window = { MBDSDR: {} };
(new Function("window", src))(global.window);
const A = global.window.MBDSDR.astro;
if (!A || typeof A.propagateProperMotion !== "function")
  throw new Error("14-astro-frame.js 未导出 propagateProperMotion");

const D2R = Math.PI / 180;
const PIN_TOL = golden.meta.linearPinTolDeg;     // 1e-9°
const SKY_TOL = golden.meta.toleranceArcsec;      // 2″
let worstPin = 0, worstPinAt = "";
let worstSky = 0, worstSkyAt = "";
let n = 0;

function sepArcsec(ra1, dec1, ra2, dec2) {
  // haversine：小角度下数值稳定（acos 在 θ→0 时灾难性抵消，会把浮点噪声当成偏差）
  const d1 = dec1 * D2R, d2 = dec2 * D2R;
  const dd = (dec2 - dec1) * D2R, da = (ra2 - ra1) * D2R;
  const h = Math.sin(dd / 2) ** 2 + Math.cos(d1) * Math.cos(d2) * Math.sin(da / 2) ** 2;
  return 2 * Math.asin(Math.min(1, Math.sqrt(h))) / D2R * 3600;
}

for (const c of golden.cases) {
  for (const s of c.samples) {
    const p = A.propagateProperMotion(c.ra0, c.dec0, c.pmra, c.pmdec, s.dtYr);
    const label = `${c.name || c.hygId} jd=${s.jd}`;
    // 1. 紧钉扎：JS ↔ Python 线性（RA 用角距而非裸差，避免 0/360 绕回假红）
    const pin = sepArcsec(p.ra, p.dec, s.linear.ra, s.linear.dec) / 3600; // → 度
    if (pin > worstPin) { worstPin = pin; worstPinAt = label; }
    if (!isFinite(pin) || pin > PIN_TOL)
      throw new Error(`${label}: 线性转录偏差 ${pin}° > ${PIN_TOL}° `
        + `(${p.ra},${p.dec}) vs (${s.linear.ra},${s.linear.dec})`);
    // 2. 显示级：JS ↔ skyfield 严格
    const sky = sepArcsec(p.ra, p.dec, s.skyfield.ra, s.skyfield.dec);
    if (sky > worstSky) { worstSky = sky; worstSkyAt = label; }
    if (!isFinite(sky) || sky > SKY_TOL)
      throw new Error(`${label}: 对 skyfield 残差 ${sky}" > ${SKY_TOL}"`);
    // 3. J2000 零历元：必须等于 catalog 原位（无漂移）
    if (s.dtYr === 0) {
      const d0 = sepArcsec(p.ra, p.dec, c.ra0, c.dec0) / 3600;
      if (d0 > PIN_TOL)
        throw new Error(`${label}: dtYr=0 却漂移 ${d0}°（应等于 J2000 原位）`);
    }
    n++;
  }
}

/* 极区守卫：dec0=±90 → cosδ0≈0 < 1e-9 → RA 不得漂移（否则除以 ~0 爆炸）。 */
for (const dec0 of [90, -90]) {
  const p = A.propagateProperMotion(123.0, dec0, 5000, 0, 50);
  if (Math.abs(p.ra - 123.0) > 1e-9)
    throw new Error(`极区守卫失效 dec0=${dec0}: RA 漂到 ${p.ra}（应锁定 123）`);
}
/* dec 钳位：大 pmdec 不得把 dec 推过 ±90。 */
{
  const p = A.propagateProperMotion(0, 89.0, 0, 100000, 100);
  if (p.dec > 90 + 1e-12 || p.dec < -90 - 1e-12)
    throw new Error(`dec 未钳位: ${p.dec}`);
}
/* 零自行：pmra=pmdec=0 → 原位不动（缺测星不推进）。 */
{
  const p = A.propagateProperMotion(200.5, -33.25, 0, 0, 50);
  if (Math.abs(p.ra - 200.5) > 1e-12 || Math.abs(p.dec - (-33.25)) > 1e-12)
    throw new Error(`零自行却漂移: (${p.ra},${p.dec})`);
}

console.log(`proper-motion cross-language OK · ${golden.cases.length} stars / ${n} samples / `
  + `linear pin worst ${worstPin.toExponential(2)}° (${worstPinAt}) / `
  + `skyfield worst ${worstSky.toFixed(4)}" (${worstSkyAt}) ≤ ${SKY_TOL}"`);