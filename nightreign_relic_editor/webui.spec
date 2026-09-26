# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 规格：0.7.0 新界面（pywebview + Web 前端），onedir 打包。

为什么用 onedir 而不是 onefile：
  1. onefile 每次启动都要把整包解压到临时目录，冷启动更慢；
  2. onedir 便于用户看到程序结构，也便于替换单个文件；
  3. 计划里的分发形态是「绿色文件夹 + 安装包」。

**仍然显式排除 PySide6**：0.7.0 已删除旧的 app/ui/，但保留这条排除是**防御性**的 ——
一旦将来有人误引入 Qt 依赖，产物会从约 28MB 暴涨到 45MB 以上，而这条排除会让问题
在打包阶段就暴露，而不是悄悄塞进交付物里。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(SPECPATH))

from app.version import APP_NAME, __version__  # noqa: E402

root = Path(SPECPATH)

datas = [
    (str(root / "data" / "mapping.json"), "data"),
    (str(root / "data" / "effects_std.json"), "data"),
    (str(root / "data" / "effects_don.json"), "data"),
    (str(root / "data" / "effects_debuff.json"), "data"),
    (str(root / "data" / "effects_all.json"), "data"),
    # 前端资源：main.py 的 base_dir() 在冻结态取 _MEIPASS，onedir 下即 _internal/
    (str(root / "webui" / "web"), "web"),
    (str(root / "docs" / "DISCLAIMER.md"), "docs"),
    (str(root / "docs" / "USER_GUIDE.md"), "docs"),
]

a = Analysis(
    [str(root / "webui" / "main.py")],
    pathex=[str(root)],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "webui.bridge",
        "webui.serial",
        "webui.demo",
        "app.logging_setup",
        "app.paths",
        "app.version",
        "app.services.relic_service",
        "domain.models",
        "domain.enums",
        "domain.rules",
        "mapping.address_expr",
        "mapping.mapping_config",
        "infra.errors",
        "infra.memory.base",
        "infra.memory.win_process",
        "infra.memory.pointer",
        "infra.memory.transaction",
        "infra.memory.symbols",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Qt 与新界面无关；显式排除以免被旧 UI 的导入链拖进来
    excludes=["PySide6", "shiboken6", "tkinter", "matplotlib", "numpy", "pandas", "scipy"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME + "_v" + __version__,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME + "_v" + __version__,
)
