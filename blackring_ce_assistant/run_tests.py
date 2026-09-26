# -*- coding: utf-8 -*-
"""零依赖测试运行器（兼容无 pytest 环境）。用法：python run_tests.py"""
from __future__ import annotations

import importlib
import inspect
import sys
import traceback
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

MODULES = [
    "tests.test_paths",
    "tests.test_aob_pattern",
    "tests.test_symbols",
    "tests.test_address_expr",
    "tests.test_pointer_transaction",
    "tests.test_rules",
    "tests.test_candidates",
    "tests.test_ct_parser",
    "tests.test_relic_service",
    "tests.test_bridge",
]


def _collect(mod):
    """收集模块级 test_* 函数与 unittest.TestCase 中的 test_* 方法。

    旧实现只遍历模块属性，类里的用例会被**静默跳过**（外表全绿实则没跑）。
    """
    cases = []
    for name in sorted(dir(mod)):
        obj = getattr(mod, name)
        if name.startswith("test_") and inspect.isfunction(obj):
            cases.append((name, obj))
    for name in sorted(dir(mod)):
        obj = getattr(mod, name)
        if inspect.isclass(obj) and issubclass(obj, unittest.TestCase):
            for mname in sorted(dir(obj)):
                if mname.startswith("test_"):
                    cases.append((f"{name}.{mname}", getattr(obj(mname), mname)))
    return cases


def main() -> int:
    passed = failed = 0
    failures = []
    for mod_name in MODULES:
        try:
            mod = importlib.import_module(mod_name)
        except Exception:
            # 导入失败必须报成 FAIL 而不是让整个 runner 崩掉
            failed += 1
            failures.append((f"{mod_name} <import>", traceback.format_exc()))
            print(f"FAIL  {mod_name} <import>")
            continue
        for name, fn in _collect(mod):
            try:
                fn()
                passed += 1
                print(f"PASS  {mod_name}::{name}")
            except Exception:
                failed += 1
                failures.append((f"{mod_name}::{name}", traceback.format_exc()))
                print(f"FAIL  {mod_name}::{name}")
    print("-" * 40)
    print(f"passed={passed} failed={failed}")
    for name, tb in failures:
        print("=" * 40)
        print(name)
        print(tb)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
