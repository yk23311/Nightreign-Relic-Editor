# -*- coding: utf-8 -*-
"""定位资源目录：兼容源码运行与 PyInstaller 冻结。"""
from __future__ import annotations

import sys
from pathlib import Path


def app_root() -> Path:
    """开发态=项目根（nightreign_relic_editor/）；冻结态=exe 解包目录（onefile 为 _MEIPASS）。

    注意层级：本文件是 <项目>/app/paths.py，所以项目根是 parents[1]。
    曾误写成 parents[2]，于是源码运行时 data_dir() 指向**工作区根目录**、
    data/mapping.json 根本不存在，python main.py 会直接弹「映射加载失败」。
    （例如 app/services/relic_service.py 比本文件深一层，那里才是 parents[2]。）
    """
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[1]


def data_dir() -> Path:
    return app_root() / "data"


def user_data_dir() -> Path:
    """可写目录（预设/备份）：冻结时放在 exe 旁，便于用户找到。"""
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
    else:
        base = app_root()
    p = base / "data"
    p.mkdir(parents=True, exist_ok=True)
    return p
