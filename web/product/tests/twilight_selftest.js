/* 晨昏天空亮度 → 极限星等压制 跨语言自检：14-astro-frame.js ↔ 内核金标准。
   金标准由 build/gen_twilight_golden.py 生成（直接 import 内核
   skyengine.twilight.sky_brightness_mag_arcsec2 / twilight_limit_penalty_mag，
   表值从内核读取不抄写）。三层钉扎：
     1. 常量/表：JS 导出夜值 === 内核；JS 亮度表逐元素 === 内核表（防 21.6/13 knots 漂移）。
     2. 分量 1e-12 等：skyBrightnessMagArcsec2 / twilightLimitPenaltyMag 逐点 == 内核。
     3. 合成 1e-12 等：limitMagnitude(fov,bortle[,cap]) - penalty(sunAlt) == 桌面
        rf_sky_view.py:4246 的 base - penalty（default-depth 9.0 与 web-depth 7.5 两口径）。
   另镜像 tests/test_sky_realism_batch.py::TestTwilightPenalty 不变量：
     ≤-18° 惩罚精确 0；None/NaN/undefined → 夜值 → 惩罚 0；惩罚随太阳高度单调不减；
     民用晨昏 -6° → 6.5-penalty ∈ [-2,1]；白天(0/5/30) → 6.5-penalty < -6；
     端点 brightness(60)=4、brightness(-30)=21.6；亮度随太阳高度单调不增。 */
const fs = require("fs");
const path = require("path");

const ROOT = path.join(__dirname, "..");
const golden = JSON.parse(fs.readFileSync(path.join(ROOT, "build/twilight-golden.json"), "utf8"));
const src = fs.readFileSync(path.join(ROOT, "src/14-astro-frame.js"), "utf8");
global.window = { MBDSDR: {} };
(new Function("window", src))(global.window);
const A = global.window.MBDSDR.astro;
for (const fn of ["skyBrightnessMagArcsec2", "twilightLimitPenaltyMag", "limitMagnitude"])
  if (typeof A[fn] !== "function") throw new Error(`14-astro-frame.js 未导出 ${fn}`);

const M = golden.meta;
const CTOL = M.componentTolMag;       // 1e-12 等
const PTOL = M.compositionTolMag;     // 1e-12 等
const KC = M.kernelConstants;
const NIGHT = KC.SKY_BRIGHTNESS_NIGHT_MAG_ARCSEC2;   // 21.6
const CAP = M.webDepthCap;            // 7.5（前端 MAG_CAP）

/* 1. 常量 + 表精确钉扎。 */
if (A.SKY_BRIGHTNESS_NIGHT_MAG_ARCSEC2 !== NIGHT)
  throw new Error(`夜值漂移: JS=${A.SKY_BRIGHTNESS_NIGHT_MAG_ARCSEC2} vs 内核=${NIGHT}`);
const kt = KC.SKY_BRIGHTNESS_TABLE, jt = A.SKY_BRIGHTNESS_TABLE;
if (!Array.isArray(jt) || jt.length !== kt.length)
  throw new Error(`亮度表长度漂移: JS=${jt && jt.length} vs 内核=${kt.length}`);
for (let i = 0; i < kt.length; i++)
  if (jt[i][0] !== kt[i][0] || jt[i][1] !== kt[i][1])
    throw new Error(`亮度表[${i}]漂移: JS=[${jt[i]}] vs 内核=[${kt[i]}]`);

let worstComp = 0, worstCompAt = "", worstEff = 0, worstEffAt = "";

/* 2. 分量级紧钉扎：亮度 + 惩罚逐点 == 内核。 */
for (const c of golden.components) {
  const label = `sunAlt=${c.sunAlt}`;
  const sb = A.skyBrightnessMagArcsec2(c.sunAlt);
  const pen = A.twilightLimitPenaltyMag(c.sunAlt);
  const eSb = Math.abs(sb - c.skyBrightness), ePen = Math.abs(pen - c.penalty);
  if (eSb > worstComp) { worstComp = eSb; worstCompAt = label + " brightness"; }
  if (ePen > worstComp) { worstComp = ePen; worstCompAt = label + " penalty"; }
  if (!isFinite(eSb) || eSb > CTOL)
    throw new Error(`${label}: skyBrightness=${sb} vs 内核 ${c.skyBrightness} err=${eSb} > ${CTOL}`);
  if (!isFinite(ePen) || ePen > CTOL)
    throw new Error(`${label}: penalty=${pen} vs 内核 ${c.penalty} err=${ePen} > ${CTOL}`);
  if (pen < 0) throw new Error(`${label}: 惩罚为负 ${pen}（应 ≥0）`);
}

