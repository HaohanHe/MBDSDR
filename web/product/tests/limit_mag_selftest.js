/* 极限星等跨语言自检：14-astro-frame.js ↔ 内核金标准。
   金标准由 build/gen_limit_mag_golden.py 生成（直接 import 内核
   skyengine.stars.naked_eye_limit_mag + star_catalog.detail_gain_mag/limit_magnitude，
   不重写公式）。三层钉扎：
     1. 常量：JS 导出常量 === 内核常量（精确，防 6.5/0.35/30/1.5/9.0 漂移）。
     2. 分量 1e-12 等：nakedEyeLimitMag / detailGainMag 逐点 == 内核。
     3. 合成 1e-12 等：limitMagnitude 默认 depth(9.0) == 内核 limit_magnitude；
        显式 webDepthCap(7.5) == min(cap, naked+gain)（前端调用点口径）。
   另钉扎关键不变量：广角(fov>=30)增益恒 0、极限==裸眼基线（旧版负漂移不得回潮）；
   增益有界 [0,1.5]；Bortle int 向零取整；极小 fov 地板封顶；默认参数生效；
   **Bortle 1..9 全程线性、不夹 Bortle<3**（1=7.2 / 2=6.85 / 3=6.5 / 9=4.4）。
   三条反证随测试一起跑（不是另跑脚本）：
     A. 六组点名样本 Bortle 1/2/3/5/7/9 逐点对内核金标准；
     B. 把 max(0, …) 截断注入回 JS 源码副本，跨语言对拍必须变红（变异存活即失败）；
     C. 画面级星数：真实 HYG 星表 + 渲染同一道星等门，截断版 Bortle1==Bortle3，
        当前实现 Bortle1>Bortle3。 */
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

/* Bortle int 向零取整（内核 int()，JS Math.trunc()）：3.7→3、5.9→5。 */
if (A.nakedEyeLimitMag(3.7) !== A.nakedEyeLimitMag(3))
  throw new Error("Bortle 3.7 未向零取整到 3");
if (A.nakedEyeLimitMag(5.9) !== A.nakedEyeLimitMag(5))
  throw new Error("Bortle 5.9 未向零取整到 5");
/* Bortle 1..9 全程线性、**不夹 Bortle<3**：1=7.2、2=6.85、3=6.5、…、9=4.4。
   夹平（max(0,b-3)）会让 Bortle 1/2 与 3 同值，暗空区间的光害设置完全失效（P1-46）。 */
const NEL_SEQ = [7.2, 6.85, 6.5, 6.15, 5.8, 5.45, 5.1, 4.75, 4.4];
for (let b = 1; b <= 9; b++) {
  const got = A.nakedEyeLimitMag(b), want = NEL_SEQ[b - 1];
  if (Math.abs(got - want) > CTOL)
    throw new Error(`Bortle ${b}: nakedEyeLimitMag=${got} != ${want}（线性律被截断/夹平）`);
  if (b > 1 && !(A.nakedEyeLimitMag(b - 1) > got))
    throw new Error(`Bortle ${b - 1}→${b} 极限星等未随光害升高而下降`);
}
if (A.nakedEyeLimitMag(1) === A.nakedEyeLimitMag(3))
  throw new Error("Bortle 1 与 3 被夹平（max(0,b-3) 截断回潮）");
/* 六组点名反证（Bortle 1/2/3/5/7/9）逐点对内核金标准（fov=60 广角，增益 0）。 */
const SIX_BORTLES = [1, 2, 3, 5, 7, 9];
for (const b of SIX_BORTLES) {
  const c = golden.cases.find(x => x.fov === 60 && x.bortle === b);
  if (!c) throw new Error(`金标准缺 fov=60 bortle=${b} 样本（先跑 build/gen_limit_mag_golden.py）`);
  const got = A.nakedEyeLimitMag(b), err = Math.abs(got - c.nakedEye);
  if (err > CTOL)
    throw new Error(`六组反证 Bortle ${b}: JS=${got} vs Python 内核=${c.nakedEye} err=${err}`);
  console.log(`  Bortle ${b}: JS nakedEye=${got.toFixed(2)} == Python 内核 ${c.nakedEye.toFixed(2)}`);
}

/* ---------- 反证 A：把 max(0, …) 截断加回去，跨语言对拍必须变红 ----------
 * 若变异存活（测试仍绿），说明本自检对该回归失明，直接判失败而不是"通过"。 */
function loadAstro(text) {
  const sandbox = { MBDSDR: {} };
  (new Function("window", text))(sandbox);
  return sandbox.MBDSDR.astro;
}
const LINEAR = "- NAKED_EYE_BORTLE_SLOPE * (Math.trunc(bortle) - 3);";
const CLAMPED = "- NAKED_EYE_BORTLE_SLOPE * Math.max(0, Math.trunc(bortle) - 3);";
if (src.split(LINEAR).length !== 2)
  throw new Error("变异锚点不唯一/不存在：nakedEyeLimitMag 线性律已变，反证需同步更新");
const AM = loadAstro(src.replace(LINEAR, CLAMPED));
function crossLangError(Ax) {
  for (const c of golden.cases) {
    const e = Math.abs(Ax.nakedEyeLimitMag(c.bortle) - c.nakedEye);
    if (!(e <= CTOL))
      return `bortle=${c.bortle}: ${Ax.nakedEyeLimitMag(c.bortle)} vs 内核 ${c.nakedEye} err=${e}`;
  }
  return null;
}
if (crossLangError(A) !== null) throw new Error("当前实现未通过跨语言对拍");
const mutantErr = crossLangError(AM);
if (mutantErr === null)
  throw new Error("变异存活：截断加回后跨语言对拍仍通过（自检失明）");
console.log(`  变异反证：把 max(0,…) 加回 → 对拍变红（${mutantErr}）`);

/* ---------- 反证 B：画面级星数（真实 HYG 星表 + 与渲染同一道星等门） ----------
 * eachStar/starStyle 的判据是 mag > L 不画（消光/视锥只会再减星），广角 fov=60
 * 增益 0 → L = min(MAG_CAP, nakedEye(bortle))。改前 Bortle 1 与 3 完全同值，
 * 改后 Bortle 1 必须多出暗星。 */
const cat = JSON.parse(fs.readFileSync(path.join(ROOT, "build/sky-catalog.json"), "utf8"));
const MAG = cat.stars.map(s => s[2] / 100);
function drawnCount(Ax, bortle, fov = 60) {
  const L = Ax.limitMagnitude(fov, bortle, CAP);
  return MAG.filter(m => m <= L).length;
}
const c1 = drawnCount(A, 1), c3 = drawnCount(A, 3);
const m1 = drawnCount(AM, 1), m3 = drawnCount(AM, 3);
if (m1 !== m3)
  throw new Error(`画面级反证前提失败：截断版 Bortle 1/3 星数应相同，实际 ${m1} vs ${m3}`);
if (c1 === c3)
  throw new Error(`画面级证据失败：改后 Bortle 1/3 星数仍相同（${c1}）`);
if (!(c1 > c3))
  throw new Error(`画面级证据方向错误：Bortle 1 应比 3 多，实际 ${c1} vs ${c3}`);
console.log(`  画面级：HYG ${cat.stars.length} 颗 / fov=60 / cap=${CAP} · `
  + `改前 Bortle1=${m1} == Bortle3=${m3}；改后 Bortle1=${c1} > Bortle3=${c3}（+${c1 - c3} 颗）`);
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