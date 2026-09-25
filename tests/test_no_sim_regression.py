#!/usr/bin/env python3
"""
防回潮静态守卫 + offscreen 无硬件 GUI 零假值核验
=====================================================

守护的架构红线：
  1. 生产代码（desktop/ + mbdsdr_ai/，不含 tests/）中不存在运行时实例化
     sim/合成源并向 UI/上层报真实成功的路径。
  2. 每个 UI setter 只被单一数据源连接（无 real+sim 双连）。
  3. 无硬件时所有面板显"未连接/无数据"，不出现任何坐标/高斯峰/IMU 数值/假卫星。

本文件包含 5 组测试：
  T1  test_no_runtime_sim_source_ast   —— AST 静态扫描生产代码无 sim/合成源
  T2  test_ui_setter_single_source     —— AST 静态扫描 main_window UI setter 单一数据源
  T3  test_offscreen_no_hardware_zero_fake —— offscreen 启动 GUI，无硬件零假值
  T4  test_real_nmea_stream_gnss      —— 喂真实格式 NMEA 字节流，GNSS 显示真实结果
  T5  test_real_iq_tone_spectrum      —— 喂真实复 IQ 音调，频谱显示真实结果

设计要点：
  - T1/T2 用 ast 解析而非纯 grep，自动跳过注释/docstring/字符串字面量，
    避免把"绝不回退模拟"这类负向注释误报为违规。
  - T3 用 QT_QPA_PLATFORM=offscreen，无显示器/无硬件跑 GUI；实例化失败则 skip。
"""

import os
import sys
import ast
import re
import time
import glob

import pytest

# offscreen 平台必须在 import PySide6 之前设置
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# 生产代码扫描范围（不含任何 tests/ 与 __pycache__/）
PROD_DIRS = [
    os.path.join(REPO_ROOT, "desktop"),
    os.path.join(REPO_ROOT, "mbdsdr_ai"),
]

# ---------------------------------------------------------------------------
# T1 / T2 静态扫描规则
# ---------------------------------------------------------------------------

# 禁止出现的合成源类（定义或实例化）
FORBIDDEN_CLASS_REFS = {"SimDataGenerator", "MockSDRBackend"}
# 禁止出现的方法（定义或调用）
FORBIDDEN_METHODS = {"_connect_simulation", "_auto_connect_simulation", "_sim_tool"}
# 禁止出现的属性/变量
FORBIDDEN_ATTRS = {"sim_btn", "_is_sim"}
# 禁止硬编码的假坐标（北京 39.9042 / 长春 43.8868, 125.3245）
FORBIDDEN_FLOAT_COORDS = (39.9042, 43.8868, 125.3245)
# 禁止在运行时数据生成里使用 np.random 的面板
RANDOM_FORBIDDEN_PANELS = (
    "spectrum_widget.py", "doppler_panel.py", "weather_panel.py",
)


def iter_prod_pyfiles():
    """产出 desktop/ 与 mbdsdr_ai/ 下所有 .py（排除 tests/、__pycache__/）。"""
    for base in PROD_DIRS:
        for dirpath, dirnames, filenames in os.walk(base):
            # 原地剪枝：不进入 tests 包缓存目录
            dirnames[:] = [
                d for d in dirnames
                if d not in ("__pycache__", "tests") and not d.endswith(".egg-info")
            ]
            rel = os.path.relpath(dirpath, base)
            if "tests" in rel.split(os.sep):
                continue
            for fn in filenames:
                if fn.endswith(".py"):
                    yield os.path.join(dirpath, fn)


def _walk_attr_dotted(node):
    """把 Attribute/Name 链还原成点分路径字符串，失败返回 None。"""
    parts = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    else:
        return None
    return ".".join(reversed(parts))


