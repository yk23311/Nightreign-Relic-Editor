# -*- coding: utf-8 -*-
"""资源路径解析测试。

回归背景：app_root() 曾用 parents[2]，而 app/paths.py 只比项目根深一层 ——
源码运行时 data_dir() 会指向工作区根目录，data/mapping.json 不存在，
于是 python main.py 必然弹「映射加载失败」，整个开发态不可用。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.paths import app_root, data_dir


def test_app_root_is_the_project_dir():
    assert app_root() == ROOT, f"app_root()={app_root()} 应为 {ROOT}"


def test_data_dir_contains_real_data():
    d = data_dir()
    assert d == ROOT / "data"
    assert (d / "mapping.json").exists(), "源码态必须能找到 data/mapping.json"
    assert (d / "effects_std.json").exists()


def test_data_dir_is_inside_project():
    # 防止再次漂到工作区根目录（那里有 CT 表等输入文件，极易混淆）
    assert ROOT in data_dir().parents
