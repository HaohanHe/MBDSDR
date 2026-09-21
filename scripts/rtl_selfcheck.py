#!/usr/bin/env python3
"""
RTL-SDR 一键硬件自检（MBDSDR）
================================
插上 RTL2832U 棒后在本机运行，逐级验证整条接收链路：

    python scripts/rtl_selfcheck.py                 # 默认在 FM 广播段 98MHz 测试
    python scripts/rtl_selfcheck.py --freq 101800000
    python scripts/rtl_selfcheck.py --ppm 42        # 已知晶振频偏
    python scripts/rtl_selfcheck.py --tcp 127.0.0.1:1234   # 走 rtl_tcp，不依赖本机 librtlsdr
    python scripts/rtl_selfcheck.py --save          # 额外保存 IQ(npy) 和频谱图

每一级输出 PASS/FAIL/WARN，任何一级失败都给出可操作的修复建议。
不依赖 matplotlib 也能跑（只打印数值）；装了 matplotlib 会存频谱 PNG。
"""

import argparse
import sys
import time
import os

# 允许从仓库根目录直接运行
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def step(name):
    print(f"\n=== {name} ===")


def ok(msg):
    print(f"  [PASS] {msg}")


def warn(msg):
    print(f"  [WARN] {msg}")


def fail(msg):
    print(f"  [FAIL] {msg}")


def check_dependencies():
    """阶段 0：依赖检测。"""
    step("阶段0  依赖检测")
    have = {}
    try:
        import rtlsdr  # noqa: F401
        ver = getattr(rtlsdr, "__version__", "未知")
        ok(f"pyrtlsdr 已安装（{ver}）")
        have["pyrtlsdr"] = True
    except Exception:
        fail("未安装 pyrtlsdr。安装方法：")
        print("        pip install pyrtlsdr numpy")
        print("        还需系统库 librtlsdr：")
        print("          Debian/Ubuntu : sudo apt install rtl-sdr librtlsdr-dev")
        print("          macOS         : brew install librtlsdr")
        print("          Windows       : 下载 librtlsdr 的 .dll 放到 PATH（或用 --tcp 走 rtl_tcp）")
        have["pyrtlsdr"] = False

    try:
        import numpy  # noqa: F401
        ok(f"numpy 已安装（{numpy.__version__}）")
        have["numpy"] = True
    except Exception:
        fail("未安装 numpy：pip install numpy")
        have["numpy"] = False

    have["matplotlib"] = False
    try:
        import matplotlib  # noqa: F401
        have["matplotlib"] = True
    except Exception:
        warn("未安装 matplotlib，将不保存频谱图（不影响自检）。pip install matplotlib")

    return have


def open_device(args):
    """阶段 1/2：枚举并打开设备。"""
    from rtlsdr import RtlSdr, RtlSdrTcpClient
    from mbdsdr_ai.sdr_backend import RTLSDRBackend

    step("阶段1  设备枚举")
    if args.tcp:
        host, _, port_s = args.tcp.partition(":")
        port = int(port_s or "1234")
        print(f"  使用 rtl_tcp 网络模式 {host}:{port}（跳过本地 USB 枚举）")
        backend = RTLSDRBackend(host=host, port=port, ppm=args.ppm)
    else:
        devs = RTLSDRBackend.list_devices()
        if not devs:
            n = 0
            try:
                n = RtlSdr.get_device_count()
            except Exception:
                n = 0
            if n == 0:
                fail("未发现 RTL-SDR 设备。排查：")
                print("        1) 棒是否插好、换一个 USB 口（优先 USB2.0 直连，勿用劣质 HUB）")
                print("        2) Linux: lsusb 看是否有 Realtek Semiconductor Device 2838")
                print("        3) Linux: 黑名单 dvb_usb_rtl28xxu 内核驱动，或装 udev 规则后重新插拔")
                print("           echo 'blacklist dvb_usb_rtl28xxu' | sudo tee /etc/modprobe.d/rtl-sdr-blacklist.conf")
                print("        4) Windows: 用 Zadig 给 RTL2832 设备安装 WinUSB 驱动")
                print("        5) 是否被别的软件（GQRX/SDR#/rtl_fm）占用")
            return None
        for d in devs:
            print(f"  发现设备 #{d['index']}  tuner={d['tuner']}  serial={d.get('serial') or '-'}")
        idx = min(args.index, len(devs) - 1)
        backend = RTLSDRBackend(device_index=idx, ppm=args.ppm)

    step("阶段2  打开设备")
    if backend.connect():
        ok(f"已打开：{backend.device.name}，调谐器={backend.tuner_name}")
        if backend.tuner_name in ("Unknown",) and not args.tcp:
            warn("调谐器型号读不到（个别库/棒正常），不影响后续采样")
        return backend
    fail("打开设备失败。常见原因：驱动未装(Zadig)、被占用、权限不足(udev)、librtlsdr 缺失。")
    return None


