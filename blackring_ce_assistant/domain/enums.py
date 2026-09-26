# -*- coding: utf-8 -*-
"""词条枚举目录：从 JSON 加载 CT DropDownList 数据。"""
from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Iterable, Optional

from .models import CATEGORY_ATTR, CATEGORY_DEBUFF, EffectMeta, RelicSlotKind

_RO_RE = re.compile(r"ro(\d+)", re.I)
# CT 全量表用 "-1:--[攻击]--" 这类行做分组标题
_GROUP_RE = re.compile(r"^--\[(.+)\]--$")


def _group_label(meta: EffectMeta) -> Optional[str]:
    """非分组行返回 None；分组行返回组名（如 '攻击'）。"""
    if meta.effect_id is not None and meta.effect_id >= 0:
        return None
    m = _GROUP_RE.match((meta.name or "").strip())
    return m.group(1) if m else None


def parse_effect_line(line: str) -> Optional[EffectMeta]:
    """解析一行 ``id:label`` 或 ``id:roN|COMP|Y/N|GROUP|Name``。"""
    line = (line or "").strip()
    if not line or ":" not in line:
        return None
    id_s, label = line.split(":", 1)
    id_s = id_s.strip()
    label = label.strip()
    try:
        eid = int(id_s, 0)
    except ValueError:
        return None

    roll_order: int | None = None
    compatibility = ""
    req_debuff = False
    roll_group = ""
    name = label

    if "|" in label:
        parts = [p.strip() for p in label.split("|")]
        if len(parts) >= 2:
            ro = _RO_RE.fullmatch(parts[0]) or _RO_RE.search(parts[0])
            if ro:
                roll_order = int(ro.group(1))
            elif parts[0].upper() in ("RO", "RO N/A", "N/A"):
                roll_order = None
            compatibility = parts[1].upper() if parts[1] else ""
            req_debuff = parts[2].upper() == "Y" if len(parts) > 2 else False
            roll_group = parts[3] if len(parts) > 3 else ""
            name = parts[-1]
        else:
            name = label
    else:
        name = label

    return EffectMeta(
        effect_id=eid,
        name=name,
        roll_order=roll_order,
        compatibility=compatibility,
        req_debuff=req_debuff,
        roll_group=roll_group.upper() if roll_group else "",
        raw=line,
    )


def load_effect_list(path: Path | str) -> list[EffectMeta]:
    """从 JSON 数组（raw 行）或已结构化数组加载。"""
    p = Path(path)
    data = json.loads(p.read_text(encoding="utf-8"))
    out: list[EffectMeta] = []
    for item in data:
        if isinstance(item, str):
            meta = parse_effect_line(item)
        elif isinstance(item, dict) and "raw" in item and "name" not in item:
            meta = parse_effect_line(item["raw"])
        elif isinstance(item, dict) and "effect_id" in item:
            meta = EffectMeta(
                effect_id=item.get("effect_id"),
                name=item.get("name") or item.get("label") or "",
                roll_order=_coerce_ro(item.get("roll_order")),
                compatibility=(item.get("compatibility") or "").upper(),
                req_debuff=str(item.get("req_debuff", "")).upper() == "Y",
                roll_group=(item.get("roll_group") or "").upper(),
                raw=item.get("raw") or "",
            )
        else:
            continue
        if meta is not None:
            out.append(meta)
    return out


def _coerce_ro(v) -> Optional[int]:
    if v is None:
        return None
    if isinstance(v, int):
        return v
    s = str(v)
    m = _RO_RE.search(s)
    if m:
        return int(m.group(1))
    try:
        return int(s)
    except ValueError:
        return None


