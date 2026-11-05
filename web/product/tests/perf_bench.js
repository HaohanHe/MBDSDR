/* 性能基准：全天星投影 + 地景逐列反投影的等价负载。
 * 用法：node tests/perf_bench.js
 * 只读加载 00-core / 05-repo-facts / 12-sky-catalog / 31-celestial，
 * 不需要 DOM/Canvas；输出 JSON，供优化前后对比与回归阈值。
 */
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.resolve(__dirname, "..");
const sandbox = {
  console, Math, Date, JSON, Object, Array, String, Number, Boolean,
  RegExp, Error, Map, Set, Float32Array, isNaN, parseInt, parseFloat
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
for (const f of ["00-core.js", "05-repo-facts.js", "12-sky-catalog.js", "31-celestial.js"]) {
  vm.runInContext(fs.readFileSync(path.join(ROOT, "src", f), "utf8"), sandbox, { filename: f });
}

const MB = sandbox.MBDSDR, C = MB.celestial, S = MB.state;
S.time.minutes = 0;
S.site = Object.assign(S.site || {}, { lat: 0, lon: 0 });
const W = 1280, H = 720;

function bench(fn, n) {
  for (let i = 0; i < 5; i++) fn();
  const t0 = process.hrtime.bigint();
  for (let i = 0; i < n; i++) fn();
  return Number(process.hrtime.bigint() - t0) / 1e6 / n;
}

const rows = [];
for (const fov of [22, 60, 100, 158]) {
  C.V.fov = fov; C.V.az = 33; C.V.alt = 15; C.V.intro = 1;
  let shown = 0;
  const cb = () => { shown++; };
  C.eachStar(S, W, H, cb);
  const ms = bench(() => { shown = 0; C.eachStar(S, W, H, cb); }, 40);
  rows.push({ fov, ms: +ms.toFixed(3), stars: shown });
}

/* 地景边界：逐屏幕列 × 纵向粗扫，每点一次球面反投影。
   这里用等价负载测两种步长的成本（真实函数在 30-sky.js 内部）。 */
function scanProxy(step, scanStep) {
  let n = 0;
  for (let x = 0; x <= W; x += step)
    for (let y = 0; y <= H; y += scanStep) { C.unproject(x, y, W, H); n++; }
  return n;
}
C.V.fov = 100;
const nOld = scanProxy(2, 6), nNew = scanProxy(3, 10);
const oldMs = bench(() => scanProxy(2, 6), 12);
const newMs = bench(() => scanProxy(3, 10), 12);

console.log(JSON.stringify({
  stars: C.N,
  starProjection: rows,
  landscapeProxy: {
    old: { step: 2, scan: 6, unprojectCalls: nOld, ms: +oldMs.toFixed(2) },
    lean: { step: 3, scan: 10, unprojectCalls: nNew, ms: +newMs.toFixed(2) },
    reductionPct: +(100 * (1 - newMs / oldMs)).toFixed(1)
  }
}));