def acquire_and_analyze(backend, args, have):
    """阶段 3/4：配置、采 IQ、做频谱分析。"""
    import numpy as np

    step("阶段3  配置与采样")
    rate = args.rate
    freq = args.freq
    if not backend.set_sample_rate(rate):
        warn(f"采样率 {rate} 设置失败，尝试 2.048MHz")
        rate = 2048000
        backend.set_sample_rate(rate)
    backend.set_frequency(freq)
    backend.set_agc(True)
    if args.ppm:
        backend.set_ppm(args.ppm)
    # 调频广播段以外，direct sampling 仅在 <24MHz 时提示
    if freq < 24_000_000:
        ds = backend.set_direct_sampling("q")
        if ds:
            warn("频率低于 24MHz，已尝试开启 direct sampling(Q)；若无信号可改 --branch i 或需上变频器")
    ok(f"中心频率 {freq/1e6:.3f} MHz，采样率 {rate/1e6:.3f} MHz，AGC 开，ppm={args.ppm}")

    n = args.samples
    print(f"  采集 {n} 个 IQ 样本 ...")
    t0 = time.time()
    try:
        samples = backend.read_samples(n)
    except Exception as e:
        fail(f"采样抛异常：{e}")
        return False
    dt = time.time() - t0
    if samples is None or len(samples) == 0:
        fail("没有采到样本（read_samples 返回空）。")
        return False
    ok(f"采到 {len(samples)} 个复数样本，用时 {dt:.2f}s（约 {len(samples)/dt/1e6:.2f} MS/s）")

    step("阶段4  频谱与信号质量分析")
    x = np.asarray(samples, dtype=np.complex128)
    # 直流偏置（RTL 典型存在，软件需去 DC）
    dc = np.mean(x)
    dc_db = 20 * np.log10(abs(dc) + 1e-12)
    x_ac = x - dc
    rms = np.sqrt(np.mean(np.abs(x_ac) ** 2))
    rms_db = 20 * np.log10(rms + 1e-12)
    # FFT 功率谱
    N = min(8192, len(x_ac))
    win = np.hanning(N)
    spec = np.fft.fftshift(np.fft.fft(x_ac[:N] * win))
    pwr = np.abs(spec) ** 2 + 1e-20
    pdb = 10 * np.log10(pwr)
    # 去掉中心直流 bin 后找峰
    center = N // 2
    pdb_nodc = pdb.copy()
    pdb_nodc[center - 2:center + 3] = np.median(pdb)
    peak_idx = int(np.argmax(pdb_nodc))
    peak_db = float(pdb_nodc[peak_idx])
    median_db = float(np.median(pdb_nodc))
    snr_est = peak_db - median_db
    peak_offset_hz = (peak_idx - center) * rate / N
    clipping = float(np.mean(np.abs(x_ac) > 0.98) * 100)

    print(f"  直流分量      : {dc_db:6.1f} dB（软件去 DC 后不影响解调）")
    print(f"  信号 RMS      : {rms_db:6.1f} dB（归一化）")
    print(f"  底噪中位数    : {median_db:6.1f} dB")
    print(f"  最强峰        : {peak_db:6.1f} dB，相对中心 {peak_offset_hz/1e3:+.1f} kHz")
    print(f"  峰-噪 裕度     : {snr_est:6.1f} dB")
    print(f"  削波样本占比  : {clipping:5.2f}%（>1% 说明增益过高）")

    if rms_db < -60:
        warn("RMS 很低：可能天线没接、增益过低、或该频点本来就空。换 FM 广播段(88-108MHz)复测。")
    elif rms_db > -6:
        warn("RMS 很高，注意是否过载削波；可关 AGC 改用手动低增益。")
    else:
        ok("信号电平在合理区间。")
    if snr_est > 15:
        ok(f"检测到明显信号（裕度 {snr_est:.0f}dB），接收链路工作正常。")
    elif snr_est > 6:
        warn(f"有弱信号起伏（裕度 {snr_est:.0f}dB），换强台/接天线再确认。")
    else:
        warn("未见明显单峰：若你在 FM 段却平坦，检查天线；若在空闲频点则正常。")
    if clipping > 1:
        warn("削波明显，建议降低增益。")

    if args.save:
        outdir = os.path.join(os.getcwd(), "experiments", "rtl_selfcheck")
        os.makedirs(outdir, exist_ok=True)
        iq_path = os.path.join(outdir, f"iq_{int(freq/1e6)}mhz.npy")
        np.save(iq_path, x_ac)
        ok(f"已保存去 DC 的 IQ：{iq_path}")
        if have.get("matplotlib"):
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            freqs_mhz = (np.arange(N) - N / 2) * rate / N / 1e6 + freq / 1e6
            fig, ax = plt.subplots(figsize=(10, 4))
            ax.plot(freqs_mhz, pdb, lw=0.6, color="#3a6ea5")
            ax.set_xlabel("Frequency (MHz)")
            ax.set_ylabel("Power (dB)")
            ax.set_title(f"RTL-SDR self-check  center={freq/1e6:.3f} MHz  tuner={backend.tuner_name}")
            ax.grid(alpha=0.3)
            fig.tight_layout()
            png_path = os.path.join(outdir, f"spectrum_{int(freq/1e6)}mhz.png")
            fig.savefig(png_path, dpi=130)
            ok(f"已保存频谱图：{png_path}")
    return True


