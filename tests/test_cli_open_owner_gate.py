#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""CLI 打开路径必须进同一个占用闸门（``DeviceManager.open``）。

桌面端 / MCP 走 ``SDRBackendManager`` → ``SDRBackend.connect()`` 的 claim；
CLI 走 ``DeviceManager.open()`` 直连 ``rtlsdr.RtlSdr`` / SoapySDR。两条路径
若各判各的，同一根 USB 棒就能被 CLI 和桌面端同时打开——三态展示、unknown
不冒充空闲、fail-closed，全都建立在「所有打开路径先问登记」这个前提上。

本文件锁住 CLI 这一半：
- 别的进程持有（活 pid）→ 拒绝，文案含持有者 pid，且**没有进入打开路径**；
- 登记不可读 → 拒绝，结论是「读不出来」而不是「被别的程序占用」；
- 打开成功 → 登记写入本进程 pid，close() 归还；
- 打开失败（claim 之后）→ 归还，不留下永久锁。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from mbdsdr_ai import sdr_backend as sb
from mbdsdr_ai.core import cli
from mbdsdr_ai.core.errors import DeviceBusyError, OwnerRegistryUnavailableError
from mbdsdr_ai.device_manager import DeviceInfo, DeviceManager, OpenDevice


if not hasattr(DeviceManager, "_claim_owner"):
    # 闸门尚未合入（device_manager.py 正被其它线 claim）。这种情况下跳过，
    # 避免把一个「必然红」的用例留给别人；闸门落地后本文件自动开始生效。
    pytest.skip("CLI 占用闸门尚未落地（DeviceManager._claim_owner 不存在）",
                allow_module_level=True)


KEY = "rtlsdr|serial:CLI-SN-1"


@pytest.fixture()
def registry(tmp_path, monkeypatch):
    """把占用登记指到临时文件，避免碰用户真实的 ~/.mbdsdr/device_owner.json。"""
    path = tmp_path / "device_owner.json"
    monkeypatch.setenv(sb.OWNER_REGISTRY_ENV, str(path))
    return path


def _info() -> DeviceInfo:
    return DeviceInfo(
        name="RTL-SDR #0 (R820T)",
        driver="rtlsdr",
        serial="CLI-SN-1",
        enum_index=0,
        # 与 enumerate_all_sdr_devices() 的条目同源（rtlsdr_native_entry）。
        identity={
            "driver": "rtlsdr",
            "serial": "CLI-SN-1",
            "label": "RTL-SDR #0 (R820T)",
            "device_args": {"driver": "rtlsdr", "index": 0},
        },
    )


def _owners(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))["owners"]


def _forbid_open(monkeypatch):
    """任何真正打开设备的调用都记下来；claim 命中时它必须为空。"""
    calls = []

    def _fake_open(self, info):  # noqa: ANN001
        calls.append(info)
        raise AssertionError("claim 未通过却进入了打开路径")

    monkeypatch.setattr(DeviceManager, "_open_rtlsdr", _fake_open)
    monkeypatch.setattr(DeviceManager, "_open_soapysdr", _fake_open)
    return calls


def _forbid_probe(monkeypatch):
    """claim 未过时**连枚举都不该做**——枚举本身会 probe 打开设备。

    光断言 `_open_rtlsdr` 未被调用还不够：真跳过 claim 时，``_open_checked``
    会先走 ``list_devices()``（真实 probe 硬件、日志噪音、耗时），再撞上
    `_forbid_open`。把它也钉住，反证时红在第一步，判据是「没碰设备」而不是
    「最后没打开」。
    """
    calls = []

    def _fake_list(self, *a, **kw):  # noqa: ANN001
        calls.append(1)
        raise AssertionError("claim 未通过却开始了设备枚举")

    monkeypatch.setattr(DeviceManager, "list_devices", _fake_list)
    return calls


def test_cli_open_refuses_when_live_process_holds_claim(registry,
                                                         monkeypatch):
    """另一个**活进程**持有登记：拒绝，报出 pid，且不碰设备。

    反证：把 ``_claim_owner`` 改成「未知/失败都当空闲」，本用例立刻红——
    ``_open_rtlsdr`` 会被调用并抛 AssertionError。
    """
    holder = subprocess.Popen([sys.executable, "-c",
                               "import time; time.sleep(30)"])
    try:
        registry.write_text(json.dumps({
            "version": 1,
            "owners": {KEY: {"owner": "desktop", "pid": holder.pid,
                             "device_id": KEY, "aliases": []}},
        }), encoding="utf-8")
        opened = _forbid_open(monkeypatch)
        probed = _forbid_probe(monkeypatch)

        with pytest.raises(DeviceBusyError) as ei:
            DeviceManager().open(_info())

        assert str(holder.pid) in ei.value.message, ei.value.message
        assert "进程仍在运行" in ei.value.message
        assert opened == [], "被占用时不得进入打开路径"
        assert probed == [], "被占用时连枚举都不该做（枚举会 probe 打开设备）"
        assert _owners(registry)[KEY]["pid"] == holder.pid, \
            "拒绝时不得改动持有者的登记"
    finally:
        holder.terminate()
        holder.wait(timeout=10)


