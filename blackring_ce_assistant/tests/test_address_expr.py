# -*- coding: utf-8 -*-
"""地址表达式求值测试。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mapping.address_expr import AddressExprEvaluator, ExprError, parse_offset


def _raises(fn):
    try:
        fn()
    except ExprError:
        return True
    return False


class FakeMem:
    def __init__(self):
        self.d = {}

    def read_u32(self, a):
        return self.d.get(a, 0)

    def read_u64(self, a):
        return self.d.get(a, 0)


def test_parse_offset():
    assert parse_offset("+18") == 0x18
    assert parse_offset("18") == 0x18
    assert parse_offset("0x18") == 0x18
    assert parse_offset("+1c") == 0x1C
    assert parse_offset("") == 0


def test_arith_and_deref():
    mem = FakeMem()
    mem.d[0x2000] = 100  # Gaitem
    symbols = {"Gaitem": 0x2000, "csgaitem": 0x3000}
    ev = AddressExprEvaluator(symbols.__getitem__, read_u32=mem.read_u32, read_u64=mem.read_u64)
    assert ev.eval("8+8*[Gaitem]") == 8 + 8 * 100
    assert ev.eval("8+8*[Gaitem+4]") == 8 + 8 * 0  # 未写则为 0
    mem.d[0x2004] = 50
    assert ev.eval("8+8*[Gaitem+4]") == 8 + 8 * 50
    assert ev.eval("+18") == 0x18
    assert ev.eval("csgaitem+4") == 0x3004
    assert ev.eval("2*3+4") == 10
    assert ev.eval("(2+3)*4") == 20


def test_eval_is_reentrant():
    """回归：解析游标曾挂在求值器实例上，嵌套求值会让外层解析读到内层的记号表。

    解析 csgaitem 时，resolver 重入同一个求值器求 "1+1" 得到 2，
    因此 csgaitem 的值是 2，外层 "csgaitem+4" 应为 6。
    旧实现下内层 eval 会把 _tokens/_i 换成 [1,+,1]/3，外层随后读到记号表末尾，
    直接返回 2（吞掉了 +4）—— 本用例正是用来钉住这个差异。
    """
    state = {"n": 0}

    def resolver(name):
        state["n"] += 1
        if state["n"] == 1:
            return ev.eval("1+1")  # 重入同一个求值器
        return 0x2000

    ev = AddressExprEvaluator(resolver, read_u32=lambda a: 0)
    assert ev.eval("csgaitem+4") == 6


def test_illegal():
    ev = AddressExprEvaluator({"x": 1}.__getitem__, read_u32=lambda a: 0)
    assert _raises(lambda: ev.eval("1+"))
    assert _raises(lambda: ev.eval("??"))
