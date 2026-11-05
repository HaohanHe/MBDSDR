/* MBDSDR 产品前端 · 50-dialog.js
 * 上层：模型现场能力协商（"模型问你"，不是预制向导）。
 * 铁律实现处：
 *   - 追踪/接收/指向按钮只负责"打开对话"，模型才提问；没有固定步骤条
 *   - 定轨在 UI 上零入口：只有 sendAI() 里用户说出口，isOrbitRequest() 才触发
 *   - 工具名全部来自原仓库真实工具面；调用走 MB.data.invokeTool()
 *   - 离线 → not_connected 回执；写工具未确认 → gated:true（人工门，不是失败）
 */
(function () {
  "use strict";
  const MB = window.MBDSDR, S = MB.state;
  const $ = MB.$, t = (k, v) => MB.i18n.t(k, v);
  const stream = $("#aiStream");
  stream.setAttribute("data-i18n-skip", "");

  function bubble(whoKey, html, cls) {
    const d = document.createElement("div");
    d.className = "msg " + (cls || "model");
    d.innerHTML = `<div class="who">${t(whoKey)}</div>${html}`;
    stream.appendChild(d); stream.scrollTop = stream.scrollHeight;
    return d;
  }
  function tool(name, state, cls) {
    const d = document.createElement("div");
    d.className = "toolcard";
    d.innerHTML = `${name}<span class="st ${cls || "ok"}">${state}</span>`;
    stream.appendChild(d); stream.scrollTop = stream.scrollHeight;
  }
  function quicks(opts) {
    const w = document.createElement("div");
    w.className = "quick";
    w.innerHTML = opts.map(o => `<button data-q="${o[1]}">${t(o[0])}</button>`).join("");
    w.addEventListener("click", e => {
      const b = e.target.closest("button"); if (!b) return;
      bubble("ai.you", b.textContent, "user");
      w.remove();
      respond(b.dataset.q);
    });
    stream.appendChild(w); stream.scrollTop = stream.scrollHeight;
  }
  function typeText(el, text, done) {
    let i = 0;
    el.innerHTML = `<div class="who">${t("ai.head")}</div><span class="tw"></span>`;
    const span = el.querySelector(".tw");
    const timer = setInterval(() => {
      span.textContent = text.slice(0, ++i);
      stream.scrollTop = stream.scrollHeight;
      if (i >= text.length) { clearInterval(timer); done && done(); }
    }, 16);
  }

  /* 调用内核工具并如实显示回执：gated / not_connected / unreachable 各有口径 */
  function callTool(name, args, stateKey) {
    MB.data.invokeTool(name, args || {}).then(res => {
      if (res && res.ok) tool(name + "(…)", t(stateKey || "ai.toolCalled"), "ok");
      else if (res && res.gated) tool(name + "(…)",
        t(res.error === "manual_gate_required" || res.error === "manual_gate_unavailable"
          ? "bridge.needToken" : "bridge.gated"), "wait");
      else if (res && res.offline) tool(name + "(…)", t("tool.offline"), "off");
      else tool(name + "(…)", t("ai.unavailable"), "off");
    }).catch(() => tool(name + "(…)", t("ai.unavailable"), "off"));
  }

  const Q = {
    rtl: ["dialog.q.rtl", "sdr:rtl"], pluto: ["dialog.q.pluto", "sdr:pluto"],
    noSdr: ["dialog.q.noSdr", "sdr:none"],
    gy: ["dialog.q.gimbalYes", "gimbal:yes"], gn: ["dialog.q.gimbalNo", "gimbal:no"],
    ny: ["dialog.q.gnssYes", "gnss:yes"], nn: ["dialog.q.gnssNo", "gnss:no"],
    findRtl: ["dialog.q.findRtl", "sdr:rtl"]
  };

  function open(act, sat) {
    $("#aiDrawer").classList.add("open");
    $("#capDrawer").classList.add("folded");
    $("#capDrawer").style.display = "none";
    const name = sat ? sat.id : "—";
    stream.innerHTML = "";
    S.flow = { sdr: null, gimbal: null, gnss: null };

    const key = act === "receive" ? "dialog.open.receive"
      : act === "track" ? "dialog.open.track"
        : act === "point" ? "dialog.open.point" : "dialog.open.calib";
    let line = t(key, { name });
    if (!S.site.trusted) line += " " + t("site.untrusted") + ". ";
    if (!MB.data.getTle(sat ? sat.norad : "")) line += " " + t("bridge.tleMissing") + ".";

    const first = bubble("ai.head", "");
    typeText(first, line, () => {
      callTool("get_status", {}, "ai.toolCalled");
      quicks([Q.rtl, Q.pluto, Q.noSdr, Q.gy, Q.gn, Q.ny]);
    });
  }

  function respond(key) {
    const band = S.cam.locked ? S.cam.locked.band : "—";
    const map = {
      "sdr:rtl": () => {
        S.flow.sdr = "RTL-SDR";
        return { text: t("dialog.r.rtl", { band }), tools: [["set_frequency(Hz)", "ai.available", "ok"]],
          next: [Q.gy, Q.gn] };
      },
      "sdr:pluto": () => {
        S.flow.sdr = "ADALM-Pluto";
        return { text: t("dialog.r.pluto"), tools: [
          ["set_frequency(325e6..3.8e9)", "tool.bandLimited", "wait"],
          ["sat_link_tuning(freq)", "tool.external", "wait"]], next: [Q.gy, Q.gn, Q.findRtl] };
      },
      "sdr:none": () => {
        S.flow.sdr = null;
        return { text: t("dialog.r.noSdr"), tools: [
          ["set_frequency(…)", "tool.noDevice", "off"],
          ["get_satellite_pass(…)", "tool.offline", "off"]], next: [Q.gy, Q.gn] };
      },
      "gimbal:yes": () => {
        S.flow.gimbal = true;
        return { text: t("dialog.r.gimbalYes"), tools: [
          ["gimbal_move(az, el)", "bridge.gated", "wait"],
          ["get_status(…)", "ai.available", "ok"]], next: [Q.ny, Q.nn] };
      },
      "gimbal:no": () => {
        S.flow.gimbal = false;
        return { text: t("dialog.r.gimbalNo"), tools: [
          ["gimbal_move(az, el)", "pro.noGimbal", "off"],
          ["get_satellite_pass(…)", "tool.degraded", "wait"]], next: [Q.ny, Q.nn] };
      },
      "gnss:yes": () => { S.flow.gnss = true; return finish(); },
      "gnss:no": () => { S.flow.gnss = false; return finish(); }
    };
    const r = (map[key] || (() => ({ text: t("dialog.continue"), tools: [], next: [Q.rtl, Q.noSdr] })))();
    setTimeout(() => {
      const m = bubble("ai.head", "");
      typeText(m, r.text, () => {
        for (const [name, stateKey, cls] of (r.tools || [])) tool(name, t(stateKey), cls);
        if (r.next) quicks(r.next);
      });
    }, 260);
  }

  function finish() {
    const f = S.flow, name = S.cam.locked ? S.cam.locked.id : "—";
    const parts = [
      f.sdr || t("dialog.part.noSdr"),
      f.gimbal ? t("dialog.part.gimbal") : t("dialog.part.fixed"),
      f.gnss ? t("dialog.part.gnss") : t("dialog.part.freerun")
    ].join(" · ");
    const plan = f.sdr && f.gimbal ? t("dialog.plan.full")
      : f.sdr ? t("dialog.plan.sdr") : t("dialog.plan.none");
    return {
      text: t("dialog.finish", { parts, name, plan }),
      tools: [["get_status(…)", "ai.toolCalled", "ok"],
      ["sat_link_tuning(…)", f.sdr ? "ai.generated" : "tool.offline",
        f.sdr ? "ok" : "off"]],
      next: null
    };
  }

  /* ---------- 对话输入：定轨等任务的唯一触发口 ---------- */
  function sendAI() {
    const inp = $("#aiInput"), v = inp.value.trim();
    if (!v) return;
    bubble("ai.you", v, "user");
    inp.value = "";
    setTimeout(() => {
      const m = bubble("ai.head", "");
      if (MB.i18n.isOrbitRequest(v)) {
        // 只有用户说出口，模型才动；UI 上没有这个能力位
        typeText(m, t("dialog.orbit"), () => quicks([Q.rtl, Q.pluto, Q.noSdr, Q.ny]));
      } else {
        typeText(m, t("dialog.generic"), () => callTool("get_status", {}, "ai.toolScanned"));
      }
    }, 240);
  }
  $("#aiSend").addEventListener("click", sendAI);
  $("#aiInput").addEventListener("keydown", e => { if (e.key === "Enter") sendAI(); });
  $("#aiClose").addEventListener("click", () => {
    $("#aiDrawer").classList.remove("open");
    $("#capDrawer").style.display = "";
  });

  MB.on("i18n:refresh", () => { stream.innerHTML = ""; });

  MB.dialog = { open, respond, sendAI, finish };
})(window);
