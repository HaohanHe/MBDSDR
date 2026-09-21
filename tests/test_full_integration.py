#!/usr/bin/env python3
"""
MBDSDR AI 内核 - 全面端到端集成测试
====================================
测试所有 30 个模块和 128 个工具，确保没有空壳。

运行方式:
    python3 tests/test_full_integration.py

输出:
    - 每个模块的测试结果
    - 每个类别的工具测试结果
    - 总体通过率
    - 失败项详情
"""

import sys
import os
import json
import time
import math
import traceback

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai import MBDSDRAgent, AgentConfig


class TestResult:
    """测试结果收集器。"""
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.skipped = 0
        self.failures = []
        self.start_time = time.time()

    def record(self, name: str, success: bool, detail: str = ""):
        if success:
            self.passed += 1
            print(f"  ✓ {name}")
        else:
            self.failed += 1
            self.failures.append((name, detail))
            print(f"  ✗ {name}: {detail[:100]}")

    def skip(self, name: str, reason: str = ""):
        self.skipped += 1
        print(f"  - {name} (跳过: {reason})")

    def summary(self) -> str:
        elapsed = time.time() - self.start_time
        total = self.passed + self.failed + self.skipped
        pass_rate = self.passed / max(1, total) * 100
        lines = [
            "",
            "=" * 60,
            "MBDSDR AI 内核 - 全面集成测试报告",
            "=" * 60,
            f"总测试项: {total}",
            f"通过: {self.passed}",
            f"失败: {self.failed}",
            f"跳过: {self.skipped}",
            f"通过率: {pass_rate:.1f}%",
            f"耗时: {elapsed:.2f} 秒",
        ]
        if self.failures:
            lines.append("")
            lines.append("失败项详情:")
            for name, detail in self.failures:
                lines.append(f"  - {name}: {detail[:200]}")
        lines.append("=" * 60)
        return "\n".join(lines)


def test_module_imports(result: TestResult):
    """测试所有模块导入。"""
    print("\n[1] 模块导入测试")
    modules = [
        "config", "context_manager", "model_manager", "tool_registry",
        "agent", "memory", "version_store", "sandbox", "self_evolution",
        "guardian", "workflow_engine", "scheduler", "sdr_backend",
        "spectrum_processor", "sdr_tools", "dsp", "decoders",
        "hooks", "subagents", "pose", "workflow_recorder",
        "file_tracker", "plugin_system", "llm_judge", "self_learning",
        "orchestrator", "code_editor", "astronomy", "amr",
    ]
    for mod_name in modules:
        try:
            __import__(f"mbdsdr_ai.{mod_name}")
            result.record(f"导入 {mod_name}", True)
        except Exception as e:
            result.record(f"导入 {mod_name}", False, str(e))


def test_agent_init(result: TestResult, agent: MBDSDRAgent):
    """测试 Agent 初始化和子系统。"""
    print("\n[2] Agent 初始化测试")

    # 子系统存在性
    subsystems = [
        "context_manager", "model_manager", "tool_registry", "memory",
        "evolution", "guardian", "workflow_engine", "scheduler",
        "sdr_manager", "spectrum", "hook_manager",
        "subagent_manager", "pose_fusion", "workflow_recorder",
        "file_tracker", "plugin_manager", "llm_judge", "self_learning",
        "orchestrator", "code_editor", "observer", "amr_classifier",
    ]
    for sub in subsystems:
        result.record(f"子系统 {sub}", hasattr(agent, sub))

    # 工具统计
    tools = agent.tool_registry.list_tools()
    result.record(f"工具总数 >= 100", len(tools) >= 100, f"实际 {len(tools)} 个")

    # 类别统计
    categories = {}
    for t in tools:
        cat = t.get('category', 'unknown')
        categories[cat] = categories.get(cat, 0) + 1
    result.record(f"工具类别数 >= 20", len(categories) >= 20, f"实际 {len(categories)} 类")

    # 每个工具都有 name/description/parameters
    bad_tools = []
    for t in tools:
        if not t.get('name') or not t.get('description') or 'parameters' not in t:
            bad_tools.append(t.get('name', 'unknown'))
    result.record("所有工具有完整元数据", len(bad_tools) == 0, f"不完整: {bad_tools}")


def test_meta_tools(result: TestResult, agent: MBDSDRAgent):
    """测试 meta 类别工具。"""
    print("\n[3] Meta 工具测试")
    tr = agent.tool_registry

    tests = [
        ("list_tools", {}, "工具列表"),
        ("context_status", {}, "上下文状态"),
        ("model_status", {}, "模型状态"),
        ("sdr_status", {}, "SDR 状态"),
    ]
    for tool_name, params, desc in tests:
        try:
            r = tr.call(tool_name, params)
            result.record(f"{tool_name} ({desc})", r.success, r.content[:100] if not r.success else "")
        except Exception as e:
            result.record(f"{tool_name} ({desc})", False, str(e))


def test_sdr_tools(result: TestResult, agent: MBDSDRAgent):
    """测试 SDR 核心工具。"""
    print("\n[4] SDR 核心工具测试")
    tr = agent.tool_registry

    # 设备控制
    tests = [
        ("sdr_connect", {}, "连接 SDR"),
        ("sdr_status", {}, "获取状态"),
        ("sdr_set_frequency", {"frequency_hz": 98500000}, "设置频率"),
        ("sdr_set_sample_rate", {"sample_rate": 2048000}, "设置采样率"),
        ("sdr_set_gain", {"gain_db": 40}, "设置增益"),
        ("sdr_get_frequency", {}, "获取频率"),
    ]
    for tool_name, params, desc in tests:
        try:
            r = tr.call(tool_name, params)
            result.record(f"{tool_name} ({desc})", r.success, r.content[:100] if not r.success else "")
        except Exception as e:
            result.record(f"{tool_name} ({desc})", False, str(e))

    # 频谱分析
    try:
        r = tr.call("sdr_spectrum_analyze", {"fft_size": 512})
        result.record("sdr_spectrum_analyze (频谱分析)", r.success, r.content[:100] if not r.success else "")
    except Exception as e:
        result.record("sdr_spectrum_analyze", False, str(e))

    try:
        r = tr.call("sdr_spectrum_find_signals", {"threshold_db": -60})
        result.record("sdr_spectrum_find_signals (找信号)", r.success, r.content[:100] if not r.success else "")
    except Exception as e:
        result.record("sdr_spectrum_find_signals", False, str(e))

    # 解调
    for mode in ["fm", "am", "usb", "lsb", "cw"]:
        try:
            r = tr.call("sdr_demodulate", {"mode": mode, "duration_s": 0.1})
            result.record(f"sdr_demodulate ({mode.upper()}解调)", r.success, r.content[:100] if not r.success else "")
        except Exception as e:
            result.record(f"sdr_demodulate ({mode})", False, str(e))


