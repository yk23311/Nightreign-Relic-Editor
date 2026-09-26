# -*- coding: utf-8 -*-
"""符号解析测试：AOB 失败按符号隔离、按模块分组扫描。

回归背景：旧实现用 pending[0] 的 module 扫描所有 pattern，
且任一 pattern 未命中就整批抛异常 —— 一个坏符号会拖垮其它符号。
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from infra.errors import SymbolResolveError
from infra.memory.symbols import (
    CSGAITEM_AOB,
    CSGAITEM_OFFSET,
    GAMEDATAMAN_AOB,
    SymbolTable,
)


class FakeBackend:
    """只实现符号解析需要的四个方法；aob_scan_multi 记录被请求的模块名。"""

    def __init__(self) -> None:
        self.mem: dict[int, int] = {}
        self.hits: dict[str, list[int]] = {}
        self.scanned_modules: list[str] = []

    def resolve_module_base(self, module_name: str) -> int:
        return 0x140000000

    def read_bytes(self, address: int, size: int) -> bytes:
        return bytes(self.mem.get(address + i, 0) for i in range(size))

    def aob_scan(self, module_name, pattern: str) -> list[int]:
        return list(self.hits.get(pattern, []))

    def aob_scan_multi(self, module_name, patterns: dict) -> dict:
        self.scanned_modules.append(module_name)
        return {name: list(self.hits.get(pat, [])) for name, pat in patterns.items()}

    def poke_i32(self, address: int, value: int) -> None:
        for i, b in enumerate(struct.pack("<i", value)):
            self.mem[address + i] = b


def _backend_with_only_csgaitem():
    """只让 csgaitem 命中，GameDataMan 故意不命中。"""
    b = FakeBackend()
    hit = 0x140001000
    b.hits[CSGAITEM_AOB] = [hit]
    b.hits[GAMEDATAMAN_AOB] = []
    base = hit + CSGAITEM_OFFSET
    disp = 0x1000
    b.poke_i32(base + 3, disp)  # RIP 位移位于 hit+offset+3
    return b, base + 7 + disp


def test_aob_miss_is_isolated_per_symbol():
    b, expected = _backend_with_only_csgaitem()
    st = SymbolTable(b)
    st.load_specs({})

    # csgaitem 必须正常解析 —— 旧实现会因 GameDataMan 未命中而整批失败
    assert st.resolve("csgaitem") == expected

    try:
        st.resolve("GameDataMan")
        raise AssertionError("GameDataMan 未命中时应抛 SymbolResolveError")
    except SymbolResolveError as exc:
        assert "AOB 未命中" in str(exc)


def test_aob_resolution_is_cached_per_symbol():
    b, expected = _backend_with_only_csgaitem()
    st = SymbolTable(b)
    st.load_specs({})
    assert st.resolve("csgaitem") == expected
    b.hits.clear()  # 清掉命中表：已缓存的符号不应再触发扫描
    assert st.resolve("csgaitem") == expected


def test_aob_grouped_by_module():
    """每个符号用自己的 module 扫描，而不是统一用 pending[0] 的。"""
    b = FakeBackend()
    b.hits[CSGAITEM_AOB] = [0x140001000]
    b.hits[GAMEDATAMAN_AOB] = [0x140002000]
    b.hits["AA BB"] = [0x200000000]
    b.poke_i32(0x140001000 + CSGAITEM_OFFSET + 3, 0)
    b.poke_i32(0x140002000 + 3, 0)

    st = SymbolTable(b)
    st.load_specs(
        {"other": {"kind": "aob_rip", "module": "other.exe", "aob": "AA BB", "aob_offset": 0}}
    )
    # 触发整批解析
    assert st.resolve("csgaitem") == 0x140001000 + CSGAITEM_OFFSET + 7
    assert st.resolve("other") == 0x200000000 + 7
    assert set(b.scanned_modules) == {"nightreign.exe", "other.exe"}


def test_invalidate_clears_manual_and_cached_values():
    """跨进程后旧的绝对地址必须失效，否则会静默用错地址。"""
    b, expected = _backend_with_only_csgaitem()
    st = SymbolTable(b)
    st.load_specs({})
    st.set_manual("csgaitem", 0xDEADBEEF)
    assert st.resolve("csgaitem") == 0xDEADBEEF
    st.invalidate()
    assert st.resolve("csgaitem") == expected
