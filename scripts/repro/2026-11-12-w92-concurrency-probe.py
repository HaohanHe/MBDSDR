# -*- coding: utf-8 -*-
"""W92 并发/权限探针（复跑入口）。

看四件真事：
  A. 多进程同时刷新 -> 缓存 JSON 会不会被写坏/读到半截；
  B. 双进程「先读后写」交叉 -> 是否丢星（跨进程锁是否有效）；
  C. 读者持有缓存文件时 os.replace 是否失败（Windows 共享冲突）；
  D. 只读目录（ACL deny W）下刷新与启动读取路径是否抛异常。

复跑（仓库根目录，Windows）::

    python scripts/repro/2026-11-12-w92-concurrency-probe.py

结果写到系统临时目录的 ``w92-concurrency-results.json``，不覆盖入库证据。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from multiprocessing import Barrier, Process, Queue
from pathlib import Path

REPO = str(Path(__file__).resolve().parents[2])
sys.path.insert(0, REPO)
os.chdir(REPO)

WORK = Path(tempfile.mkdtemp(prefix="w92-concurrency-")) / "work"
if WORK.exists():
    shutil.rmtree(WORK, ignore_errors=True)
WORK.mkdir(parents=True, exist_ok=True)
OUT = Path(tempfile.gettempdir()) / "w92-concurrency-results.json"
RESULTS = []

TARGET = {"norad_cat_id": 25338, "key": "noaa_15"}


def tx(uuid):
    return {"uuid": uuid, "downlink_low": 137620000, "downlink_high": None,
            "mode": "APT", "baud": None, "status": "active",
            "service": "Test", "sat_id": "T", "description": "APT"}


def rec(name, **kw):
    kw["case"] = name
    RESULTS.append(kw)
    print(f"\n[{name}]")
    for k, v in kw.items():
        if k != "case":
            print(f"  {k} = {v!r}")


# ------------------------------------------------------------------ A
def _writer(pid, cache_dir, iters, q):
    os.environ["MBDSDR_CACHE_DIR"] = cache_dir
    from mbdsdr_ai import sat_links_fetch as SF
    counter = [0]

    def opener(url, timeout_s, deadline_s):
        counter[0] += 1
        return json.dumps([tx(f"p{pid}-{counter[0]}")])

    ok = fail = 0
    for _ in range(iters):
        res = SF.refresh([TARGET], opener=opener)
        if res.get("written"):
            ok += 1
        else:
            fail += 1
    q.put((pid, ok, fail))


def case_a(n_proc=6, iters=25):
    d = WORK / "a_atomic"
    d.mkdir(parents=True)
    cache = d / "sat_links_machine.json"
    os.environ["MBDSDR_CACHE_DIR"] = str(d)
    from mbdsdr_ai import sat_links_fetch as SF
    SF.clear_cache_memo()
    q = Queue()
    procs = [Process(target=_writer, args=(i, str(d), iters, q))
             for i in range(n_proc)]
    for p in procs:
        p.start()
    reads = parse_errors = empty_reads = missing = other_err = 0
    lc_ok = lc_invalid = lc_missing = 0
    bad_samples = []
    while any(p.is_alive() for p in procs):
        # 应用真实读路径（含重试、不 memo 化瞬时失败）并发读
        _p, _st = SF.load_cache()
        s = _st.get("state")
        if s == "ok":
            lc_ok += 1
        elif s == "invalid":
            lc_invalid += 1
        else:
            lc_missing += 1
        try:
            raw = cache.read_bytes()
        except FileNotFoundError:
            missing += 1
            continue
        except OSError as e:
            other_err += 1
            bad_samples.append(f"read:{type(e).__name__}:{e}")
            continue
        reads += 1
        if not raw:
            empty_reads += 1
            continue
        try:
            json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as e:
            parse_errors += 1
            if len(bad_samples) < 3:
                bad_samples.append(f"{type(e).__name__}:{e} bytes={raw[:60]!r}")
        time.sleep(0.001)
    for p in procs:
        p.join()
    workers = [q.get() for _ in procs]
    final_ok = final_n = None
    try:
        final = json.loads(cache.read_text(encoding="utf-8"))
        final_ok = True
        final_n = len((final.get("satellites") or {}).get("25338", {})
                      .get("transmitters") or [])
    except Exception as e:  # noqa: BLE001
        final_ok = f"{type(e).__name__}: {e}"
    rec("A_多进程并发刷新_原子性",
        n_proc=n_proc, iters=iters, reads=reads,
        parse_errors=parse_errors, empty_reads=empty_reads,
        missing_reads=missing, other_read_errors=other_err,
        load_cache_ok=lc_ok, load_cache_invalid=lc_invalid,
        load_cache_missing=lc_missing,
        bad_samples=bad_samples,
        worker_ok=[w[1] for w in workers],
        worker_fail=[w[2] for w in workers],
        final_json_valid=final_ok, final_tx_count=final_n,
        leftover_tmp=sorted(p.name for p in d.glob("*.tmp*")))


# ------------------------------------------------------------------ B
def _racer(norad, cache_dir, barrier, q):
    os.environ["MBDSDR_CACHE_DIR"] = cache_dir
    from mbdsdr_ai import sat_links_fetch as SF

    def opener(url, timeout_s, deadline_s):
        # load_cache 已在 refresh() 里先跑完；到这里两进程都已读到旧缓存
        barrier.wait(timeout=30)
        return json.dumps([tx(f"race-{norad}")])

    try:
        res = SF.refresh([{"norad_cat_id": norad, "key": f"k{norad}"}],
                         opener=opener)
        q.put((norad, res.get("state"), res.get("written")))
    except Exception as e:  # noqa: BLE001
        q.put((norad, "EXC", f"{type(e).__name__}: {e}"))


def case_b():
    d = WORK / "b_lost_update"
    d.mkdir(parents=True)
    cache = d / "sat_links_machine.json"
    barrier = Barrier(2)
    q = Queue()
    procs = [Process(target=_racer, args=(norad, str(d), barrier, q))
             for norad in (25338, 33591)]
    for p in procs:
        p.start()
    for p in procs:
        p.join()
    procs_out = [q.get() for _ in procs]
    sats = {}
    valid = None
    try:
        payload = json.loads(cache.read_text(encoding="utf-8"))
        sats = sorted((payload.get("satellites") or {}).keys())
        valid = True
    except Exception as e:  # noqa: BLE001
        valid = f"{type(e).__name__}: {e}"
    rec("B_双进程交叉刷新_丢更新",
        procs=procs_out, final_valid=valid,
        final_sat_keys=sats, expected=2, lost=2 - len(sats or []))


# ------------------------------------------------------------------ C
def case_c():
    from mbdsdr_ai import sat_links_fetch as SF
    d = WORK / "c_reader_holds"
    d.mkdir(parents=True)
    os.environ["MBDSDR_CACHE_DIR"] = str(d)
    SF.clear_cache_memo()
    SF.refresh([TARGET], opener=lambda u, t, dl: json.dumps([tx("before")]))
    cache = Path(SF.cache_path())
    before = cache.read_bytes()
    fh = open(cache, "r", encoding="utf-8")   # 模拟 UI 正在读缓存
    try:
        res = SF.refresh(
            [TARGET], opener=lambda u, t, dl: json.dumps([tx("after")]))
        holder_fh_kept = True
    finally:
        fh.close()
    after = cache.read_bytes()
    rec("C_读者持有文件时写盘",
        reader_handle_kept_open=holder_fh_kept,
        state=res.get("state"), written=res.get("written"),
        detail=res.get("detail"),
        cache_unchanged=(before == after),
        disk_uuid=(json.loads(after.decode("utf-8"))
                   .get("satellites", {}).get("25338", {})
                   .get("transmitters", [{}])[0].get("uuid")))


# ------------------------------------------------------------------ D
def case_d():
    from mbdsdr_ai import sat_links as SL
    from mbdsdr_ai import sat_links_fetch as SF
    d = WORK / "d_readonly_dir"
    d.mkdir(parents=True)
    os.environ["MBDSDR_CACHE_DIR"] = str(d)
    SF.clear_cache_memo()
    SF.refresh([TARGET], opener=lambda u, t, dl: json.dumps([tx("pre")]))
    who = subprocess.run(["whoami"], capture_output=True, text=True).stdout.strip()
    rc = subprocess.run(["icacls", str(d), "/deny", f"{who}:(W)"],
                        capture_output=True, text=True).returncode
    try:
        SF.clear_cache_memo()
        status = SL.machine_status()
        entry = SL.load_sat_link("noaa_15")
        read_ok = {"state": status["state"], "text": status["text"],
                   "freq": entry["links"][0]["freq_hz"]}
        res = SF.refresh(
            [TARGET], opener=lambda u, t, dl: json.dumps([tx("post")]))
        rec("D_只读目录_ACL拒绝写",
            icacls_deny_rc=rc, read_path=read_ok,
            refresh_state=res.get("state"), written=res.get("written"),
            detail=res.get("detail"),
            status_text_after=SL.machine_status_text(),
            manual_sats=len(SL.list_link_sats()),
            exceptions="none")
    except Exception as e:  # noqa: BLE001
        rec("D_只读目录_ACL拒绝写", icacls_deny_rc=rc, exceptions=(
            f"{type(e).__name__}: {e}"))
    finally:
        subprocess.run(["icacls", str(d), "/remove:d", who],
                       capture_output=True, text=True)


def main():
    case_a()
    case_b()
    case_c()
    case_d()
    OUT.write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"\nWROTE {OUT}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        raise
