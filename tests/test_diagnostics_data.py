# SPDX-License-Identifier: MIT
"""诊断数据层（W106）四条口径。

1. 备份清单带**理由全文**（页面不用自己再拼路径）；
2. 只认 ``*.reason.txt``：裸 ``*.corrupt-*`` 与站址 ``.bak`` 不计入——
   否则「一个坏文件」会在页面上出现两次；
3. 日志路径取「实际写成功过」的记录，``collect`` 本身不许造文件/目录；
4. 实例类对象的 ``last_error`` 经弱引用注册可见，置空即消失；
   取不到的对象明确列在 ``unwired``，不拿空值冒充「无错误」。

取数在 ``desktop.diagnostics_data``（界面层），脱敏与注册表在
``mbdsdr_ai.diagnostics``（纯逻辑层）；本文件验证两者拼起来的对外口径。
只 import PySide6，不需要 QApplication。
"""

import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import mbdsdr_ai.config as mbdsdr_config  # noqa: E402
import mbdsdr_ai.corrupt_evidence as ce  # noqa: E402
import mbdsdr_ai.diagnostics as diag  # noqa: E402
import desktop.diagnostics_data as dd  # noqa: E402
from desktop import startup_paths as sp  # noqa: E402


def _pair(directory, name="bookmarks.json", reason="书签文件读取失败：测试"):
    backup = directory / f"{name}.corrupt-20261112-220000"
    backup.write_text("{}", encoding="utf-8")
    ce.write_reason(str(backup), reason, None)
    return backup


def test_backups_carry_full_reason(tmp_path, monkeypatch):
    backup = _pair(tmp_path)
    monkeypatch.setattr(dd, "_scan_directories", lambda: [str(tmp_path)])

    data = dd.collect_diagnostics()

    assert set(data) == {"backups", "log", "errors"}
    assert data["backups"]["total"] == 1
    item = data["backups"]["items"][0]
    assert item["backup"] == str(backup)
    assert item["reason"] == "书签文件读取失败：测试"


def test_bare_corrupt_and_bak_are_not_counted(tmp_path, monkeypatch):
    (tmp_path / "bookmarks.json.corrupt-20261112-220000").write_text("{}")
    (tmp_path / "config.json.bak").write_text("{}")
    monkeypatch.setattr(dd, "_scan_directories", lambda: [str(tmp_path)])

    data = dd.collect_diagnostics()

    assert data["backups"]["total"] == 0, "裸备份 / .bak 混进了「有证据的损坏」"


def test_log_path_comes_from_recorder_without_side_effects(tmp_path, monkeypatch):
    log = tmp_path / "logs" / "mbdsdr_startup.log"
    monkeypatch.setenv("MBDSDR_STARTUP_LOG", str(log))
    monkeypatch.setattr(sp, "_LAST_LOG_PATH", "")
    monkeypatch.setattr(dd, "_scan_directories", lambda: [str(tmp_path)])

    assert dd.collect_diagnostics()["log"]["path"] == ""
    assert sp.append_startup_log("配置清点：0 个") == str(log)

    before = sorted(str(p) for p in tmp_path.rglob("*"))
    data = dd.collect_diagnostics()
    after = sorted(str(p) for p in tmp_path.rglob("*"))

    assert data["log"]["path"] == str(log)
    assert data["log"]["notice"] == ""
    assert after == before, "collect 自己造了文件/目录"


class _Store:
    def __init__(self, error):
        self.last_error = error


def test_registered_error_appears_then_drops():
    store = _Store("持久化错误：磁盘已满")
    diag.register_store("memory", store)

    items = dd._collect_errors()["items"]
    hit = [i for i in items if i["name"] == "memory"]
    assert hit and "磁盘已满" in hit[-1]["error"]

    store.last_error = None
    items = dd._collect_errors()["items"]
    assert not [i for i in items if i["name"] == "memory"], "错误置空后仍在页面上"


def test_unwired_objects_are_named_not_faked():
    unwired = {u["name"] for u in dd._collect_errors()["unwired"]}
    assert {"desktop_settings", "layout_store", "bookmarks",
            "frequency_manager", "version_store"} <= unwired


#: 反证用的假 key：形状与真实 key 一致，但不可能被误当成有效凭证。
_FAKE_KEY = "sk-abcdef1234567890"


