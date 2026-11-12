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
#
# 每一项为什么进/不进包（改这里必须同步 scripts/check_dist_head_parity.py 的判据）：
#
#   合规 · 不可省（不是“可选资源”，缺件即发布不合规）：
#     LICENSE / NOTICE.md / third_party_licenses/ —— MIT 与第三方许可要求随
#     二进制分发。
#
#   功能 · 运行期按文件路径读取（静态分析看不见，漏了不报错、只是静默降级）：
#     assets/                —— QIcon/QPixmap 直接读 desktop/assets/icon.*。
#     mbdsdr_ai/locale/      —— mbdsdr_ai/i18n.py:22 用 <pkg>/locale/<lang>.json
#                               惰性加载；缺文件时 tr() 静默回退原文，文案不生效。
#     mbdsdr_ai/data/        —— astronomy/dso.py:47、astronomy/moon_albedo.py:39
#                               等按 <pkg>/data/*.json|npz 读星表/月球反照率。
#     mbdsdr_ai/web/static/  —— WebBridge 面板加载的 index.html/app.js/style.css。
#     mbdsdr_ai/VERSION      —— version.py:12 运行期读它算 __version__
#                               （QApplication/MCP serverInfo），不是纯构建期文件。
#
#   排除 · 既非合规也非功能（别塞进包，会撑体积并让 parity 读出陈旧件）：
#     mbdsdr_ai/tests/       —— pytest 资产。
#     mbdsdr_ai/artifacts/   —— 实验产物（未跟踪 PNG/NPY），不属于任何一次 HEAD。
#     mbdsdr_ai/README.md    —— 开发文档；全仓库无运行期读取点（已按 *.py 搜过）。
#       （包内 README 若将来要发到 PyPI，走 wheel 的 package-data，不经 PyInstaller。）
#
# Path 先按 os.sep 归一，避免 Windows 上漏判。
_EXCLUDED_PKG_DIRS = ('/tests/', '/artifacts/')
_EXCLUDED_PKG_FILES = ('/mbdsdr_ai/README.md',)


def _keep_package_data(item):
    src = item[0].replace(os.sep, '/')
    if any(part in src for part in _EXCLUDED_PKG_DIRS):
        return False
    return not any(src.endswith(name) for name in _EXCLUDED_PKG_FILES)


datas += [item for item in collect_data_files('mbdsdr_ai')
          if _keep_package_data(item)]

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
    # pandas 不是产品依赖：PyInstaller 的 xref 里出现 matplotlib.pyplot → pandas
    # 这条边，但实测 `import matplotlib.pyplot` 并不加载 pandas（py3.14 验证）。
    # pandas 一旦进包，它的可选 IO 面又会把 openpyxl / lxml / xlsxwriter 整棵拖进来
    # （`pandas.io.excel._openpyxl`、`pandas.io.xml`、`pandas.io.excel._xlsxwriter`）。
    # 产品代码对四者零 import，属纯过度收集，显式排除以免白占几十 MB。
    excludes=[
        'pytest', '_pytest', 'pluggy', 'iniconfig', 'mbdsdr_ai.tests',
        'pandas', 'openpyxl', 'lxml', 'xlsxwriter',
    ],
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
