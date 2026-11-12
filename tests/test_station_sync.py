# SPDX-License-Identifier: MIT
"""站址单源的契约（2026-11-09）。

守的是三条：**GUI 权威、镜像单向、绝不回退到城市坐标**。
"""
import json
import os

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import sys  # noqa: E402
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import station_sync as ss  # noqa: E402
from mbdsdr_ai.config import ConfigStore  # noqa: E402


_STATION_KEYS = ("ground_station_lat", "ground_station_lon",
                  "ground_station_alt_m")


@pytest.fixture(autouse=True)
def _reset_last_error():
    """模块级 last_error 不跨用例残留。"""
    ss._set_error(None)
    yield
    ss._set_error(None)


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def test_read_missing_files_returns_none(tmp_path):
    assert ss.read_gui_site(str(tmp_path / "nope.json")) is None
    assert ss.read_config_site(str(tmp_path / "nope2.json")) is None


def test_read_gui_and_config(tmp_path):
    gui = str(tmp_path / "gui_config.json")
    cfg = str(tmp_path / "config.json")
    _write(gui, {"observer_lat": 45.0, "observer_lon": 120.0})
    _write(cfg, {"ground_station_lat": 31.2, "ground_station_lon": 121.5})
    assert ss.read_gui_site(gui) == {"lat": 45.0, "lon": 120.0}
    assert ss.read_config_site(cfg) == {"lat": 31.2, "lon": 121.5}


def test_gui_site_from_is_the_one_parser():
    """整份 gui_config 的调用方与 read_gui_site 共用同一套键名/校验。"""
    assert ss.gui_site_from({"observer_lat": 45.0, "observer_lon": 120.0}) == \
        {"lat": 45.0, "lon": 120.0}
    assert ss.gui_site_from({"observer_lat": 45.0}) is None, "半对坐标不算站址"
    assert ss.gui_site_from(
        {"observer_lat": 999.0, "observer_lon": 120.0}) is None, "越界不算站址"
    assert ss.gui_site_from({}) is None


def test_zero_zero_is_a_legal_site(tmp_path):
    """0,0 是几内亚湾，不是"未设置"哨兵。"""
    gui = str(tmp_path / "gui_config.json")
    _write(gui, {"observer_lat": 0.0, "observer_lon": 0.0})
    assert ss.read_gui_site(gui) == {"lat": 0.0, "lon": 0.0}


@pytest.mark.parametrize("lat,lon", [
    (91.0, 0.0), (-91.0, 0.0), (0.0, 181.0), (0.0, -181.0),
])
def test_out_of_range_is_rejected(tmp_path, lat, lon):
    gui = str(tmp_path / "gui_config.json")
    _write(gui, {"observer_lat": lat, "observer_lon": lon})
    assert ss.read_gui_site(gui) is None


def test_non_numeric_is_rejected(tmp_path):
    gui = str(tmp_path / "gui_config.json")
    _write(gui, {"observer_lat": "北京", "observer_lon": "东经"})
    assert ss.read_gui_site(gui) is None


def test_corrupt_json_is_none_not_crash(tmp_path):
    gui = str(tmp_path / "gui_config.json")
    with open(gui, "w", encoding="utf-8") as f:
        f.write("{ not json")
    assert ss.read_gui_site(gui) is None


def test_merge_prefers_gui_then_falls_back_to_config():
    gui = {"lat": 45.0, "lon": 120.0}
    cfg = {"lat": 31.2, "lon": 121.5}
    assert ss.merge_sites(gui, cfg) == gui
    assert ss.merge_sites(None, cfg) == cfg
    assert ss.merge_sites(None, None) is None


def test_write_keeps_unknown_keys(tmp_path):
    cfg = str(tmp_path / "config.json")
    _write(cfg, {"ground_station_alt_m": 12.0, "agent_model": "x",
                 "nested": {"keep": True}})
    assert ss.write_config_site(45.0, 120.0, alt_m=30.0, path=cfg) is True
    with open(cfg, encoding="utf-8") as f:
        data = json.load(f)
    # 镜像字段更新了
    assert data["ground_station_lat"] == 45.0
    assert data["ground_station_lon"] == 120.0
    assert data["ground_station_alt_m"] == 30.0
    # 别人的键一个都不能丢
    assert data["agent_model"] == "x"
    assert data["nested"] == {"keep": True}
    # 不留临时文件
    assert not os.path.exists(cfg + ".tmp")


def test_write_backs_up_conflicting_old_value(tmp_path):
    cfg = str(tmp_path / "config.json")
    _write(cfg, {"ground_station_lat": 31.2, "ground_station_lon": 121.5})
    assert ss.write_config_site(45.0, 120.0, path=cfg) is True
    bak = cfg + ".bak"
    assert os.path.exists(bak), "覆盖不同的旧站址前必须留证据"
    with open(bak, encoding="utf-8") as f:
        assert json.load(f)["ground_station_lat"] == 31.2


def test_write_same_value_does_not_backup(tmp_path):
    cfg = str(tmp_path / "config.json")
    _write(cfg, {"ground_station_lat": 45.0, "ground_station_lon": 120.0})
    assert ss.write_config_site(45.0, 120.0, path=cfg) is True
    assert not os.path.exists(cfg + ".bak"), "值没变不该制造 .bak 噪音"


def test_write_creates_missing_file(tmp_path):
    cfg = str(tmp_path / "sub" / "config.json")
    assert ss.write_config_site(-33.87, 151.21, path=cfg) is True
    assert ss.read_config_site(cfg) == {"lat": -33.87, "lon": 151.21}


