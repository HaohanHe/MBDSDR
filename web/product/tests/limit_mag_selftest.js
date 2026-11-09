/* 极限星等跨语言自检：14-astro-frame.js ↔ 内核金标准。
   金标准由 build/gen_limit_mag_golden.py 生成（直接 import 内核
   skyengine.stars.naked_eye_limit_mag + star_catalog.detail_gain_mag/limit_magnitude，
   不重写公式）。三层钉扎：
     1. 常量：JS 导出常量 === 内核常量（精确，防 6.5/0.35/30/1.5/9.0 漂移）。
     2. 分量 1e-12 等：nakedEyeLimitMag / detailGainMag 逐点 == 内核。
     3. 合成 1e-12 等：limitMagnitude 默认 depth(9.0) == 内核 limit_magnitude；
        显式 webDepthCap(7.5) == min(cap, naked+gain)（前端调用点口径）。
   另钉扎关键不变量：广角(fov>=30)增益恒 0、极限==裸眼基线（旧版负漂移不得回潮）；
   增益有界 [0,1.5]；Bortle int 向零取整；极小 fov 地板封顶；默认参数生效。 */
const fs = require("fs");
const path = require("path");

const ROOT = path.join(__dirname, "..");
const golden = JSON.parse(fs.readFileSync(path.join(ROOT, "build/limit-mag-golden.json"), "utf8"));
const src = fs.readFileSync(path.join(ROOT, "src/14-astro-frame.js"), "utf8");
global.window = { MBDSDR: {} };
(new Function("window", src))(global.window);
const A = global.window.MBDSDR.astro;
for (const fn of ["nakedEyeLimitMag", "detailGainMag", "limitMagnitude"])
  if (typeof A[fn] !== "function") throw new Error(`14-astro-frame.js 未导出 ${fn}`);

const M = golden.meta;
const CTOL = M.componentTolMag;       // 1e-12 等
const PTOL = M.compositionTolMag;     // 1e-12 等
const KC = M.kernelConstants;
const CAP = M.webDepthCap;            // 7.5（前端 MAG_CAP）

/* 1. 常量精确钉扎：JS 导出 === 内核常量。 */
const consts = {
  DEFAULT_BORTLE: KC.DEFAULT_BORTLE,
  NAKED_EYE_LIMIT_SCALE: KC.NAKED_EYE_LIMIT_SCALE,
  NAKED_EYE_BORTLE_SLOPE: KC.NAKED_EYE_BORTLE_SLOPE,
  DETAIL_REF_FOV_DEG: KC.DETAIL_REF_FOV_DEG,
  MAX_DETAIL_GAIN_MAG: KC.MAX_DETAIL_GAIN_MAG,
  CATALOG_DEPTH_MAG: KC.CATALOG_DEPTH_MAG,
};
for (const [k, v] of Object.entries(consts))
  if (A[k] !== v) throw new Error(`常量漂移 ${k}: JS=${A[k]} vs 内核=${v}`);

