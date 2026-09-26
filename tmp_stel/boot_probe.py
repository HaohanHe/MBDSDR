#!/usr/bin/env python3
import sys, time, json
sys.path.insert(0, "/home/user/Doubao/chats/38438160041798146/tmp_stel")
from driver import CDP, launch_chromium, WORK

proc = launch_chromium()
print("chromium PID:", proc.pid)
c = CDP()
c._send("Page.enable")
c._send("Runtime.enable")

# This local build's assignWasmExports forgets to attach _free/_malloc onto the
# Module object.  Strategy:
#   - no-op Module._free (the early observer getter calls it; leaking is fine)
#   - disable translateFn bridge (avoids early Module._malloc)
#   - no-op setFont (avoids fetch+_malloc+writeArrayToMemory which corrupts heap
#     when Module._malloc is missing)
# The wasm renderer itself uses internal closure malloc, unaffected.
hook = r"""
(function(){
  if (window.__hooked) return; window.__hooked = true;
  window.__bootLog = [];
  Object.defineProperty(window, 'StelWebEngine', {
    configurable: true,
    set: function(v){
      window.__StelWrapped = function(opts){
        opts.translateFn = null;
        opts._free = function(ptr){};
        opts._malloc = function(n){ return 0; };
        var origReady = opts.onReady;
        opts.onReady = function(mod){
          window.__stel = mod;
          window.__bootLog.push('onReady fired');
          mod.setFont = function(){ return Promise.resolve(); };
          try { return origReady && origReady.apply(this, arguments); }
          catch(e){ window.__bootLog.push('onReady threw: '+e.stack); throw e; }
        };
        var p = v(opts);
        if (p && p.catch) p.catch(function(e){ window.__bootLog.push('REJ: '+(e && (e.stack||e))); });
        return p;
      };
    },
    get: function(){ return window.__StelWrapped; }
  });
})();
"""
c._send("Page.addScriptToEvaluateOnNewDocument", {"source": hook})
c._send("Page.reload")
time.sleep(3)

c.ws.settimeout(45)
deadline = time.time() + 45
exc = []
while time.time() < deadline:
    try:
        raw = c.ws.recv()
    except Exception:
        break
    d = json.loads(raw)
    if d.get("method") == "Runtime.exceptionThrown":
        ed = d["params"]["exceptionDetails"]
        exc.append(str(ed.get("exception",{}).get("description", ed.get("text","")))[:500])
    if c.eval("window.__stel?1:0") == 1:
        print(">>> onReady fired")
        break

print("bootLog:", json.loads(c.eval("JSON.stringify(window.__bootLog||[])")))
print("exceptions:", exc)
for i in range(40):
    pb = c.eval("window.__stel.core.progressbars.length")
    if not pb:
        print("data loaded after", i, "s"); break
    time.sleep(1)
print("fov deg:", c.eval("window.__stel.core.fov*180/Math.PI"))
print("observer.v:", c.eval("window.__stel.core.observer && window.__stel.core.observer.v"))
print("lat deg:", c.eval("window.__stel.core.observer.latitude*180/Math.PI"))
c.wait_redraw(1500)
c.screenshot(WORK+"/boot.png")
c.close()