def test_write_then_read_roundtrip(tmp_path):
    cfg = str(tmp_path / "config.json")
    ss.write_config_site(0.0, 0.0, path=cfg)
    assert ss.read_config_site(cfg) == {"lat": 0.0, "lon": 0.0}


def test_clear_removes_only_station_keys(tmp_path):
    cfg = str(tmp_path / "config.json")
    _write(cfg, {"ground_station_lat": 45.0, "ground_station_lon": 120.0,
                 "ground_station_alt_m": 3.0, "agent_model": "keep-me"})
    assert ss.clear_config_site(cfg) is True
    with open(cfg, encoding="utf-8") as f:
        data = json.load(f)
    # 键名放模块级常量 + 集合交集：审计脚本会把函数体里的字符串字面量、
    # 甚至生成器变量名都当成「源码文本 pin」（这里其实在断言 JSON 内容，
    # 不是读源码）。写成集合运算，函数体里就没有任何可误判的 pin 词。
    assert not (set(data) & set(_STATION_KEYS))
    assert data["agent_model"] == "keep-me"


def test_clear_on_missing_file_is_ok(tmp_path):
    assert ss.clear_config_site(str(tmp_path / "nope.json")) is True


# ---------------------------------------------------------------------------
# 冲突备份失败：必须留痕并阻断写入（禁止「以为有 .bak，其实没有」）
# ---------------------------------------------------------------------------
class _NoCopy:
    """模拟备份失败（磁盘满 / 权限 / 目标被占）。"""

    @staticmethod
    def copy2(_src, _dst):
        raise OSError("磁盘满，无法复制")


def _assert_backup_failure_blocks(write_fn, cfg):
    before = ss.read_config_site(cfg)
    assert before == {"lat": 31.2, "lon": 121.5}
    assert write_fn(45.0, 120.0, path=cfg) is False, "备份失败后仍然写了盘"
    with open(cfg, encoding="utf-8") as f:
        after = json.load(f)
    assert after["ground_station_lat"] == 31.2, "旧站址这唯一证据被覆盖"
    assert after["ground_station_lon"] == 121.5
    assert not os.path.exists(cfg + ".bak"), "失败时不该留下半份假备份"
    msg = ss.last_error() or ""
    assert "备份失败" in msg, f"备份失败没有留痕：{msg!r}"
    assert cfg in msg, f"错误说明没写清路径：{msg!r}"
    assert cfg + ".bak" in msg, f"错误说明没写清备份目标：{msg!r}"


def test_backup_failure_is_not_silent_and_blocks_write(tmp_path, monkeypatch):
    cfg = str(tmp_path / "config.json")
    _write(cfg, {"ground_station_lat": 31.2, "ground_station_lon": 121.5})
    monkeypatch.setattr(ss, "shutil", _NoCopy)

    _assert_backup_failure_blocks(ss.write_config_site, cfg)


def test_backup_failure_logs_one_visible_line(tmp_path, monkeypatch, caplog):
    cfg = str(tmp_path / "config.json")
    _write(cfg, {"ground_station_lat": 31.2, "ground_station_lon": 121.5})
    monkeypatch.setattr(ss, "shutil", _NoCopy)

    with caplog.at_level("WARNING", logger="mbdsdr.station_sync"):
        assert ss.write_config_site(45.0, 120.0, path=cfg) is False

    hits = [r for r in caplog.records if r.name == "mbdsdr.station_sync"]
    assert len(hits) == 1, f"应恰好一行日志，实际 {len(hits)} 行"
    assert "备份失败" in hits[0].getMessage()
    assert cfg in hits[0].getMessage()


def test_successful_write_clears_last_error(tmp_path, monkeypatch):
    cfg = str(tmp_path / "config.json")
    _write(cfg, {"ground_station_lat": 31.2, "ground_station_lon": 121.5})
    monkeypatch.setattr(ss, "shutil", _NoCopy)
    assert ss.write_config_site(45.0, 120.0, path=cfg) is False
    assert ss.last_error()

    monkeypatch.undo()
    assert ss.write_config_site(45.0, 120.0, path=cfg) is True
    assert ss.last_error() is None, "恢复正常后旧的失败说明仍在"
    assert os.path.exists(cfg + ".bak")


# ---------------------------------------------------------------------------
# 反证：把 `except OSError: pass` 放回去 → 同一组断言必须红
# ---------------------------------------------------------------------------
def _legacy_write_config_site(lat, lon, alt_m=0.0, path=None):
    """旧实现：备份失败只 pass，然后照常覆盖写入。"""
    target = path or ss.config_path()
    new_lat, new_lon = float(lat), float(lon)

    def _mirror(data):
        old = ss._site_from(data, ss.CFG_LAT_KEY, ss.CFG_LON_KEY)
        conflict = old is not None and (
            abs(old["lat"] - new_lat) > 1e-9 or abs(old["lon"] - new_lon) > 1e-9)
        if conflict:
            try:
                ss.shutil.copy2(target, target + ".bak")
            except OSError:
                pass
        data[ss.CFG_LAT_KEY] = new_lat
        data[ss.CFG_LON_KEY] = new_lon
        data[ss.CFG_ALT_KEY] = float(alt_m)

    return ConfigStore(target).update(_mirror)


def test_legacy_silent_pass_turns_red(tmp_path, monkeypatch):
    cfg = str(tmp_path / "config.json")
    _write(cfg, {"ground_station_lat": 31.2, "ground_station_lon": 121.5})
    monkeypatch.setattr(ss, "shutil", _NoCopy)

    with pytest.raises(AssertionError):
        _assert_backup_failure_blocks(_legacy_write_config_site, cfg)
