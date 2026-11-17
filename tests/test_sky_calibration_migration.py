# SPDX-License-Identifier: MIT
"""校准档案的向前兼容：版本表、迁移注册表、未知字段保留。

全部使用合成档案；不联网、不碰硬件。旧版本迁移通过在测试里向
``MIGRATIONS`` 注入临时迁移函数验证，产品代码不新造版本号。
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.sky_calibration import (  # noqa: E402
    MIGRATIONS,
    SCHEMA_VERSION,
    SUPPORTED_SCHEMA_VERSIONS,
    load_profile,
    save_profile,
)

_FIELDS = {"schema_version", "device_id", "fov_deg", "boresight_az_deg",
           "boresight_alt_deg", "roll_zero_deg", "created_utc",
           "verified_utc", "source"}


def _profile():
    # 偏置取二进制可精确表示的值，避免 _wrap180 的浮点尾差干扰相等断言。
    return {
        "schema_version": SCHEMA_VERSION,
        "device_id": "示例设备",
        "fov_deg": 8.0,
        "boresight_az_deg": -0.5,
        "boresight_alt_deg": 0.5,
        "roll_zero_deg": 1.75,
        "created_utc": "2026-11-17T00:00:00Z",
        "verified_utc": "2026-11-17T00:00:00Z",
        "source": "two_star",
    }


def _write(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path.read_bytes()


# ---------------------------------------------------------------------------
# 版本表
# ---------------------------------------------------------------------------
def test_schema_table_is_anchored_to_v1():
    assert SCHEMA_VERSION == 1
    assert SUPPORTED_SCHEMA_VERSIONS == (1,)
    assert SCHEMA_VERSION in MIGRATIONS
    # 注册表里的每个起始版本都必须比当前版本旧，否则就是死条目。
    assert all(version < SCHEMA_VERSION or version == SCHEMA_VERSION
               for version in MIGRATIONS)


# ---------------------------------------------------------------------------
# v1 往返
# ---------------------------------------------------------------------------
def test_v1_roundtrip_is_unchanged(tmp_path):
    path = tmp_path / "calibration.json"
    profile = _profile()
    saved = save_profile(profile, str(path))
    assert saved["ok"] is True, saved["reason"]

    loaded = load_profile(str(path))
    assert loaded["ok"] is True, loaded["reason"]
    assert loaded["calibrated"] is True
    assert loaded["profile"] == profile
    assert "extra_fields" not in loaded["profile"]
    assert "migrated_from" not in loaded
    assert set(json.loads(path.read_text(encoding="utf-8"))) == _FIELDS


# ---------------------------------------------------------------------------
# 未知字段保留
# ---------------------------------------------------------------------------
def test_unknown_fields_survive_load_and_save(tmp_path):
    path = tmp_path / "calibration.json"
    extras = {"vendor_note": "野外初校", "sensor_rev": 3,
              "nested": {"gain": 1.5, "tags": ["a", "b"]}}
    _write(path, {**_profile(), **extras})

    loaded = load_profile(str(path))
    assert loaded["ok"] is True, loaded["reason"]
    assert loaded["profile"]["extra_fields"] == extras
    assert loaded["profile"]["device_id"] == "示例设备"

    out = tmp_path / "rewritten.json"
    saved = save_profile(loaded["profile"], str(out))
    assert saved["ok"] is True, saved["reason"]
    on_disk = json.loads(out.read_text(encoding="utf-8"))
    for key, value in extras.items():
        assert on_disk[key] == value
    for key in _FIELDS:
        assert on_disk[key] == _profile()[key]

    reloaded = load_profile(str(out))
    assert reloaded["ok"] is True
    assert reloaded["profile"]["extra_fields"] == extras


def test_extra_fields_cannot_override_known_fields(tmp_path):
    path = tmp_path / "calibration.json"
    profile = _profile()
    profile["extra_fields"] = {"device_id": "冒名设备", "fov_deg": 99.0,
                               "note": "保留"}
    saved = save_profile(profile, str(path))
    assert saved["ok"] is True, saved["reason"]

    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["device_id"] == "示例设备"
    assert on_disk["fov_deg"] == 8.0
    assert on_disk["note"] == "保留"
    assert "extra_fields" not in on_disk


# ---------------------------------------------------------------------------
# 未知 / 未来版本
# ---------------------------------------------------------------------------
def test_future_version_is_rejected_and_file_untouched(tmp_path):
    path = tmp_path / "calibration.json"
    before = _write(path, {**_profile(), "schema_version": SCHEMA_VERSION + 98})
    loaded = load_profile(str(path))
    assert loaded["ok"] is False
    assert loaded["calibrated"] is False
    assert loaded["profile"] is None
    assert loaded["error"] == "unsupported_version"
    assert str(SCHEMA_VERSION + 98) in loaded["reason"]
    assert path.read_bytes() == before


def test_old_version_without_migration_is_rejected(tmp_path):
    path = tmp_path / "calibration.json"
    before = _write(path, {**_profile(), "schema_version": 0})
    loaded = load_profile(str(path))
    assert loaded["ok"] is False
    assert loaded["error"] == "unsupported_version"
    assert "0" in loaded["reason"]
    assert path.read_bytes() == before


# ---------------------------------------------------------------------------
# 注入旧版本迁移
# ---------------------------------------------------------------------------
def _migrate_v0(profile):
    """模拟 v0 → v1：旧字段改名，未知字段与时间戳原样带过来。"""
    out = dict(profile)
    out["schema_version"] = SCHEMA_VERSION
    out["boresight_az_deg"] = out.pop("az_bias_deg")
    out["boresight_alt_deg"] = out.pop("alt_bias_deg")
    out["roll_zero_deg"] = out.pop("roll_bias_deg")
    return out


def _legacy_v0():
    profile = _profile()
    profile["schema_version"] = 0
    profile["az_bias_deg"] = profile.pop("boresight_az_deg")
    profile["alt_bias_deg"] = profile.pop("boresight_alt_deg")
    profile["roll_bias_deg"] = profile.pop("roll_zero_deg")
    profile["legacy_note"] = "v0 时代的手写备注"
    return profile


def test_injected_migration_upgrades_without_writing_back(tmp_path, monkeypatch):
    monkeypatch.setitem(MIGRATIONS, 0, _migrate_v0)
    path = tmp_path / "calibration.json"
    before = _write(path, _legacy_v0())

    loaded = load_profile(str(path))
    assert loaded["ok"] is True, loaded["reason"]
    assert loaded["calibrated"] is True
    assert loaded["migrated_from"] == 0
    profile = loaded["profile"]
    assert profile["schema_version"] == SCHEMA_VERSION
    assert profile["boresight_az_deg"] == -0.5
    assert profile["boresight_alt_deg"] == 0.5
    assert profile["roll_zero_deg"] == 1.75
    assert profile["device_id"] == "示例设备"
    assert profile["extra_fields"] == {"legacy_note": "v0 时代的手写备注"}
    # 迁移只在内存里发生：原文件一个字节都不动。
    assert path.read_bytes() == before

    out = tmp_path / "upgraded.json"
    assert save_profile(profile, str(out))["ok"] is True
    on_disk = json.loads(out.read_text(encoding="utf-8"))
    assert on_disk["schema_version"] == SCHEMA_VERSION
    assert on_disk["boresight_az_deg"] == -0.5
    assert on_disk["legacy_note"] == "v0 时代的手写备注"
    assert "az_bias_deg" not in on_disk


def test_broken_migration_degrades_to_uncalibrated(tmp_path, monkeypatch):
    def broken(profile):
        raise RuntimeError("迁移实现有 bug")

    monkeypatch.setitem(MIGRATIONS, 0, broken)
    path = tmp_path / "calibration.json"
    before = _write(path, _legacy_v0())

    loaded = load_profile(str(path))
    assert loaded["ok"] is False
    assert loaded["calibrated"] is False
    assert loaded["error"] == "unsupported_version"
    assert "RuntimeError" in loaded["reason"]
    assert path.read_bytes() == before


# ---------------------------------------------------------------------------
# 损坏文件
# ---------------------------------------------------------------------------
def test_corrupt_json_is_rejected_and_file_untouched(tmp_path):
    path = tmp_path / "calibration.json"
    path.write_bytes(b'{"schema_version": 1, "device_id": "half')
    before = path.read_bytes()
    loaded = load_profile(str(path))
    assert loaded["ok"] is False
    assert loaded["calibrated"] is False
    assert loaded["error"] == "corrupt"
    assert path.read_bytes() == before
