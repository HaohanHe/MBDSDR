/* MBDSDR 产品前端 · 10-data.js
 * 下层数据面：卫星目录 / 能力矩阵 / 工具面 / 数据源适配器。
 *
 * 本文件不含任何业务事实字面量：域名、工具、频率、TLE 历元、写工具模式、
 * 端口、时间倍率全部来自 MB.REPO（build/sync_from_repo.py 只读抽取原仓库代码）。
 * 产品侧只保留：视觉轨道参数、译名、图标、Wikipedia 链接、场景组合。
 *
 * 诚实口径（AGENTS.md §2）：无硬件 → 显式空态；不合成 IQ；演示目录不可用于
 * 精确过境；站址不可信拒绝预报；写工具由宿主 / 外部确认通道把关，
 * 前端不自造放行（gated≠失败，模型自报 arm_manual 无效）。
 */
(function () {
  "use strict";
  const MB = window.MBDSDR, S = MB.state, R = MB.REPO || {};
  const LOCALES = ["zh-CN", "ja-JP", "en-US"];
  const loc = v => ({ "zh-CN": v, "ja-JP": v, "en-US": v });

  /* ---------- 卫星目录（事实：sat_tracker.py 的 BUILTIN_DOWNLINK_HZ / _BUILTIN_TLES） ----------
   * 视觉轨道参数与 Wikipedia 链接是产品侧演示层；信息卡读数一律标演示口径，
   * 不把演示值伪装成实时测量。内置 TLE 仅供离线演示（builtin_tle_meta.py 策略）。
   */
  const VISUAL = {
    "NOAA 15": { orbit: { a: 1.02, b: .48, tilt: .26, w: .00019, phase: .8 }, size: 3.0,
      mode: { "zh-CN": "APT", "ja-JP": "APT", "en-US": "APT" },
      wiki: "https://en.wikipedia.org/wiki/NOAA_15", typeKey: "sat.type.weather" },
    "NOAA 18": { orbit: { a: 1.08, b: .51, tilt: -.34, w: .00020, phase: 2.4 }, size: 3.0,
      mode: loc("APT"), wiki: "https://en.wikipedia.org/wiki/NOAA_18", typeKey: "sat.type.weather" },
    "NOAA 19": { orbit: { a: .94, b: .44, tilt: .58, w: .00021, phase: 4.1 }, size: 3.1,
      mode: loc("APT"), wiki: "https://en.wikipedia.org/wiki/NOAA_19", typeKey: "sat.type.weather" },
    "ISS (ZARYA)": { orbit: { a: .72, b: .72, tilt: .9, w: .00031, phase: 4.4 }, size: 3.8,
      mode: loc("FM / SSTV"), wiki: "https://en.wikipedia.org/wiki/International_Space_Station",
      typeKey: "sat.type.station" },
    "FUNBCUBE-1 (AO-73)": { orbit: { a: 1.20, b: .36, tilt: -1.0, w: .00025, phase: 1.3 }, size: 2.7,
      mode: loc("BPSK"), wiki: "https://en.wikipedia.org/wiki/FUNcube-1", typeKey: "sat.type.comm" },
    "RADFXSAT (FOX-1B)": { orbit: { a: 1.34, b: .58, tilt: .18, w: .00023, phase: 5.5 }, size: 2.6,
      mode: loc("FM"), wiki: "https://en.wikipedia.org/wiki/Fox-1B", typeKey: "sat.type.comm" }
  };
  const fallbackSat = { items: [] };
  const cat = (R.satellites || fallbackSat).items || [];
  const mhz = hz => (Number(hz) / 1e6).toFixed(4).replace(/0+$/, "").replace(/\.$/, "") + " MHz";

  MB.SATS = cat.map((s, i) => {
    const v = VISUAL[s.name] || {
      orbit: { a: .9 + i * .05, b: .45, tilt: .2, w: .0002, phase: i }, size: 3.0,
      mode: loc("—"), wiki: "", typeKey: "sat.type.comm"
    };
    const band = s.downlinkHz ? mhz(s.downlinkHz) : "—";
    return {
      id: s.name, norad: s.norad, typeKey: v.typeKey,
      alias: [s.name, "NORAD " + s.norad],
      band, mode: v.mode, wiki: v.wiki,
      descKey: "sat.catalog.desc",
      orbit: v.orbit, size: v.size,
      kv: [
        { k: "field.downlink", v: band },
        { k: "field.scheme", v: v.mode },
        { k: "field.tleEpoch", v: s.tleEpoch || "—" },
        { k: "field.magnitude", v: "—" },
        { k: "field.distance", v: "—" },
        { k: "field.azAlt", v: "—" },
        { k: "field.riseSet", rise: "—", set: "—" },
        { k: "field.dataPolicy", v: "field.demo" }
      ]
    };
  });

  /* ---------- 能力矩阵：真实工具域（事实）+ 规划未验证（诚实标注） ----------
   * 域名/工具/空域来自 mcp_server.py 的 FROZEN/PYTHON_EXTENSION 域与 _TOOL_DOMAIN。
   * 空域保留为「该面无工具」，不塞假能力凑数（仓库注释：诚实空态）。
   */
  const domainCaps = (R.domains || []).map(d => ({
    id: d.id, icon: d.icon,
    name: { "zh-CN": d.name, "ja-JP": d.ja, "en-US": d.en },
    desc: d.empty ? null : d.sample.join(" · "),
    descKey: d.empty ? "caps.empty" : null,
    group: d.group, planned: false, on: !d.empty,
    tools: d.tools, summary: d.summary, source: d.source
  }));
  const plannedCaps = (R.planned || []).map(p => ({
    id: p.id, icon: p.icon,
    name: { "zh-CN": p.zh, "ja-JP": p.ja, "en-US": p.en },
    desc: null, descKey: "caps.notVerified",
    group: "planned", planned: true, on: false,
    tools: [], summary: p.why, source: p.source
  }));
  MB.CAPS = domainCaps.concat(plannedCaps);
  MB.DOMAINS = domainCaps;
  MB.PLANNED = plannedCaps;

  /* ---------- 工具面：全部为 _TOOL_DOMAIN 里真实存在的工具名 ---------- */
  MB.TOOLS = [
    { name: "list_devices", descKey: "tool.devices", needs: ["system"] },
    { name: "connect", descKey: "tool.connect", needs: ["system"] },
    { name: "set_frequency", descKey: "tool.tune", needs: ["sdr-vfo"] },
    { name: "read_spectrum", descKey: "tool.spectrum", needs: ["sdr-vfo"] },
    { name: "get_satellite_pass", descKey: "tool.pass", needs: ["sat-scan"] },
    { name: "start_recording", descKey: "tool.record", needs: ["export"] },
    { name: "get_status", descKey: "tool.status", needs: ["system"] },
    { name: "sat_link_identify", descKey: "tool.satlink", needs: ["sat-link"] }
  ];

  /* ---------- 场景（科教教培 / 科学研究 / 通联），引用真实域 id ---------- */
  MB.SCENARIOS = [
    { id: "education", nameKey: "scene.edu.name", descKey: "scene.edu.desc",
      caps: ["sdr-vfo", "sat-scan", "decoder-tail"], doc: "scenarios/education.md" },
    { id: "research", nameKey: "scene.res.name", descKey: "scene.res.desc",
      caps: ["sdr-vfo", "system", "export", "decoder-tail"], doc: "scenarios/research.md" },
    { id: "comms", nameKey: "scene.com.name", descKey: "scene.com.desc",
      caps: ["sdr-vfo", "sat-scan", "sat-link", "system"], doc: "scenarios/comms.md" }
  ];

  /* ---------- 默认能力态（持久化优先；规划项恒关） ---------- */
  const saved = MB.store.get("caps", {});
  for (const c of MB.CAPS)
    S.caps[c.id] = c.planned ? false : ((c.id in saved) ? !!saved[c.id] : c.on);
  MB.capById = id => MB.CAPS.find(c => c.id === id) || null;

  /* ---------- 数据源 A：离线诚实空态（默认） ----------
   * 原仓库 headless_api.md：无硬件返回 None / connected=False，绝不伪造数据。
   * 这里工具调用统一返回 not_connected 信封，UI 不伪装成功。
   */
  const OfflineSource = {
    sourceName() { return "bridge.offline"; },
    isLive() { return false; },
    listSatellites() { return MB.SATS.slice(); },
    getTle() { return null; },
    getPasses() { return []; },
    getCapabilities() { return MB.CAPS.filter(c => S.caps[c.id]); },
    readSpectrum() { return null; },
    invokeTool(name, args) {
      return Promise.resolve(MB.envelope("not_connected", name, {
        offline: true,
        suggestion: "connect a device or enable the ControlHub endpoint"
      }));
    }
  };

  /* ---------- 数据源 B：ControlHub 回环 HTTP（真实通道） ----------
   * 端口/端点/写工具模式全部来自 MB.REPO（cpp/src/core/tokens.h 等）。
   * POST /command body = {tool, args}。写命令的门在 **C++ 宿主**
   * （row->write && !writeEnabled_.load()），Web 前端无权打开、也不自判；
   * 内核回 gated:true 时如实回显，与执行失败严格区分。
   */
  const hub = (R.channels && R.channels.controlHub) || { host: "127.0.0.1", port: 0 };
  const ControlHubSource = {
    _enabled: false,
    sourceName() { return "bridge.online"; },
    isLive() {
      return this._enabled && typeof window.fetch === "function" && !!hub.port;
    },
    enable(on) { this._enabled = !!on; return this.isLive(); },
    endpoint(path) { return "http://" + hub.host + ":" + hub.port + path; },
    listSatellites() { return MB.SATS.slice(); },
    getTle() { return null; },           // 通道未提供 TLE → 诚实 null，不编造
    getPasses() { return []; },          // 站址不可信时不做过境预报
    getCapabilities() { return MB.CAPS.filter(c => S.caps[c.id]); },
    readSpectrum() {
      if (!this.isLive()) return null;
      return this.invokeTool("read_spectrum", { nfft: 1024 }).then(
        r => (r && r.ok && Array.isArray(r.data) ? r.data : null));
    },
    invokeTool(name, args) {
      if (!this.isLive()) return OfflineSource.invokeTool(name, args);
      // 前端不判「是否已确认」——那是 C++ 宿主 writeEnabled_ 的职责。
      // 只剥掉模型自报标记（内核明确拒绝），其余 args 原样下发。
      const clean = MB.stripSelfReport(args);
      return window.fetch(this.endpoint(hub.postCommand ? hub.postCommand.path : "/command"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tool: name, args: clean.args })
      }).then(r => r.json()).then(r => {
        if (!r || typeof r !== "object") return MB.envelope("bad_envelope", name);
        // ControlHub::execute() 的回执原样透传：ok:false / gated:true 都是诚实结果
        const env = Object.assign({ ok: false, tool: name, data: null }, r);
        if (env.gated === true) {
          env.notFailure = true;             // 未获确认 ≠ 执行失败
          env.gateFacts = MB.gateFacts("controlHub");
        }
        if (clean.selfReport.length) env.selfReportRejected = clean.selfReport;
        return env;
      }).catch(err => MB.envelope("unreachable", name, {
        detail: String(err && err.message || err)
      }));
    }
  };

  /* ---------- 数据源 C：宿主注入桥（Qt/WebEngine 可选） ----------
   * 原仓库以 ControlHub / MCP 为准；此桥只作为嵌入式宿主的可选便利通道。
   */
  const BridgeSource = {
    _bridge: null,
    lastError: null,
    /* 宿主桥没提供方法 = 诚实的空值；宿主桥抛异常 = 另一回事。
       旧实现 catch(_) 一律返回 null/[]，UI 分不清「没有」和「坏了」，
       也不留任何记录（体检 B5）。 */
    _call(method, fallback, args) {
      try {
        const b = this._bridge;
        return (b && typeof b[method] === "function")
          ? b[method].apply(b, args || []) : fallback;
      } catch (err) {
        this.lastError = { method, detail: String(err && err.message || err), at: Date.now() };
        MB.emit("bridge:exception", this.lastError);
        return fallback;
      }
    },
    sourceName() { return "bridge.online"; },
    isLive() { return !!(this._bridge && typeof this._bridge.invoke === "function"); },
    listSatellites() {
      const b = this._bridge;
      return (b && Array.isArray(b.satellites)) ? b.satellites : MB.SATS.slice();
    },
    getTle(norad) { return this._call("getTle", null, [norad]); },
    getPasses(norad) { return this._call("getPasses", [], [norad]); },
    getCapabilities() { return MB.CAPS.filter(c => S.caps[c.id]); },
    readSpectrum() { return this._call("readSpectrum", null, []); },
    invokeTool(name, args) {
      if (!this.isLive()) return OfflineSource.invokeTool(name, args);
      const clean = MB.stripSelfReport(args);
      // Python 内核的门：写工具必须由外部确认通道签发一次性 confirm_token。
      // 前端无权签发；缺 token 时**不送进内核**，直接给诚实 gated 回执。
      if (MB.isWriteTool(name) && !MB.hasConfirmToken(clean.args))
        return Promise.resolve(MB.gateEnvelope(name, "mcp", {
          selfReportRejected: clean.selfReport,
          suggestion: "external confirm_token required; the front end cannot issue one"
        }));
      try {
        const r = this._bridge.invoke(name, clean.args);
        return Promise.resolve(r).then(v => {
          // 宿主返回结构化回执时原样透传，绝不能把 gated:true / ok:false
          // 包成 {ok:true, data:...} 伪装成功。
          if (v && typeof v === "object" && ("ok" in v || "gated" in v || "error" in v))
            return Object.assign({ ok: false, tool: name, data: null }, v);
          return { ok: true, tool: name, data: v == null ? null : v };
        }).catch(err => MB.envelope("exception", name, {
          detail: String(err && err.message || err)
        }));
      } catch (err) {
        return Promise.resolve(MB.envelope("exception", name, {
          detail: String(err && err.message || err)
        }));
      }
    }
  };

  MB.offlineSource = OfflineSource;
  MB.controlHub = ControlHubSource;
  MB.bridgeSource = BridgeSource;

  MB.detectSource = () => {
    const b = window.mbdsdrBridge;
    BridgeSource._bridge = b || null;
    const qs = (typeof location !== "undefined" && location.search) || "";
    const wantHub = /(\?|&)hub=1(&|$)/.test(qs) || !!window.MBDSDR_USE_HUB;
    ControlHubSource.enable(wantHub);
    for (const s of [OfflineSource, ControlHubSource, BridgeSource]) MB.assertContract(s);
    if (BridgeSource.isLive()) { MB.data = BridgeSource; S.source = "bridge"; }
    else if (ControlHubSource.isLive()) { MB.data = ControlHubSource; S.source = "controlhub"; }
    else { MB.data = OfflineSource; S.source = "offline"; }
    MB.emit("source:changed", S.source);
    return MB.data;
  };
  MB.detectSource();

})(window);
