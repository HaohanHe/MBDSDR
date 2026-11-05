/* MBDSDR 产品前端 · 70-docs.js
 * 文档中心：把 docs/ 编译进产品，wiki 与规格随手可查（离线可用）。
 * 数据来自 70-docs-data.js（build.py 生成，不引外部依赖、不发网络请求）。
 * 语言降级：缺译文档显示原文并如实标注，不假装已本地化。
 */
(function () {
  "use strict";
  const MB = window.MBDSDR, S = MB.state;
  const $ = MB.$, t = (k, v) => MB.i18n.t(k, v);
  const DATA = window.MBDSDR_DOC_DATA || [];
  const GROUPS = [
    ["spec", "docs.group.spec"], ["arch", "docs.group.arch"],
    ["wiki", "docs.group.wiki"], ["scene", "docs.group.scene"],
    ["ref", "docs.group.ref"]
  ];
  let current = null, ready = false;

  function esc(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function resolveDocId(basePath, href) {
    const clean = String(href).split("#")[0];
    const segs = (basePath || "").split("/").slice(0, -1).concat(clean.split("/"));
    const parts = [];
    for (const s of segs) {
      if (s === "..") parts.pop();
      else if (s && s !== ".") parts.push(s);
    }
    return parts.join("/").replace(/\.md$/, "").replace(/\//g, "-");
  }
  function inline(s, basePath) {
    return esc(s)
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/\[([^\]]+)\]\(([^)]+)\)/g, (m, label, href) => {
        if (/^https?:/.test(href))
          return `<a href="${href}" target="_blank" rel="noopener">${label}</a>`;
        // 文档内相对链接 → 文档中心站内跳转（单文件产物里相对路径并不存在）
        return `<a href="#" data-doc-link="${resolveDocId(basePath, href)}">${label}</a>`;
      });
  }
  /* 轻量 Markdown：只覆盖本产品文档用到的语法，先转义再渲染 */
  function md(src, basePath) {
    const lines = String(src).replace(/\r\n/g, "\n").split("\n");
    let out = "", inCode = false, inList = false, inTable = false;
    const closeList = () => { if (inList) { out += "</ul>"; inList = false; } };
    const closeTable = () => { if (inTable) { out += "</table>"; inTable = false; } };
    for (const raw of lines) {
      const line = raw.trimEnd();
      if (/^```/.test(line)) { closeList(); closeTable(); out += inCode ? "</pre>" : "<pre><code>"; inCode = !inCode; continue; }
      if (inCode) { out += esc(raw) + "\n"; continue; }
      if (/^\s*$/.test(line)) { closeList(); closeTable(); out += ""; continue; }
      if (/^\|/.test(line)) {
        closeList();
        const cells = line.split("|").slice(1, -1).map(c => c.trim());
        if (cells.every(c => /^:?-{2,}:?$/.test(c))) continue;
        if (!inTable) { out += "<table><thead><tr>" + cells.map(c => "<th>" + inline(c, basePath) + "</th>").join("") + "</tr></thead><tbody>"; inTable = true; }
        else out += "<tr>" + cells.map(c => "<td>" + inline(c, basePath) + "</td>").join("") + "</tr>";
        continue;
      }
      closeTable();
      const h = /^(#{1,4})\s+(.*)$/.exec(line);
      if (h) { closeList(); const n = h[1].length; out += `<h${n}>${inline(h[2], basePath)}</h${n}>`; continue; }
      if (/^>\s?/.test(line)) { closeList(); out += "<blockquote>" + inline(line.replace(/^>\s?/, ""), basePath) + "</blockquote>"; continue; }
      if (/^[-*]\s+/.test(line)) {
        if (!inList) { out += "<ul>"; inList = true; }
        out += "<li>" + inline(line.replace(/^[-*]\s+/, ""), basePath) + "</li>"; continue;
      }
      closeList();
      out += "<p>" + inline(line, basePath) + "</p>";
    }
    closeList(); closeTable();
    if (inCode) out += "</pre>";
    return out;
  }

  function localeKey(loc) { return { "zh-CN": "zh", "ja-JP": "ja", "en-US": "en" }[loc] || "zh"; }
  function pick(doc) {
    const lk = localeKey(S.docLocale);
    const body = doc.body && doc.body[lk];
    const title = (doc.title && doc.title[lk]) || doc.title.zh;
    return { title, body, original: !body, lk };
  }

  function renderNav() {
    const nav = $("#docNav");
    let html = "";
    for (const [gid, gkey] of GROUPS) {
      const items = DATA.filter(d => d.group === gid);
      if (!items.length) continue;
      html += `<div class="doc-group">${t(gkey)}</div>`;
      for (const d of items) {
        const title = (d.title && d.title[localeKey(S.docLocale)]) || d.title.zh;
        html += `<button class="doc-link ${current === d.id ? "on" : ""}" data-doc="${d.id}">${esc(title)}</button>`;
      }
    }
    nav.innerHTML = html || `<div class="doc-group">${t("docs.empty")}</div>`;
  }
  function renderBody() {
    const doc = DATA.find(d => d.id === current);
    const body = $("#docBody");
    if (!doc) {
      $("#docCrumb").textContent = t("docs.crumb");
      body.innerHTML = `<h1>${t("docs.crumb")}</h1><p>${t("docs.hint")}</p>`;
      return;
    }
    const p = pick(doc);
    const fallback = p.original
      ? `<div class="doc-meta">${t("docs.langHint")}: ${MB.i18n.label(S.docLocale)} · ${t("docs.noTranslation")}</div>`
      : `<div class="doc-meta">${t("docs.langHint")}: ${MB.i18n.label(S.docLocale)}</div>`;
    $("#docCrumb").textContent = t("docs.crumb") + " / " + p.title;
    body.innerHTML = fallback + md(p.body || doc.body.zh, doc.path);
    body.scrollTop = 0;
  }

  const docs = MB.docs = {
    init() {
      ready = true;
      $("#docNav").setAttribute("data-i18n-skip", "");
      $("#docBody").setAttribute("data-i18n-skip", "");
      current = DATA.length ? DATA[0].id : null;
      renderNav(); renderBody();
      $("#docNav").addEventListener("click", e => {
        const b = e.target.closest("[data-doc]"); if (!b) return;
        current = b.dataset.doc; renderNav(); renderBody();
      });
      $("#docBody").addEventListener("click", e => {
        const a = e.target.closest("[data-doc-link]"); if (!a) return;
        e.preventDefault();
        const id = a.getAttribute("data-doc-link");
        if (DATA.some(d => d.id === id)) { current = id; renderNav(); renderBody(); }
      });
      this.refresh();
    },
    open() { $("#docPanel").classList.add("open"); renderNav(); renderBody(); },
    close() { $("#docPanel").classList.remove("open"); },
    cycleLocale() {
      const ls = MB.i18n.locales, i = ls.indexOf(S.docLocale);
      S.docLocale = ls[(i + 1) % ls.length];
      if (MB.app) MB.app.syncLangButtons();
      renderNav(); renderBody();
    },
    refresh() { if (!ready) return; renderNav(); renderBody(); },
    count: DATA.length
  };
})(window);
