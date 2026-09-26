# -*- coding: utf-8 -*-
"""CT 解析与枚举加载测试。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from domain.enums import load_effect_list, parse_effect_line
from mapping.mapping_config import default_mapping_dict, mapping_from_dict


def test_parse_effect_line_full():
    m = parse_effect_line("7010700 : ro109|STD|N|CHARA|[女公爵] 连续匕首")
    assert m is not None
    assert m.effect_id == 7010700
    assert m.roll_order == 109
    assert m.compatibility == "STD"
    assert m.req_debuff is False
    assert m.roll_group == "CHARA"
    assert "女公爵" in m.name


def test_parse_debuff_line():
    m = parse_effect_line("6820000:roN/A|DoN|N|CURSE|受到伤害时中毒值累积")
    assert m.effect_id == 6820000
    assert m.compatibility == "DON"
    assert m.req_debuff is False


def test_default_mapping_has_6_slots():
    ms = mapping_from_dict(default_mapping_dict())
    assert len(ms.slots) == 6
    s1 = ms.slot(1)
    assert s1.kind == "STD"
    assert s1.fields["attr1"].offset == "+18"
    assert s1.fields["attr1"].writable is True
    assert s1.fields["debuff1"].writable is True
    assert s1.index_expr == "8+8*[Gaitem]"
    s6 = ms.slot(6)
    assert s6.kind == "DoN"
    assert "Gaitem+14" in s6.index_expr


def test_load_exported_effects_if_present():
    p = ROOT / "data" / "effects_std.json"
    if p.exists():
        items = load_effect_list(p)
        assert len(items) > 10