class EffectCatalog:
    """按槽类型查询合法词条。"""

    def __init__(
        self,
        std: Iterable[EffectMeta] | None = None,
        don: Iterable[EffectMeta] | None = None,
        debuff: Iterable[EffectMeta] | None = None,
        all_entries: Iterable[EffectMeta] | None = None,
    ) -> None:
        self._by_id: dict[int, EffectMeta] = {}
        self._std: list[EffectMeta] = []
        self._don: list[EffectMeta] = []
        self._debuff: list[EffectMeta] = []
        self._all: list[EffectMeta] = []

        for m in std or []:
            self._add(self._std, m, CATEGORY_ATTR)
        for m in don or []:
            self._add(self._don, m, CATEGORY_ATTR)
        for m in debuff or []:
            self._add(self._debuff, m, CATEGORY_DEBUFF)
        # 全量表（CT 的 RelicID）最后加入。它**只补齐** _by_id 里还没有的 ID，
        # 不覆盖前三个枚举 —— 实测有 7 个 ID 在 std 与 RelicID 里名称不同，
        # 覆盖会让既有显示与校验口径发生变化，属于无声的行为变更。
        self._add_all(all_entries or [])

    def _add(self, bucket: list[EffectMeta], meta: EffectMeta, category: str) -> None:
        """按来源列表给条目打 category，并保证目录里同一 ID 只有一个实例。"""
        tagged = meta if meta.category == category else replace(meta, category=category)
        bucket.append(tagged)
        self._index(tagged)

    def _add_all(self, entries: Iterable[EffectMeta]) -> None:
        """按 CT 自带的分组行切分全量表，并给每条打上 group。"""
        current = ""
        for m in entries:
            label = _group_label(m)
            if label is not None:
                current = label
                continue
            if not self._usable(m):
                continue
            tagged = m if m.group == current else replace(m, group=current)
            self._all.append(tagged)
            self._index(tagged, override=False)

    def _index(self, m: EffectMeta, *, override: bool = True) -> None:
        if m.effect_id is None or m.effect_id < 0:
            return
        if not override and m.effect_id in self._by_id:
            return
        self._by_id[m.effect_id] = m

    @staticmethod
    def _usable(m: EffectMeta) -> bool:
        """可写入的条目：排除表头行、占位行与负 ID。"""
        return (
            not m.is_header
            and not m.is_placeholder
            and m.effect_id is not None
            and m.effect_id >= 0
        )

    @classmethod
    def from_data_dir(cls, data_dir: Path | str) -> "EffectCatalog":
        d = Path(data_dir)

        def optional(name: str) -> list[EffectMeta]:
            f = d / name
            return load_effect_list(f) if f.exists() else []

        return cls(
            std=optional("effects_std.json"),
            don=optional("effects_don.json"),
            debuff=optional("effects_debuff.json"),
            all_entries=optional("effects_all.json"),
        )

    def get(self, effect_id: int) -> Optional[EffectMeta]:
        return self._by_id.get(effect_id)

    def list_for_slot(self, kind: RelicSlotKind) -> list[EffectMeta]:
        """本槽类型的可编辑属性词条。"""
        src = self._std if kind == RelicSlotKind.STANDARD else self._don
        return [m for m in src if self._usable(m)]

    def list_debuffs(self) -> list[EffectMeta]:
        return [m for m in self._debuff if self._usable(m)]

    def list_all_entries(self) -> list[EffectMeta]:
        """高级模式的候选 —— **与 CT「高级模式遗物编辑器」在 CE 里可选的完全一致**。

        即 RelicID 全量表：1076 个唯一 ID、零重复，完整包含 std/don/debuff 的 497 条，
        另有 579 条只存在于全量表。每条带 group（CT 自带的 8 组：
        攻击 / 角色 / 装备 / FP·HP 恢复 / 潜伏之力 / 杂项 / 诅咒 / 非法），
        界面按它分组 —— 也就是 CE 里看到的分组。

        没有全量表时（合成小目录、老数据）退回三表并集，保证调用方逻辑不变。
        """
        if self._all:
            return list(self._all)
        out: list[EffectMeta] = []
        seen: set[int] = set()
        for src in (self._std, self._don, self._debuff):
            for m in src:
                if not self._usable(m) or m.effect_id in seen:
                    continue
                seen.add(m.effect_id)
                out.append(m)
        return out

    def candidates(
        self, kind: RelicSlotKind, field_name: str, *, advanced: bool
    ) -> list[EffectMeta]:
        """某个字段的候选词条 —— **界面取候选的唯一入口**（新界面复用同一出口）。

        非高级模式：字段语义严格 —— 属性字段只给本槽类型的属性词条，
        减益字段只给减益词条，与既有行为完全一致。
        高级模式：给全量，于是属性字段也能选减益、减益字段也能选属性；
        界面按 EffectMeta.category 分组显示。
        """
        if advanced:
            return self.list_all_entries()
        if field_name.startswith("debuff"):
            return self.list_debuffs()
        return self.list_for_slot(kind)

    def allowed_for_slot(self, kind: RelicSlotKind) -> set[int]:
        return {m.effect_id for m in self.list_for_slot(kind)}
