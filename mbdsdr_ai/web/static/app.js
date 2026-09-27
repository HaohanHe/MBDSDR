/* MBDSDR Web 前端 — 频谱/瀑布实时绘制 + 远程控制
 *
 * 协议（与 mbdsdr_ai/web/streamer.py 一致，大端）：
 *   二进制帧: >B I I f f | uint8[bins]
 *     B = 0x01 频谱标记
 *     I = center_freq_hz, I = samp_rate_hz
 *     f = db_min, f = db_max
 *   上行文本: JSON {cmd, value}
 *   下行文本: JSON {type:"status"|"ack"|"error", ...}
 *
 * 红线：未收到二进制帧时一直显"等待数据"，绝不画假频谱。
 */
"use strict";

(() => {
  // ---- DOM --------------------------------------------------------------- //
  const $ = (id) => document.getElementById(id);
  const connEl = $("conn"), waitingEl = $("waiting");
  const roFreq = $("ro-freq"), roSr = $("ro-sr"), roMode = $("ro-mode"), roGain = $("ro-gain");
  const specCanvas = $("spectrum"), wfCanvas = $("waterfall");
  const sctx = specCanvas.getContext("2d"), wctx = wfCanvas.getContext("2d");
  const toastEl = $("toast");

  // ---- 状态 --------------------------------------------------------------- //
  let ws = null;
  let reconnectTimer = null;
  let latest = null;          // 最近一帧 {center, sr, dbMin, dbMax, bins}
  let annotations = [];       // AI 标注 [{freq_hz,label,confidence}]
  let peakHold = new Float32Array(0);
  const WF_ROWS = 80;         // 瀑布滚动行数
  let wfRow = 0;              // 下一行写在哪
  let wfImage = null;          // 离屏行缓冲 Uint8[WF_ROWS * bins]
  let useJet = true;
  let connected = false;

  // ---- WebSocket ---------------------------------------------------------- //
  function wsUrl() {
    const proto = location.protocol === "https:" ? "wss://" : "ws://";
    return proto + location.host + "/ws/spectrum";
  }

  function connect() {
    if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
    try { ws = new WebSocket(wsUrl()); } catch (e) { scheduleReconnect(); return; }
    ws.binaryType = "arraybuffer";

    ws.onopen = () => {
      connected = true;
      connEl.textContent = "已连接";
      connEl.className = "conn conn-on";
      ws.send(JSON.stringify({ cmd: "get_status" }));
    };
    ws.onclose = () => {
      connected = false;
      connEl.textContent = "未连接";
      connEl.className = "conn conn-off";
      scheduleReconnect();
    };
    ws.onerror = () => { try { ws.close(); } catch (e) {} };
    ws.onmessage = (ev) => {
      if (typeof ev.data === "string") {
        handleText(ev.data);
      } else {
        handleBinary(new DataView(ev.data));
      }
    };
  }

  function scheduleReconnect() {
    waitingEl.classList.remove("hidden");
    reconnectTimer = setTimeout(connect, 1500);
  }

  function sendCmd(obj) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(obj));
    } else {
      toast("未连接");
    }
  }

  // ---- 消息解析 ----------------------------------------------------------- //
  // ">B I I f f" = 1 + 4 + 4 + 4 + 4 = 17 字节头
  const HDR = { marker: 0, center: 1, sr: 5, dbMin: 9, dbMax: 13 };
  const SPEC_HEADER_LEN = 17;

  function handleBinary(dv) {
    if (dv.byteLength < SPEC_HEADER_LEN + 1) return;
    if (dv.getUint8(0) !== 0x01) return; // 非频谱帧忽略
    latest = {
      center: dv.getUint32(HDR.center),
      sr: dv.getUint32(HDR.sr),
      dbMin: dv.getFloat32(HDR.dbMin),
      dbMax: dv.getFloat32(HDR.dbMax),
      bins: new Uint8Array(dv.buffer, dv.byteOffset + SPEC_HEADER_LEN),
    };
    waitingEl.classList.add("hidden");

    // 峰值保留
    if (peakHold.length !== latest.bins.length) {
      peakHold = new Float32Array(latest.bins.length);
    }
    for (let i = 0; i < latest.bins.length; i++) {
      if (latest.bins[i] > peakHold[i]) peakHold[i] = latest.bins[i];
    }
    // 峰值缓慢衰减
    for (let i = 0; i < peakHold.length; i++) peakHold[i] *= 0.985;

    // 推一行进瀑布
    if (!wfImage || wfImage.length !== WF_ROWS * latest.bins.length) {
      wfImage = new Uint8Array(WF_ROWS * latest.bins.length);
      wfRow = 0;
    }
    wfImage.set(latest.bins, wfRow * latest.bins.length);
    wfRow = (wfRow + 1) % WF_ROWS;
  }

  function handleText(text) {
    let msg;
    try { msg = JSON.parse(text); } catch (e) { return; }
    if (msg.type === "status") {
      if (typeof msg.center_freq_hz === "number") roFreq.textContent = fmtFreq(msg.center_freq_hz);
      if (typeof msg.samp_rate_hz === "number") roSr.textContent = msg.samp_rate_hz.toLocaleString();
      if (msg.mode) roMode.textContent = msg.mode;
      if (typeof msg.gain_db === "number") roGain.textContent = msg.gain_db;
      if (Array.isArray(msg.annotations)) annotations = msg.annotations;
    } else if (msg.type === "error") {
      toast(msg.error || "error");
    } else if (msg.type === "ack") {
      toast("ok: " + msg.cmd);
    }
  }

  function fmtFreq(hz) {
    if (hz >= 1e6) return (hz / 1e6).toFixed(3) + " MHz";
    if (hz >= 1e3) return (hz / 1e3).toFixed(1) + " kHz";
    return String(hz);
  }

  let toastTimer = null;
  function toast(s) {
    toastEl.textContent = s;
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (toastEl.textContent = ""), 2000);
  }

  // ---- 颜色映射 jet / grayscale（预计算 256 项 LUT） ----------------------- //
  const JET_LUT = (() => {
    const clip = (x) => Math.max(0, Math.min(1, x));
    const lut = new Uint8Array(256 * 3);
    for (let v = 0; v < 256; v++) {
      const t = v / 255;
      lut[v * 3] = Math.round(255 * clip(1.5 - Math.abs(4 * t - 3)));
      lut[v * 3 + 1] = Math.round(255 * clip(1.5 - Math.abs(4 * t - 2)));
      lut[v * 3 + 2] = Math.round(255 * clip(1.5 - Math.abs(4 * t - 1)));
    }
    return lut;
  })();

  // ---- 绘制循环 ----------------------------------------------------------- //
  function drawSpectrum() {
    const w = specCanvas.width, h = specCanvas.height;
    sctx.clearRect(0, 0, w, h);
    if (!latest) return;
    const bins = latest.bins, n = bins.length;

    // 主曲线
    sctx.strokeStyle = "#3fd0c9";
    sctx.lineWidth = 1;
    sctx.beginPath();
    for (let i = 0; i < n; i++) {
      const x = (i / (n - 1)) * w;
      const y = h - (bins[i] / 255) * (h - 10) - 5;
      i ? sctx.lineTo(x, y) : sctx.moveTo(x, y);
    }
    sctx.stroke();

    // 峰值保留
    sctx.strokeStyle = "rgba(255,200,80,0.7)";
    sctx.beginPath();
    for (let i = 0; i < n; i++) {
      const x = (i / (n - 1)) * w;
      const y = h - (peakHold[i] / 255) * (h - 10) - 5;
      i ? sctx.lineTo(x, y) : sctx.moveTo(x, y);
    }
    sctx.stroke();

    // AI 信号标注（我们的增强：频谱上自动标注识别到的信号类型）
    for (const ann of annotations) {
      if (typeof ann.freq_hz !== "number") continue;
      const rel = (ann.freq_hz - (latest.center - latest.sr / 2)) / latest.sr;
      const x = rel * w;
      if (x < 0 || x > w) continue;
      sctx.strokeStyle = "rgba(229,83,75,0.9)";
      sctx.beginPath(); sctx.moveTo(x, 0); sctx.lineTo(x, h); sctx.stroke();
      sctx.fillStyle = "#e5534b";
      sctx.font = "11px monospace";
      sctx.fillText(ann.label + (ann.confidence ? " " + ann.confidence.toFixed(2) : ""), x + 3, 12);
    }
  }

  function drawWaterfall() {
    const w = wfCanvas.width, h = wfCanvas.height;
    if (!wfImage || !latest) return;
    const n = latest.bins.length;
    const out = wctx.createImageData(w, h);
    const rowH = h / WF_ROWS;
    const newest = (wfRow - 1 + WF_ROWS) % WF_ROWS; // 最新一行已写入 wfRow-1
    for (let y = 0; y < h; y++) {
      // 顶部 = 最新行，向下滚动变旧
      const row = (newest - Math.floor(y / rowH) + WF_ROWS * 2) % WF_ROWS;
      for (let x = 0; x < w; x++) {
        const v = wfImage[row * n + Math.floor((x / w) * n)];
        const o = (y * w + x) * 4;
        if (useJet) {
          out.data[o] = JET_LUT[v * 3];
          out.data[o + 1] = JET_LUT[v * 3 + 1];
          out.data[o + 2] = JET_LUT[v * 3 + 2];
        } else {
          out.data[o] = v; out.data[o + 1] = v; out.data[o + 2] = v;
        }
        out.data[o + 3] = 255;
      }
    }
    wctx.putImageData(out, 0, 0);
  }

  function frame() {
    drawSpectrum();
    drawWaterfall();
    requestAnimationFrame(frame);
  }

  // ---- 交互：点击调谐 / 滚轮缩放 / 控制条 --------------------------------- //
  specCanvas.addEventListener("click", (e) => {
    if (!latest) return;
    const rect = specCanvas.getBoundingClientRect();
    const rel = (e.clientX - rect.left) / rect.width;
    const f = Math.round(latest.center - latest.sr / 2 + rel * latest.sr);
    $("ctl-freq").value = f;
    sendCmd({ cmd: "set_frequency", value: f });
  });

  specCanvas.addEventListener("wheel", (e) => {
    e.preventDefault();
    // 客户端显示层面的中心平移（仅演示；真实带宽调整需后端支持）
    toast("滚轮：缩放（需后端支持带宽切换）");
  }, { passive: false });

  $("ctl-freq").addEventListener("change", (e) => {
    sendCmd({ cmd: "set_frequency", value: Number(e.target.value) });
  });
  $("ctl-gain").addEventListener("change", (e) => {
    sendCmd({ cmd: "set_gain", value: Number(e.target.value) });
  });
  document.querySelectorAll("[data-mode]").forEach((btn) => {
    btn.addEventListener("click", () => sendCmd({ cmd: "set_mode", value: btn.dataset.mode }));
  });
  $("cmap-jet").addEventListener("change", (e) => { useJet = e.target.checked; });

  // ---- 录制文件列表 -------------------------------------------------------- //
  fetch("/api/recordings")
    .then((r) => r.json())
    .then((d) => {
      const ul = $("recordings");
      ul.innerHTML = "";
      if (!d.recordings || !d.recordings.length) {
        ul.innerHTML = '<li class="empty">（暂无录制文件）</li>';
        return;
      }
      for (const f of d.recordings) {
        const li = document.createElement("li");
        const a = document.createElement("a");
        a.href = "/api/recordings/" + encodeURIComponent(f.name);
        a.textContent = f.name;
        const size = document.createElement("span");
        size.className = "size";
        size.textContent = (f.size_bytes / 1024).toFixed(1) + " KB";
        li.appendChild(a); li.appendChild(size);
        ul.appendChild(li);
      }
    })
    .catch(() => { $("recordings").innerHTML = '<li class="empty">录制目录不可用</li>'; });

  // ---- 启动 --------------------------------------------------------------- //
  connect();
  requestAnimationFrame(frame);
})();
