/* MBDSDR 产品前端 · 60-app.js
 * 引导层：形态切换 / 时钟 / 工具栏 / 能力矩阵 / 语言切换 / 帧循环。
 * 一套系统两形态在这里汇合；能力位是状态，不是流程步骤。
 */
(function () {
  "use strict";
  const MB = window.MBDSDR, S = MB.state;
  const $ = MB.$, $$ = MB.$$, t = (k, v) => MB.i18n.t(k, v);
  /* 时间倍率来自原仓库 desktop/rf_sky_view.py 的 5 档按钮（同一套事实源） */
  const RATE_ENTRIES = (MB.REPO.timeRates && MB.REPO.timeRates.values) || [];
  const TIME_RATES = RATE_ENTRIES.map(x => Number(x.rate));
  const RATE_LABELS = RATE_ENTRIES.map(x => x.label);
  const rateLabel = () => RATE_LABELS[S.time.rateIdx] || (S.time.rate + "×");

  /* ---------- 形态 ---------- */
  function applyForm(form) {
    S.form = form; MB.persist("form");
    $$("#modeSwitch button").forEach(b => b.classList.toggle("on", b.dataset.form === form));
    $("#skyForm").classList.toggle("on", form === "sky");
    $("#proForm").classList.toggle("on", form === "pro");
    document.body.classList.toggle("sky-immersive", form === "sky");
    MB.emit("form:changed", form);
  }
  $("#modeSwitch").addEventListener("click", e => {
    const b = e.target.closest("button"); if (b) applyForm(b.dataset.form);
  });

  /* ---------- 汉堡 / 侧栏 ---------- */
  $("#burger").addEventListener("click", () => $("#sidebar").classList.toggle("open"));
  $("#skyForm").addEventListener("click", e => {
    if (!e.target.closest("#sidebar") && !e.target.closest("#burger"))
      $("#sidebar").classList.remove("open");
  });
  $("[data-doc-open]").addEventListener("click", () => {
    $("#sidebar").classList.remove("open");
    MB.docs.open();
  });
  $("#docOpen").addEventListener("click", () => MB.docs.open());
  $("#docClose").addEventListener("click", () => MB.docs.close());

  /* ---------- 时钟（唯一时间源 = MB.celestial.simUnix，docs/time-source.md） ---------- */
  function tickClock() {
    // 不再用 new Date() 当计算链第二时间源：顶栏/角标/HUD 全取同一个模拟时刻。
    const sim = new Date(MB.celestial.simUnix(S) * 1000);
    const p = n => String(n).padStart(2, "0");
    $("#clock").textContent = p(sim.getUTCHours()) + ":" + p(sim.getUTCMinutes())
      + ":" + p(sim.getUTCSeconds()) + " " + t("time.utc");
    $("#timeChip").textContent = p(sim.getUTCHours()) + ":" + p(sim.getUTCMinutes())
      + ":" + p(sim.getUTCSeconds());
    $("#dateChip").textContent = sim.getUTCFullYear() + "-"
      + p(sim.getUTCMonth() + 1) + "-" + p(sim.getUTCDate())
      + (S.time.rate !== 1 ? "  ·  " + rateLabel() : "");
  }
  setInterval(tickClock, 1000);

  /* ---------- 工具栏 ---------- */
  $("#skyToolbar").addEventListener("click", e => {
    const b = e.target.closest("[data-tb]"); if (!b) return;
    const k = b.dataset.tb;
    if (k === "fullscreen") {
      if (document.fullscreenElement) document.exitFullscreen();
      else (document.documentElement.requestFullscreen || (() => {})).call(document.documentElement);
      b.classList.toggle("on");
      return;
    }
    if (k === "time") {
      S.time.rateIdx = (S.time.rateIdx + 1) % TIME_RATES.length;
      S.time.rate = TIME_RATES[S.time.rateIdx];
      $("#rateTag").textContent = rateLabel();
      b.classList.toggle("on", S.time.rate !== 0);
      tickClock();
      return;
    }
    S.sky[k] = !S.sky[k];
    b.classList.toggle("on", S.sky[k]);
    MB.persist("sky");
  });
  function syncToolbar() {
    for (const b of $$("#skyToolbar [data-tb]")) {
      const k = b.dataset.tb;
      if (k === "fullscreen" || k === "time") continue;
      b.classList.toggle("on", !!S.sky[k]);
    }
    $("#rateTag").textContent = rateLabel();
  }

  /* ---------- 能力矩阵（真实工具域 + 规划未验证，可开关、可持久化） ---------- */
  function renderCaps() {
    $("#capHead").innerHTML = "◧ " + t("sidebar.caps")
      + `<span class="badge">${t("caps.badge")}</span>`;
    $("#capBody").innerHTML = MB.CAPS.map(c => {
      const nm = (c.name && (c.name[S.locale] || c.name["zh-CN"])) || c.id;
      const ds = c.descKey ? t(c.descKey) : (c.desc || "");
      const tag = c.planned ? `<span class="tag">${t("caps.notVerified")}</span>`
        : (c.group === "python" ? `<span class="tag">${t("caps.pythonExt")}</span>` : "");
      return `
      <div class="cap ${S.caps[c.id] ? "on" : "dim"} ${c.planned ? "planned" : ""}" data-cap="${c.id}">
        <div class="n"><i>${c.icon}</i>${nm}${tag}</div>
        <div class="m">${ds}</div>
        <div class="sw"></div>
      </div>`;
    }).join("");
  }
  $("#capBody").addEventListener("click", e => {
    const el = e.target.closest("[data-cap]"); if (!el) return;
    const cap = MB.capById(el.dataset.cap);
    if (cap && cap.planned) return;          // 规划能力不可伪装成已上线
    S.caps[el.dataset.cap] = !S.caps[el.dataset.cap];
    MB.persist("caps");
    renderCaps(); MB.workbench.renderStatus();
    MB.emit("caps:changed", S.caps);
  });
  $("#capHead").addEventListener("click", () => $("#capDrawer").classList.toggle("folded"));
  $("[data-cap-open]").addEventListener("click", () => {
    $("#capDrawer").classList.remove("folded");
    $("#sidebar").classList.remove("open");
  });

  /* ---------- 语言切换 ---------- */
  const langBtn = $("#langSwitch"), docLangBtn = $("#docLang");
  langBtn.setAttribute("data-i18n-skip", "");
  docLangBtn.setAttribute("data-i18n-skip", "");
  function syncLangButtons() {
    langBtn.textContent = MB.i18n.label(S.locale);
    docLangBtn.textContent = MB.i18n.label(S.docLocale);
  }
  langBtn.addEventListener("click", () => {
    const i = MB.i18n.locales.indexOf(S.locale);
    MB.i18n.setLocale(MB.i18n.locales[(i + 1) % MB.i18n.locales.length]);
  });
  docLangBtn.addEventListener("click", () => MB.docs.cycleLocale());

  MB.on("locale:changed", () => {
    MB.i18n.refreshDynamic();      // 40/50 重绘动态文案
    renderCaps();
    MB.sky.retranslate();          // 锁定的信息卡重渲染
    if (MB.docs) MB.docs.refresh();
    syncLangButtons();
    tickClock();
  });

  /* ---------- 帧循环 ---------- */
  let last = 0;
  function animate(ts) {
    const dt = Math.min(50, ts - (last || ts)); last = ts;
    if ($("#skyForm").classList.contains("on")) MB.sky.draw(dt);
    MB.workbench.draw(dt);
    requestAnimationFrame(animate);
  }

  /* ---------- 启动 ---------- */
  MB.sky.makeSky(); MB.sky.resize();
  applyForm(S.form);
  MB.i18n.applyStatic();
  MB.i18n.setLocale(S.locale, false);   // 应用持久化语言
  MB.workbench.resetChat();
  MB.workbench.renderStatus();
  renderCaps(); syncToolbar(); syncLangButtons(); tickClock();
  MB.docs.init();
  requestAnimationFrame(animate);
  /* 不自动锁星：星象仪打开就是全天视野，锁星是用户点选后的动作。
     预制「几秒后自动锁某颗卫星」会把全天收窄成 26°，也违背「能力而非流程」。 */
  MB.app = { applyForm, renderCaps, tickClock, syncLangButtons };
})(window);