def test_redact_secrets_keeps_labels_and_hides_values():
    assert diag.redact_secrets(f"api_key={_FAKE_KEY}") == "api_key=***"
    assert diag.redact_secrets(f"Authorization: Bearer {_FAKE_KEY}") == \
        "Authorization: Bearer ***"
    assert diag.redact_secrets(f"解析失败：{_FAKE_KEY}") == "解析失败：sk-***"
    # 只替换命中片段，路径/文件名等定位信息必须留下
    assert diag.redact_secrets("/home/sk-workbench/config.json") == \
        "/home/sk-***/config.json"
    # 正常文案不能被吞掉
    assert diag.redact_secrets("书签文件读取失败：测试") == "书签文件读取失败：测试"


def test_api_key_never_leaks_from_collected_structure(tmp_path, monkeypatch):
    """完整流程反证：含 ``sk-`` 假 key 的损坏配置跑完后，返回结构里没有它。

    三个真实通道同时打开：
    1. 配置目录名带 key 形状串 → 备份路径、理由里的路径都带上它；
    2. 理由文件是自由文本 → 模拟未来的 writer 把值本身写进去；
    3. 注册实例的 ``last_error`` 与内存日志缓冲 → 典型「写失败原因」。
    """
    leak_dir = tmp_path / f"{_FAKE_KEY}-leak"
    leak_dir.mkdir()
    cfg_path = leak_dir / "config.json"
    cfg_path.write_text(
        f'{{"api_key": "{_FAKE_KEY}", "model": ', encoding="utf-8")

    mbdsdr_config.load_config(str(cfg_path))
    reason_file = next(leak_dir.glob("*.reason.txt"))
    reason_file.write_text(f"解析失败片段：api_key={_FAKE_KEY}\n",
                           encoding="utf-8")
    assert _FAKE_KEY in reason_file.read_text(encoding="utf-8"), \
        "前提失效：理由里根本没有 key，这条反证证明不了脱敏"

    store = _Store(f"保存失败：token={_FAKE_KEY}")
    diag.register_store("memory", store)
    monkeypatch.setattr(sp, "last_log_path", lambda: "")
    monkeypatch.setattr(sp, "buffered_log",
                        lambda: [f"Authorization: Bearer {_FAKE_KEY}"])
    monkeypatch.setattr(dd, "_scan_directories", lambda: [str(leak_dir)])

    data = dd.collect_diagnostics()
    blob = json.dumps(data, ensure_ascii=False)

    assert _FAKE_KEY not in blob, "诊断结构里仍能拿到完整 API key"
    assert "api_key=***" in blob
    assert "token=***" in blob
    assert "Bearer ***" in blob
    assert data["backups"]["total"] == 1, "脱敏把证据本身弄丢了"
    mbdsdr_config._set_error(None)


def test_redaction_keeps_location_type_and_step(tmp_path, monkeypatch):
    """路径里有 ``sk-`` 形状（但不是密钥）时，打码不能把定位信息一起打掉。

    两个断言必须同时成立：
    * 定位信息在——哪个源文件、什么异常、哪一步失败；
    * 命中片段被替换——``sk-workbench`` 不再原样出现。
    只保前一个就是泄露，只保后一个就是「为了安全把诊断弄瞎」。
    """
    workdir = tmp_path / "sk-workbench"
    workdir.mkdir()
    cfg_path = workdir / "config.json"
    cfg_path.write_text('{"api_key": "sk-abcdef1234567890", "model": ',
                        encoding="utf-8")

    mbdsdr_config.load_config(str(cfg_path))
    monkeypatch.setattr(dd, "_scan_directories", lambda: [str(workdir)])

    data = dd.collect_diagnostics()
    blob = json.dumps(data, ensure_ascii=False)
    reason = data["backups"]["items"][0]["reason"]

    assert "config.json" in reason, "打码后看不出哪个源文件坏了"
    assert "JSONDecodeError" in reason, "打码后看不出异常类型"
    assert "配置文件损坏或不可读" in reason, "打码后看不出是哪一步失败的"
    assert "sk-workbench" not in blob, "命中片段没有被替换"
    assert "sk-***" in blob, "替换标记丢了，看不出这里被脱敏"
    assert data["backups"]["total"] == 1, "脱敏把证据本身弄丢了"
    mbdsdr_config._set_error(None)
