#!/usr/bin/env python3
"""CDP driver for Stellarium Web Engine. Persistent in workspace."""
import base64, json, time, sys, subprocess, os, requests
import websocket

PORT = 9333
WORK = "/home/user/Doubao/chats/38438160041798146/tmp_stel"

class CDP:
    def __init__(self, port=PORT):
        targets = requests.get(f"http://127.0.0.1:{port}/json").json()
        pages = [t for t in targets if t["type"] == "page" and "stellarium" in t.get("url","")]
        if not pages:
            pages = [t for t in targets if t["type"] == "page"]
        self.ws = websocket.create_connection(pages[0]["webSocketDebuggerUrl"],
                                              suppress_origin=True, timeout=60)
        self.i = 0
        self.ws.settimeout(60)

    def _send(self, method, params=None, sid=None):
        self.i += 1
        mid = sid if sid is not None else self.i
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            data = json.loads(self.ws.recv())
            if data.get("id") == mid:
                return data

    def eval(self, expr, await_promise=True):
        res = self._send("Runtime.evaluate", {
            "expression": expr, "awaitPromise": await_promise,
            "returnByValue": True, "timeout": 30000})
        r = res.get("result", {})
        if "exceptionDetails" in r:
            ed = r["exceptionDetails"]
            return {"__error__": ed.get("exception",{}).get("description", ed.get("text","?"))}
        return r.get("result", {}).get("value")

    def sleep(self, s): time.sleep(s)

    def wait_redraw(self, ms=600):
        self.eval(f"new Promise(r=>{{let n=0;function f(){{n++; if(n>4) return r(); requestAnimationFrame(f);}} requestAnimationFrame(f); setTimeout(r,{ms});}})", True)
        time.sleep(ms/1000)

    def screenshot(self, path):
        res = self._send("Page.captureScreenshot", {"format": "png"})
        with open(path, "wb") as f:
            f.write(base64.b64decode(res["result"]["data"]))
        return path

    def mouse(self, typ, x, y, button="none"):
        self._send("Input.dispatchMouseEvent",
                   {"type": typ, "x": x, "y": y, "button": button, "clickCount": 1})

    def drag(self, x0, y0, x1, y1, steps=25, hold=0.015):
        self.mouse("mousePressed", x0, y0, "left")
        for k in range(1, steps+1):
            self.mouse("mouseMoved", x0+(x1-x0)*k/steps, y0+(y1-y0)*k/steps, "left")
            time.sleep(hold)
        time.sleep(0.1)
        self.mouse("mouseReleased", x1, y1, "left")

    def close(self):
        try: self.ws.close()
        except Exception: pass


def launch_chromium():
    udir = WORK + "/chrome-profile"
    os.makedirs(udir, exist_ok=True)
    log = open(WORK+"/chromium.log", "w")
    p = subprocess.Popen([
        "/usr/local/bin/chromium", "--headless=new",
        "--use-angle=swiftshader", "--enable-unsafe-swiftshader",
        "--ignore-gpu-blocklist", f"--remote-debugging-port={PORT}",
        "--window-size=1280,800", "--no-sandbox", "--disable-dev-shm-usage",
        f"--user-data-dir={udir}",
        "http://127.0.0.1:8777/apps/simple-html/stellarium-web-engine.html",
    ], stdout=log, stderr=subprocess.STDOUT)
    # wait for devtools
    for _ in range(30):
        try:
            requests.get(f"http://127.0.0.1:{PORT}/json/version", timeout=1)
            return p
        except Exception:
            time.sleep(0.5)
    raise RuntimeError("chromium did not start")