def test_cli_open_refuses_unreadable_registry_as_unknown(registry,
                                                          monkeypatch):
    """登记不可读：拒绝，结论是「读不出来」而不是「被程序占用」。

    反证：把 unknown 当 free，``_open_rtlsdr`` 会被调用 → 本用例红。
    """
    registry.write_bytes(b'{"owners": ')
    monkeypatch.setattr(sb, "_backup_corrupt_registry", lambda reason: None)
    opened = _forbid_open(monkeypatch)
    probed = _forbid_probe(monkeypatch)

    with pytest.raises(OwnerRegistryUnavailableError) as ei:
        DeviceManager().open(_info())

    assert "登记表不可用" in ei.value.message, ei.value.message
    assert "无法判断" in ei.value.message
    assert "进程仍在运行" not in ei.value.message
    assert ei.value.code == "owner_registry_unavailable"
    assert opened == [], "登记不可读时必须 fail-closed"
    assert probed == [], "登记不可读时连枚举都不该做"


def test_cli_open_claims_and_close_releases(registry, monkeypatch):
    """打开成功 → 登记里是本进程 pid；close() 归还（谁开谁 release）。"""
    info = _info()
    monkeypatch.setattr(DeviceManager, "list_devices",
                        lambda self, *a, **kw: [info])

    class _FakeDev:
        def close(self):
            pass

    def _fake_open(self, info):  # noqa: ANN001
        # 顺序判据：打开**之前**登记里必须已经有本进程。少了这一句，
        # 「先打开后 claim」也能让下面的 pid 断言通过。
        assert KEY in _owners(registry), "打开设备前必须先登记占用"
        return OpenDevice(info, _FakeDev())

    monkeypatch.setattr(DeviceManager, "_open_rtlsdr", _fake_open)

    dev = DeviceManager().open(info)
    rec = _owners(registry)[KEY]
    assert rec["pid"] == os.getpid()

    dev.close()
    assert KEY not in _owners(registry), "close() 必须归还占用登记"


def test_cli_open_releases_claim_when_open_fails(registry, monkeypatch):
    """claim 成功后打开失败：必须归还，不能让设备被一个失败进程永久锁住。"""
    info = _info()
    monkeypatch.setattr(DeviceManager, "list_devices",
                        lambda self, *a, **kw: [info])

    def _boom(self, info):  # noqa: ANN001
        # 先证明这一步真的持有过登记，否则「归还」断言可能是空转：
        # claim 根本没写进去，最后当然也不会有残留。
        assert KEY in _owners(registry), "打开失败前本应有本进程的登记"
        raise RuntimeError("driver down")

    monkeypatch.setattr(DeviceManager, "_open_rtlsdr", _boom)

    with pytest.raises(RuntimeError):
        DeviceManager().open(info)
    assert KEY not in _owners(registry), "打开失败后必须归还登记"


class _CtrlWithError:
    """只实现 connect：把真实 DeviceManager 造出来的异常交给 CLI 排版。"""

    def __init__(self, exc: Exception):
        self._exc = exc

    def connect(self, device_id: str):
        raise self._exc


@pytest.mark.xfail(
    strict=True,
    reason="device_manager.py 的 _claim_owner 尚未传 channel=OWNER_CHANNEL_CLI"
           "（该文件此刻被其它线持有）；放开后本用例转绿，strict 会以 XPASS "
           "强制摘掉本标记，别让它长期挂着")
def test_real_device_manager_conflict_prints_cli_hint(registry, monkeypatch,
                                                      capsys):
    """端到端：真 DeviceManager 被拒 → 异常交给 CLI → 用户看到 CLI 命令。

    上面四条验的是闸门本身；本条验**真实装配下**的最终文案，防止「各层
    单测都绿、接起来仍打印 MCP 工具名」。当前 `_claim_owner` 缺
    ``channel=OWNER_CHANNEL_CLI``，所以本条是红的（附 U.11）。
    """
    holder = subprocess.Popen([sys.executable, "-c",
                               "import time; time.sleep(30)"])
    try:
        registry.write_text(json.dumps({
            "version": 1,
            "owners": {KEY: {"owner": "desktop", "pid": holder.pid,
                             "device_id": KEY, "aliases": []}},
        }), encoding="utf-8")
        _forbid_open(monkeypatch)
        _forbid_probe(monkeypatch)

        with pytest.raises(DeviceBusyError) as ei:
            DeviceManager().open(_info())

        code = cli.run(["--connect", "rtlsdr:CLI-SN-1"],
                       controller=_CtrlWithError(ei.value))
        err = capsys.readouterr().err

        assert code == 1
        assert str(holder.pid) in err, err
        assert "--list-devices" in err, err
        assert "sdr_list_devices" not in err, err
    finally:
        holder.terminate()
        holder.wait(timeout=10)