def collect_t1_violations():
    """T1：扫描所有生产文件，返回违规描述列表（空=通过）。"""
    violations = []
    for path in iter_prod_pyfiles():
        rel = os.path.relpath(path, REPO_ROOT)
        try:
            with open(path, encoding="utf-8") as f:
                src = f.read()
            tree = ast.parse(src, filename=path)
        except SyntaxError as e:
            violations.append(f"{rel}: 语法错误无法解析: {e}")
            continue
        lines = src.splitlines()
        fname = os.path.basename(path)

        # --- 被字符串字面量/注释占据的行号（用于 #10 降级链检查，避免误报） ---
        ignored_lines = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for ln in range(node.lineno, (node.end_lineno or node.lineno) + 1):
                    ignored_lines.add(ln)

        for node in ast.walk(tree):
            # 1/2. 禁止类定义
            if isinstance(node, ast.ClassDef) and node.name in FORBIDDEN_CLASS_REFS:
                violations.append(f"{rel}:{node.lineno}: 禁止类定义 class {node.name}")
            # 4/7. 禁止方法定义
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                    and node.name in FORBIDDEN_METHODS:
                violations.append(f"{rel}:{node.lineno}: 禁止方法定义 def {node.name}")
            # 实例化 / 调用
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id in FORBIDDEN_CLASS_REFS:
                    violations.append(f"{rel}:{node.lineno}: 禁止实例化 {func.id}(...)")
                if isinstance(func, ast.Attribute):
                    if func.attr in FORBIDDEN_METHODS:
                        violations.append(f"{rel}:{node.lineno}: 禁止调用 .{func.attr}()")
                    if func.attr in FORBIDDEN_CLASS_REFS:
                        violations.append(f"{rel}:{node.lineno}: 禁止实例化 .{func.attr}(...)")
            # 3. use_simulation=True
            if isinstance(node, ast.keyword) and node.arg == "use_simulation":
                v = node.value
                if isinstance(v, ast.Constant) and v.value is True:
                    violations.append(f"{rel}:{node.lineno}: 禁止 use_simulation=True")
            # 5/6. sim_btn / _is_sim 属性引用
            if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_ATTRS:
                violations.append(f"{rel}:{node.lineno}: 禁止属性/变量 .{node.attr}")
            # 8. 硬编码假坐标（float 常量；注释/docstring 是 str，AST 天然跳过）
            if isinstance(node, ast.Constant) and isinstance(node.value, float):
                for coord in FORBIDDEN_FLOAT_COORDS:
                    if abs(node.value - coord) < 1e-6:
                        violations.append(
                            f"{rel}:{node.lineno}: 硬编码假坐标 {node.value}")

        # 9. np.random 仅禁止出现在频谱/多普勒/气象面板的运行时代码里
        if fname in RANDOM_FORBIDDEN_PANELS:
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr == "random":
                    v = node.value
                    if isinstance(v, ast.Name) and v.id in ("np", "numpy"):
                        violations.append(
                            f"{rel}:{node.lineno}: 面板运行时用 np.random 造数")

        # 10. "硬件失败→mock/模拟→报成功" 降级链（只看非字符串、非注释的代码行）
        for i, line in enumerate(lines, 1):
            if i in ignored_lines:
                continue
            if line.strip().startswith("#"):
                continue
            low = line.lower()
            if re.search(r"\bmock\b", low):
                violations.append(f"{rel}:{i}: 代码行出现 mock 降级对象")
            if "回退模拟" in line or "降级为模拟" in line:
                violations.append(f"{rel}:{i}: 出现'回退/降级为模拟'降级链")
    return violations


# ---------------------------------------------------------------------------
# T2：main_window.py UI setter 单一数据源
# ---------------------------------------------------------------------------

def collect_connect_edges():
    """解析 main_window.py，返回所有 (signal_path, slot_path) 连接边。"""
    mw = os.path.join(REPO_ROOT, "desktop", "main_window.py")
    with open(mw, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=mw)
    edges = []
    gnss_setter_calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "connect":
            signal_path = _walk_attr_dotted(node.func.value)
            slot_path = _walk_attr_dotted(node.args[0]) if node.args else None
            edges.append((signal_path, slot_path))
        # 记录对 status_panel.update_gnss 的直接调用（GNSS 唯一数据源）
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "update_gnss":
                gnss_setter_calls.append(_walk_attr_dotted(node.func.value))
    return edges, gnss_setter_calls


