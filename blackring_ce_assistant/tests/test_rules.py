# -*- coding: utf-8 -*-
"""规则校验测试。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from domain.enums import EffectCatalog, parse_effect_line
from domain.models import FieldWrite, Relic, RelicEffect, RelicSlotKind
from domain import rules


def _catalog():
    std = [
        parse_effect_line("7001400:ro448|STD|N|ATTACK|物理攻击力提升"),
        parse_effect_line("7001401:ro449|STD|N|CHARA|角色强化 +1"),
        parse_effect_line("7001402:ro450|BTH|N|NONE|物理攻击力提升 +2"),
        parse_effect_line("7001410:ro460|STD|N|ATTACK|火攻击力提升"),
        parse_effect_line("7010700:ro109|STD|N|CHARA|[女公爵] 连续匕首"),
    ]
    don = [
        parse_effect_line("6001400:ro378|DoN|Y|ATTACK|物理攻击力提升 +3"),
        parse_effect_line("6001401:ro379|DoN|N|ATTACK|物理攻击力提升 +4"),
        parse_effect_line("7001402:ro450|BTH|N|ATTACK|物理攻击力提升 +2"),
    ]
    debuff = [parse_effect_line("6820000:roN/A|DoN|N|CURSE|受到伤害时中毒值累积")]
    return EffectCatalog(std=std, don=don, debuff=debuff)


def _relic(kind=RelicSlotKind.STANDARD, ids=(7001400, -1, -1), debuffs=None):
    effects = [
        RelicEffect(slot_index=1, position=i, effect_id=ids[i] if i < len(ids) else -1)
        for i in range(3)
    ]
    return Relic(
        slot_index=1,
        kind=kind,
        effects=effects,
        debuffs=list(debuffs or []),
    )


def test_compatibility_blocks_wrong_slot():
    cat = _catalog()
    r = _relic(RelicSlotKind.STANDARD, ids=(6001400, -1, -1))  # DoN only
    v = rules.validate_compatibility(cat, r.kind, 6001400, "attr1")
    assert rules.has_block(v)


def test_roll_order_blocks_descending():
    cat = _catalog()
    # ro450 then ro448 -> 非法
    v = rules.validate_roll_order((7001402, 7001400, -1), cat)
    assert rules.has_block(v)
    v2 = rules.validate_roll_order((7001400, 7001401, 7001402), cat)
    assert not rules.has_block(v2)


def test_roll_group_conflict():
    cat = _catalog()
    # 两条 ATTACK 组冲突
    v = rules.validate_roll_group((7001400, 7001410, -1), cat)
    assert rules.has_block(v)


def test_req_debuff_blocks():
    cat = _catalog()
    v = rules.validate_req_debuff((6001400, -1, -1), cat, debuffs=[])
    assert rules.has_block(v)
    v2 = rules.validate_req_debuff((6001400, -1, -1), cat, debuffs=[6820000])
    assert not rules.has_block(v2)


def test_empty_effect_id_is_not_blocked():
    """回归：effect_id 0 与 -1 都是空位，兼容性规则不应阻断（恢复备份依赖此点）。"""
    cat = _catalog()
    assert not rules.has_block(rules.validate_compatibility(cat, RelicSlotKind.STANDARD, 0, "attr1"))
    assert not rules.has_block(rules.validate_compatibility(cat, RelicSlotKind.STANDARD, -1, "attr1"))
    assert rules.has_block(rules.validate_compatibility(cat, RelicSlotKind.STANDARD, 9999999, "attr1"))


def test_validate_proposal_ok():
    cat = _catalog()
    r = _relic(RelicSlotKind.STANDARD, ids=(7001400, -1, -1))
    writes = [FieldWrite(1, "attr2", -1, 7001401)]
    v = rules.validate_proposal(r, writes, cat)
    assert not rules.has_block(v)