def main():
    ap = argparse.ArgumentParser(description="RTL-SDR 一键硬件自检")
    ap.add_argument("--freq", type=float, default=98_000_000, help="中心频率 Hz，默认 98MHz(FM)")
    ap.add_argument("--rate", type=float, default=2_400_000, help="采样率 Hz，默认 2.4MHz")
    ap.add_argument("--ppm", type=int, default=0, help="晶振频偏校正 ppm")
    ap.add_argument("--index", type=int, default=0, help="设备序号")
    ap.add_argument("--tcp", type=str, default="", help="rtl_tcp 地址 host:port，例如 127.0.0.1:1234")
    ap.add_argument("--samples", type=int, default=1_000_000, help="采样点数，默认 100 万")
    ap.add_argument("--branch", type=str, default="q", help="HF 直采分支 i/q")
    ap.add_argument("--save", action="store_true", help="保存 IQ(npy) 与频谱图")
    args = ap.parse_args()

    print("MBDSDR RTL-SDR 自检")
    have = check_dependencies()
    if not have.get("pyrtlsdr") or not have.get("numpy"):
        print("\n依赖未齐，先按上面提示安装后重跑。")
        sys.exit(2)

    backend = open_device(args)
    if backend is None:
        sys.exit(1)
    try:
        good = acquire_and_analyze(backend, args, have)
    finally:
        backend.disconnect()
    print("\n=== 结论 ===")
    print("自检完成。把本输出（及 experiments/rtl_selfcheck 下的频谱图）发回，即可据此校准 ppm 与选频。"
          if good else "自检未通过，按上面 FAIL/WARN 排查。")
    sys.exit(0 if good else 1)


if __name__ == "__main__":
    main()
