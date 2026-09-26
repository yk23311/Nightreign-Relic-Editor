# -*- coding: utf-8 -*-
"""遗物写入前规则校验（默认全部为阻断规则，已与需求确认）。"""
from __future__ import annotations

from typing import Iterable, Optional, Sequence

from .enums import EffectCatalog
from .models import (
    FieldWrite,
    Relic,
    RelicSlotKind,
    RuleViolation,
)

BLOCK = "block"
WARN = "warn"


def _meta(catalog: EffectCatalog, effect_id: int):
    return catalog.get(effect_id)


def validate_compatibility(
    catalog: EffectCatalog,
    kind: RelicSlotKind,
    effect_id: int,
    field: str,
) -> list[RuleViolation]:
    m = _meta(catalog, effect_id)
    if m is None:
        # 空位统一按 effect_id <= 0 处理（与 roll_order/roll_group/req_debuff 一致）。
        # CT 枚举中不存在 effect_id == 0 的条目，0 与 -1/0xFFFFFFFF 同为「空」。
        if effect_id is None or effect_id <= 0:
            return []
        return [
            RuleViolation(
                rule_id="compatibility",
                severity=BLOCK,
                message=f"未知效果 ID {effect_id}，拒绝写入（请勿编造词条）",
                field=field,
            )
        ]
    comp = (m.compatibility or "").upper()
    if kind == RelicSlotKind.STANDARD:
        ok = comp in ("STD", "BTH")
        want = "STD 或 BTH"
    else:
        ok = comp in ("DON", "BTH")
        want = "DoN 或 BTH"
    if not ok:
        return [
            RuleViolation(
                rule_id="compatibility",
                severity=BLOCK,
                message=f"「{m.name}」兼容性 {comp or '?'} 不适用于{kind.value}槽（需要 {want}）",
                field=field,
            )
        ]
    return []


def validate_roll_order(ids: Sequence[int], catalog: EffectCatalog) -> list[RuleViolation]:
    """属性 1→3 的 Roll_Order 必须非递减；-1 视为空位（仅允许尾部连续空）。"""
    violations: list[RuleViolation] = []
    orders: list[Optional[int]] = []
    for i, eid in enumerate(ids):
        if eid is None or eid <= 0:
            orders.append(None)
            continue
        m = _meta(catalog, eid)
        if m is None:
            # 未知 ID 不参与顺序（validate_compatibility 会拦截）
            orders.append(None)
        elif m.roll_order is None:
            orders.append(0)
        else:
            orders.append(m.roll_order)

    # 空位只能在尾部
    seen_empty = False
    for i, o in enumerate(orders):
        if o is None:
            seen_empty = True
        elif seen_empty:
            violations.append(
                RuleViolation(
                    rule_id="roll_order",
                    severity=BLOCK,
                    message=f"属性 {i + 1} 有词条，但前面存在空位（请从属性 1 起连续填写，或清空后面）",
                    field=f"attr{i + 1}",
                )
            )

    # 非递减
    for i in range(1, 3):
        a, b = orders[i - 1], orders[i]
        if a is None or b is None:
            continue
        if b < a:
            violations.append(
                RuleViolation(
                    rule_id="roll_order",
                    severity=BLOCK,
                    message=f"Roll_Order 须从低到高：属性 {i}={a} 不应大于属性 {i + 1}={b}",
                    field=f"attr{i + 1}",
                )
            )
    return violations


def validate_roll_group(ids: Sequence[int], catalog: EffectCatalog) -> list[RuleViolation]:
    """同槽非 NONE 组不得重复。"""
    violations: list[RuleViolation] = []
    seen: dict[str, int] = {}
    for i, eid in enumerate(ids):
        if eid is None or eid <= 0:
            continue
        m = _meta(catalog, eid)
        if not m or not m.roll_group or m.roll_group == "NONE":
            continue
        key = m.roll_group
        if key in seen:
            violations.append(
                RuleViolation(
                    rule_id="roll_group",
                    severity=BLOCK,
                    message=f"抽取组 {key} 冲突：属性 {seen[key] + 1} 与属性 {i + 1} 同组",
                    field=f"attr{i + 1}",
                )
            )
        else:
            seen[key] = i
    return violations


def validate_req_debuff(
    ids: Sequence[int],
    catalog: EffectCatalog,
    debuffs: Sequence[int],
) -> list[RuleViolation]:
    """Req_Debuff=Y 的效果必须槽上已有减益（-1 视为无）。"""
    violations: list[RuleViolation] = []
    has_debuff = any(d is not None and d >= 0 for d in debuffs)
    for i, eid in enumerate(ids):
        if eid is None or eid <= 0:
            continue
        m = _meta(catalog, eid)
        if m and m.req_debuff and not has_debuff:
            violations.append(
                RuleViolation(
                    rule_id="req_debuff",
                    severity=BLOCK,
                    message=f"「{m.name}」需要搭配减益效果，但当前槽没有任何减益；请在右侧「减益 1–3」中同时选一个减益",
                    field=f"attr{i + 1}",
                )
            )
    return violations


def validate_proposal(
    relic: Relic,
    writes: Iterable[FieldWrite],
    catalog: EffectCatalog,
    *,
    allow_raw: bool = False,
    skip_rules: bool = False,
) -> list[RuleViolation]:
    """校验一组对单槽的写入提案。skip_rules=True（高级模式）时取消合法性检查。

    注：RNG 安全门已于 0.6.0 按用户要求彻底移除（含 check_rng 参数）。
    现在程序不再阻止你修改唯一遗物 —— 这件事回到文档里由使用者自行确认。
    """
    if skip_rules:
        return []
    violations: list[RuleViolation] = []
    proposed = {e.position: e.effect_id for e in relic.effects}
    proposed_debuff = {i: (relic.debuffs[i] if i < len(relic.debuffs) else -1) for i in range(3)}
    write_list = list(writes)

    debuff_ids = {m.effect_id for m in catalog.list_debuffs()}

    for w in write_list:
        if w.is_debuff:
            proposed_debuff[w.position] = w.new_value
            if w.new_value is not None and w.new_value >= 0 and w.new_value not in debuff_ids:
                if not allow_raw:
                    violations.append(
                        RuleViolation(
                            rule_id="debuff_range",
                            severity=BLOCK,
                            message=f"减益 ID {w.new_value} 不在合法减益列表中（-1 表示禁用）",
                            field=w.field_name,
                        )
                    )
            continue
        proposed[w.position] = w.new_value
        if not allow_raw and w.new_value is not None and w.new_value > 0:
            if w.new_value not in catalog.allowed_for_slot(relic.kind) and catalog.get(w.new_value) is None:
                violations.append(
                    RuleViolation(
                        rule_id="value_range",
                        severity=BLOCK,
                        message=f"效果 ID {w.new_value} 不在 {relic.kind.label} 合法列表中",
                        field=w.field_name,
                    )
                )
        violations.extend(
            validate_compatibility(catalog, relic.kind, w.new_value, w.field_name)
        )

    ids = (proposed.get(0, -1), proposed.get(1, -1), proposed.get(2, -1))
    debuffs = (proposed_debuff.get(0, -1), proposed_debuff.get(1, -1), proposed_debuff.get(2, -1))
    violations.extend(validate_roll_order(ids, catalog))
    violations.extend(validate_roll_group(ids, catalog))
    violations.extend(validate_req_debuff(ids, catalog, debuffs))
    return violations


def has_block(violations: Sequence[RuleViolation]) -> bool:
    return any(v.is_block for v in violations)
