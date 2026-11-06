/* MBDSDR 产品前端 · 00-core.js
 * 下层：领域状态 / 事件总线 / 持久化 / 投影数学 / 内核适配契约。
 * 不含任何渲染代码，不依赖 DOM 结构，可单独被 Node 无头测试加载。
 * 设计纪律：不引 AGPL 代码（Stellarium Web Engine 只学机制），本文件全部自研。
 */
(function (global) {
  "use strict";

  const MB = global.MBDSDR = global.MBDSDR || {};

  MB.VERSION = "0.3.0";
  MB.BUILD = "product";
  MB.LICENSE = "MIT (UI) · mechanisms studied from Stellarium Web Engine / AGPL-3.0; no AGPL code included";

  MB.$ = sel => document.querySelector(sel);
  MB.$$ = sel => Array.from(document.querySelectorAll(sel));

  /* ---------- 事件总线：跨层只通过事件通信 ---------- */
  const listeners = Object.create(null);
  MB.on = (topic, fn) => {
    (listeners[topic] || (listeners[topic] = [])).push(fn);
    return () => MB.off(topic, fn);
  };
  MB.off = (topic, fn) => {
    const a = listeners[topic];
    if (a) { const i = a.indexOf(fn); if (i >= 0) a.splice(i, 1); }
  };
  MB.emit = (topic, payload) => {
    for (const fn of (listeners[topic] || []).slice()) {
      try { fn(payload); } catch (err) { console.error("[MBDSDR bus]", topic, err); }
    }
  };

  /* ---------- 持久化（隐私模式下 localStorage 可能抛异常，降级内存） ---------- */
  const mem = Object.create(null);
  MB.store = {
    get(key, fallback) {
      try {
        const raw = global.localStorage.getItem("mbdsdr:" + key);
        return raw === null ? fallback : JSON.parse(raw);
      } catch (_) { return key in mem ? mem[key] : fallback; }
    },
    set(key, value) {
      mem[key] = value;
      try { global.localStorage.setItem("mbdsdr:" + key, JSON.stringify(value)); }
      catch (_) { /* 内存态即可 */ }
    }
  };

  /* ---------- 领域状态 ---------- */
  MB.state = {
    form: MB.store.get("form", "sky"),          // sky | pro
    locale: MB.store.get("locale", "zh-CN"),    // zh-CN | ja-JP | en-US
    docLocale: "zh-CN",
    cam: { x: 0, y: 0, scale: 1, tx: 0, ty: 0, ts: 1, locked: null },
    time: { rate: 1, minutes: 0, rateIdx: 1 },
    sky: Object.assign({
      // 星象仪（时空主导）：银河默认关 = 无数据·不提供，不画装饰光带
      orbits: true, labels: true, milkyway: false, landscape: true,
      grid: false, eqgrid: false, night: false, sats: true, atmosphere: true,
      constellations: true, dso: true, hud: true
    }, MB.store.get("sky", {})),
    caps: {},                                   // 10-data 由 MB.REPO.domains 生成的能力位
    site: (() => {
      const base = { name: "corner.location", lat: 0.0, lon: 0.0,
                     alt: 0.0, trusted: false };
      // 只恢复「是否已校准」标志；坐标永远用中性示例值，不持久化任何真实位置
      // （对齐 AGENTS.md §1 身份红线与 mbdsdr_ai/station.py 的站址守卫）
      const saved = MB.store.get("site", {});
      if (saved && typeof saved === "object") base.trusted = !!saved.trusted;
      return base;
    })(),
    source: "offline",                          // offline | controlhub | bridge
    flow: { sdr: null, gimbal: null, gnss: null }
  };

  MB.persist = what => {
    if (what === "form") MB.store.set("form", MB.state.form);
    if (what === "locale") MB.store.set("locale", MB.state.locale);
    if (what === "sky") MB.store.set("sky", MB.state.sky);
    if (what === "caps") MB.store.set("caps", MB.state.caps);
    if (what === "site") MB.store.set("site", MB.state.site);
  };

  /* ---------- 数学 / 投影（自研；引擎只提供机制参考） ----------
   * 小视场近似：x = cx + f·Δaz，y = cy − f·Δalt；f = k·scale。
   * 缩放改 scale（等价于 FOV），不做相机推拉 —— 见 docs/03-architecture.md。
   */
  MB.math = {
    clamp: (v, lo, hi) => Math.max(lo, Math.min(hi, v)),
    lerp: (a, b, t) => a + (b - a) * t,
    K: 260,
    worldToScreen(p, cam, view) {
      const k = cam.scale * MB.math.K;
      return { x: view.w / 2 + (p.x - cam.x) * k, y: view.h / 2 - (p.y - cam.y) * k };
    },
    screenToWorld(x, y, cam, view) {
      const k = cam.scale * MB.math.K;
      return { x: cam.x + (x - view.w / 2) / k, y: cam.y - (y - view.h / 2) / k };
    },
    smoothstep: (a, b, x) => {
      const t = MB.math.clamp((x - a) / (b - a), 0, 1);
      return t * t * (3 - 2 * t);
    }
  };

  /* ---------- 下层内核适配契约（实现见 10-data.js） ----------
   * UI 永远不直接碰硬件。真实通道由 build/sync_from_repo.py 从原仓库代码抽取：
   *   ControlHub 回环 HTTP（cpp/src/core/tokens.h 端口常量 + POST /command）、
   *   MCP tools/list / tools/call（mbdsdr_ai/core/mcp_server.py）、
   *   Headless SDRController（docs/headless_api.md）。
   * 写工具确认门由内核 / 宿主持有，事实见 MB.REPO.gates（脚本从 C++ 与 Python
   * 代码抽取）：ControlHub 是宿主写开关，Python 内核是外部签发的 confirm_token。
   * **模型自报 arm_manual 明确无效**；gated:true 是「未获确认」，不是执行失败。
   * 契约见 docs/03-architecture.md §4 与 docs/09-repo-alignment.md §3。
   */
  MB.CONTRACT = Object.freeze([
    "sourceName", "isLive", "listSatellites", "getTle",
    "getPasses", "getCapabilities", "readSpectrum", "invokeTool"
  ]);

  MB.assertContract = source => {
    const missing = MB.CONTRACT.filter(k => typeof source[k] !== "function");
    if (missing.length) throw new Error("DataSource contract missing: " + missing.join(", "));
    return true;
  };

  /* 写工具判定：模式表来自 mbdsdr_ai/tool_registry.py: WRITE_TOOL_NAME_PATTERNS。
     产品不自己编门规则，避免与内核漂移。 */
  MB.isWriteTool = name => {
    const pats = (MB.REPO && MB.REPO.writePatterns) || [];
    return pats.some(p => {
      const re = new RegExp("^" + String(p).replace(/[.+^${}()|[\]\\]/g, "\\$&")
        .replace(/\*/g, ".*") + "$");
      return re.test(String(name));
    });
  };

  /* 统一诚实回执：无硬件 / 未接内核 / 人工门拦截，三种都不是「成功」。 */
  MB.envelope = (kind, tool, extra) => Object.assign({
    ok: false, tool: tool || "", data: null
  }, { error: kind }, extra || {});

  /* ---------- 写工具确认门（事实源 MB.REPO.gates，脚本只读抽取内核代码） ----------
   * 前端只做三件事：剥离模型自报标记、透传**外部已签发**的 token、如实区分
   * gated 与执行失败。前端绝不签发 token，也绝不因 arm_manual 放行——两条
   * 真实通道都不认这个自报标记（C++ 是 writeEnabled_，Python 是 confirm_token）。
   */
  MB.gateFacts = channel => ((MB.REPO && MB.REPO.gates) || {})[channel] || null;
  MB.stripSelfReport = args => {
    const a = Object.assign({}, args || {});
    const f = MB.gateFacts("mcp");
    const keys = (f && f.selfReportFields) || [];
    const seen = keys.filter(k => k in a);
    for (const k of seen) delete a[k];
    return { args: a, selfReport: seen };
  };
  MB.hasConfirmToken = args => {
    const f = MB.gateFacts("mcp");
    const keys = (f && f.tokenFields) || [];
    return keys.some(k => !!(args && args[k]));
  };
  MB.gateEnvelope = (name, channel, extra) => MB.envelope(
    channel === "mcp" ? "manual_gate_required" : "gated", name,
    Object.assign({ gated: true, gate: channel, notFailure: true,
                    facts: MB.gateFacts(channel) }, extra || {}));

})(typeof window !== "undefined" ? window : globalThis);
