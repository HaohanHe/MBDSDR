#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
配置健壮性与防呆（tests/test_settings_corruption_recovery.py）
==============================================================

钉住「用户把 desktop_settings.json 搞坏了会不会崩 / 会不会静默变成
一个看似正常的默认值」：

  * 五类损坏：非法 JSON、顶层非对象、类型错、越界、缺键、未知键；
  * 音量量纲的 v1 → v2 迁移链（含越界旧值）；
  * 原子写（唯一临时文件 + os.replace）与两个进程并发写的字段保全；
  * 反证：把某个字段的类型校验摘掉后，上面的守卫必须能红。

所有用例都用 tmp_path 显式路径，不触碰真实 %APPDATA%；默认路径用例
显式请求 `isolated_profile`。
"""
import dataclasses
import json
import os
import subprocess
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402

import settings as settings_module  # noqa: E402
from config_schema import SCHEMA, validate_value  # noqa: E402
from settings import DEFAULTS, DesktopSettings  # noqa: E402


def _write(path, body: str):
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    return str(path)


def _notes_lower(ds) -> str:
    return " ".join(ds.migration_notes or []).lower()


# ---------------------------------------------------------------------------
# 1. 非法 JSON / 顶层非对象：崩溃还是备份 + 报错？
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("body", ["{oops", "[]", '"just a string"', ""])
def test_unparseable_file_is_backed_up_and_reported(tmp_path, body):
    path = _write(tmp_path / "desktop_settings.json", body)
    ds = DesktopSettings.load(path=path)          # 不抛 = 不崩

    assert not os.path.exists(path), "坏文件应被改名备份，不能被静默覆盖"
    assert ds.corrupt_backup and os.path.exists(ds.corrupt_backup)
    assert "corrupt-" in os.path.basename(ds.corrupt_backup)
    assert ds.last_error and "配置读取失败" in ds.last_error
    assert "备份" in ds.last_error
    assert ds.values() == DEFAULTS                # 回默认，但痕迹都在


# ---------------------------------------------------------------------------
# 2. 类型错：回默认，但 notes 必须逐项留痕
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("key, bad, expected, keyword", [
    ("sample_rate_hz", "abc", 2_400_000.0, "无法解析为数字"),
    ("volume", "x", 48, "无法解析为数字"),
    ("show_waterfall", [], True, "布尔字段"),
    ("callsign", {"a": 1}, "", "文本字段"),
    ("theme", {"x": 1}, DEFAULTS["theme"], "不在可选值"),
    ("fft_size", [2048], 2048, "不在可选值"),
])
def test_wrong_type_falls_back_with_visible_note(tmp_path, key, bad, expected, keyword):
    path = _write(tmp_path / "desktop_settings.json", json.dumps({key: bad}))
    ds = DesktopSettings.load(path=path)

    assert ds.get(key) == expected, f"{key}: 类型错必须回默认/合法档，而不是原样带坏值"
    assert ds.last_error is None, f"{key}: 类型错不是文件级损坏，不该报读取失败"
    notes = _notes_lower(ds)
    assert keyword.lower() in notes, f"{key}: 回落必须留痕，实际 notes={ds.migration_notes!r}"
    assert key in notes


# ---------------------------------------------------------------------------
# 3. 越界：夹到边界，且必须写 notes
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("key, bad, expected, keyword", [
    ("peak_threshold_db", 999, 30.0, "上限"),
    ("bortle", 42, 9, "上限"),
    ("squelch_default_db", 10, -20, "上限"),
])
def test_out_of_range_is_clamped_with_note(tmp_path, key, bad, expected, keyword):
    path = _write(tmp_path / "desktop_settings.json", json.dumps({key: bad}))
    ds = DesktopSettings.load(path=path)
    assert ds.get(key) == expected
    assert keyword in _notes_lower(ds)
    assert key in _notes_lower(ds)


def test_volume_negative_is_clamped_with_note(tmp_path):
    """当前格式（有 v2 标记）下 volume=-5 必须夹到 0 并留痕。"""
    path = _write(tmp_path / "desktop_settings.json",
                  json.dumps({"volume": -5, "volume_scale_v2": True}))
    ds = DesktopSettings.load(path=path)
    assert ds.get("volume") == 0
    assert "低于下限" in _notes_lower(ds)


def test_sample_rate_999ms_is_accepted_known_boundary(tmp_path):
    """已知边界：schema 对采样率没有上限（设备能力才是事实源）。

    `mbdsdr_ai/sdr_backend.py` 的 network_iq 后端声明可达 1 GHz，所以
    schema 层无法把 999 MS/s 判成"非法"；实际设备在 set_sample_rate 时
    会取最近可用档并回报实际值。这不是静默改成默认值，故本用例锁定
    「接受、不写 notes」的现状，并把局限写进文档而不是假装能拦。
    """
    path = _write(tmp_path / "desktop_settings.json",
                  json.dumps({"sample_rate_hz": 999_000_000}))
    ds = DesktopSettings.load(path=path)
    assert ds.get("sample_rate_hz") == 999_000_000
    assert not ds.migration_notes


# ---------------------------------------------------------------------------
# 4. 缺键 / 未知键
# ---------------------------------------------------------------------------
def test_missing_keys_are_filled_from_defaults(tmp_path):
    path = _write(tmp_path / "desktop_settings.json",
                  json.dumps({"frequency_hz": 100_000_000.0}))
    ds = DesktopSettings.load(path=path)

    assert ds.get("frequency_hz") == 100_000_000.0
    expected = dict(DEFAULTS)
    expected["frequency_hz"] = 100_000_000.0
    assert ds.values() == expected
    # 缺键是版本升级的正常路径（不是损坏），用 has_file_key 区分原位/补齐。
    assert ds.has_file_key("frequency_hz")
    assert not ds.has_file_key("theme")
    assert not ds.migration_notes


def test_unknown_keys_are_reported_and_preserved_on_write(tmp_path):
    path = _write(tmp_path / "desktop_settings.json",
                  json.dumps({"frequency_hz": 1e8, "ghost_key": 1,
                              "future_new_flag": {"nested": True}}))
    ds = DesktopSettings.load(path=path)

    assert "未知键" in _notes_lower(ds)
    assert ds.get("ghost_key") is None, "未知键不得进入设置表面"
    assert ds.get("frequency_hz") == 1e8

    ds.set("theme", "dark")
    ds.flush()
    with open(path, encoding="utf-8") as f:
        on_disk = json.load(f)
    # 旧版本不许把新版本的设置键吃掉：写盘时保留未知键。
    assert on_disk["ghost_key"] == 1
    assert on_disk["future_new_flag"] == {"nested": True}
    assert on_disk["theme"] == "dark"


# ---------------------------------------------------------------------------
# 5. 迁移链：v1（0–63 硬件音量）→ v2（0–100）
# ---------------------------------------------------------------------------
def test_v1_volume_migration_is_ordered_and_idempotent(tmp_path):
    path = _write(tmp_path / "desktop_settings.json",
                  json.dumps({"volume": 42, "theme": "dark",
                              "frequency_hz": 99_000_000.0}))
    ds = DesktopSettings.load(path=path)

    assert ds.get("volume") == 67, "42/63 × 100 ≈ 67"
    assert ds.get("volume_scale_v2") is True
    assert ds.get("theme") == "dark" and ds.get("frequency_hz") == 99_000_000.0
    assert "换算" in _notes_lower(ds) and "volume" in _notes_lower(ds)

    ds.flush()                                    # 迁移结果必须能落盘
    again = DesktopSettings.load(path=path)
    assert again.get("volume") == 67
    assert again.get("volume_scale_v2") is True
    assert not again.migration_notes, "二次读取不得再迁移（不幂等）"


def test_v1_volume_out_of_hardware_range_is_reported(tmp_path):
    path = _write(tmp_path / "desktop_settings.json", json.dumps({"volume": -5}))
    ds = DesktopSettings.load(path=path)

    assert ds.get("volume") == 0
    notes = _notes_lower(ds)
    assert "0–63" in notes and "夹到" in notes, (
        f"旧格式越界值不许静默夹取，实际 notes={ds.migration_notes!r}")


def test_settings_backend_fields_all_have_defaults():
    """迁移讨论里"没默认值的字段"：settings 后端一个都没有。"""
    assert all(v is not None for v in DEFAULTS.values())
    assert all(it.default is not None
               for it in SCHEMA.values() if it.storage == "settings")


# ---------------------------------------------------------------------------
# 6. 原子写：replace 失败时旧文件必须完好，且不留临时文件
# ---------------------------------------------------------------------------
def test_atomic_write_keeps_old_file_when_replace_fails(tmp_path, monkeypatch):
    path = _write(tmp_path / "desktop_settings.json", json.dumps({"theme": "dark"}))
    ds = DesktopSettings.load(path=path)
    ds.set("theme", "dark")                       # 保证磁盘上有一份已知内容
    ds.flush()
    with open(path, encoding="utf-8") as f:
        before = f.read()

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(settings_module.os, "replace", boom)
    ds.set("theme", "dark_car")
    ds.flush()                                    # 不抛

    assert ds.last_error and "保存失败" in ds.last_error
    with open(path, encoding="utf-8") as f:
        after = f.read()
    assert after == before, "原子写失败不得留下半截文件"
    assert json.loads(after)["theme"] == "dark"
    assert not [n for n in os.listdir(tmp_path) if ".tmp" in n]


# ---------------------------------------------------------------------------
# 7. 并发：两个进程各写 100 次，字段不许互丢
# ---------------------------------------------------------------------------
_WORKER = r"""
import json, os, sys
sys.path.insert(0, os.path.join(os.getcwd(), "desktop"))
from settings import DesktopSettings
path, who = sys.argv[1], sys.argv[2]
keys = (["frequency_hz", "callsign"] if who == "A" else ["gain_db", "demod_mode"])
vals = ({"frequency_hz": 100_000_000.0, "callsign": "CALL-A"} if who == "A"
        else {"gain_db": 30.0, "demod_mode": "WFM"})
