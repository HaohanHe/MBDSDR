# SPDX-License-Identifier: MIT
"""W88 / P0-18：TLE 落盘的**真·跨进程**断言（子进程退出 → 重启读回）。

与既有 ``test_sat_tracker_persistence.py`` 的分工（顺着已有接缝扩，不重写）：
  - 既有文件证明「同一进程内新建实例」能读回刷新写入的历元（等价重启）；
  - 本文件把边界推到**真正的进程边界**：writer 是一个独立子进程，刷新落盘后
    **退出**；reader 是另一个全新解释器，只靠磁盘文件（不共享任何内存/模块
    状态）重建 tracker。这才钉死「更新 TLE → 退出进程 → 重启 → 读回 epoch
    必须等于更新值」——同进程新实例无法排除模块级单例/环境残留的掩盖。

红线口径（科学理性务实）：
  - 正例锚点：读回历元 == 刷新写入的 ``2026-11-11T12:00:00Z``（精确到秒）；
  - 负例锚点：未落盘（空缓存）时读回内置 NOAA 15 的**精确**历元
    ``2026-09-25T20:42:15Z``，不是只比日期前缀；
  - 测试夹具是把内置真实 TLE 的历元列换成另一天并重算校验和得到的**结构
    合法** TLE（``_with_epoch`` 对内置原样往返可证），不是凭空捏造的轨道根数；
    本测试断言的是「落盘 plumbing」，不做任何轨道精度声明。

反证（counter-proof，自包含，无需外部探针插件）：
  - 正例 ``test_cross_process_persist_readback`` 即 silent 控制（未patch→绿）；
  - ``test_counterproof_disable_load_falls_back_to_builtin``：reader 子进程禁用
    ``_load_disk_cache`` → 读回退回内置历元（正例的 ``== NEW_EPOCH`` 断言在此
    被违反），证明正例对「读回机制」敏感；
  - ``test_counterproof_disable_persist_writes_nothing``：writer 子进程禁用
    ``_persist_entry`` → 磁盘无文件、读回退回内置，证明正例对「落盘机制」敏感。
"""
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mbdsdr_ai import sat_tracker  # noqa: E402
from mbdsdr_ai.sat_tracker import BUILTIN_TLE_DATE  # noqa: E402
from mbdsdr_ai.tle_integrity import freshness  # noqa: E402

# ---------------------------------------------------------------------------
# 夹具：内置真实 NOAA 15 TLE + 只换历元列并重算校验和的「更新版」
# ---------------------------------------------------------------------------
_BUILTIN_NOAA15 = next(b for b in sat_tracker._BUILTIN_TLES
                       if str(b[0]) == "NOAA 15")
_BUILTIN_L1 = _BUILTIN_NOAA15[1]
_BUILTIN_L2 = _BUILTIN_NOAA15[2]

#: 精确历元锚点（由 parse_tle_epoch 从内置/夹具 line1 实算，见下方 sanity 测试）
BUILTIN_EPOCH = "2026-09-25T20:42:15Z"
NEW_EPOCH = "2026-11-11T12:00:00Z"
_NEW_EPOCH_FIELD = "26315.50000000"   # 年内第 315.5 天 = 2026-11-11T12:00:00Z


def _tle_checksum(line: str) -> int:
    """TLE 校验和：前 68 字符里数字按值累加、'-' 记 1、其余记 0，模 10。"""
    s = 0
    for ch in line[:68]:
        if ch.isdigit():
            s += int(ch)
        elif ch == "-":
            s += 1
    return s % 10


def _with_epoch(line1: str, epoch_field: str) -> str:
    """把 line1 的历元列（0 基 [18:32]，14 字符）换成 epoch_field 并重算校验和。

    只动历元列与末位校验和，其余轨道根数原样保留——夹具仍是结构合法的 TLE。
    """
    body = line1[:18] + epoch_field + line1[32:68]
    return body + str(_tle_checksum(body))


