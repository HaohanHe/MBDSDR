"""
MBDSDR AI - 可选依赖检测与降级
================================

自动检测可选依赖是否可用，
缺失时提供降级方案。
"""

from typing import Dict, Optional


def check_optional_deps() -> Dict:
    """检查所有可选依赖的可用性。"""
    deps = {}

    # scipy
    try:
        import scipy
        deps['scipy'] = {'available': True, 'version': scipy.__version__}
    except ImportError:
        deps['scipy'] = {'available': False, 'fallback': 'numpy简化版'}

    # skyfield
    try:
        import skyfield
        deps['skyfield'] = {'available': True, 'version': skyfield.__version__}
    except ImportError:
        deps['skyfield'] = {'available': False, 'fallback': 'sgp4简化版'}

    # pyserial
    try:
        import serial
        deps['pyserial'] = {'available': True, 'version': serial.__version__}
    except ImportError:
        deps['pyserial'] = {'available': False, 'fallback': '无串口功能'}

    # PySide6
    try:
        import PySide6
        deps['PySide6'] = {'available': True, 'version': PySide6.__version__}
    except ImportError:
        deps['PySide6'] = {'available': False, 'fallback': '无GUI，仅CLI/MCP'}

    # SoapySDR
    try:
        import SoapySDR
        deps['SoapySDR'] = {'available': True, 'version': 'unknown'}
    except ImportError:
        deps['SoapySDR'] = {'available': False, 'fallback': '无SDR硬件接入'}

    return deps


def print_deps_status():
    """打印依赖状态。"""
    deps = check_optional_deps()

    print("=== MBDSDR 依赖状态 ===")
    print()

    for name, info in deps.items():
        status = "✓" if info['available'] else "✗"
        version = info.get('version', 'N/A')
        fallback = info.get('fallback', '')

        print(f"  {status} {name:15s}  v{version}")
        if not info['available']:
            print(f"    降级方案: {fallback}")

    print()
    print("核心功能不需要任何重依赖即可运行")
    print("可选依赖用于增强功能（详见README）")


if __name__ == "__main__":
    print_deps_status()