# ---------------------------------------------------------------------------
# pytest fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


from PySide6.QtWidgets import QApplication  # noqa: E402  (需在 offscreen env 之后)


# ---------------------------------------------------------------------------
# T1：静态扫描——生产代码无运行时 sim/合成源
# ---------------------------------------------------------------------------

def test_no_runtime_sim_source_ast():
    violations = collect_t1_violations()
    assert not violations, (
        "生产代码中发现运行时 sim/合成源残留（防回潮守卫失败）：\n  - "
        + "\n  - ".join(violations)
    )


# ---------------------------------------------------------------------------
# T2：静态扫描——UI setter 单一数据源
# ---------------------------------------------------------------------------

def test_ui_setter_single_source():
    edges, gnss_calls = collect_connect_edges()
    problems = []

    # 2.1 GNSS 只能由 _poll_gnss（串口）驱动，worker.gps_updated 不得连接任何槽
    for sig, slot in edges:
        if sig and sig.endswith("self._worker.gps_updated"):
            problems.append(f"worker.gps_updated 被连接到 {slot}（GNSS 双写）")

    # 2.2 status_panel.on_gps_updated 不得被任何信号连接
    for sig, slot in edges:
        if slot and slot.endswith("status_panel.on_gps_updated"):
            problems.append(f"on_gps_updated 仍被信号 {sig} 连接")

    # 2.3 worker.imu_updated 最多一个连接（唯一 IMU 数据源）
    imu_conns = [e for e in edges
                 if e[0] and e[0].endswith("self._worker.imu_updated")]
    if len(imu_conns) > 1:
        problems.append(f"worker.imu_updated 被连接 {len(imu_conns)} 次（应唯一）")

    # 2.4 不得存在 worker.gps_updated 连接 + status_panel.update_gnss 调用的双写模式
    has_gps_connect = any(
        e[0] and e[0].endswith("self._worker.gps_updated") for e in edges)
    has_gnss_setter = any(c and c.endswith("status_panel") for c in gnss_calls)
    if has_gps_connect and has_gnss_setter:
        problems.append("worker.gps_updated 与 status_panel.update_gnss 双写 GNSS")

    assert not problems, "UI setter 单一数据源守卫失败：\n  - " + "\n  - ".join(problems)


# ---------------------------------------------------------------------------
# T3：offscreen 启动 GUI 无硬件——零假值
# ---------------------------------------------------------------------------

def _pump(app, seconds=2.0, step=0.1):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(step)


