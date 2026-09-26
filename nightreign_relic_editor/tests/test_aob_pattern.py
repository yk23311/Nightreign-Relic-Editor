# -*- coding: utf-8 -*-
"""AOB 通配符解析测试（防止 ?? 被加倍）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from infra.memory.aob import parse_aob_pattern, scan_buffer


def test_parse_aob_double_question():
    # '??' 必须是 1 字节通配，绝不能变成 2 字节
    p = parse_aob_pattern("48 8B 0D ?? ?? ?? ?? 48 85 C9")
    assert len(p) == 10
    assert p[0] == 0x48
    assert p[3] is None
    assert p[6] is None
    assert p[7] == 0x48


def test_parse_aob_compact_and_spaces():
    a = parse_aob_pattern("48 8B 0D ???????? 48")
    b = parse_aob_pattern("488B0D????????48")
    assert a == b
    assert len(a) == 8
    assert a[3] is None and a[6] is None


def test_csgaitem_aob_length():
    from infra.memory.symbols import CSGAITEM_AOB

    p = parse_aob_pattern(CSGAITEM_AOB)
    # 48 8D 44 24 40 | 48 89 44 24 50 | 8B 02 | 89 44 24 40 | 48 8B 0D ?? ?? ?? ?? | 48 85 C9
    # 5+5+2+4+7+3 = 26
    assert len(p) == 26, len(p)


def test_aob_scan_finds_pattern():
    from infra.memory.aob import parse_aob_pattern, scan_buffer

    buf = bytes([0x90, 0x48, 0x8B, 0x0D, 0x11, 0x22, 0x33, 0x44, 0x48, 0x85, 0xC9, 0x90])
    p = parse_aob_pattern("48 8B 0D ?? ?? ?? ?? 48 85 C9")
    assert len(p) == 10
    hits = scan_buffer(buf, p)
    assert hits == [1]
