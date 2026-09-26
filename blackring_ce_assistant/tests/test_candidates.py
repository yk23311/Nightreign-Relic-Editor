# -*- coding: utf-8 -*-
"""候选词条（高级模式跨选）与手动 ID 写入测试。

需求来源（2026-09-26 确认）：
  - 高级模式下，属性字段要能选到减益词条，减益字段要能选到属性词条；
  - 非高级模式维持原有的严格过滤，行为不得变化；
  - 允许在高级模式下手动输入任意 ID 写入。
"""
from __future__ import annotations

import collections
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from domain.enums import EffectCatalog, parse_effect_line
from domain.models import CATEGORY_ATTR, CATEGORY_DEBUFF, FieldWrite, RelicSlotKind
from mapping.mapping_config import default_mapping_dict, mapping_from_dict
from tests.test_relic_service import _make_service


def _catalog():
    return EffectCatalog(
        std=[parse_effect_line("7001400:ro448|STD|N|ATTACK|标准属性")],
        don=[parse_effect_line("6001400:ro378|DoN|N|ATTACK|深夜属性")],
        debuff=[parse_effect_line("6820000:roN/A|DoN|N|CURSE|中毒减益")],
    )


def test_non_advanced_keeps_strict_filtering():
    """非高级模式的行为必须与改动前完全一致。"""
    cat = _catalog()
    std_attr = cat.candidates(RelicSlotKind.STANDARD, "attr1", advanced=False)
    std_db = cat.candidates(RelicSlotKind.STANDARD, "debuff1", advanced=False)
    don_attr = cat.candidates(RelicSlotKind.DEEP_NIGHT, "attr1", advanced=False)
    assert [m.effect_id for m in std_attr] == [7001400], "普通槽属性字段只给 STD 属性词条"
    assert [m.effect_id for m in std_db] == [6820000]
    assert [m.effect_id for m in don_attr] == [6001400], "深夜槽属性字段只给 DoN 属性词条"


def test_advanced_attr_field_offers_debuffs():
    """高级模式：属性字段也要能选到减益词条。"""
    cat = _catalog()
    got = {m.effect_id for m in cat.candidates(RelicSlotKind.STANDARD, "attr1", advanced=True)}
    assert got == {7001400, 6001400, 6820000}


def test_advanced_debuff_field_offers_attrs():
    """高级模式：减益字段也要能选到属性词条。"""
    cat = _catalog()
    got = {m.effect_id for m in cat.candidates(RelicSlotKind.STANDARD, "debuff1", advanced=True)}
    assert got == {7001400, 6001400, 6820000}


def test_category_tagging():
    """category 由来源列表决定，供界面分组显示。"""
    cat = _catalog()
    assert cat.get(7001400).category == CATEGORY_ATTR
    assert cat.get(6001400).category == CATEGORY_ATTR
    assert cat.get(6820000).category == CATEGORY_DEBUFF
    assert cat.get(6820000).category_label == "减益词条"
    assert cat.get(7001400).category_label == "属性词条"


def test_full_list_is_deduplicated():
    """同一 ID 出现在多个来源列表时必须去重（真实数据 STD∩DoN 有 200 条重叠）。"""
    cat = EffectCatalog(
        std=[parse_effect_line("7001402:ro450|BTH|N|NONE|通用")],
        don=[parse_effect_line("7001402:ro450|BTH|N|NONE|通用")],
        debuff=[parse_effect_line("6820000:roN/A|DoN|N|CURSE|中毒")],
    )
    allm = cat.list_all_entries()
    assert len(allm) == 2, [m.effect_id for m in allm]


def test_group_labels_come_from_ct_separators():
    """分组行 "-1:--[攻击]--" 必须解析成组名；分组行与「(空)」都不得进候选。"""
    rows = [
        parse_effect_line("-1:(空)"),
        parse_effect_line("-1:--[攻击]--"),
        parse_effect_line("7001400:ro448|STD|N|ATTACK|甲"),
        parse_effect_line("7001401:ro449|STD|N|ATTACK|乙"),
        parse_effect_line("-1:--[非法]--"),
        parse_effect_line("10000:N/A|N/A|N|NONE|丙"),
    ]
    cat = EffectCatalog(all_entries=rows)
    got = cat.list_all_entries()
    assert [m.effect_id for m in got] == [7001400, 7001401, 10000]
    assert [m.group for m in got] == ["攻击", "攻击", "非法"]


