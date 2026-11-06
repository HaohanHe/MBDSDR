/* MBDSDR 产品前端 · 无头运行时烟测
 * 用 mock DOM/Canvas 按真实加载顺序执行全部 src 模块，跑帧、切形态、
 * 切三语、打开文档中心、触发模型协商，抓初始化顺序与绘制期异常。
 * 用法：node tests/verify_runtime.js
 */
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.resolve(__dirname, "..");
const html = fs.readFileSync(path.join(ROOT, "index.html"), "utf8");
const files = [...html.matchAll(/<script src="src\/([^"]+)"><\/script>/g)].map(m => m[1]);
if (!files.length) { console.error("未找到分层脚本"); process.exit(1); }

const errors = [];
const rafQueue = [];
const timers = [];
let textNodes = [];

class TextNode {
  constructor(value, parent) {
    this.nodeType = 3;
    this.nodeValue = value;
    this.parentElement = parent;
    this._i18nSrc = undefined;
  }
}
class ClassList {
  constructor() { this.s = new Set(); }
  add(...c) { c.forEach(x => this.s.add(x)); }
  remove(...c) { c.forEach(x => this.s.delete(x)); }
  contains(c) { return this.s.has(c); }
  toggle(c, force) {
    const want = force === undefined ? !this.s.has(c) : !!force;
    want ? this.s.add(c) : this.s.delete(c);
    return want;
  }
}
const gradient = { addColorStop() {} };
const ctx2d = new Proxy({}, {
  get(t, k) {
    if (k === "createRadialGradient" || k === "createLinearGradient") return () => gradient;
    if (k in t) return t[k];
    return () => {};
  },
  set(t, k, v) { t[k] = v; return true; }
});

class El {
  constructor(id = "", tag = "div") {
    this.id = id; this.tagName = tag.toUpperCase();
    this.classList = new ClassList();
    this.style = {}; this.dataset = {}; this.attrs = {};
    this.handlers = {}; this.children = [];
    this.clientWidth = 1280; this.clientHeight = 720;
    this.scrollTop = 0; this.scrollHeight = 1000;
    this.value = ""; this.width = 0; this.height = 0;
    this._html = ""; this._text = new TextNode("", this);
    textNodes.push(this._text);
    this.on = false;
  }
  get innerHTML() { return this._html; }
  set innerHTML(v) {
    this._html = String(v);
    this._text.nodeValue = String(v).replace(/<[^>]*>/g, " ").slice(0, 200);
  }
  get textContent() { return this._text.nodeValue; }
  set textContent(v) { this._text.nodeValue = String(v); }
  getContext() { return ctx2d; }
  getBoundingClientRect() { return { left: 0, top: 0, width: 1280, height: 720 }; }
  addEventListener(type, fn) { (this.handlers[type] || (this.handlers[type] = [])).push(fn); }
  removeEventListener() {}
  fire(type, ev) { for (const fn of (this.handlers[type] || []).slice()) fn(ev || {}); }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  removeAttribute(k) { delete this.attrs[k]; }
  querySelector(sel) { return new El("", "span"); }
  querySelectorAll() { return []; }
  appendChild(c) { this.children.push(c); return c; }
  insertAdjacentHTML(_where, html) { this.innerHTML += html; }
  remove() {}
  closest(sel) {
    if (!sel) return null;
    if (sel.startsWith("#") && this.id === sel.slice(1)) return this;
    if (sel.startsWith(".") && this.classList.contains(sel.slice(1))) return this;
    return null;
  }
  requestFullscreen() {}
}

const cache = new Map();
const getEl = (id) => {
  if (!cache.has(id)) cache.set(id, new El(id));
  return cache.get(id);
};
// 预置带 dataset 的集合元素
const modeButtons = [new El("", "button"), new El("", "button")];
modeButtons[0].dataset.form = "sky"; modeButtons[1].dataset.form = "pro";
modeButtons[0].classList.add("on");
const toolbarButtons = ["orbits", "labels", "milkyway", "atmosphere", "landscape",
  "grid", "night", "sats", "time", "fullscreen"].map(k => {
  const e = new El("", "button"); e.dataset.tb = k; return e;
});
const attrEls = [getEl("searchInput"), getEl("aiInput"), getEl("proInput"), getEl("langSwitch")];
Object.assign(getEl("searchInput").attrs, { placeholder: "搜索卫星 / 天体…（试试 METEOR 或 ISS）" });
Object.assign(getEl("aiInput").attrs, { placeholder: "直接跟模型说…（能力协商 / 下达任务）" });
Object.assign(getEl("proInput").attrs, { placeholder: "对模型说话…（能力协商 / 下达任务）" });
Object.assign(getEl("langSwitch").attrs, { title: "Language / 语言 / 言語" });
getEl("skyForm").classList.add("on");

