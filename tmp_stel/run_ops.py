#!/usr/bin/env python3
import sys, time, json
sys.path.insert(0, "/home/user/Doubao/chats/38438160041798146/tmp_stel")
from driver import CDP
from PIL import Image
import os

WORK = "/home/user/Doubao/chats/38438160041798146/tmp_stel"
c = CDP()
S = "window.__stel"
shots = []

def shot(name):
    p = f"{WORK}/{name}.png"
    c.screenshot(p)
    shots.append((name, p))
    print("saved", name)

def set_toggle(path, val):
    c.eval(f"window.__stel.core.{path}.visible = {str(val).lower()}")
    c.wait_redraw(700)

# a. initial panorama
c.wait_redraw(500)
shot("a_initial")

# b. search Moon
r = c.eval('(function(){var o=window.__stel.getObj("Moon"); if(!o) return "null"; window.__stel.pointAndLock(o, 1.5); return o.designations()[0];})()')
print("Moon ->", r)
c.wait_redraw(2000)
shot("b_moon")

# c. search Sirius
r = c.eval('(function(){var o=window.__stel.getObj("Sirius"); if(!o) return "null"; window.__stel.pointAndLock(o, 1.5); return o.designations()[0];})()')
print("Sirius ->", r)
c.wait_redraw(2000)
shot("c_sirius")

# d. search Mars (try; may be below horizon)
r = c.eval('(function(){var o=window.__stel.getObj("Mars"); if(!o) return "null"; window.__stel.pointAndLock(o, 1.5); return o.designations()[0];})()')
print("Mars ->", r)
c.wait_redraw(2000)
shot("d_mars")

# e. zoom to 20 deg FOV
c.eval("window.__stel.zoomTo(20*Math.PI/180, 1.5)")
c.wait_redraw(2000)
print("fov after zoom:", c.eval("window.__stel.core.fov*180/Math.PI"))
shot("e_zoom20")

# reset fov
c.eval("window.__stel.zoomTo(70*Math.PI/180, 1.0)")
c.wait_redraw(1500)

# f. drag pan (center of canvas ~640,400; drag right/down)
c.drag(640, 400, 300, 500, steps=30)
c.wait_redraw(800)
shot("f_drag")

# g. time flow: set time_speed to fast
print("time_speed before:", c.eval("window.__stel.core.time_speed"))
c.eval("window.__stel.core.time_speed = 86400*10")  # 10 days/sec? try
c.wait_redraw(3000)
shot("g_timeflow")
c.eval("window.__stel.core.time_speed = 0")
c.wait_redraw(500)

# h. azimuthal grid on
set_toggle("lines.azimuthal", True)
shot("h_azgrid")

# i. equatorial grid on (az off first for clarity)
c.eval("window.__stel.core.lines.azimuthal.visible = false")
set_toggle("lines.equatorial", True)
shot("i_eqgrid")
c.eval("window.__stel.core.lines.equatorial.visible = false")

# j. atmosphere off
set_toggle("atmosphere", False)
shot("j_noatmo")

# k. landscape off
set_toggle("landscapes", False)
shot("k_nolandscape")

# l. nebulae off
set_toggle("dsos", False)
shot("l_nonebulae")

# restore
c.eval("window.__stel.core.atmosphere.visible = true")
c.eval("window.__stel.core.landscapes.visible = true")
c.eval("window.__stel.core.dsos.visible = true")

print("=== shots ===")
for n,p in shots: print(n, p)
c.close()
