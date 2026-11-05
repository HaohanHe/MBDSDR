/* MBDSDR 产品前端 · 40-workbench.js
 * 上层：专业模式（传统 SDR 工具化 + 模型协作栏）。
 *
 * 诚实口径对齐 desktop/spectrum_widget.py：无 SDR / 无 IQ 时**只画网格 +
 * 「未连接 SDR」提示**，绝不合成谱线或预填充瀑布历史冒充测量。
 * 接上 ControlHub 后，谱行来自 read_spectrum 的真实回执；拿不到数据就是空态。
 */
(function () {
  "use strict";
  const MB = window.MBDSDR, S = MB.state;
  const $ = MB.$, t = (k, v) => MB.i18n.t(k, v);
  const sc = $("#specCanvas"), sx = sc.getContext("2d");
  let wfRows = [], lastSpectrum = null, lastPoll = 0;
  /* 瀑布的每一行必须是**一次真实采样**。此前每帧都 unshift 同一帧谱线，
     220 行长得一模一样，看起来在滚动其实没有数据——体检 B1。
     另：节流时钟不能用 MB.sky.now()（只在 sky.draw 里累加），专业模式下
     帧循环不调 sky.draw，时钟冻结，readSpectrum 一次都不会被调。 */
  let cachedSpec = null;

  for (const sel of ["#proStream", ".status-bar", "#capBody", "#capHead", "#aiStream", "#satCard", "#docNav"]) {
    const el = $(sel); if (el) el.setAttribute("data-i18n-skip", "");
  }

  function normalize(row) {
    const n = row.length, out = new Float32Array(n);
    let lo = Infinity, hi = -Infinity;
    for (let i = 0; i < n; i++) { const v = Number(row[i]); lo = Math.min(lo, v); hi = Math.max(hi, v); }
    const span = (hi - lo) || 1;
    for (let i = 0; i < n; i++) out[i] = .08 + .84 * (Number(row[i]) - lo) / span;
    return out;
  }

  function emptyState(line1, line2) {
    sx.fillStyle = "rgba(255,255,255,.72)";
    sx.font = "600 16px MiSans, sans-serif";
    sx.textAlign = "center";
    sx.fillText(line1, sc.clientWidth / 2, sc.clientHeight * .44);
    sx.font = "12px MiSans, sans-serif";
    sx.fillStyle = "rgba(255,255,255,.42)";
    sx.fillText(line2, sc.clientWidth / 2, sc.clientHeight * .44 + 26);
    sx.textAlign = "left";
    $("#rmsVal").textContent = "—";
    $("#cfoVal").textContent = "—";
  }

  function draw(dt) {
    if (!$("#proForm").classList.contains("on")) return;
    const w = sc.clientWidth, h = sc.clientHeight;
    if (sc.width !== w * 2 || sc.height !== h * 2) { sc.width = w * 2; sc.height = h * 2; }
    sx.setTransform(2, 0, 0, 2, 0, 0);
    sx.fillStyle = "#050505"; sx.fillRect(0, 0, w, h);
    const specH = h * .42;
    sx.strokeStyle = "rgba(255,255,255,.06)"; sx.lineWidth = 1;
    for (let i = 0; i <= 8; i++) {
      const x = i / 8 * w, y = i / 8 * specH;
      sx.beginPath(); sx.moveTo(x, 0); sx.lineTo(x, h); sx.stroke();
      sx.beginPath(); sx.moveTo(0, y); sx.lineTo(w, y); sx.stroke();
    }

    if (!MB.data.isLive()) {
      wfRows = []; lastSpectrum = null; cachedSpec = null;
      emptyState(t("spec.empty"), t("spec.emptyHint"));
      return;
    }
    // 墙钟：与形态无关，专业模式也持续推进（体检 B1）
    const now = (typeof performance !== "undefined" && performance.now)
      ? performance.now() : Date.now();
    if (now - lastPoll > 480) {
      lastPoll = now;
      const accept = a => {
        if (!Array.isArray(a) || !a.length) return;
        lastSpectrum = a;
        cachedSpec = normalize(a);
        wfRows.unshift(cachedSpec);            // 一次采样 = 一行
        if (wfRows.length > 220) wfRows.pop();
      };
      try {
        const r = MB.data.readSpectrum();
        if (r && typeof r.then === "function") r.then(accept, () => { lastSpectrum = null; });
        else accept(r);
      } catch (err) {
        lastSpectrum = null;
        MB.emit("spectrum:exception", { detail: String(err && err.message || err) });
      }
    }
    if (!cachedSpec || !cachedSpec.length) {
      wfRows = []; cachedSpec = null;
      emptyState(t("spec.noIq"), t("spec.noIqHint"));
      return;
    }

    const spec = cachedSpec, n = spec.length;
    sx.beginPath(); sx.moveTo(0, specH);
    for (let i = 0; i < n; i++) sx.lineTo(i / (n - 1) * w, specH - spec[i] * specH * .92);
    sx.lineTo(w, specH); sx.closePath();
    const fg = sx.createLinearGradient(0, 0, 0, specH);
    fg.addColorStop(0, "rgba(255,255,255,.30)"); fg.addColorStop(1, "rgba(255,255,255,.02)");
    sx.fillStyle = fg; sx.fill();
    sx.strokeStyle = "#ffffff"; sx.lineWidth = 1.3;
    sx.beginPath();
    for (let i = 0; i < n; i++) {
      const x = i / (n - 1) * w, y = specH - spec[i] * specH * .92;
      i ? sx.lineTo(x, y) : sx.moveTo(x, y);
    }
    sx.stroke();
    const wfTop = specH + 6, wfH = h - wfTop, rowH = wfH / 220;
    for (let r = 0; r < wfRows.length; r++) {
      const row = wfRows[r];
      for (let i = 0; i < n; i++) {
        const lum = Math.min(235, Math.pow(row[i] * 1.25, 1.2) * 235) | 0;
        sx.fillStyle = `rgb(${lum},${lum},${lum})`;
        sx.fillRect(i / n * w, wfTop + r * rowH, w / n + 1, rowH + 1);
      }
    }
    let sum = 0; for (let i = 0; i < n; i++) sum += spec[i] * spec[i];
    $("#rmsVal").textContent = (20 * Math.log10(Math.sqrt(sum / n) + 1e-9)).toFixed(1);
    $("#cfoVal").textContent = "—";
  }

  /* ---------- 动态文案（工具名来自真实工具面） ---------- */
  function toolCard(name, state, cls, id) {
    return `<div class="toolcard"${id ? ` data-initcard="${id}"` : ""}>${name}` +
           `<span class="st ${cls}">${state}</span></div>`;
  }
  function resetChat() {
    const live = MB.data.isLive(), st = t(live ? "ai.available" : "tool.offline"),
          cls = live ? "ok" : "off";
    $("#proStream").innerHTML =
      `<div class="msg model" data-welcome><div class="who">${t("ai.head")}</div>${t("pro.ready")}</div>` +
      toolCard("get_status()", st, cls, "status") +
      toolCard("read_spectrum(nfft: 1024)", st, cls, "spectrum");
  }
  /* 切语言只重译欢迎语与初始工具卡，**保留用户对话**（体检 B4）。
     旧实现 i18n:refresh → resetChat() → innerHTML 整体覆盖，消息全丢。 */
  function retranslateChat() {
    const stream = $("#proStream");
    const w = stream.querySelector("[data-welcome]");
    if (w) w.innerHTML = `<div class="who">${t("ai.head")}</div>${t("pro.ready")}`;
    const live = MB.data.isLive();
    stream.querySelectorAll("[data-initcard]").forEach(el => {
      const s = el.querySelector(".st");
      if (!s) return;
      s.textContent = t(live ? "ai.available" : "tool.offline");
      s.className = "st " + (live ? "ok" : "off");
    });
  }
  function renderStatus() {
    const n = MB.CAPS.filter(c => S.caps[c.id]).length, total = MB.CAPS.length;
    const parts = [
      `<span class="${MB.data.isLive() ? "ok" : ""}">${t(MB.data.isLive() ? "status.connected" : "status.disconnected")}</span>`,
      `<span>${t(MB.data.isLive() ? "status.liveHint" : "status.noDecode")}</span>`,
      `<span>${t("caps.count", { n, total })}</span>`,
      `<span id="cpuLine">${t("status.cpu")} —</span>`,
      `<span>${t(MB.data.sourceName())}</span>`,
      `<span>${t("app.slogan")}</span>`
    ];
    const bar = $(".status-bar");
    if (bar) bar.innerHTML = parts.join('<span class="sep">│</span>');
  }

  function sendPro() {
    const inp = $("#proInput"), v = inp.value.trim();
    if (!v) return;
    const s = $("#proStream");
    s.insertAdjacentHTML("beforeend", `<div class="msg user"><div class="who">${t("ai.you")}</div>${v}</div>`);
    inp.value = ""; s.scrollTop = s.scrollHeight;
    setTimeout(() => {
      s.insertAdjacentHTML("beforeend",
        `<div class="msg model"><div class="who">${t("ai.head")}</div>${t("pro.reply")}</div>` +
        toolCard("get_status()", MB.data.isLive() ? t("ai.toolCalled") : t("tool.offline"),
          MB.data.isLive() ? "ok" : "off"));
      s.scrollTop = s.scrollHeight;
    }, 420);
  }
  $("#proSend").addEventListener("click", sendPro);
  $("#proInput").addEventListener("keydown", e => { if (e.key === "Enter") sendPro(); });

  /* 弹性布局：分隔条可拖 */
  (() => {
    const split = $("#proSplit"), right = $("#proRight");
    let dragging = false;
    split.addEventListener("mousedown", () => { dragging = true; split.classList.add("drag"); });
    window.addEventListener("mouseup", () => { dragging = false; split.classList.remove("drag"); });
    window.addEventListener("mousemove", e => {
      if (!dragging) return;
      right.style.width = Math.max(280, Math.min(620, window.innerWidth - e.clientX)) + "px";
    });
  })();

  MB.on("i18n:refresh", () => { retranslateChat(); renderStatus(); });
  /* 诊断入口：让无头回归能证明「一次采样=一行」，而不必猜画面。
     不参与 UI，也不改数据面。 */
  function diag() {
    const a = wfRows[0], b = wfRows[1];
    let distinct = null;
    if (a && b && a.length === b.length) {
      distinct = false;
      for (let i = 0; i < a.length; i++) {
        if (a[i] !== b[i]) { distinct = true; break; }
      }
    }
    return { rows: wfRows.length, distinct, samples: lastPoll > 0 };
  }
  MB.workbench = { draw, resetChat, renderStatus, diag };
})(window);