const document = {
  body: new El("body", "body"),
  documentElement: new El("html", "html"),
  title: "",
  fullscreenElement: null,
  exitFullscreen() {},
  querySelector(sel) {
    if (sel.startsWith("#")) return getEl(sel.slice(1));
    return new El("", "div");
  },
  querySelectorAll(sel) {
    if (sel === "#modeSwitch button") return modeButtons;
    if (sel === "#skyToolbar [data-tb]") return toolbarButtons;
    if (sel.includes("[title],[placeholder]")) return attrEls;
    if (sel.startsWith("[data-")) return [new El("", "div")];
    return [];
  },
  createElement(tag) { return new El("", tag); },
  createTreeWalker() {
    const snap = textNodes.slice();
    let i = 0;
    return { nextNode() { return i < snap.length ? snap[i++] : null; }, get currentNode() { return snap[i - 1]; } };
  }
};

const storage = new Map();
const sandbox = {
  console, document,
  NodeFilter: { SHOW_TEXT: 4 },
  localStorage: {
    getItem: k => (storage.has(k) ? storage.get(k) : null),
    setItem: (k, v) => storage.set(k, String(v)),
    removeItem: k => storage.delete(k)
  },
  devicePixelRatio: 1,
  requestAnimationFrame(fn) { rafQueue.push(fn); return rafQueue.length; },
  cancelAnimationFrame() {},
  setTimeout(fn, ms) { timers.push({ fn, ms }); return timers.length; },
  clearTimeout() {},
  setInterval(fn, ms) { timers.push({ fn, ms, interval: true }); return timers.length; },
  clearInterval() {},
  addEventListener(type, fn) { (sandbox._win || (sandbox._win = {}))[type] = fn; },
  Math, Date, Promise, JSON, Object, Array, String, Number, Boolean, RegExp, Error, Set, Map,
  isNaN, parseInt, parseFloat
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);

// 按真实顺序加载
for (const name of files) {
  const code = fs.readFileSync(path.join(ROOT, "src", name), "utf8");
  try { vm.runInContext(code, sandbox, { filename: name }); }
  catch (e) { errors.push(`LOAD ${name}: ${e.stack}`); }
}

function frames(n, t0) {
  let ts = t0 || 0;
  for (let i = 0; i < n; i++) {
    const batch = rafQueue.splice(0, rafQueue.length);
    if (!batch.length) break;
    ts += 16;
    for (const fn of batch) {
      try { fn(ts); } catch (e) { errors.push(`FRAME: ${e.stack}`); }
    }
  }
  return ts;
}
function flushTimers(limit = 40) {
  const list = timers.splice(0, limit);
  for (const { fn, interval } of list) {
    if (interval) continue;   // 时钟周期回调不重复执行
    try { fn(); } catch (e) { errors.push(`TIMER: ${e.stack}`); }
  }
}
function click(el, ev) { try { el.fire("click", ev); } catch (e) { errors.push(`CLICK ${el.id}: ${e.stack}`); } }

let ts = 0;
try {
  ts = frames(8);
  // 切到专业模式并跑帧（drawWaterfall 路径）
  click(getEl("modeSwitch"), { target: { closest: () => modeButtons[1] } });
  ts = frames(5, ts);
  click(getEl("modeSwitch"), { target: { closest: () => modeButtons[0] } });
  ts = frames(4, ts);
  // 三语切换
  for (let i = 0; i < 3; i++) { click(getEl("langSwitch")); ts = frames(2, ts); }
  // 工具栏：时间流速 + 图层开关
  click(getEl("skyToolbar"), { target: { closest: () => toolbarButtons[8] } });
  click(getEl("skyToolbar"), { target: { closest: () => toolbarButtons[3] } });
  ts = frames(3, ts);
  // 文档中心：打开 + 选一篇 + 切文档语言
  click(getEl("docOpen"));
  const first = sandbox.MBDSDR_DOC_DATA && sandbox.MBDSDR_DOC_DATA[0];
  if (first) {
    const b = new El("", "button"); b.dataset.doc = first.id;
    click(getEl("docNav"), { target: { closest: () => b } });
  }
  if (!getEl("docBody").innerHTML.includes("data-doc-link")) {
    errors.push("DOC-LINK: 文档内相对链接未转成站内跳转");
  }
  click(getEl("docLang")); click(getEl("docLang"));
  click(getEl("docClose"));
  // 自动锁定演示（启动时的 setTimeout）
  flushTimers(); ts = frames(4, ts);
  // 模型协商：动作按钮 + 对话输入（含定轨关键词）
  const actBtn = new El("", "button"); actBtn.dataset.act = "receive";
  click(getEl("satCard"), { target: { closest: () => actBtn } });
  flushTimers(); ts = frames(3, ts);
  getEl("aiInput").value = "定轨";
  click(getEl("aiSend"));
  flushTimers(); ts = frames(2, ts);
  // 能力矩阵开关
  const capBtn = new El("", "div"); capBtn.dataset.cap = "gimbal";
  click(getEl("capBody"), { target: { closest: () => capBtn } });
  ts = frames(2, ts);
} catch (e) { errors.push(`SEQ: ${e.stack}`); }

