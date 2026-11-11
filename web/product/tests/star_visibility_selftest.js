/* MBDSDR 产品前端 · 星点「画/不画」跨语言集合相等自检
 *
 * 事实源：mbdsdr_ai/skyengine/star_catalog.py
 *   - _HORIZON_GATE_DEG = -0.6
 *   - 可见 = alt >= 门  且  mag + extinction_delta_mag(alt) <= L
 * golden：build/star-visibility-golden.json（build/gen_star_visibility_golden.py）
 *
 * 这条断言对应 2026-11-11 用户裁定：星点「观感律」允许分层，但
 * 「同一星表、同一时刻、同一 fov，两边画/不画的集合必须完全相等」。
 * 本自检直接调用 31-celestial.js 的 visibleSet（与 eachStar 同一判据），
 * 不含视锥/屏幕裁剪——那是投影层，允许 web 独有。
 */
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.resolve(__dirname, "..");
const GOLDEN = JSON.parse(fs.readFileSync(path.join(ROOT, "build/star-visibility-golden.json"), "utf8"));
const SRC31 = fs.readFileSync(path.join(ROOT, "src/31-celestial.js"), "utf8");
const PREFIX = ["src/12-sky-catalog.js", "src/14-astro-frame.js"];

function loadCelestial(src31) {
  const sandbox = { console, performance: { now: () => 0 } };
  sandbox.window = sandbox;
  sandbox.MBDSDR = {};
  const ctx = vm.createContext(sandbox);
  for (const f of PREFIX) {
    vm.runInContext(fs.readFileSync(path.join(ROOT, f), "utf8"), ctx, { filename: f });
  }
  vm.runInContext(src31, ctx, { filename: "src/31-celestial.js" });
  return sandbox.MBDSDR.celestial;
}

const C = loadCelestial(SRC31);
const META = GOLDEN.meta;
const STATE = { time: { minutes: 0 }, site: { lat: META.station.lat, lon: META.station.lon } };
C.TIME.kernelAttached = false;
C.TIME.baseUnix = (META.jdUt1 - 2440587.5) * 86400;   // 让 nowJd() 严格等于 golden 的 jd

const approx = (a, b) => Math.abs(a - b) <= 1e-12;
const sorted = a => a.slice().sort((x, y) => x - y);

/* 1. 地平门常量必须与内核同值。 */
if (Math.abs(C.HORIZON_GATE_DEG - META.horizonGateDeg) > 1e-15)
  throw new Error(`HORIZON_GATE_DEG=${C.HORIZON_GATE_DEG} vs 内核 ${META.horizonGateDeg}`);
if (!approx(C.HORIZON_SIN, Math.sin(META.horizonGateDeg * Math.PI / 180)))
  throw new Error("HORIZON_SIN 与 -0.6° 不一致");

/* 2. 强断言：web visibleSet 与内核 golden 集合逐一相等。 */
const got = C.visibleSet(STATE, META.limitMag);
const want = sorted(GOLDEN.visibleIndices);
const have = sorted(got);
if (have.length !== want.length) {
  const onlyWeb = have.filter(i => !want.includes(i)).slice(0, 8);
  const onlyKernel = want.filter(i => !have.includes(i)).slice(0, 8);
  throw new Error(`画/不画集合大小不等: web=${have.length} kernel=${want.length}`
    + ` 仅web=[${onlyWeb}] 仅内核=[${onlyKernel}]`);
}
for (let k = 0; k < want.length; k++) {
  if (have[k] !== want[k])
    throw new Error(`画/不画集合第 ${k} 项不等: web=${have[k]} kernel=${want[k]}`);
}
if (have.length !== GOLDEN.visibleAfterGateCount)
  throw new Error(`计数与 golden 不符: ${have.length} vs ${GOLDEN.visibleAfterGateCount}`);

/* 3. 地平带画像（用户点名要的数字，留档防止只报"集合相等"）。 */
console.log(`  画/不画集合相等: ${have.length} 颗（同星表 25,792 / J2000 / L=${META.limitMag}）`);
console.log(`  地平带 alt∈(-1.2°,-0.6°]: 全表 ${GOLDEN.bandStarCount} 颗 · `
  + `旧门多画 ${GOLDEN.addedByOldGateCount} 颗 · `
  + `fov${META.fovDeg} 画面 ${GOLDEN.frameAfterCount} vs ${GOLDEN.frameBeforeCount}`);
console.log(`  默认广角 fov60 (L=${GOLDEN.meta.defaultWideField.limitMag}): `
  + `after=${GOLDEN.meta.defaultWideField.after} before=${GOLDEN.meta.defaultWideField.before} `
  + `（消光把该带整段压黑，差 ${GOLDEN.meta.defaultWideField.added}）`);

/* 4. 变异反证：把地平门改回 -1.2°，集合必须变红且恰好等于 before 集。 */
const mutated = SRC31.replace("const HORIZON_GATE_DEG = -0.6;", "const HORIZON_GATE_DEG = -1.2;");
if (mutated === SRC31) throw new Error("变异注入失败：HORIZON_GATE_DEG 锚点不存在");
const CM = loadCelestial(mutated);
CM.TIME.kernelAttached = false;
CM.TIME.baseUnix = C.TIME.baseUnix;
const mut = sorted(CM.visibleSet(STATE, META.limitMag));
const beforeWant = sorted(GOLDEN.visibleIndices.concat(GOLDEN.addedIndices));
if (mut.length !== beforeWant.length || mut.some((v, k) => v !== beforeWant[k]))
  throw new Error(`变异集合 != 旧门 before 集: ${mut.length} vs ${beforeWant.length}`);
if (mut.length === have.length)
  throw new Error("变异存活：-1.2° 门与 -0.6° 门集合相同（golden 失去区分力）");
console.log(`  变异反证：地平门改回 -1.2° → 集合多出 ${mut.length - have.length} 颗 `
  + `(${have.length} → ${mut.length})，与 golden.addedIndices 完全一致`);

console.log("[star-visibility] PASS");
