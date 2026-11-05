# -*- mode: python ; coding: utf-8 -*-
"""
MBDSDR PyInstaller 打包配置（onedir 模式，无控制台窗口）。

用法:
  pyinstaller MBDSDR.spec --clean
  输出: dist/MBDSDR/MBDSDR(.exe)

说明:
  - console=False  => Windows 下不弹出黑色控制台窗口（--windowed）
  - onedir         => 输出目录便于调试；如需单文件可改为 onefile（去掉 COLLECT，
                      并把 a.binaries/a.zipfiles/a.datas 并入 EXE）
  - icon           => assets/icon.ico（内嵌 16/32/48/256 多尺寸）
  - datas          => 打包 assets/ 图标资源
  - hiddenimports  => PySide6 与 mbdsdr_ai 全量子模块（动态导入较多，避免漏包）
"""

import os
from PyInstaller.utils.hooks import collect_submodules, collect_data_files
from PyInstaller.utils.win32.versioninfo import (
    VSVersionInfo, FixedFileInfo, StringFileInfo, StringTable, StringStruct,
    VarFileInfo, VarStruct,
)

block_cipher = None

here = os.path.abspath(os.path.dirname(SPEC))   # desktop/
root = os.path.dirname(here)                    # 仓库根（mbdsdr_ai 包所在）

# 版本单一事实源：mbdsdr_ai/VERSION（与 CMake / pyproject / Python 同源）
import re as _re
with open(os.path.join(root, 'mbdsdr_ai', 'VERSION'), encoding='utf-8') as _fh:
    _version = _fh.read().strip()
# VSVersionInfo 的 filevers/prodvers 只收 4 个整数：预发布后缀（x.y.z-verify 之类）
# 只保留在字符串字段里，数字段取版本开头。
_m = _re.match(r'(\d+)\.(\d+)\.(\d+)(?:\.(\d+))?', _version)
if not _m:
    raise SystemExit(f"mbdsdr_ai/VERSION 不是版本号：{_version!r}")
_parts = [int(part) if part else 0 for part in _m.groups()]

# --- 隐藏导入：避免 PyInstaller 模块图漏掉动态加载的子模块 ---
hiddenimports = []
hiddenimports += collect_submodules('PySide6')
# mbdsdr_ai/tests 只服务开发期，且会经 pytest 把测试框架整条闭包拖进产物。
hiddenimports += collect_submodules(
    'mbdsdr_ai', filter=lambda name: not name.startswith('mbdsdr_ai.tests'))

# scipy 的私有子包（_external/_lib）在 __init__ 里是条件导入，PyInstaller 的
# scipy hook 只收一部分，冻结版会在启动时抛
#   No module named 'scipy._external.array_api_compat.numpy.fft'
# 这里整棵子树全收（跳过 tests，避免无谓体积），确保 scipy.signal / special /
# fft 等走的私有依赖都在包里。
hiddenimports += collect_submodules(
    'scipy', filter=lambda name: '.tests' not in name)


def _collect_namespace_submodules(package, subdir):
    """按文件系统收集 namespace package 里的 .py 模块。

    坑：``scipy/_external`` **没有 __init__.py**（PEP 420 namespace package），
    ``pkgutil.iter_modules`` / ``collect_submodules`` 都枚举不到它，于是
    ``scipy._external.array_api_compat.numpy.fft``（由
    ``__import__(__package__ + ".fft")`` 动态加载）永远进不了包，
    冻结版启动即 ``ModuleNotFoundError``。只有按目录扫才可靠。
    """
    import scipy as _scipy

    base = os.path.join(os.path.dirname(_scipy.__file__), subdir)
    prefix = package + "." + subdir.replace(os.sep, ".") + "."
    names = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for filename in filenames:
            if not filename.endswith(".py"):
                continue
            relative = os.path.relpath(os.path.join(dirpath, filename), base)
            module = prefix + relative.replace(os.sep, ".")[:-3]
            if module.endswith(".__init__"):
                module = module[:-len(".__init__")]
            names.append(module)
    return names


hiddenimports += _collect_namespace_submodules('scipy', '_external')

# --- 数据文件：图标资源 + 项目合规文件 + mbdsdr_ai 包内数据（如有） ---
datas = [
    (os.path.join(here, 'assets'), 'assets'),
    (os.path.join(root, 'LICENSE'), '.'),
    (os.path.join(root, 'NOTICE.md'), '.'),
    (os.path.join(root, 'third_party_licenses'), 'third_party_licenses'),
]
datas += collect_data_files('mbdsdr_ai')

# mbdsdr_ai/astronomy/__init__.py 用 importlib.spec_from_file_location 按**文件路径**
# 加载被同名 package 遮住的 ../astronomy.py（改名 mbdsdr_ai._astronomy_legacy）。
# 静态分析看不到这条路径，冻结版里文件不存在 → Observer 等名字全部缺失，
# 启动报 "cannot import name 'Observer' from 'mbdsdr_ai.astronomy'"。
# 必须把源文件当数据文件带进 _internal/mbdsdr_ai/。
datas.append((os.path.join(root, 'mbdsdr_ai', 'astronomy.py'), 'mbdsdr_ai'))

# 诊断构建开关：noarchive=True 时纯 Python 模块以 .pyc 落盘，
# scripts/release_check.py 才能“删整棵 scipy/_external”复现缺件对话框；
# 发布构建（默认）保持归档，体积/加载行为不变。
noarchive = os.environ.get('MBDSDR_NOARCHIVE') == '1'

a = Analysis(
    [os.path.join(here, 'main.py')],
    pathex=[here, root],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['pytest', '_pytest', 'pluggy', 'iniconfig', 'mbdsdr_ai.tests'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=noarchive,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='MBDSDR',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # --windowed：无控制台
    icon=os.path.join(here, 'assets', 'icon.ico'),
    version=VSVersionInfo(
        ffi=FixedFileInfo(
            filevers=tuple(_parts), prodvers=tuple(_parts),
            mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0,
            date=(0, 0)),
        kids=[
            StringFileInfo([StringTable('080404B0', [
                StringStruct('CompanyName', 'MBDSDR Project'),
                StringStruct('FileDescription', 'MBDSDR - AI 定义无线电'),
                StringStruct('FileVersion', _version),
                StringStruct('InternalName', 'MBDSDR'),
                StringStruct('OriginalFilename', 'MBDSDR.exe'),
                StringStruct('ProductName', 'MBDSDR'),
                StringStruct('ProductVersion', _version),
                StringStruct('LegalCopyright', 'MIT License'),
            ])]),
            VarFileInfo([VarStruct('Translation', [2052, 1200])]),
        ]),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='MBDSDR',
)