ds = DesktopSettings.load(path=path)
errors = []
for _ in range(100):
    for k in keys:
        ds.set(k, vals[k])
    ds.flush()
    if ds.last_error:
        errors.append(ds.last_error)
print(json.dumps({"who": who, "err_count": len(errors), "errors": errors[:3]}))
"""


def test_two_processes_can_write_concurrently_without_losing_fields(tmp_path):
    script = tmp_path / "worker.py"
    script.write_text(_WORKER, encoding="utf-8")
    config = str(tmp_path / "desktop_settings.json")
    procs = [subprocess.Popen(
        [sys.executable, str(script), config, who],
        cwd=_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for who in ("A", "B")]
    results = []
    for proc in procs:
        out, err = proc.communicate(timeout=120)
        assert proc.returncode == 0, err[-500:]
        results.append(json.loads(out.strip().splitlines()[-1]))
    # 锁超时在重载负载下可能出现（Windows 上多进程同抢 0.01s 步进的互斥）。
    # 此时仍走原子替换、且读-合并保证 A/B 字段都保留——所以「报错但不丢」
    # 是可接受结局；断言的是最终字段保全，而不是 0 次锁竞争。
    for r in results:
        assert r["err_count"] <= 2, r
        for e in r["errors"]:
            assert "写锁" in e, e

    with open(config, encoding="utf-8") as f:
        on_disk = json.load(f)                    # 文件必须完整可解析
    assert on_disk["frequency_hz"] == 100_000_000.0
    assert on_disk["callsign"] == "CALL-A"
    assert on_disk["gain_db"] == 30.0
    assert on_disk["demod_mode"] == "WFM"
    assert not [n for n in os.listdir(tmp_path) if ".tmp" in n]


# ---------------------------------------------------------------------------
# 8. 反证：摘掉某字段的类型校验，守卫必须红
# ---------------------------------------------------------------------------
def test_removing_type_check_makes_the_guard_red(tmp_path, monkeypatch):
    key, bad = "sample_rate_hz", "abc"
    path = _write(tmp_path / "desktop_settings.json", json.dumps({key: bad}))

    ds = DesktopSettings.load(path=path)
    norm, reason = validate_value(SCHEMA[key], bad)
    assert ds.get(key) == norm == DEFAULTS[key]
    assert reason and "无法解析为数字" in reason     # 守卫绿（有校验时）

    # 变异：把该字段当 text（= 删掉数字类型校验），同一份坏文件应能混过去。
    mutant = dataclasses.replace(SCHEMA[key], kind="text")
    mutant_path = _write(tmp_path / "mutant.json", json.dumps({key: bad}))
    monkeypatch.setitem(settings_module.SCHEMA, key, mutant)
    bad_ds = DesktopSettings.load(path=str(mutant_path))

    assert bad_ds.get(key) == bad, "反证失败：摘掉校验后守卫仍拦住了，说明测的不是类型校验"
    assert not bad_ds.migration_notes, "反证失败：摘掉校验后仍留下说明"


# ---------------------------------------------------------------------------
# 9. 默认路径必须被 isolated_profile 关进临时目录
# ---------------------------------------------------------------------------
def test_default_path_stays_inside_isolated_profile(isolated_profile):
    ds = DesktopSettings()
    assert ds.path.startswith(os.environ["APPDATA"]), ds.path


# ---------------------------------------------------------------------------
# 10. 写盘路径遇到坏文件：先备份，备份失败则拒绝覆盖
# ---------------------------------------------------------------------------
#: 加载成功后磁盘文件被别的进程/编辑器弄坏的形态（截断 JSON）。
_CORRUPT_SETTINGS = '{"theme": "dark", "future_flag": {"trunc'


def _corrupt_on_disk(path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(_CORRUPT_SETTINGS)


def _backups(path):
    directory, name = os.path.split(str(path))
    return sorted(
        os.path.join(directory, entry)
        for entry in os.listdir(directory)
        if entry.startswith(name + ".corrupt-")
        and not entry.endswith(".reason.txt"))


def _assert_write_after_corrupt_backs_up(tmp_path):
    """load 之后文件才损坏：写盘必须先备份，且备份字节就是坏文件原样。"""
    path = _write(tmp_path / "desktop_settings.json", json.dumps({"theme": "dark"}))
    ds = DesktopSettings.load(path=path)
    _corrupt_on_disk(path)

    ds.set("callsign", "N0CALL")
    ds.flush()

    backups = _backups(path)
    assert len(backups) == 1, "写盘路径遇到坏文件却没留下备份"
    with open(backups[0], encoding="utf-8") as f:
        assert f.read() == _CORRUPT_SETTINGS, "备份内容与坏文件原样不一致"
    assert ds.corrupt_backup == backups[0]
    assert ds.last_error and "已备份" in ds.last_error, ds.last_error
    with open(path, encoding="utf-8") as f:
        on_disk = json.load(f)
    assert on_disk["callsign"] == "N0CALL"
    return path, ds, backups


def test_write_path_backs_up_newly_corrupted_file(tmp_path):
    _assert_write_after_corrupt_backs_up(tmp_path)


def test_write_after_corrupt_keeps_backup_and_old_bytes(tmp_path):
    path, ds, backups = _assert_write_after_corrupt_backs_up(tmp_path)
    # 再写一次：备份必须还在、字节不变——旧证据不能被后续写入吞掉。
    ds.set("callsign", "N0CALL2")
    ds.flush()
    assert _backups(path) == backups, "第二次写盘后备份消失了"
    with open(backups[0], encoding="utf-8") as f:
        assert f.read() == _CORRUPT_SETTINGS, "旧证据被后续写入吞掉"
    with open(path, encoding="utf-8") as f:
        assert json.load(f)["callsign"] == "N0CALL2"


def test_without_real_backup_the_guard_turns_red(tmp_path, monkeypatch):
    # 假备份：返回路径但不真改名 —— 等价于"备份逻辑被去掉"。
    target = tmp_path / "mutation"
    target.mkdir()
    monkeypatch.setattr(
        settings_module, "backup_corrupt_config",
        lambda p: f"{p}.corrupt-fake")
    with pytest.raises(AssertionError):
        _assert_write_after_corrupt_backs_up(target)


def test_backup_failure_refuses_to_overwrite(tmp_path, monkeypatch):
    """备份失败 → 本次不写，坏文件原样留盘（不能拿内存默认值顶掉证据）。"""
    path = _write(tmp_path / "desktop_settings.json", json.dumps({"theme": "dark"}))
    ds = DesktopSettings.load(path=path)
    _corrupt_on_disk(path)
    monkeypatch.setattr(settings_module, "backup_corrupt_config", lambda p: None)

    ds.set("callsign", "N0CALL")
    ds.flush()

    with open(path, encoding="utf-8") as f:
        assert f.read() == _CORRUPT_SETTINGS, "备份失败时仍把坏文件覆盖了"
    assert ds.last_error and "备份失败" in ds.last_error, ds.last_error
    assert ds._dirty is True, "未写入时应保留 dirty，等下次重试"


# ---------------------------------------------------------------------------
# 11. OSError（文件被占 / 读不了）不是损坏：不许备份、不许写、保留 dirty
# ---------------------------------------------------------------------------
def _assert_oserror_is_not_corruption(path, backups, dirty, last_error):
    assert backups == [], "OSError 被当成损坏，造了备份"
    assert dirty is True, "读取失败后没有保留 dirty，下次不会重试"
    assert last_error and "读取失败" in last_error, last_error
    assert "已备份" not in last_error, "临时读失败不该报「已备份」"


def test_read_oserror_does_not_backup_and_keeps_dirty(tmp_path):
    path = _write(tmp_path / "desktop_settings.json", json.dumps({"theme": "dark"}))
    ds = DesktopSettings.load(path=path)

    # 把路径换成目录：open(目录) → IsADirectoryError（OSError），不是内容损坏。
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    ds._path = str(blocked)
    ds.set("callsign", "N0CALL")
    ds.flush()

    _assert_oserror_is_not_corruption(
        str(blocked), _backups(blocked), ds._dirty, ds.last_error)
    assert not os.listdir(blocked), "读取失败后仍然写了文件"
    with open(path, encoding="utf-8") as f:
        assert json.load(f) == {"theme": "dark"}, "原配置被改了"


def test_mutated_oserror_branch_backs_up_and_turns_red(tmp_path, monkeypatch):
    """变异：把 OSError 也当损坏（备份后照写）→ 上面的断言必须红。

    这里不伪造结果、而是把「变异后的 ``_merged_payload``」原样喂给同一组
    断言：读失败时先备份再写，等价于把 ``except OSError`` 并进
    ``except ValueError``。
    """
    path = _write(tmp_path / "desktop_settings.json", json.dumps({"theme": "dark"}))
    ds = DesktopSettings.load(path=path)
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    ds._path = str(blocked)

    def mutated_merged(self):
        try:
            with open(self._path, encoding="utf-8") as f:
                return dict(json.load(f))
        except OSError:
            # 变异点：读失败也当损坏 → 备份 + 用默认值覆盖。
            backup = settings_module.backup_corrupt_config(self._path)
            self.corrupt_backup = backup
            self.last_error = f"配置文件损坏：原文件已备份为 {backup}"
            return dict(self._data)

    monkeypatch.setattr(DesktopSettings, "_merged_payload", mutated_merged)

    ds.set("callsign", "N0CALL")
    ds.flush()

    with pytest.raises(AssertionError):
        _assert_oserror_is_not_corruption(
            str(blocked), _backups(blocked), ds._dirty, ds.last_error)