_NEW_L1 = _with_epoch(_BUILTIN_L1, _NEW_EPOCH_FIELD)
_NEW_L2 = _BUILTIN_L2                 # line2 不变，校验和仍合法
_NEW_TLE_TEXT = "NOAA 15\n" + _NEW_L1 + "\n" + _NEW_L2 + "\n"


# ---------------------------------------------------------------------------
# 子进程脚本（writer 刷新落盘后退出；reader 全新解释器只读磁盘）
# ---------------------------------------------------------------------------
_WRITER_TEMPLATE = r"""
import os, sys, types
os.environ.pop("MBDSDR_OFFLINE", None)
arm = os.environ.get("PROBE_W88_ARM", "")
req = types.ModuleType("requests")
class _Resp:
    text = __TLE_TEXT_REPR__
    @staticmethod
    def raise_for_status():
        return None
req.get = lambda *a, **k: _Resp()
sys.modules["requests"] = req
from mbdsdr_ai import orbit
orbit.reset_tle_cache()
from mbdsdr_ai.sat_tracker import SatelliteTracker
from mbdsdr_ai.sat_passes import GroundStation
if arm == "no_persist":
    SatelliteTracker._persist_entry = lambda self, entry, fetched_at: None
t = SatelliteTracker(GroundStation(lat_deg=0.0, lon_deg=0.0, alt_m=0.0))
n = t.update_tle_from_celestrak()
print("UPDATED=%d" % n)
print("PERSIST_ERR=%s" % t.last_persist_error)
"""

_READER_TEMPLATE = r"""
import os, sys, json
os.environ["MBDSDR_OFFLINE"] = "1"
arm = os.environ.get("PROBE_W88_ARM", "")
from mbdsdr_ai import orbit
orbit.reset_tle_cache()
from mbdsdr_ai.sat_tracker import SatelliteTracker
from mbdsdr_ai.sat_passes import GroundStation
if arm == "no_load":
    SatelliteTracker._load_disk_cache = lambda self: 0
t = SatelliteTracker(GroundStation(lat_deg=0.0, lon_deg=0.0, alt_m=0.0))
sat = next(s for s in t.list_satellites() if s["name"] == "NOAA 15")
print("READER_JSON=" + json.dumps({
    "epoch": sat["tle_epoch_utc"], "tle_date": t.tle_date,
    "line1": sat["tle"][0], "line2": sat["tle"][1]}))
"""


def _run(script: str, cache_dir, arm: str = "") -> str:
    """在独立子进程里跑脚本；非零退出即带 stdout/stderr 抛错（不吞现场）。"""
    env = dict(os.environ)
    env["MBDSDR_TLE_CACHE_DIR"] = str(cache_dir)
    env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PROBE_W88_ARM"] = arm
    proc = subprocess.run(
        [sys.executable, "-c", script], cwd=ROOT, env=env,
        capture_output=True, text=True, encoding="utf-8", timeout=120)
    if proc.returncode != 0:
        raise AssertionError(
            f"子进程退出码 {proc.returncode}\nSTDOUT:\n{proc.stdout}\n"
            f"STDERR:\n{proc.stderr}")
    return proc.stdout


def _write_via_refresh(cache_dir, arm: str = "") -> str:
    """writer 子进程：mock 网络刷新 NOAA 15 → 落盘 → 退出。"""
    script = _WRITER_TEMPLATE.replace("__TLE_TEXT_REPR__", repr(_NEW_TLE_TEXT))
    return _run(script, cache_dir, arm)


def _read_back(cache_dir, arm: str = "") -> dict:
    """reader 子进程：全新解释器只靠磁盘重建 tracker，读回 NOAA 15 历元。"""
    out = _run(_READER_TEMPLATE, cache_dir, arm)
    for line in out.splitlines():
        if line.startswith("READER_JSON="):
            return json.loads(line[len("READER_JSON="):])
    raise AssertionError(f"reader 输出无 READER_JSON：\n{out}")


