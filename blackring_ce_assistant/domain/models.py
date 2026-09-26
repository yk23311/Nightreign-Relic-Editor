# -*- coding: utf-8 -*-
"""领域模型：遗物 / 词条 / 映射 / 预设 / 备份 / 应用配置。

仅供学习与离线测试；禁止在线作弊。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class RelicSlotKind(str, Enum):
    STANDARD = "STD"
    DEEP_NIGHT = "DoN"

    @property
    def label(self) -> str:
        """界面文案：普通 / 深夜"""
        return "普通" if self is RelicSlotKind.STANDARD else "深夜"


CATEGORY_ATTR = "attr"
CATEGORY_DEBUFF = "debuff"


@dataclass(frozen=True)
class EffectMeta:
    """CT 词条元数据（Effect_ID : Roll_Order | Compatibility | Req_Debuff | Roll_Group | Name）。

    category 由 EffectCatalog 按**来源列表**填（属性列表 -> attr，减益列表 -> debuff），
    不来自 CT 文本 —— CT 的行格式里没有这个字段。高级模式下界面靠它分组显示。
    """

    effect_id: int
    name: str
    roll_order: Optional[int]
    compatibility: str  # STD | BTH | DoN | N/A | ...
    req_debuff: bool
    roll_group: str
    raw: str = ""
    category: str = ""  # "attr" | "debuff"；未分类时为空
    # CT 全量表（RelicID）自带的组名，来自 "-1:--[攻击]--" 这类分组行。
    # 界面在高级模式下按它分组 —— 与 CE 里看到的分组完全一致。
    group: str = ""

    @property
    def is_attr(self) -> bool:
        return self.category == CATEGORY_ATTR

    @property
    def is_debuff(self) -> bool:
        return self.category == CATEGORY_DEBUFF

    @property
    def category_label(self) -> str:
        """界面上用于分组显示的标题。"""
        if self.category == CATEGORY_ATTR:
            return "属性词条"
        if self.category == CATEGORY_DEBUFF:
            return "减益词条"
        return "未分类"

    @property
    def is_header(self) -> bool:
        return self.effect_id is None or self.effect_id < 0

    @property
    def is_placeholder(self) -> bool:
        return bool(self.raw) and (self.name in ("(空)",) or self.name.startswith("--"))


@dataclass
class RelicEffect:
    slot_index: int  # 1..6
    position: int  # 0..2 -> 属性 1..3
    effect_id: int
    meta: Optional[EffectMeta] = None
    raw_address: Optional[int] = None
    editable: bool = True

    @property
    def field_name(self) -> str:
        return f"attr{self.position + 1}"


@dataclass
class Relic:
    slot_index: int  # 1..6
    kind: RelicSlotKind
    effects: list[RelicEffect] = field(default_factory=list)
    debuffs: list[int] = field(default_factory=list)  # 减益 1-3（可写）
    raw_addresses: dict[str, int] = field(default_factory=dict)
    notes: str = ""

    def effect_ids(self) -> tuple[int, int, int]:
        ids = [e.effect_id for e in sorted(self.effects, key=lambda x: x.position)]
        while len(ids) < 3:
            ids.append(-1)
        return (ids[0], ids[1], ids[2])


@dataclass
class Preset:
    name: str
    slot_kind: RelicSlotKind
    effect_ids: tuple[int, int, int]
    created_at: str
    debuff_ids: tuple[int, int, int] = (-1, -1, -1)
    notes: str = ""


@dataclass(frozen=True)
class FieldWrite:
    """一次待写入的字段提案。"""

    slot_index: int
    field_name: str  # attr1|attr2|attr3|debuff1|debuff2|debuff3
    old_value: int
    new_value: int
    resolved_address: Optional[int] = None

    @property
    def position(self) -> int:
        return {
            "attr1": 0, "attr2": 1, "attr3": 2,
            "debuff1": 0, "debuff2": 1, "debuff3": 2,
        }[self.field_name]

    @property
    def is_debuff(self) -> bool:
        return self.field_name.startswith("debuff")


@dataclass
class RuleViolation:
    rule_id: str
    severity: str  # "block" | "warn"
    message: str
    field: str = ""

    @property
    def is_block(self) -> bool:
        return self.severity == "block"


@dataclass
class WriteReport:
    ok: bool
    changed: list[tuple[int, str, int, int]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    rolled_back: bool = False
