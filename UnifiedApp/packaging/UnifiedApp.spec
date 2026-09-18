# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec，可在 Windows 或 Ubuntu 上分别执行打包出各自平台的可执行文件。

用法（在 UnifiedApp 目录下）：
    pyinstaller packaging/UnifiedApp.spec

注意：UnifiedApp 本身只负责调度子进程，不需要把 J6B/MMT/CompareKPI 的
业务依赖（h5py/pandas/openpyxl 等）打进这个可执行文件；那三个脚本继续用
各自机器上配置好的 Python 解释器运行（见“环境设置”对话框）。
"""

from pathlib import Path

project_root = Path(SPECPATH).resolve().parent

a = Analysis(
    [str(project_root / "main.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="UnifiedMiningApp",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
