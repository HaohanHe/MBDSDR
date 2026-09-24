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

# --- 数据文件：图标资源 + mbdsdr_ai 包内数据（如有） ---
datas = [
    (os.path.join(here, 'assets'), 'assets'),
]
datas += collect_data_files('mbdsdr_ai')

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