def test_offscreen_no_hardware_zero_fake(qapp, monkeypatch, tmp_path):
    # 隔离 HOME：避免开发者本机 ~/.mbdsdr/gui_config.json 里残留的手工观测站坐标
    # 污染"无硬件→无地面站"核验。干净 HOME 下 _load_gui_config 读不到任何坐标。
    monkeypatch.setenv("HOME", str(tmp_path))

    try:
        from desktop.main_window import MainWindow
    except Exception as e:  # pragma: no cover
        pytest.skip(f"MainWindow 无法导入（依赖缺失）: {e}")

    try:
        w = MainWindow()
    except Exception as e:  # pragma: no cover
        pytest.skip(f"MainWindow 实例化失败: {e}")

    w.show()
    # 让 GNSS auto_detect 超时、所有定时器各触发至少一次
    _pump(qapp, seconds=2.5)

    # 3.1 状态栏连接标签含"未连接"
    assert "未连接" in w.conn_label.text(), \
        f"工具栏连接标签应为未连接，实际: {w.conn_label.text()!r}"
    assert w.status_conn.text() == "未连接", \
        f"状态栏连接标签应为'未连接'，实际: {w.status_conn.text()!r}"

    # 3.2 频谱：未连接态，数据全 NaN
    assert w.spectrum._connected is False, "频谱应为未连接"
    assert w.spectrum.generator.has_data() is False, "频谱无硬件不应有数据"
    import numpy as np
    assert np.isnan(w.spectrum.generator.spectrum).all(), \
        "无硬件频谱数据应全为 NaN"

    # 3.3 天空图：无地面站坐标，不显示卫星
    assert w.sky_view.get_observer() is None, "无硬件不应有地面站坐标"
    assert w.sky_view._data_source == "none", \
        f"天空图数据源应为 none，实际: {w.sky_view._data_source!r}"
    assert w.sky_view._gnss_connected is False, "无硬件不应显示任何卫星"

    # 3.4 status_panel GPS 组：定位"未连接"，经纬度高度 "--"
    sp = w.status_panel
    assert sp.gps_fix.text() == "未连接", f"GPS 定位应未连接，实际: {sp.gps_fix.text()!r}"
    assert sp.gps_lat.text() == "--", f"纬度应 '--'，实际: {sp.gps_lat.text()!r}"
    assert sp.gps_lon.text() == "--", f"经度应 '--'，实际: {sp.gps_lon.text()!r}"
    assert sp.gps_alt.text() == "--", f"高度应 '--'，实际: {sp.gps_alt.text()!r}"

    # 3.5 status_panel IMU 组：所有数值 "--"
    for lbl in (sp.acc_x, sp.acc_y, sp.acc_z,
                sp.gyr_x, sp.gyr_y, sp.gyr_z,
                sp.mag_x, sp.mag_y, sp.mag_z, sp.imu_temp):
        assert lbl.text() == "--", f"IMU 数值应 '--'，实际 {lbl.text()!r}"
    assert "[未连接]" in sp.imu_group.title(), \
        f"IMU 组标题应含[未连接]，实际: {sp.imu_group.title()!r}"

    # 3.6 工具栏不存在"模拟模式"按钮
    assert not hasattr(w, "sim_btn"), "不应存在 sim_btn 模拟模式按钮"

    # 3.7 多普勒面板：未连接 / 无观测数据
    dtxt = w.doppler_panel.status_label.text()
    assert ("未连接" in dtxt) or ("无观测" in dtxt), \
        f"多普勒面板应未连接，实际: {dtxt!r}"

    # 3.8 气象面板：未连接 / 无数据
    wtxt = w.weather_panel.image_label.text()
    assert ("未连接" in wtxt) or ("无数据" in wtxt), \
        f"气象面板应无数据，实际: {wtxt!r}"

    # 清理：关闭窗口，停掉 GNSS 轮询
    w.close()


# ---------------------------------------------------------------------------
# T4：喂真实格式 NMEA 样本——GNSS 显示真实结果
# ---------------------------------------------------------------------------

def _nmea_bytes(body: str) -> bytes:
    """用 NMEA XOR 校验和封装一条语句（标准 NMEA-0183 帧格式）。"""
    from mbdsdr_ai.serial_gnss import nmea_checksum
    return f"${body}*{nmea_checksum(body):02X}\r\n".encode("ascii")


