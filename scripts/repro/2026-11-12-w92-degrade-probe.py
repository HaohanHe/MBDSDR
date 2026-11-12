# -*- coding: utf-8 -*-
"""W92 降级路径实测探针（复跑入口）。

每种情况都真跑一遍 sat_links 的读取/刷新路径，输出 JSON 结果。
网络不通用「代理指向本机已关闭端口」模拟真实连接拒绝，不拔网线。

复跑（仓库根目录，Windows）::

    python scripts/repro/2026-11-12-w92-degrade-probe.py

结果写到系统临时目录的 ``w92-degrade-results.json``，不覆盖入库证据
``docs/evidence/w92-degrade-paths-20261112.json``。
"""
import json
import os
import shutil
import stat
import sys
import tempfile
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

REPO = str(Path(__file__).resolve().parents[2])
sys.path.insert(0, REPO)
os.chdir(REPO)

WORK = Path(tempfile.mkdtemp(prefix="w92-degrade-")) / "work"
if WORK.exists():
    shutil.rmtree(WORK, ignore_errors=True)
WORK.mkdir(parents=True, exist_ok=True)

from mbdsdr_ai import sat_links as SL          # noqa: E402
from mbdsdr_ai import sat_links_fetch as SF   # noqa: E402

RESULTS = []


def r(name, **kw):
    kw["case"] = name
    RESULTS.append(kw)
    print(f"\n[{name}]")
    for k, v in kw.items():
        if k != "case":
            print(f"  {k} = {v!r}")


def manual_snapshot():
    out = {}
    for key in SL.list_link_sats():
        e = SL.load_sat_link(key)
        out[key] = [(l["freq_hz"], l["mode"]) for l in e["links"]]
    return out


def fake_opener(payloads):
    def _open(url, timeout_s, deadline_s):
        for norad, body in payloads.items():
            if f"norad_cat_id={norad}" in url:
                return body if isinstance(body, str) else json.dumps(body)
        raise RuntimeError(f"unexpected url {url}")
    return _open


def tx(uuid, low, high=None, mode="APT", status="active"):
    return {"uuid": uuid, "downlink_low": low, "downlink_high": high,
            "mode": mode, "baud": None, "status": status,
            "service": "Test", "sat_id": "T", "description": mode}


def set_cache_dir(path):
    os.environ["MBDSDR_CACHE_DIR"] = str(path)
    SF.clear_cache_memo()