let worstComp = 0, worstCompAt = "", worstLimit = 0, worstLimitAt = "";
let n = 0;
for (const c of golden.cases) {
  const label = `fov=${c.fov} bortle=${c.bortle}`;
  /* 2. 分量级紧钉扎。 */
  const nel = A.nakedEyeLimitMag(c.bortle);
  const dg = A.detailGainMag(c.fov);
  const eNel = Math.abs(nel - c.nakedEye), eDg = Math.abs(dg - c.detailGain);
  if (eNel > worstComp) { worstComp = eNel; worstCompAt = label + " nakedEye"; }
  if (eDg > worstComp) { worstComp = eDg; worstCompAt = label + " detailGain"; }
  if (!isFinite(eNel) || eNel > CTOL)
    throw new Error(`${label}: nakedEyeLimitMag=${nel} vs 内核 ${c.nakedEye} err=${eNel} > ${CTOL}`);
  if (!isFinite(eDg) || eDg > CTOL)
    throw new Error(`${label}: detailGainMag=${dg} vs 内核 ${c.detailGain} err=${eDg} > ${CTOL}`);
  /* 3. 合成级：默认 depth(9.0) == 内核 limit_magnitude。 */
  const ld = A.limitMagnitude(c.fov, c.bortle);
  const eLd = Math.abs(ld - c.limitDefault);
  if (eLd > worstLimit) { worstLimit = eLd; worstLimitAt = label + " default"; }
  if (!isFinite(eLd) || eLd > PTOL)
    throw new Error(`${label}: limitMagnitude(default)=${ld} vs 内核 ${c.limitDefault} err=${eLd} > ${PTOL}`);
  /* 合成级：显式 webDepthCap(7.5) == min(cap, naked+gain)（前端调用点）。 */
  const lw = A.limitMagnitude(c.fov, c.bortle, CAP);
  const eLw = Math.abs(lw - c.limitWeb);
  if (eLw > worstLimit) { worstLimit = eLw; worstLimitAt = label + " web"; }
  if (!isFinite(eLw) || eLw > PTOL)
    throw new Error(`${label}: limitMagnitude(cap=${CAP})=${lw} vs 内核 ${c.limitWeb} err=${eLw} > ${PTOL}`);
  /* 不变量：广角增益恒 0、极限==裸眼基线（封顶 cap），旧版负漂移不得回潮。 */
  if (c.fov >= KC.DETAIL_REF_FOV_DEG) {
    if (dg !== 0) throw new Error(`${label}: 广角(>=${KC.DETAIL_REF_FOV_DEG})增益应恒 0，实际 ${dg}`);
    const expect = Math.min(CAP, c.nakedEye);
    if (Math.abs(lw - expect) > PTOL)
      throw new Error(`${label}: 广角极限 ${lw} != 裸眼基线 ${expect}（负漂移回潮）`);
  }
  /* 不变量：增益有界 [0, MAX_DETAIL_GAIN_MAG]。 */
  if (dg < 0 || dg > KC.MAX_DETAIL_GAIN_MAG + 1e-12)
    throw new Error(`${label}: 增益越界 ${dg}（应 [0,${KC.MAX_DETAIL_GAIN_MAG}]）`);
  n++;
}

/* Bortle int 向零取整（内核 int()，JS Math.trunc()）：3.7→3、5.9→5、<3→基线。 */
if (A.nakedEyeLimitMag(3.7) !== A.nakedEyeLimitMag(3))
  throw new Error("Bortle 3.7 未向零取整到 3");
if (A.nakedEyeLimitMag(5.9) !== A.nakedEyeLimitMag(5))
  throw new Error("Bortle 5.9 未向零取整到 5");
if (A.nakedEyeLimitMag(-1) !== KC.NAKED_EYE_LIMIT_SCALE)
  throw new Error("Bortle<3 应恒为裸眼基线 6.5");
/* 默认参数：undefined bortle → DEFAULT_BORTLE；limitMagnitude 默认 depth → 9.0。 */
if (A.nakedEyeLimitMag() !== A.nakedEyeLimitMag(KC.DEFAULT_BORTLE))
  throw new Error("默认 bortle 未生效");
if (A.limitMagnitude(15) !== A.limitMagnitude(15, KC.DEFAULT_BORTLE, KC.CATALOG_DEPTH_MAG))
  throw new Error("limitMagnitude 默认参数（bortle/depthCap）未生效");
/* 极小 fov 地板 + 封顶：detailGain(0)==detailGain(1e-9)==1.5（防除零/发散）。 */
if (A.detailGainMag(0) !== KC.MAX_DETAIL_GAIN_MAG)
  throw new Error("fov=0 应经地板 1e-6 封顶到 1.5");
if (A.detailGainMag(1e-9) !== KC.MAX_DETAIL_GAIN_MAG)
  throw new Error("fov<地板应封顶到 1.5");

console.log(`limit-mag cross-language OK · ${golden.cases.length} cases / `
  + `component worst ${worstComp.toExponential(2)} mag (${worstCompAt}) / `
  + `composition worst ${worstLimit.toExponential(2)} mag (${worstLimitAt}) ≤ ${PTOL}`);