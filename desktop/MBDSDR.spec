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

block_cipher = None

here = os.path.abspath(os.path.dirname(SPEC))   # desktop/
root = os.path.dirname(here)                    # 仓库根（mbdsdr_ai 包所在）

# --- 隐藏导入：避免 PyInstaller 模块图漏掉动态加载的子模块 ---
hiddenimports = []
hiddenimports += collect_submodules('PySide6')
hiddenimports += collect_submodules('mbdsdr_ai')

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

# --- 数据文件：图标资源 + mbdsdr_ai 包内数据（如有） ---
datas = [
    (os.path.join(here, 'assets'), 'assets'),
]
datas += collect_data_files('mbdsdr_ai')

# mbdsdr_ai/astronomy/__init__.py 用 importlib.spec_from_file_location 按**文件路径**
# 加载被同名 package 遮住的 ../astronomy.py（改名 mbdsdr_ai._astronomy_legacy）。
# 静态分析看不到这条路径，冻结版里文件不存在 → Observer 等名字全部缺失，
# 启动报 "cannot import name 'Observer' from 'mbdsdr_ai.astronomy'"。
# 必须把源文件当数据文件带进 _internal/mbdsdr_ai/。
datas.append((os.path.join(root, 'mbdsdr_ai', 'astronomy.py'), 'mbdsdr_ai'))

a = Analysis(
    [os.path.join(here, 'main.py')],
    pathex=[here, root],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
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