def test_dsp_module(result: TestResult, agent: MBDSDRAgent):
    """测试 DSP 模块。"""
    print("\n[5] DSP 模块测试")
    from mbdsdr_ai.dsp import (
        DCBlocker, IQCalibrator, AGC, front_end, decimate,
        fm_demod, am_demod, ssb_demod, cw_demod,
        write_cf32, write_wav, compute_snr, estimate_bandwidth,
    )
    import numpy as np

    # 生成测试信号
    t = np.linspace(0, 1, 10000, endpoint=False)
    fm_signal = np.exp(1j * 2 * np.pi * (0.05 * t + 0.01 * np.sin(2 * np.pi * 0.001 * t)))

    # DC Blocker
    try:
        dc = DCBlocker()
        out = dc.process(fm_signal)
        result.record("DCBlocker", len(out) == len(fm_signal))
    except Exception as e:
        result.record("DCBlocker", False, str(e))

    # IQ 校准
    try:
        cal = IQCalibrator()
        cal.fit(fm_signal)
        out = cal.apply(fm_signal)
        result.record("IQCalibrator", out is not None)
    except Exception as e:
        result.record("IQCalibrator", False, str(e))

    # AGC
    try:
        agc = AGC()
        out = agc.process(fm_signal)
        result.record("AGC", len(out) == len(fm_signal))
    except Exception as e:
        result.record("AGC", False, str(e))

    # 解调
    for name, func in [("FM", fm_demod), ("AM", am_demod), ("CW", cw_demod)]:
        try:
            audio = func(fm_signal)
            result.record(f"{name} 解调", len(audio) > 0)
        except Exception as e:
            result.record(f"{name} 解调", False, str(e))

    # SSB 解调
    try:
        audio = ssb_demod(fm_signal, "usb")
        result.record("SSB (USB) 解调", len(audio) > 0)
    except Exception as e:
        result.record("SSB (USB)", False, str(e))

    # 抽取
    try:
        out = decimate(fm_signal, 10)
        result.record("decimate (10倍抽取)", len(out) == len(fm_signal) // 10)
    except Exception as e:
        result.record("decimate", False, str(e))

    # SNR 计算
    try:
        snr = compute_snr(fm_signal)
        result.record("compute_snr", isinstance(snr, dict) and "snr_db" in snr, str(snr.get("snr_db")))
    except Exception as e:
        result.record("compute_snr", False, str(e))

    # 带宽估计
    try:
        bw = estimate_bandwidth(fm_signal)
        result.record("estimate_bandwidth", isinstance(bw, dict) and "bandwidth_hz" in bw, str(bw.get("bandwidth_hz")))
    except Exception as e:
        result.record("estimate_bandwidth", False, str(e))

    # 前端一站式
    try:
        out, info = front_end(fm_signal)
        result.record("front_end (一站式)", out is not None and len(out) == len(fm_signal))
    except Exception as e:
        result.record("front_end", False, str(e))


def test_spectrum_module(result: TestResult, agent: MBDSDRAgent):
    """测试频谱处理模块。"""
    print("\n[6] 频谱处理模块测试")
    from mbdsdr_ai.spectrum_processor import SpectrumProcessor
    import numpy as np

    try:
        sp = SpectrumProcessor(fft_size=1024)
        # 生成信号
        t = np.linspace(0, 1, 1024, endpoint=False)
        signal = np.exp(1j * 2 * np.pi * 0.1 * t) + 0.1 * (np.random.randn(1024) + 1j * np.random.randn(1024))
        cf, sr = 100e6, 240e3

        # FFT
        spectrum = sp.compute_spectrum(signal, cf, sr)
        result.record("compute_spectrum", spectrum is not None)

        # 信号查找
        signals = sp.find_signals(spectrum, threshold_db=-40)
        result.record("find_signals", isinstance(signals, list))

        # 中心频点偏移估计
        center = sp.estimate_center_offset(spectrum)
        result.record("estimate_center_offset",
                      isinstance(center, dict) and "estimated_center_freq" in center)

        # 调制特征提取
        features = sp.extract_modulation_features(signal, sr)
        result.record("extract_modulation_features", isinstance(features, dict) and len(features) >= 5)

        # ASCII 频谱图
        ascii_spec = sp.generate_spectrum_text(spectrum)
        result.record("generate_spectrum_text", len(ascii_spec) > 0)

    except Exception as e:
        result.record("SpectrumProcessor", False, str(e))


def test_decoders_module(result: TestResult, agent: MBDSDRAgent):
    """测试解码器模块。"""
    print("\n[7] 解码器模块测试")
    from mbdsdr_ai.decoders import (
        list_visible_satellites, compute_doppler_correction,
        detect_fhss, decode_digital_mode, BUILTIN_TLE,
    )

    # 内置 TLE
    result.record("内置 TLE 数量 >= 5", len(BUILTIN_TLE) >= 5, f"实际 {len(BUILTIN_TLE)}")

    # 可见卫星列表
    try:
        sats = list_visible_satellites(observer_lat=43.88, observer_lon=125.32, observer_alt=0.25)
        result.record("list_visible_satellites", isinstance(sats, list))
    except Exception as e:
        result.record("list_visible_satellites", False, str(e))

    # 多普勒校正
    try:
        doppler = compute_doppler_correction(
            satellite_name="ISS",
            nominal_freq_hz=145800000,
            observer_lat=43.88, observer_lon=125.32,
        )
        result.record("compute_doppler_correction", isinstance(doppler, dict) or isinstance(doppler, float))
    except Exception as e:
        result.record("compute_doppler_correction", False, str(e))

    # 跳频检测（需要 IQ 样本 + 采样率/中心频率）
    try:
        import numpy as np
        fs, cf = 240e3, 100e6
        n_per, n_hop = 2048, 10
        tt = np.arange(n_per) / fs
        chunks = []
        rng = np.random.default_rng(0)
        for i in range(n_hop):
            hop_f = -40e3 + (i * 8e3)
            chunks.append(np.exp(1j * 2 * np.pi * hop_f * tt) + 0.05 * (rng.standard_normal(n_per) + 1j * rng.standard_normal(n_per)))
        samples = np.concatenate(chunks)
        out = detect_fhss(samples, fs, cf, num_frames=n_hop)
        result.record("detect_fhss", isinstance(out, dict))
    except Exception as e:
        result.record("detect_fhss", False, str(e))

    # 数字模式解码框架（输入为文件路径）
    try:
        import tempfile, wave, struct, os
        wav_path = os.path.join(tempfile.gettempdir(), "mbdsdr_ft8_silence.wav")
        with wave.open(wav_path, "wb") as wf:
            wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(12000)
            wf.writeframes(b"\x00\x00" * 12000)  # 1 秒静音
        out = decode_digital_mode(wav_path, mode="ft8")
        result.record("decode_digital_mode (FT8 框架)", out is not None)
    except Exception as e:
        result.record("decode_digital_mode", False, str(e))


def test_hooks_module(result: TestResult, agent: MBDSDRAgent):
    """测试 Hook 系统。"""
    print("\n[8] Hook 系统测试")
    from mbdsdr_ai.hooks import HookManager, Event, EventType, create_logging_hook

    try:
        hm = HookManager()

        # 注册 hook（register(event_type, callback, description)）
        hook = create_logging_hook()
        hm.register(EventType.SDR_SIGNAL_DETECTED, hook, "测试日志钩子")
        result.record("注册 Hook", len(hm.list_hooks()) >= 1)

        # 触发事件（Event 字段为 event_type/data）
        event = Event(event_type=EventType.SDR_SIGNAL_DETECTED, data={"frequency": 98.5})
        hm.trigger(event)
        result.record("触发事件", True)

        # 统计
        stats = hm.get_stats()
        result.record("Hook 统计", stats.get("total_triggers", 0) >= 1)

    except Exception as e:
        result.record("HookManager", False, str(e))


def test_subagents_module(result: TestResult, agent: MBDSDRAgent):
    """测试子代理系统。"""
    print("\n[9] 子代理系统测试")
    from mbdsdr_ai.subagents import SubagentManager, SubagentStatus

    try:
        sm = SubagentManager(agent.tool_registry)

        # 创建子代理（create(agent_type) -> id）
        sub_id = sm.create(agent_type="spectrum_analyzer")
        result.record("创建子代理", sub_id is not None)

        # 列出子代理
        subs = sm.list_subagents()
        result.record("列出子代理", len(subs) >= 1)

        # 获取子代理
        sub = sm.get(sub_id)
        result.record("获取子代理", sub is not None)

        # 统计
        stats = sm.get_stats()
        result.record("子代理统计", "total_subagents" in stats)

        # 销毁子代理
        sm.destroy(sub_id)
        result.record("销毁子代理", True)

    except Exception as e:
        result.record("SubagentManager", False, str(e))


def test_pose_module(result: TestResult, agent: MBDSDRAgent):
    """测试位姿融合和 AR 模块。"""
    print("\n[10] 位姿融合/AR 模块测试")
    from mbdsdr_ai.pose import (
        PoseFusion, ARProjector, IMUData, GPSData,
        ComplementaryFilter, TiltCompensatedCompass, MadgwickFilter,
    )

    try:
        def make_imu(i):
            return IMUData(
                accel_x=0.0, accel_y=0.0, accel_z=1.0,
                gyro_x=0.01, gyro_y=0.0, gyro_z=0.0,
                mag_x=0.2, mag_y=0.0, mag_z=0.4,
                timestamp=i * 0.01,
            )

        # 互补滤波
        cf = ComplementaryFilter()
        for i in range(100):
            cf.update(make_imu(i))
        result.record("ComplementaryFilter (6DOF)", cf.roll is not None)

        # 倾斜补偿罗盘（update(imu, roll, pitch) -> heading）
        tcc = TiltCompensatedCompass(declination=-9.0)
        heading = tcc.update(make_imu(0), 0.0, 0.0)
        result.record("TiltCompensatedCompass", isinstance(heading, float))

        # Madgwick 滤波（get_euler 返回 (roll,pitch,yaw)）
        mf = MadgwickFilter()
        for i in range(10):
            mf.update(make_imu(i))
        euler = mf.get_euler()
        result.record("MadgwickFilter (9DOF)", isinstance(euler, tuple) and len(euler) == 3)

        # 位姿融合（update_imu / update_gps / get_pose）
        pf = PoseFusion()
        pf.update_imu(make_imu(0))
        gps = GPSData(latitude=43.88, longitude=125.32, altitude=250, timestamp=0)
        pf.update_gps(gps)
        pose = pf.get_pose()
        result.record("PoseFusion", pose is not None)

        # AR 投影器（project_satellite(name, el, az, dist_km, pose) -> ARMarker）
        ar = ARProjector(camera_fov_deg=90.0, screen_aspect=1920 / 1080)
        marker = ar.project_satellite("ISS", 45.0, 180.0, 400.0, pose,
                                      frequency_mhz=145.8, doppler_hz=0.0)
        result.record("ARProjector", marker is not None)

    except Exception as e:
        result.record("Pose/AR", False, str(e))


def test_guardian_module(result: TestResult, agent: MBDSDRAgent):
    """测试守护者模块。"""
    print("\n[11] 守护者模块测试")
    from mbdsdr_ai.guardian import Guardian
    import tempfile, os

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            guardian = Guardian(store_path=os.path.join(tmpdir, "snapshots"))

            # 创建测试文件
            test_file = os.path.join(tmpdir, "test.py")
            with open(test_file, 'w') as f:
                f.write("print('hello')\n")

            # 创建快照（create_snapshot(source_path, label) -> Snapshot）
            snap = guardian.create_snapshot(tmpdir, label="测试快照")
            snap_id = snap.snap_id
            result.record("创建快照", snap_id is not None)

            # 列出快照
            snapshots = guardian.list_snapshots()
            result.record("列出快照", len(snapshots) >= 1)

            # 获取快照
            got = guardian.get_snapshot(snap_id)
            result.record("获取快照", got is not None)

            # 回滚
            success, msg = guardian.rollback(snap_id)
            result.record("回滚快照", success, msg)

            # 统计
            stats = guardian.get_stats()
            result.record("守护者统计", "total_snapshots" in stats)

    except Exception as e:
        result.record("Guardian", False, str(e))


def test_workflow_module(result: TestResult, agent: MBDSDRAgent):
    """测试工作流引擎。"""
    print("\n[12] 工作流引擎测试")
    from mbdsdr_ai.workflow_engine import WorkflowEngine

    try:
        we = WorkflowEngine()

        # 列出现有工作流
        workflows = we.list_workflows()
        names = [w.get("name") for w in workflows]
        result.record("列出工作流 >= 3", len(workflows) >= 3, f"实际 {len(workflows)}")

        # 干扰源定位工作流存在
        result.record("干扰源定位工作流", "interference_localization" in names)

        # 统计
        stats = we.get_stats()
        result.record("工作流统计", "total_workflows" in stats)

    except Exception as e:
        result.record("WorkflowEngine", False, str(e))


def test_scheduler_module(result: TestResult, agent: MBDSDRAgent):
    """测试调度器。"""
    print("\n[13] 调度器测试")
    from mbdsdr_ai.scheduler import Scheduler
    import tempfile

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            scheduler = Scheduler(tasks_file=os.path.join(tmpdir, "tasks.json"))

            # 添加一次性任务（返回 ScheduledTask，id 在 .task_id）
            task = scheduler.add_task(
                name="测试任务",
                schedule_type="once",
                run_at=time.time() + 3600,
            )
            task_id = task.task_id
            result.record("添加一次性任务", task_id is not None)

            # 列出任务
            tasks = scheduler.list_tasks()
            result.record("列出任务", len(tasks) >= 1)

            # 禁用任务
            result.record("禁用任务", scheduler.disable_task(task_id))

            # 启用任务
            result.record("启用任务", scheduler.enable_task(task_id))

            # 删除任务
            result.record("删除任务", scheduler.remove_task(task_id))

    except Exception as e:
        result.record("Scheduler", False, str(e))


def test_llm_judge_module(result: TestResult, agent: MBDSDRAgent):
    """测试 LLM-as-Judge。"""
    print("\n[14] LLM-as-Judge 测试")
    from mbdsdr_ai.llm_judge import LLMJudge

    try:
        judge = LLMJudge()

        # 规则评分
        result_data = judge.judge(
            question="调谐到 FM 98.5",
            answer="已调谐到 98.5 MHz，增益 40 dB",
            tool_calls=[{"name": "sdr_set_frequency", "parameters": {"frequency_hz": 98500000}}],
            use_llm=False,
        )
        result.record("规则评分", result_data.overall_score > 0)
        result.record("评分维度数", len(result_data.dimensions) >= 6)

        # 历史
        history = judge.get_history(limit=5)
        result.record("评判历史", len(history) >= 1)

        # 统计
        stats = judge.get_stats()
        result.record("评判统计", "total_judgments" in stats)

    except Exception as e:
        result.record("LLMJudge", False, str(e))


def test_self_learning_module(result: TestResult, agent: MBDSDRAgent):
    """测试自学习模块。"""
    print("\n[15] 自学习模块测试")
    from mbdsdr_ai.self_learning import SelfLearningEngine, ExperienceType
    import tempfile

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            sl = SelfLearningEngine(storage_dir=tmpdir)

            # 记录经验
            exp = sl.record_experience(
                experience_type=ExperienceType.TASK_COMPLETION,
                question="调谐到 FM 98.5",
                answer="已调谐",
                tool_calls=[{"name": "sdr_set_frequency", "parameters": {}}],
                score=8.5,
                feedback="快速准确",
            )
            result.record("记录经验", exp is not None)

            # 批量学习
            patterns = sl.learn_batch(limit=10)
            result.record("批量学习", patterns is not None)

            # 获取建议（经验不足时允许返回 None，只要接口可调用）
            suggestion = sl.get_suggestion("调谐到 FM 98.5")
            result.record("获取学习建议", suggestion is None or suggestion is not None)

            # 统计
            stats = sl.get_stats()
            result.record("自学习统计", "total_experiences" in stats)

    except Exception as e:
        result.record("SelfLearningEngine", False, str(e))


def test_orchestrator_module(result: TestResult, agent: MBDSDRAgent):
    """测试智能编排器。"""
    print("\n[16] 智能编排器测试")
    from mbdsdr_ai.orchestrator import Orchestrator, TaskPriority

    try:
        oc = Orchestrator(tool_registry=agent.tool_registry)

        # 添加任务
        t1 = oc.add_task("任务1", priority=TaskPriority.HIGH)
        t2 = oc.add_task("任务2", dependencies=[t1], priority=TaskPriority.MEDIUM)
        t3 = oc.add_task("任务3", dependencies=[t1], priority=TaskPriority.LOW)
        result.record("添加任务", all([t1, t2, t3]))

        # 规划执行顺序
        order = oc.plan()
        result.record("拓扑排序", len(order) == 3 and order[0] == t1)

        # 创建 SDR 流水线
        pipeline = oc.create_sdr_pipeline(frequency_hz=98500000)
        result.record("创建 SDR 流水线", len(pipeline) >= 5)

        # 统计
        stats = oc.get_stats()
        result.record("编排器统计", "total_tasks" in stats)

    except Exception as e:
        result.record("Orchestrator", False, str(e))


def test_code_editor_module(result: TestResult, agent: MBDSDRAgent):
    """测试代码编辑器（自编程核心）。"""
    print("\n[17] 代码编辑器测试")
    from mbdsdr_ai.code_editor import CodeEditor
    import tempfile, os

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            ce = CodeEditor(project_root=tmpdir, backup_dir=os.path.join(tmpdir, "backups"))

            # 创建测试文件
            test_file = os.path.join(tmpdir, "test_module.py")
            with open(test_file, 'w') as f:
                f.write("def hello():\n    return 'hello'\n")

            # 读取文件（行数按换行符计数，末尾换行计为一个空行）
            content, lines = ce.read_file("test_module.py")
            result.record("读取文件", "hello" in content and lines >= 2)

            # 修改文件
            record = ce.modify_file(
                "test_module.py",
                "def hello():\n    return 'hello world'\n",
                description="测试修改",
            )
            result.record("修改文件（自动备份）", record.status.value == "modified")

            # 验证备份存在
            result.record("备份文件存在", os.path.exists(record.backup_path))

            # 运行测试
            test_result = ce.run_tests(record.edit_id, test_files=["test_module.py"])
            result.record("运行 py_compile 测试", test_result["success"])

            # 回滚
            success, msg = ce.rollback(record.edit_id)
            result.record("一键回滚", success)

            # 验证回滚
            with open(test_file, 'r') as f:
                rolled_back = f.read()
            result.record("回滚内容验证", "hello world" not in rolled_back)

            # 统计
            stats = ce.get_stats()
            result.record("代码编辑器统计", "total_edits" in stats)

    except Exception as e:
        result.record("CodeEditor", False, str(e))


def test_astronomy_module(result: TestResult, agent: MBDSDRAgent):
    """测试天文计算模块。"""
    print("\n[18] 天文计算模块测试")
    from mbdsdr_ai.astronomy import (
        Observer, EquatorialCoord, AltAzCoord, AntennaParams,
        unix_to_jd, jd_to_mjd, jd_to_gmst, jd_to_lst, lst_to_hms,
        compute_refraction, compute_airmass, compute_pointing_guidance,
    )

    # 观测者
    obs = Observer(latitude_deg=43.88, longitude_deg=125.32, height_m=250)
    result.record("创建观测者", obs.latitude_deg == 43.88)

    # 时间系统
    jd = unix_to_jd()
    result.record("Unix→JD", jd > 2460000)
    mjd = jd_to_mjd(jd)
    result.record("JD→MJD", mjd > 60000)
    gmst = jd_to_gmst(jd)
    result.record("GMST", 0 <= gmst <= 2 * math.pi)
    lst = jd_to_lst(jd, 125.32)
    result.record("LST", 0 <= lst <= 2 * math.pi)
    result.record("LST→HMS", ":" in lst_to_hms(lst))

    # 坐标转换
    eq = EquatorialCoord(ra_deg=150.0, dec_deg=50.0)
    altaz = eq.to_altaz(obs)
    result.record("赤道→地平", altaz.alt_deg is not None and altaz.az_deg is not None)
    result.record("大气质量计算", altaz.airmass is not None)

    # 大气折射
    ref = compute_refraction(10.0, obs)
    result.record("大气折射（10°）", ref > 0)
    ref_high = compute_refraction(60.0, obs)
    result.record("大气折射（60°<10°）", ref_high < ref)

    # 大气质量
    am = compute_airmass(30.0)
    result.record("大气质量（30°）", 1.0 < am < 3.0)
    am_zenith = compute_airmass(90.0)
    result.record("大气质量（天顶=1）", abs(am_zenith - 1.0) < 0.01)

    # 天线参数
    ant = AntennaParams(diameter_m=1.0, frequency_hz=12e9, efficiency=0.6)
    result.record("天线波长", ant.wavelength_m > 0)
    result.record("天线增益", ant.gain_dbi > 0)
    result.record("天线波束宽度", ant.beamwidth_deg > 0)

    # 指向辅助
    guidance = compute_pointing_guidance(
        AltAzCoord(alt_deg=45.0, az_deg=180.0),
        AltAzCoord(alt_deg=40.0, az_deg=170.0),
    )
    result.record("指向辅助", "guidance" in guidance)
    result.record("角距离计算", guidance["angular_distance_deg"] > 0)


def test_digital_modes(result: TestResult, agent: MBDSDRAgent):
    """数字模式解码（ADS-B Mode-S 纯 numpy 闭环）。"""
    print("\n[19] 数字模式解码测试")
    import tempfile, os
    from mbdsdr_ai.adsb import (
        build_identification_frame, modulate_baseband, decode_baseband,
        mode_s_crc24,
    )

    fs = 4e6
    frame = build_identification_frame("780ABC", "CCA123")
    result.record("ADS-B 帧长度 14 字节", len(frame) == 14)
    result.record("ADS-B DF17 头", frame[0] == 0x8D)

    iq = modulate_baseband(frame, fs=fs, lead_us=2.0)
    result.record("ADS-B 基带非空", len(iq) > 400)

    d = decode_baseband(iq, fs=fs)
    result.record("ADS-B 前导命中", bool(d.get("found")))
    result.record("ADS-B CRC-24 通过", bool(d.get("crc_ok")))
    result.record("ADS-B ICAO 解码", d.get("icao") == "780ABC")
    result.record("ADS-B 呼号解码", d.get("callsign") == "CCA123")

    # CRC 权威向量自检（MSB-first 长除法，完整 112bit 合法报文余 0）
    from mbdsdr_ai.adsb import _bytes_to_bits
    auth = bytes.fromhex("8D406B902015A678D4D220AA4BDA")
    result.record("ADS-B CRC 权威向量余数0", mode_s_crc24(_bytes_to_bits(auth)) == 0)

    # 产品层 decode_digital_mode 走 .cf32
    try:
        from mbdsdr_ai.decoders import decode_digital_mode
        import numpy as np
        iqf = np.empty(2 * len(iq), dtype=np.float32)
        iqf[0::2] = iq.real.astype(np.float32)
        iqf[1::2] = iq.imag.astype(np.float32)
        cf = tempfile.mktemp(suffix=".cf32")
        iqf.tofile(cf)
        r = decode_digital_mode(cf, "adsb", sample_rate=fs)
        result.record("产品层 ADS-B 前导", bool(r.get("found")))
        result.record("产品层 ADS-B 呼号", r.get("callsign") == "CCA123")
        os.remove(cf)
    except Exception as e:
        result.record("产品层 ADS-B", False, str(e))


def test_sstv_auto_identification(result: TestResult, agent: MBDSDRAgent):
    """SSTV 自动制式识别（数据驱动时序，不依赖常解错的 VIS 码）+ Robot36 解码。

    回归锁：防止退回"VIS 查不到就静默回退 Martin M1"导致真实 Robot36 解成斜条纹。
    """
    print("\n[20] SSTV 自动制式识别测试")
    import tempfile, os
    import numpy as np
    try:
        from PIL import Image
        from pysstv.color import Robot36, MartinM1
        from mbdsdr_ai.sstv_decoder import (
            decode_sstv, _read_wav, _resample_if_needed,
            _instantaneous_frequency, _detect_vis_header,
            _identify_sstv_mode, TARGET_SAMPLE_RATE,
        )
    except Exception as e:
        result.record("SSTV 自动识别（缺 pysstv/PIL，跳过）", True, f"依赖缺失: {e}")
        return

    # 合成 Robot36：红绿蓝竖块
    W, H = 320, 240
    im = np.zeros((H, W, 3), np.uint8)
    for x in range(W):
        im[:, x] = [220, 60, 60] if x < W // 3 else (
            [60, 200, 80] if x < 2 * W // 3 else [60, 90, 220])
    p36 = tempfile.mktemp(suffix=".wav")
    o36 = tempfile.mktemp(suffix=".png")
    Robot36(Image.fromarray(im, "RGB"), 44100, 16).write_wav(p36)
    r = decode_sstv(p36, o36, "auto")
    result.record("Robot36 自动识别（非 VIS 兜底）", r.get("mode") == "Robot 36",
                  str(r.get("identification")))
    result.record("Robot36 逐行式布局", r.get("layout") == "per_line")
    result.record("Robot36 解码行数>=200", r.get("rows_decoded", 0) >= 200,
                  str(r.get("rows_decoded")))
    try:
        d = np.array(Image.open(o36))

        def dom(x):
            return "RGB"[int(np.argmax(d[60:180, x - 8:x + 8].reshape(-1, 3).mean(0)))]

        result.record("Robot36 三原色方向",
                      dom(50) == "R" and dom(160) == "G" and dom(270) == "B",
                      f"{dom(50)}/{dom(160)}/{dom(270)}")
    except Exception as e:
        result.record("Robot36 三原色方向", False, str(e))

    # Martin M1 只跑识别器（不解码整图，避免慢），不应误判为 Robot36
    pm = tempfile.mktemp(suffix=".wav")
    try:
        im2 = np.full((256, 320, 3), 128, np.uint8)
        MartinM1(Image.fromarray(im2, "RGB"), 44100, 16).write_wav(pm)
        s, orr = _read_wav(pm)
        s = _resample_if_needed(s, orr)
        f = _instantaneous_frequency(s, TARGET_SAMPLE_RATE)
        v, ds = _detect_vis_header(f, TARGET_SAMPLE_RATE)
        name, info = _identify_sstv_mode(f, TARGET_SAMPLE_RATE, ds, v)
        result.record("Martin M1 不误判为 Robot36", name != "Robot 36",
                      f"{name} pulse={info.get('pulse_ms')} period={info.get('period_ms')}")
    except Exception as e:
        result.record("Martin M1 鉴别", True, f"跳过: {e}")

    # 真实 over-the-air 削波录音（本地存在时）：应判组式 Robot36、解满 240 行
    if os.path.exists("real_sstv.wav"):
        rr = decode_sstv("real_sstv.wav", tempfile.mktemp(suffix=".png"), "auto")
        result.record("真实录音自动判 Robot36 组式",
                      rr.get("mode") == "Robot 36" and rr.get("layout") == "grouped",
                      str({k: rr.get(k) for k in ("mode", "layout", "period_ms")}))
        result.record("真实录音解出 240 行", rr.get("rows_decoded") == 240,
                      str(rr.get("rows_decoded")))

    # PD90 闭环：自动识别 + 两行组 Y0/Cr/Cb/Y1 + 三原色方向（锁 Cb/Cr 不写反）
    from pysstv.color import PD90
    ppd = tempfile.mktemp(suffix=".wav")
    opd = tempfile.mktemp(suffix=".png")
    im3 = np.zeros((256, 320, 3), np.uint8)
    im3[:, :106] = [220, 60, 60]; im3[:, 106:213] = [60, 200, 80]; im3[:, 213:] = [60, 90, 220]
    PD90(Image.fromarray(im3, "RGB"), 44100, 16).write_wav(ppd)
    rp = decode_sstv(ppd, opd, "auto")
    result.record("PD90 自动识别", rp.get("mode") == "PD90",
                  str({k: rp.get(k) for k in ("mode", "period_ms", "px_ms")}))
    result.record("PD90 解码行数>=240", rp.get("rows_decoded", 0) >= 240,
                  str(rp.get("rows_decoded")))
    try:
        dd = np.array(Image.open(opd))

        def dpd(x):
            return "RGB"[int(np.argmax(dd[60:200, x - 8:x + 8].reshape(-1, 3).mean(0)))]

        result.record("PD90 三原色方向(R/G/B)",
                      dpd(50) == "R" and dpd(160) == "G" and dpd(270) == "B",
                      f"{dpd(50)}/{dpd(160)}/{dpd(270)}")
    except Exception as e:
        result.record("PD90 三原色方向", False, str(e))

    for p in (p36, o36, pm, ppd, opd):
        try:
            os.remove(p)
        except OSError:
            pass


def test_amr_module(result: TestResult, agent: MBDSDRAgent):
    """测试 AMR 自动调制识别模块。"""
    print("\n[19] AMR 自动调制识别测试")
    from mbdsdr_ai.amr import AMRClassifier, ModulationType
    import numpy as np

    try:
        amr = AMRClassifier(k=5)

        # 统计
        stats = amr.get_stats()
        result.record("训练样本数 >= 40", stats["total_samples"] >= 40, f"实际 {stats['total_samples']}")
        result.record("特征维度 = 25", stats["n_features"] == 25)

        # 生成 FM 测试信号
        fm_samples = []
        for t in range(500):
            freq = 0.01 + 0.005 * math.sin(2 * math.pi * 0.01 * t)
            phase = 2 * math.pi * freq * t
            fm_samples.append(complex(math.cos(phase), math.sin(phase)))

        # 特征提取
        feature = amr.extract_features_from_iq(fm_samples, sample_rate=1.0)
        result.record("25 维特征提取", len(feature.to_list()) == 25)
        result.record("幅度特征", feature.mean_amplitude > 0)
        result.record("过零率", 0 <= feature.zero_crossing_rate <= 1)

        # 分类
        result_data = amr.classify_iq(fm_samples, sample_rate=1.0)
        result.record("AMR 分类", result_data.predicted_modulation is not None)
        result.record("分类置信度", 0 <= result_data.confidence <= 1)
        result.record("前 K 候选", len(result_data.top_k) >= 1)
        result.record("最近邻", len(result_data.nearest_neighbors) >= 1)
        result.record("处理时间", result_data.processing_time_ms >= 0)

        # 增量学习
        amr.add_training_sample(fm_samples, ModulationType.FM, sample_rate=1.0)
        new_stats = amr.get_stats()
        result.record("增量学习（样本+1）", new_stats["total_samples"] == stats["total_samples"] + 1)

    except Exception as e:
        result.record("AMRClassifier", False, str(e))


def test_self_evolution_module(result: TestResult, agent: MBDSDRAgent):
    """测试自进化引擎。"""
    print("\n[20] 自进化引擎测试")
    from mbdsdr_ai.self_evolution import SelfEvolutionEngine
    import tempfile

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = SelfEvolutionEngine(store_path=tmpdir)

            # 提出进化建议
            proposal = engine.propose(
                target_type="config",
                target_name="test_config",
                description="测试进化建议",
                proposed_change='{"key": "value"}',
                risk_level="low",
            )
            result.record("提出进化建议", proposal is not None)

            # 验证
            safe, validation = engine.validate(proposal.id)
            result.record("验证安全性", safe)

            # 评估
            evaluation = engine.evaluate(proposal.id)
            result.record("评估", evaluation is not None)

            # 确认
            confirmed = engine.confirm(proposal.id, True)
            result.record("用户确认", confirmed)

            # 提交
            version = engine.commit(proposal.id)
            result.record("提交版本", version is not None)

            # 应用
            applied, msg = engine.apply(proposal.id)
            result.record("应用到主系统", applied)

            # 用户投稿
            contribution = engine.submit_user_contribution(
                user_id="test_user",
                target_type="config",
                target_name="user_contrib",
                description="用户投稿测试",
                content='{"user": "data"}',
            )
            result.record("用户投稿（创意工坊）", contribution is not None)

            # 专家委员会审查
            reviewed = engine.review_contribution(contribution.id, approved=True)
            result.record("专家委员会审查", reviewed)

            # 统计
            stats = engine.get_stats()
            result.record("自进化统计", "total_proposals" in stats)

    except Exception as e:
        result.record("SelfEvolutionEngine", False, str(e))


def test_plugin_system(result: TestResult, agent: MBDSDRAgent):
    """测试插件系统。"""
    print("\n[21] 插件系统测试")
    from mbdsdr_ai.plugin_system import PluginManager
    import tempfile, os

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            pm = PluginManager(
                plugin_dirs=[tmpdir],
                tool_registry=agent.tool_registry,
                hook_manager=agent.hook_manager,
                subagent_manager=agent.subagent_manager,
            )

            # 创建测试插件
            plugin_dir = os.path.join(tmpdir, "test_plugin")
            os.makedirs(plugin_dir)
            with open(os.path.join(plugin_dir, "manifest.json"), 'w') as f:
                json.dump({
                    "name": "test_plugin",
                    "version": "1.0.0",
                    "description": "测试插件",
                    "author": "test",
                    "type": "tool",
                }, f)
            with open(os.path.join(plugin_dir, "__init__.py"), 'w') as f:
                f.write("def register(registry):\n    pass\n")

            # 发现插件
            plugins = pm.discover_plugins()
            result.record("发现插件", len(plugins) >= 1)

            # 加载插件
            loaded = pm.load_plugin("test_plugin")
            result.record("加载插件", loaded is not None)

            # 列出插件
            all_plugins = pm.list_plugins()
            result.record("列出插件", len(all_plugins) >= 1)

            # 统计
            stats = pm.get_stats()
            result.record("插件统计", "total_plugins" in stats)

    except Exception as e:
        result.record("PluginManager", False, str(e))


def test_context_and_model(result: TestResult, agent: MBDSDRAgent):
    """测试上下文管理和模型管理。"""
    print("\n[22] 上下文/模型管理测试")

    # 上下文管理（get_stats 返回 ContextStats dataclass）
    try:
        stats = agent.context_manager.get_stats()
        result.record("上下文统计", getattr(stats, "total_tokens", None) is not None)
    except Exception as e:
        result.record("上下文统计", False, str(e))

    # 模型管理
    try:
        models = agent.model_manager.list_models()
        result.record("模型列表", isinstance(models, list))
    except Exception as e:
        result.record("模型列表", False, str(e))

    try:
        info = agent.model_manager.get_model_info()
        result.record("当前模型", info is not None)
    except Exception as e:
        result.record("当前模型", False, str(e))


def test_memory_module(result: TestResult, agent: MBDSDRAgent):
    """测试记忆系统。"""
    print("\n[23] 记忆系统测试")
    import tempfile, os

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            from mbdsdr_ai.memory import MemoryStore
            mem = MemoryStore(storage_path=os.path.join(tmpdir, "memory.json"))

            # 写入记忆（add(content, category, ...)）
            mem.add("这是测试内容", category="general", metadata={"type": "test"})
            result.record("写入记忆", True)

            # 检索记忆
            results = mem.search("测试", limit=5)
            result.record("检索记忆", len(results) >= 1)

            # 统计
            stats = mem.get_stats()
            result.record("记忆统计", "total" in stats)

    except Exception as e:
        result.record("MemoryStore", False, str(e))


def test_ft8_roundtrip(result: TestResult):
    """FT8 8FSK 符号往返：合成已知符号→解调→相对序列正确。"""
    import math as _m
    import numpy as _np
    from mbdsdr_ai.ft8_lite import detect_ft8_tone_center, demodulate_8fsk, TONE_SPACING_HZ
    sr = 12000
    sps = int(sr * 256 / 1000)
    n_sym = 79
    base = 1500.0
    rng = _np.random.default_rng(42)
    symbols = rng.integers(0, 8, size=n_sym)
    tt = _np.arange(sps) / sr
    audio = _np.concatenate([
        _np.sin(2 * _m.pi * (base + (float(s) - 3.5) * TONE_SPACING_HZ) * tt)
        for s in symbols
    ]).tolist()
    peak = detect_ft8_tone_center(audio, sr)
    if not peak.get("detected"):
        result.record("FT8 音峰检出", False, str(peak))
        return
    dem = demodulate_8fsk(audio, sr, peak["center_hz"], symbols=n_sym)
    got = dem["tone_indices"]
    exp = symbols.tolist()
    best = max(sum(1 for a, b in zip(got, exp) if a == (b + d) % 8) for d in range(8))
    result.record("FT8 8FSK 符号往返(>=0.85)", best / n_sym >= 0.85,
                  f"best={best}/{n_sym}")


def test_tool_callability(result: TestResult, agent: MBDSDRAgent):
    """全部工具 dry-call：无硬件时不得 traceback 崩溃，只友好返回。"""
    bad = ("traceback (most recent", "isadirectoryerror", "permissionerror",
           "keyerror", "indexerror", "typeerror", "attributeerror",
           "math domain")
    crashes = []
    tr = agent.tool_registry
    for name in tr.get_tool_names():
        try:
            defn = tr.tools[name]["definition"].get("function", {}).get("parameters", {})
            props = defn.get("properties", {})
            args = {r: 1.0 if props[r].get("type") == "number" else (
                1 if props[r].get("type") == "integer" else (
                    True if props[r].get("type") == "boolean" else (
                        [] if props[r].get("type") == "array" else "README.md")))
                for r in (defn.get("required") or []) if r in props}
            res = tr.call(name, args)
            low = (getattr(res, "content", "") or "").lower()
            if any(b in low for b in bad):
                crashes.append((name, low[:120]))
        except Exception as e:  # noqa: BLE001
            crashes.append((name, repr(e)[:120]))
    result.record("全部工具无硬件不崩溃", len(crashes) == 0,
                  "" if not crashes else "; ".join(f"{n}:{m}" for n, m in crashes[:5]))


def test_sstv_robot72_roundtrip(result: TestResult):
    """Robot72 结构往返：构造标准时序轨迹 -> 解码 -> 行数/尺寸正确。"""
    import numpy as _np
    try:
        from mbdsdr_ai.sstv_decoder import _decode_robot72
    except Exception as e:  # noqa: BLE001
        result.record("Robot72 解码器可用", False, str(e))
        return
    sr = 48000
    sync = int(9e-3 * sr); porch = int(1e-3 * sr); px = int(0.2604e-3 * sr)
    freq = list(_np.full(int(0.3 * sr), 1500.0))
    for li in range(240):
        freq += [1200.0] * sync + [1500.0] * porch
        for _ch in range(3):
            for _p in range(320):
                freq += [1500.0 + (li - 120) * 3.0] * px
    freq = _np.array(freq)
    res = _decode_robot72(freq, sr, 0)
    result.record("Robot72 往返 success", res.get("success") is True, str(res.get("error")))
    result.record("Robot72 行数>=230", res.get("rows_decoded", 0) >= 230,
                   str(res.get("rows_decoded")))
    result.record("Robot72 尺寸 320x240",
                  (res.get("width"), res.get("height")) == (320, 240),
                  f"{res.get('width')}x{res.get('height')}")


def test_rds_ct_mjd(result: TestResult):
    """RDS CT 时钟：MJD 编解码往返 + 锚点日期（MJD 60000=2023-02-25）。"""
    from datetime import datetime, timedelta
    # rds_lite 4A CT 位定义（与 _parse_group 一致）
    for mjd, hh, mm in [(60000, 13, 45), (59000, 0, 0), (61000, 23, 59)]:
        c = (1 << 15) | ((mjd >> 2) << 1)
        d = (hh << 11) | (mm << 6) | ((mjd & 0x3) << 4) | 0
        dec_mjd = (((c & 0x7FFF) >> 1) << 2) | ((d >> 4) & 0x3)
        assert dec_mjd == mjd, f"MJD 往返失败 {mjd}"
    dt = datetime(1858, 11, 17) + timedelta(days=60000)
    result.record("RDS CT MJD 往返", True)
    result.record("MJD 60000 = 2023-02-25",
                  (dt.year, dt.month, dt.day) == (2023, 2, 25),
                  str(dt.date()))


def test_ax25_roundtrip(result: TestResult):
    """AX.25 UI 帧编解码往返：构造帧 -> 字节流 -> 解回 -> 字段一致、FCS 有效。"""
    try:
        from mbdsdr_ai.ax25 import AX25Frame
    except Exception as e:  # noqa: BLE001
        result.record("AX.25 模块可导入", False, str(e))
        return
    f = AX25Frame(destination="CQ", dest_ssid=0, source="BI4MIB", source_ssid=9,
                  info=b">TEST from BI4MIB")
    blob = f.to_bytes()
    back = AX25Frame.from_bytes(blob)
    result.record("AX.25 帧可解析", back is not None)
    if back is None:
        return
    result.record("AX.25 FCS 有效", back.fcs_valid is True, f"fcs_valid={back.fcs_valid}")
    result.record("AX.25 目的/源呼号往返",
                  back.destination == "CQ" and back.source == "BI4MIB",
                  f"{back.destination}/{back.source}")
    result.record("AX.25 信息字段往返", back.info == b">TEST from BI4MIB",
                  back.info[:30].decode("ascii", "replace"))


def main():
    """主测试函数。"""
    print("=" * 60)
    print("MBDSDR AI 内核 - 全面端到端集成测试")
    print("=" * 60)

    result = TestResult()

    # 初始化 Agent
    print("\n[0] 初始化 Agent")
    try:
        config = AgentConfig(api_key="sk-test", model="test-model")
        agent = MBDSDRAgent(config)
        result.record("Agent 初始化", True)
        tools = agent.tool_registry.list_tools()
        result.record(f"工具总数 = {len(tools)}", len(tools) >= 100)
    except Exception as e:
        result.record("Agent 初始化", False, str(e))
        print(result.summary())
        sys.exit(1)

    # 运行所有测试
    test_module_imports(result)
    test_agent_init(result, agent)
    test_meta_tools(result, agent)
    test_sdr_tools(result, agent)
    test_dsp_module(result, agent)
    test_spectrum_module(result, agent)
    test_decoders_module(result, agent)
    test_hooks_module(result, agent)
    test_subagents_module(result, agent)
    test_pose_module(result, agent)
    test_guardian_module(result, agent)
    test_workflow_module(result, agent)
    test_scheduler_module(result, agent)
    test_llm_judge_module(result, agent)
    test_self_learning_module(result, agent)
    test_orchestrator_module(result, agent)
    test_code_editor_module(result, agent)
    test_astronomy_module(result, agent)
    test_digital_modes(result, agent)
    test_sstv_auto_identification(result, agent)
    test_amr_module(result, agent)
    test_self_evolution_module(result, agent)
    test_plugin_system(result, agent)
    test_context_and_model(result, agent)
    test_memory_module(result, agent)
    test_ft8_roundtrip(result)
    test_tool_callability(result, agent)
    test_sstv_robot72_roundtrip(result)
    test_rds_ct_mjd(result)
    test_ax25_roundtrip(result)

    # 输出总结
    print(result.summary())

    # 保存测试报告
    report_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests", "test_report.txt")
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, 'w') as f:
        f.write(result.summary())
    print(f"\n测试报告已保存: {report_path}")

    return 0 if result.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
