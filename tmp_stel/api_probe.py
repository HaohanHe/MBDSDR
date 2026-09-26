#!/usr/bin/env python3
import sys, json, time
sys.path.insert(0, "/home/user/Doubao/chats/38438160041798146/tmp_stel")
from driver import CDP
c = CDP()
S = "window.__stel"
out = []

def line(s): out.append(s); print(s)

line("=== Stellarium Web Engine API probe (live CDP) ===")
line("")
line("--- top-level stel (Module) keys ---")
line(c.eval("Object.keys(window.__stel).sort().join(', ')"))
line("")
line("--- stel.* function/properties ---")
line(c.eval("Object.keys(window.__stel).filter(k=>typeof window.__stel[k]==='function').join(', ')"))
line("")
line("--- core (SweObj) children ---")
line(c.eval("Object.keys(window.__stel.core).sort().join(', ')"))
line("")
line("--- core scalar props ---")
for k in ["fov","time_speed","bortle_index","display_limit_mag","star_linear_scale","star_relative_scale","exposure_scale","flip_view_vertical","flip_view_horizontal"]:
    line(f"core.{k} = {c.eval(f'window.__stel.core.{k}')}")
line("")
line("--- observer props ---")
obs = "window.__stel.core.observer"
for k in ["latitude","longitude","height","tt","jd","time_tt"]:
    line(f"observer.{k} = {c.eval(f'{obs}.{k}')}")
line("")
line("--- toggle modules (visible flag) ---")
for path in ["atmosphere","landscapes","milkyway","dss","dsos","stars","constellations","cardinals","labels"]:
    line(f"core.{path}.visible = {c.eval(f'window.__stel.core.{path}.visible')}")
line("")
line("--- grid lines ---")
line(f"core.lines keys: {c.eval('Object.keys(window.__stel.core.lines).join(\", \")')}")
for k in ["azimuthal","equatorial","horizontal","meridian"]:
    line(f"core.lines.{k}.visible = {c.eval(f'window.__stel.core.lines.{k} && window.__stel.core.lines.{k}.visible')}")
line("")
line("--- search test ---")
for name in ["Moon","Sirius","Mars","Venus","Jupiter","Saturn","Polaris","Vega","ISS","NORAD 25544"]:
    r = c.eval(f'(function(){{try{{var o=window.__stel.getObj("{name}"); return o?o.designations()[0]:"null";}}catch(e){{return "err:"+e.message}}}})()')
    line(f'getObj("{name}") -> {r}')
line("")
line("--- stel math helpers ---")
line(f"c2s([1,0,0]) = {c.eval('window.__stel.c2s([1,0,0])')}")
line(f"s2c(0,0) = {c.eval('window.__stel.s2c(0,0)')}")
line(f"anp(-1) = {c.eval('window.__stel.anp(-1)')}")
line(f"a2tf(1.5,1) = {c.eval('JSON.stringify(window.__stel.a2tf(1.5,1))')}")
line(f"a2af(0.5,1) = {c.eval('JSON.stringify(window.__stel.a2af(0.5,1))')}")

with open("/home/user/Doubao/chats/38438160041798146/tmp_stel/api_dump.txt","w") as f:
    f.write("\n".join(out))
c.close()