// ---------- 回归：体检 B1 / B4 / B5 / B6 ----------
try {
  const X = sandbox.MBDSDR, CX = X.celestial;
  let fakeNow = 1e6;
  sandbox.performance = { now: () => fakeNow };

  // B1：专业模式频谱轮询不再因 sky.now() 冻结而死锁；一次采样只压一行
  let samples = 0, seq = 0;
  const fakeLive = {
    sourceName: () => "bridge.online", isLive: () => true,
    listSatellites: () => [], getTle: () => null, getPasses: () => [],
    getCapabilities: () => [], invokeTool: () => Promise.resolve({ ok: true, data: null }),
    readSpectrum() {
      samples++;
      const a = new Float32Array(32);
      for (let i = 0; i < 32; i++) a[i] = (i + seq++) * 0.01;
      return a;
    }
  };
  const savedData = X.data;
  X.data = fakeLive;
  getEl("proForm").classList.add("on");   // draw() 有形态前置门
  X.workbench.resetChat();
  for (let i = 0; i < 6; i++) { fakeNow += 500; X.workbench.draw(16); }
  if (samples < 5) errors.push(`B1: 专业模式频谱轮询仍死锁（samples=${samples}）`);
  const d = X.workbench.diag ? X.workbench.diag() : null;
  if (!d) errors.push("B1: 缺 workbench.diag 诊断入口");
  else {
    if (d.rows > samples + 1) errors.push(`B1: 瀑布 ${d.rows} 行 > ${samples} 次采样（假滚动回潮）`);
    if (d.distinct === false) errors.push("B1: 瀑布相邻两行完全相同（同一帧被反复压入）");
  }
  X.data = savedData;
  delete sandbox.performance;

  // B4：切语言不得清空对话、不得强拉 FOV
  X.workbench.resetChat();
  const stream = getEl("proStream");
  stream.innerHTML += '<div class="msg user">USER-KEEP-ME</div>';
  X.emit("i18n:refresh");
  if (!stream.innerHTML.includes("USER-KEEP-ME"))
    errors.push("B4: 切语言清空了用户对话");
  if (X.SATS && X.SATS.length) {
    X.sky.lockSat(X.SATS[0]);
    CX.V.fovTarget = 62;
    X.sky.retranslate();
    if (Math.abs(CX.V.fovTarget - 62) > 1e-6)
      errors.push(`B4: 切语言强拉 FOV → ${CX.V.fovTarget}`);
    X.sky.unlock();
  }

  // B5：宿主桥抛异常必须可观测，不能静默吞成「无数据」
  let evt = null;
  X.on("bridge:exception", e => { evt = e; });
  const savedBridge = sandbox.mbdsdrBridge;
  sandbox.mbdsdrBridge = {
    invoke: () => ({ ok: true, data: null }),
    getTle: () => { throw new Error("host tle broken"); }
  };
  X.detectSource();
  X.data.getTle("25544");
  if (!evt || evt.method !== "getTle")
    errors.push("B5: 宿主桥异常被静默吞掉，未发 bridge:exception");
  if (!X.bridgeSource.lastError)
    errors.push("B5: 桥异常未留 lastError");
  sandbox.mbdsdrBridge = savedBridge;
  X.detectSource();

  // PERF：地景全量重算一帧的球面反投影次数（旧逐点粗扫约 7.7 万次）
  getEl("skyForm").classList.add("on");
  CX.V.fov = 100; CX.V.az = 45; CX.V.alt = 8;
  X.sky.draw(16);
  const edgeCalls = X.sky.edgeStat ? X.sky.edgeStat() : -1;
  if (edgeCalls < 0) errors.push("PERF: 缺 sky.edgeStat 可观测入口");
  else if (edgeCalls > 20000)
    errors.push(`PERF: 地景单帧反投影 ${edgeCalls} 次，超过 2 万次上限`);

  // B6：跨越背极点阈值的线段不得被二次 Z 判定整段丢掉
  const fov0 = CX.V.fov, az0 = CX.V.az, alt0 = CX.V.alt;
  CX.V.fov = 150; CX.V.az = 0; CX.V.alt = 0;
  let dropped = 0;
  for (let az = 0; az < 360; az += 15) {
    const front = CX.altAzVector(az, 0);
    const back = CX.altAzVector((az + 177) % 360, 0);
    if (!CX.projectSegment(front, back, 1280, 720)) dropped++;
  }
  if (dropped) errors.push(`B6: ${dropped}/24 条跨阈值线段被整段丢弃`);
  CX.V.fov = fov0; CX.V.az = az0; CX.V.alt = alt0;
} catch (e) { errors.push(`REGRESSION: ${e.stack}`); }