def test_real_data_full_list_matches_ct():
    """真实枚举：高级模式候选必须与 CT「高级模式遗物编辑器」在 CE 里可选的完全一致。

    RelicID 全量表 1076 条、零重复。其中前 7 组共 497 条正是安全模式三张表的内容，
    第 8 组「非法」579 条是 CT 自己标为 compatibility=N/A 的条目，与已知枚举零重叠。
    """
    d = ROOT / "data"
    if not (d / "effects_all.json").exists():
        return
    cat = EffectCatalog.from_data_dir(d)
    allm = cat.list_all_entries()
    assert len(allm) == 1076, len(allm)
    assert len({m.effect_id for m in allm}) == len(allm), "不得有重复 ID"

    groups = collections.Counter(m.group for m in allm)
    assert groups["非法"] == 579, groups
    valid = sum(v for k, v in groups.items() if k != "非法")
    assert valid == 497, f"前 7 组应恰好等于安全模式三张表的分量，实际 {valid}"
    assert set(groups) == {"攻击", "角色", "装备", "FP/HP 恢复", "潜伏之力", "杂项", "诅咒", "非法"}

    # 「非法」组的 compatibility 全部是 N/A —— 这是 CT 作者自己的判定，不是我们的推测
    assert {m.compatibility for m in allm if m.group == "非法"} == {"N/A"}

    # 前 7 组合起来必须完整覆盖安全模式的 497 条（无遗漏、无多余）
    valid_ids = {m.effect_id for m in allm if m.group != "非法"}
    safe_ids = (
        {m.effect_id for m in cat.list_for_slot(RelicSlotKind.STANDARD)}
        | {m.effect_id for m in cat.list_for_slot(RelicSlotKind.DEEP_NIGHT)}
        | {m.effect_id for m in cat.list_debuffs()}
    )
    assert valid_ids == safe_ids


def test_full_list_does_not_override_known_entries():
    """全量表只补齐 _by_id，不覆盖前三个枚举。

    实测有 7 个 ID 在 std 与 RelicID 里名称不同；若让全量表覆盖，
    catalog.get() 的返回值会无声变化，进而影响显示与校验文案。
    """
    std = [parse_effect_line("7001400:ro448|STD|N|ATTACK|安全模式的名字")]
    allrows = [
        parse_effect_line("-1:--[攻击]--"),
        parse_effect_line("7001400:ro448|STD|N|ATTACK|全量表的名字"),
        parse_effect_line("9999999:N/A|N/A|N|NONE|只在全量表"),
    ]
    cat = EffectCatalog(std=std, all_entries=allrows)
    assert cat.get(7001400).name == "安全模式的名字"
    assert cat.get(9999999).name == "只在全量表"


def test_service_candidates_for_uses_slot_kind():
    """服务层出口：槽 1 是普通、槽 4 是深夜，取到的候选应不同。"""
    mem, svc = _make_service()
    std = {m.effect_id for m in svc.candidates_for(1, "attr1", advanced=False)}
    don = {m.effect_id for m in svc.candidates_for(4, "attr1", advanced=False)}
    assert 7001400 in std and 6001400 not in std
    assert 6001400 in don and 7001400 not in don
    adv = {m.effect_id for m in svc.candidates_for(1, "attr1", advanced=True)}
    assert adv == std | don | {6820000}


def test_advanced_mode_writes_arbitrary_manual_id():
    """手动 ID：高级模式下必须能写入任意 u32（这是本功能存在的意义）。"""
    mem, svc = _make_service()
    weird = 1234567
    rep = svc.apply([FieldWrite(1, "attr1", 7001400, weird)], allow_raw=True, skip_rules=True)
    assert rep.ok, rep.errors
    assert mem.read_u32(0xA018) == weird


def test_non_advanced_mode_still_blocks_arbitrary_id():
    """同一个任意 ID，在非高级模式下仍必须被拦下。"""
    from infra.errors import ValidationError

    mem, svc = _make_service()
    try:
        svc.apply([FieldWrite(1, "attr1", 7001400, 1234567)])
        raise AssertionError("非高级模式下任意 ID 应被拒绝")
    except ValidationError:
        pass