/* 3. 合成级紧钉扎：base - penalty（桌面 rf_sky_view.py:4246），两 depthCap 口径。 */
for (const c of golden.compositions) {
  const label = `fov=${c.fov} bortle=${c.bortle} sunAlt=${c.sunAlt}`;
  const baseK = A.limitMagnitude(c.fov, c.bortle);            // 默认 depth 9.0
  const baseW = A.limitMagnitude(c.fov, c.bortle, CAP);       // 前端 depth 7.5
  const pen = A.twilightLimitPenaltyMag(c.sunAlt);
  const effK = baseK - pen, effW = baseW - pen;
  const eBaseK = Math.abs(baseK - c.baseKernel), eBaseW = Math.abs(baseW - c.baseWeb);
  const eEffK = Math.abs(effK - c.effectiveKernel), eEffW = Math.abs(effW - c.effectiveWeb);
  if (eBaseK > PTOL) throw new Error(`${label}: baseKernel=${baseK} vs ${c.baseKernel} err=${eBaseK}`);
  if (eBaseW > PTOL) throw new Error(`${label}: baseWeb=${baseW} vs ${c.baseWeb} err=${eBaseW}`);
  if (eEffK > worstEff) { worstEff = eEffK; worstEffAt = label + " kernel"; }
  if (eEffW > worstEff) { worstEff = eEffW; worstEffAt = label + " web"; }
  if (!isFinite(eEffK) || eEffK > PTOL)
    throw new Error(`${label}: effectiveKernel=${effK} vs ${c.effectiveKernel} err=${eEffK} > ${PTOL}`);
  if (!isFinite(eEffW) || eEffW > PTOL)
    throw new Error(`${label}: effectiveWeb=${effW} vs ${c.effectiveWeb} err=${eEffW} > ${PTOL}`);
}

/* ---- 镜像内核 TestTwilightPenalty 不变量 ---- */
/* ≤-18°（天文夜）惩罚精确为 0：夜空口径不能被无端压低。 */
for (const alt of [-18.0, -20.0, -45.0, -90.0])
  if (A.twilightLimitPenaltyMag(alt) !== 0.0)
    throw new Error(`天文夜 alt=${alt} 惩罚应精确 0，实际 ${A.twilightLimitPenaltyMag(alt)}`);
/* 太阳高度未知（null/undefined/NaN）→ 夜值 → 惩罚 0：不假装白天。 */
for (const v of [null, undefined, NaN]) {
  if (A.skyBrightnessMagArcsec2(v) !== NIGHT)
    throw new Error(`未知太阳高度 ${String(v)} 亮度应为夜值 ${NIGHT}`);
  if (A.twilightLimitPenaltyMag(v) !== 0.0)
    throw new Error(`未知太阳高度 ${String(v)} 惩罚应为 0（维持夜空口径）`);
}
/* 惩罚随太阳高度单调不减（太阳越高→天空越亮→压得越多）。 */
{
  let prev = -Infinity;
  for (let a = -40; a <= 20; a++) {
    const alt = a / 2.0, pen = A.twilightLimitPenaltyMag(alt);
    if (pen < prev - 1e-12) throw new Error(`惩罚非单调 alt=${alt}: ${pen} < ${prev}`);
    prev = pen;
  }
}
/* 亮度随太阳高度单调不增（太阳越高→数值越小越亮）。 */
{
  let prev = Infinity;
  for (let a = -40; a <= 20; a++) {
    const alt = a / 2.0, sb = A.skyBrightnessMagArcsec2(alt);
    if (sb > prev + 1e-12) throw new Error(`亮度非单调 alt=${alt}: ${sb} > ${prev}`);
    prev = sb;
  }
}
/* 民用晨昏 -6°：夜空 6.5 等压到 ≤1（只剩最亮几颗），但不到"全黑"（≥-2）。 */
{
  const limit = 6.5 - A.twilightLimitPenaltyMag(-6.0);
  if (!(limit <= 1.0)) throw new Error(`民用晨昏 -6° 极限应 ≤1，实际 ${limit}`);
  if (!(limit >= -2.0)) throw new Error(`民用晨昏 -6° 极限应 ≥-2（不全黑），实际 ${limit}`);
}
/* 白天（太阳在地平上）：压到星表深度以下，整层自然消失（不靠布尔开关）。 */
for (const alt of [0.0, 5.0, 30.0]) {
  const limit = 6.5 - A.twilightLimitPenaltyMag(alt);
  if (!(limit < -6.0)) throw new Error(`白天 alt=${alt} 极限应 <-6，实际 ${limit}`);
}
/* 天顶亮度表端点：白天 ~4 等、天文夜 21.6 等（V mag/arcsec²）。 */
if (A.skyBrightnessMagArcsec2(60.0) !== 4.0)
  throw new Error(`brightness(60) 应夹到 4.0，实际 ${A.skyBrightnessMagArcsec2(60.0)}`);
if (A.skyBrightnessMagArcsec2(-30.0) !== NIGHT)
  throw new Error(`brightness(-30) 应为夜值 ${NIGHT}，实际 ${A.skyBrightnessMagArcsec2(-30.0)}`);

console.log(`twilight cross-language OK · ${golden.components.length} components + `
  + `${golden.compositions.length} compositions / component worst ${worstComp.toExponential(2)} mag `
  + `(${worstCompAt}) / composition worst ${worstEff.toExponential(2)} mag (${worstEffAt}) ≤ ${PTOL}`);