/* 阶段 C 前端契约：宿主接确认通道时，写工具回执必须带 canConfirm，
   点确认只调 bridge.confirm（不得自造 token），确认后要重新发一次调用。 */
function finalize() {
  const MB = sandbox.MBDSDR;
  const summary = {
    modules: files.length,
    docs: MB && MB.docs ? MB.docs.count : 0,
    locale: MB && MB.state ? MB.state.locale : "?",
    caps: MB ? MB.CAPS.length : 0,
    i18nKeys: MB && MB.i18n ? MB.i18n.keys().length : 0,
    textNodes: textNodes.length,
    framesRendered: ts / 16,
    edgeUnproject: MB && MB.sky && MB.sky.edgeStat ? MB.sky.edgeStat() : -1
  };
  console.log("RUNTIME", JSON.stringify(summary));
  if (errors.length) {
    console.log("FAILURES:");
    for (const e of errors.slice(0, 12)) console.log("  " + e.split("\n").slice(0, 3).join("\n  "));
    console.log(`RESULT: ${errors.length} runtime errors`);
    process.exit(1);
  }
  if (!MB || !MB.app || !MB.docs || MB.docs.count < 10) {
    console.log("RESULT: 引导未完整执行 / 文档数据未编译");
    process.exit(1);
  }
  console.log("RESULT: RUNTIME-OK ✅");
}

(function confirmUiCheck() {
  const confirmed = [], calls = [];
  try {
    sandbox.mbdsdrBridge = {
      canConfirm: true,
      toolCount: 82,
      isLive: () => true,
      sourceName: () => "bridge.test",
      getTle: () => null,
      getPasses: () => [],
      readSpectrum: () => null,
      getCapabilities: () => [],
      invoke: (n, a) => {
        calls.push([n, a]);
        return { ok: false, gated: true, error: "manual_gate_required" };
      },
      confirm: (n, a) => { confirmed.push([n, a]); return { ok: true, pending: true }; }
    };
  } catch (e) { errors.push("CONFIRM-UI: " + e); }
  const MB = sandbox.MBDSDR;
  const done = () => { setTimeout(finalize, 0); };
  try { MB.detectSource(); } catch (e) { errors.push("C0b: detectSource 抛错 " + e); }
  if (!MB || !MB.data || typeof MB.data.canConfirm !== "function") {
    errors.push("C0: detectSource 后数据源仍缺 canConfirm()");
    return done();
  }
  if (!MB.data.canConfirm()) errors.push("C1: 宿主给了 confirm() 但 canConfirm() 仍 false");
  Promise.resolve(MB.data.invokeTool("set_frequency", { hz: 1e8 }))
    .then(res => {
      if (!res || !res.gated) errors.push("C2: 写工具未确认时应回 gated");
      else if (!res.canConfirm) errors.push("C3: gated 回执缺 canConfirm（UI 不会给确认按钮）");
      return MB.data.confirmTool("set_frequency", { hz: 1e8 });
    })
    .then(ok => {
      if (!ok) errors.push("C4: confirmTool 未把宿主 pending 当成成功");
      if (confirmed.length !== 1 || confirmed[0][0] !== "set_frequency")
        errors.push("C5: confirmTool 没走 bridge.confirm");
      const sent = JSON.stringify(calls);
      if (/confirm_token|tx_token|arm_manual/.test(sent))
        errors.push("C6: 前端往内核送了 token / 自报字段（应只送业务参数）");
    })
    .catch(e => errors.push("CONFIRM-UI: " + (e && e.stack ? e.stack : e)))
    .then(() => { try { delete sandbox.mbdsdrBridge; MB.detectSource(); } catch (e) {} done(); });
})();