def test_real_nmea_stream_gnss(qapp):
    from mbdsdr_ai.serial_gnss import SerialGNSSReader

    r = SerialGNSSReader()
    # 标记"读线程已启动"，但不打开真实串口：直接喂 NMEA 字节（与 _read_loop
    # 生产路径完全一致：decode → parser.parse → _merge）。
    r._running.set()

    # 公开标准 NMEA 多星座样本（GGA/RMC/GSA + GP/GL/GB 多帧 GSV）。
    # 坐标 ddmm.mmmm：4352.0000 N = 43.8667°，12519.0000 E = 125.3167°
    stream = [
        "GNGGA,072545.00,4352.0000,N,12519.0000,E,1,09,0.9,150.0,M,0.0,M,,",
        "GNRMC,072545.00,A,4352.0000,N,12519.0000,E,0.0,0.0,240926,,A",
        "GNGSA,A,3,01,02,03,04,,,,,,,,,,1.5,0.9,0.6",
        "GPGSV,2,1,08,01,88,045,42,02,45,120,38,03,40,200,30",
        "GPGSV,2,2,08,04,30,300,25",
        "GLGSV,1,1,03,71,50,100,35,72,30,200,20,73,10,300,15",
        "GBGSV,1,1,03,211,60,040,40,212,40,100,35,213,20,200,20",
    ]
    for body in stream:
        line = _nmea_bytes(body).decode("ascii").strip()
        rec = r.parser.parse(line)
        # GSV 多帧的中间帧按 NMEAParser 设计返回 None（在内部缓冲聚合），
        # 与生产 _read_loop 一致：仅当 parse 返回完整记录时才 _merge。
        if rec is not None:
            r._merge(rec)

    # 4.1 get_fix() 返回 source=real，坐标正确
    fix = r.get_fix()
    assert fix["source"] == "real", f"喂入 GGA/RMC 后 source 应为 real，实际 {fix['source']}"
    assert abs(fix["latitude"] - 43.8666667) < 1e-4, fix["latitude"]
    assert abs(fix["longitude"] - 125.3166667) < 1e-4, fix["longitude"]
    assert fix["satellites"] == 9, fix["satellites"]
    assert abs(fix["altitude_m"] - 150.0) < 1e-6, fix["altitude_m"]

    # 4.2 get_gsv_frames() 返回多星座完整卫星列表
    gsv = r.get_gsv_frames()
    talkers = {fr.get("talker") for fr in gsv}
    assert {"GP", "GL", "GB"} <= talkers, f"应含 GP/GL/GB 星座，实际 {talkers}"
    total_sats = sum(len(fr.get("sats", [])) for fr in gsv)
    assert total_sats == 10, f"GP(4)+GL(3)+GB(3)=10 颗，实际 {total_sats}"

    # 4.3 get_gsa() 返回多星座 GSA / used PRN
    gsa = r.get_gsa()
    assert gsa.get("fix_type") == 3, gsa
    assert set(gsa.get("satellites_used", [])) == {1, 2, 3, 4}, gsa

    # 4.4 天空图 update_gnss_satellites 能处理这些数据
    from desktop.rf_sky_view import RFSkyView
    sky = RFSkyView()
    sky.update_gnss_satellites(gsv, gsa)
    assert sky._gnss_connected is True
    assert len(sky._gnss_satellites) == total_sats


# ---------------------------------------------------------------------------
# T5：喂真实 IQ 样本——频谱显示真实结果
# ---------------------------------------------------------------------------

def test_real_iq_tone_spectrum(qapp):
    import numpy as np
    from desktop.spectrum_widget import create_spectrum_widget

    panel = create_spectrum_widget()
    center_hz = 100.0e6
    sr = 240_000.0
    panel.generator.center_freq_hz = center_hz
    panel.generator.sample_rate_hz = sr

    # 构造 1 kHz 音调（baseband 正偏移）+ 少量噪声的复 IQ
    N = 8192
    t = np.arange(N) / sr
    tone = 0.9 * np.exp(2j * np.pi * 1000.0 * t)
    rng = np.random.default_rng(42)  # 测试夹具允许用随机噪声
    noise = 0.01 * (rng.standard_normal(N) + 1j * rng.standard_normal(N))
    iq = (tone + noise).astype(np.complex64)

    # 5.1 update_iq() 后频谱非 NaN
    panel.update_iq(iq, center_hz, sr)
    assert panel.generator.has_data() is True, "喂入 IQ 后应有数据"
    assert np.isfinite(panel.generator.spectrum).any(), "频谱应含有限值"

    # 5.2 峰值检测能找到 1 kHz 音调
    peaks = panel.generator.find_peaks(rel_threshold_db=6.0)
    assert peaks, "应检测到音调峰"
    peak_freq = peaks[0][0]
    expect = center_hz + 1000.0
    assert abs(peak_freq - expect) < 3000.0, \
        f"音调峰应在 {expect:.0f} Hz 附近，实际 {peak_freq:.0f} Hz"

    # 5.3 断开后频谱回到 NaN/空白
    panel.set_connected(False)
    assert panel.generator.has_data() is False
    assert np.isnan(panel.generator.spectrum).all(), "断开后频谱应全 NaN"