def main():
    base_manual = manual_snapshot()

    # ---------------------------------------------------------------- 1
    set_cache_dir(WORK / "s1_no_cache")
    status1 = SL.machine_status()
    entry1 = SL.load_sat_link("noaa_15")
    hits1 = SL.identify_by_freq(137.1e6, 3000.0)
    r("1_无缓存",
      cache_exists=SF.cache_path() and os.path.exists(SF.cache_path()),
      state=status1["state"], text=status1["text"],
      sat_count=len(SL.list_link_sats()),
      manual=base_manual,
      noaa15_freq=entry1["links"][0]["freq_hz"],
      machine_state=entry1["_machine"]["state"],
      identify_137_1=sorted({h["_link_name"] for h in hits1}))

    # ---------------------------------------------------------------- 2a
    d2 = WORK / "s2_bad_json"
    d2.mkdir(parents=True)
    set_cache_dir(d2)
    Path(SF.cache_path()).write_text("{ this is not json", encoding="utf-8")
    st2a = SL.machine_status()
    ent2a = SL.load_sat_link("noaa_18")
    r("2a_坏JSON",
      state=st2a["state"], detail=st2a["detail"], text=st2a["text"],
      manual=manual_snapshot(),
      noaa18_freq=ent2a["links"][0]["freq_hz"],
      machine_state=ent2a["_machine"]["state"],
      exceptions=[])

    # ---------------------------------------------------------------- 2b
    d2b = WORK / "s2_wrong_schema"
    d2b.mkdir(parents=True)
    set_cache_dir(d2b)
    Path(SF.cache_path()).write_text(json.dumps(
        {"_schema": "someone-elses-v9", "satellites": "not-a-dict"}),
        encoding="utf-8")
    st2b = SL.machine_status()
    ent2b = SL.load_sat_link("iss_zarya")
    r("2b_结构不符",
      state=st2b["state"], detail=st2b["detail"], text=st2b["text"],
      iss_links=len(ent2b["links"]),
      iss_freqs=[l["freq_hz"] for l in ent2b["links"]],
      manual=manual_snapshot())

    # ---------------------------------------------------------------- 3
    d3 = WORK / "s3_expired"
    d3.mkdir(parents=True)
    set_cache_dir(d3)
    SL.refresh_machine_links(opener=fake_opener({
        25338: [tx("u-old", 137620000, 137620000, "APT", "inactive")]}))
    p = Path(SF.cache_path())
    payload = json.loads(p.read_text(encoding="utf-8"))
    old = time.time() - 30 * 86400
    payload["fetched_at"] = old
    payload["fetched_at_utc"] = datetime.fromtimestamp(
        old, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for rec in payload["satellites"].values():
        rec["fetched_at"] = old
        rec["fetched_at_utc"] = payload["fetched_at_utc"]
    p.write_text(json.dumps(payload), encoding="utf-8")
    SF.clear_cache_memo()
    st3 = SL.machine_status()
    ent3 = SL.load_sat_link("noaa_15")
    r("3_缓存过期",
      state=st3["state"], age_days=round((st3["age_s"] or 0) / 86400, 1),
      stale=st3["stale"], text=st3["text"],
      machine_state=ent3["_machine"]["state"],
      noaa15_freq=ent3["links"][0]["freq_hz"],
      noaa15_low=ent3["links"][0].get("low_hz"),
      noaa15_status=ent3["links"][0].get("status"),
      manual=manual_snapshot())

    # ---------------------------------------------------------------- 4
    # 先准备一份有效旧缓存，再模拟网络不可达
    d4 = WORK / "s4_offline_net"
    d4.mkdir(parents=True)
    set_cache_dir(d4)
    SL.refresh_machine_links(opener=fake_opener({
        25338: [tx("u-cached", 137620000, 137620000)]}))
    before_bytes = Path(SF.cache_path()).read_bytes()

    old_env = {k: os.environ.get(k) for k in
               ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY")}
    try:
        os.environ["HTTPS_PROXY"] = "http://127.0.0.1:9"
        os.environ["HTTP_PROXY"] = "http://127.0.0.1:9"
        os.environ["ALL_PROXY"] = "http://127.0.0.1:9"
        os.environ["NO_PROXY"] = ""
        t0 = time.monotonic()
        res4 = SL.refresh_machine_links()
        elapsed = time.monotonic() - t0
    finally:
        for k, v in old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    after_bytes = Path(SF.cache_path()).read_bytes()
    ent4 = SL.load_sat_link("noaa_15")
    r("4_网络不可达_代理黑洞",
      proxy="HTTPS_PROXY/HTTP_PROXY/ALL_PROXY=http://127.0.0.1:9, NO_PROXY=''",
      elapsed_s=round(elapsed, 3),
      state=res4["state"], written=res4["written"],
      failure_kinds=sorted({v["kind"] for v in res4["failures"].values()}),
      failure_count=len(res4["failures"]),
      old_cache_unchanged=(before_bytes == after_bytes),
      status_text=SL.machine_status_text(),
      manual=manual_snapshot(),
      noaa15_freq=ent4["links"][0]["freq_hz"],
      noaa15_low=ent4["links"][0].get("low_hz"))

    # ---------------------------------------------------------------- 5
    # 缓存目录被一个同名文件占住（无法建目录）
    blocker = WORK / "s5_blocker"
    blocker.parent.mkdir(parents=True, exist_ok=True)
    blocker.write_text("i am a file", encoding="utf-8")
    set_cache_dir(blocker / "cache")
    res5 = SL.refresh_machine_links(opener=fake_opener({
        25338: [tx("u5", 137620000)]}))
    r("5_缓存目录不可创建",
      state=res5["state"], written=res5["written"],
      detail=res5["detail"], status_text=SL.machine_status_text(),
      manual=manual_snapshot())

    # ---------------------------------------------------------------- 6
    # 缓存文件只读（Windows 属性）：替换目标失败
    d6 = WORK / "s6_readonly_file"
    d6.mkdir(parents=True)
    set_cache_dir(d6)
    SL.refresh_machine_links(opener=fake_opener({
        25338: [tx("u6-before", 137620000)]}))
    cache6 = Path(SF.cache_path())
    os.chmod(cache6, stat.S_IREAD)
    before6 = cache6.read_bytes()
    try:
        res6 = SL.refresh_machine_links(opener=fake_opener({
            25338: [tx("u6-after", 137620000)]}))
        after6 = cache6.read_bytes()
    finally:
        os.chmod(cache6, stat.S_IWRITE)
    ent6 = SL.load_sat_link("noaa_15")
    r("6_缓存文件只读",
      state=res6["state"], written=res6["written"],
      detail=res6["detail"], cache_unchanged=(before6 == after6),
      status_text=SL.machine_status_text(),
      manual=manual_snapshot(),
      noaa15_freq=ent6["links"][0]["freq_hz"],
      noaa15_low=ent6["links"][0].get("low_hz"))

    # ---------------------------------------------------------------- 7
    # 缓存文件不可读（deny R）：走 invalid 回退
    d7 = WORK / "s7_unreadable_file"
    d7.mkdir(parents=True)
    set_cache_dir(d7)
    SL.refresh_machine_links(opener=fake_opener({
        25338: [tx("u7", 137620000)]}))
    cache7 = Path(SF.cache_path())
    who = os.popen("whoami").read().strip()
    subprocess_ok = True
    rc1 = os.system(f'icacls "{cache7}" /deny "{who}:(R)" >nul 2>&1')
    try:
        SF.clear_cache_memo()
        st7 = SL.machine_status()
        ent7 = SL.load_sat_link("noaa_15")
        r7 = dict(state=st7["state"], detail=st7["detail"],
                  text=st7["text"], machine_state=ent7["_machine"]["state"],
                  noaa15_freq=ent7["links"][0]["freq_hz"],
                  manual=manual_snapshot(),
                  icacls_deny_rc=rc1)
    finally:
        os.system(f'icacls "{cache7}" /remove:d "{who}" >nul 2>&1')
    r("7_缓存文件不可读", **r7)

    out = Path(tempfile.gettempdir()) / "w92-degrade-results.json"
    out.write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"\nWROTE {out}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        raise