# ---------------------------------------------------------------------------
# 夹具合法性（纯函数，无子进程）：证明「换历元」夹具不是捏造数据
# ---------------------------------------------------------------------------
def test_checksum_helper_reproduces_builtin():
    """_with_epoch 对内置原样往返 == 内置原行；校验和与内置末位一致。"""
    assert _with_epoch(_BUILTIN_L1, _BUILTIN_L1[18:32]) == _BUILTIN_L1
    assert _tle_checksum(_BUILTIN_L1) == int(_BUILTIN_L1[68])
    assert _tle_checksum(_BUILTIN_L2) == int(_BUILTIN_L2[68])
    # 夹具结构合法（校验和自洽）且历元严格更新
    assert _tle_checksum(_NEW_L1) == int(_NEW_L1[68])
    assert freshness(_BUILTIN_L1)["epoch_utc"] == BUILTIN_EPOCH
    assert freshness(_NEW_L1)["epoch_utc"] == NEW_EPOCH
    assert NEW_EPOCH > BUILTIN_EPOCH


# ---------------------------------------------------------------------------
# 正例（silent 控制）：真·跨进程落盘读回
# ---------------------------------------------------------------------------
def test_cross_process_persist_readback(tmp_path):
    """writer 子进程刷新落盘后退出；reader 子进程只靠磁盘读回 == 更新历元。"""
    cache = tmp_path / "tle_cache"
    out = _write_via_refresh(cache)
    assert "UPDATED=1" in out, out          # 假响应只含 NOAA 15 → 恰好更新 1 颗
    assert out.splitlines()[-1] == "PERSIST_ERR=", out   # 落盘无错
    assert (cache / "25338.tle").is_file(), "刷新成功必须落盘"

    data = _read_back(cache)                 # 另一个进程，不共享内存
    assert data["epoch"] == NEW_EPOCH        # 精确到秒，不是只比日期
    assert data["line1"] == _NEW_L1
    assert data["line2"] == _NEW_L2
    assert data["tle_date"] == "2026-11-11"


# ---------------------------------------------------------------------------
# 负例：未落盘（空缓存）→ 读回内置精确历元
# ---------------------------------------------------------------------------
def test_empty_cache_reads_builtin_epoch_exact(tmp_path):
    """空缓存目录：reader 子进程读回内置 NOAA 15 的精确历元（W88 负例锚点）。"""
    cache = tmp_path / "empty_cache"         # 从不创建、无任何 .tle
    data = _read_back(cache)
    assert data["epoch"] == BUILTIN_EPOCH    # 2026-09-25T20:42:15Z，精确
    assert data["line1"] == _BUILTIN_L1
    assert data["tle_date"] == BUILTIN_TLE_DATE


# ---------------------------------------------------------------------------
# 反证臂：禁用读回 / 禁用落盘 → 正例期望被违反（退回内置），证明正例敏感
# ---------------------------------------------------------------------------
def test_counterproof_disable_load_falls_back_to_builtin(tmp_path):
    """磁盘确有新 TLE，但 reader 禁用 _load_disk_cache → 读回退回内置历元。

    这正是正例 ``epoch == NEW_EPOCH`` 的反面：若读回机制失效，正例必红。
    """
    cache = tmp_path / "tle_cache"
    _write_via_refresh(cache)                # 磁盘里确实写入了新 TLE
    assert (cache / "25338.tle").is_file()
    data = _read_back(cache, arm="no_load")  # reader 不读磁盘
    assert data["epoch"] == BUILTIN_EPOCH    # 降级：证明正例对读回机制敏感
    assert data["line1"] == _BUILTIN_L1


def test_counterproof_disable_persist_writes_nothing(tmp_path):
    """writer 禁用 _persist_entry → 磁盘无文件；silent reader 读回退回内置。

    证明「落盘」是跨进程存活的唯一载体：不落盘，正例必红。
    """
    cache = tmp_path / "tle_cache"
    out = _write_via_refresh(cache, arm="no_persist")
    assert out.splitlines()[-1] == "PERSIST_ERR=", out   # no-op 不报错
    assert not (cache / "25338.tle").exists(), "禁用落盘后不得有缓存文件"
    data = _read_back(cache)                 # silent reader
    assert data["epoch"] == BUILTIN_EPOCH    # 降级：证明正例对落盘机制敏感