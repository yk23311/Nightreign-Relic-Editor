# -*- coding: utf-8 -*-
"""外置映射配置加载 / 校验。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from infra.errors import MappingError
from infra.memory.symbols import CSGAITEM_AOB, CSGAITEM_OFFSET, GAMEDATAMAN_AOB


@dataclass
class SlotFieldMap:
    name: str  # attr1|attr2|attr3|debuff1...
    offset: str
    value_type: str = "u32"
    enum: str = ""
    writable: bool = False


@dataclass
class SlotMap:
    index: int  # 1..6
    kind: str  # STD | DoN
    index_expr: str
    fields: dict[str, SlotFieldMap] = field(default_factory=dict)


@dataclass
class MappingSet:
    version: int
    source_ct: str
    process_name: str
    address_expr: str
    symbols: dict[str, dict]
    slots: list[SlotMap]

    def slot(self, index: int) -> SlotMap:
        for s in self.slots:
            if s.index == index:
                return s
        raise MappingError(f"映射缺少槽位 {index}")


def load_mapping(path: Path | str) -> MappingSet:
    p = Path(path)
    if not p.exists():
        raise MappingError(f"映射文件不存在: {p}")
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise MappingError(f"映射 JSON 解析失败: {exc}") from exc
    return mapping_from_dict(raw)


def mapping_from_dict(raw: dict[str, Any]) -> MappingSet:
    try:
        slots_raw = raw["slots"]
        base = raw.get("base", raw.get("address_expr", raw.get("symbols", {}).get("csgaitem", {}).get("expr", "csgaitem")))
        if isinstance(base, dict):
            base = base.get("expr", "csgaitem")
        slots: list[SlotMap] = []
        for i, s in enumerate(slots_raw, start=1):
            fields = {}
            for fname, fraw in (s.get("fields") or {}).items():
                fields[fname] = SlotFieldMap(
                    name=fname,
                    offset=str(fraw.get("offset", "")),
                    value_type=fraw.get("type", "u32"),
                    enum=fraw.get("enum", ""),
                    writable=bool(fraw.get("writable", False)),
                )
            slots.append(
                SlotMap(
                    index=int(s.get("index", i)),
                    kind=str(s.get("kind", "STD")),
                    index_expr=str(s.get("index_expr", "")),
                    fields=fields,
                )
            )
        if len(slots) != 6:
            raise MappingError(f"映射必须包含 6 个槽位，实际 {len(slots)}")
        return MappingSet(
            version=int(raw.get("version", 1)),
            source_ct=str(raw.get("source_ct", "")),
            process_name=str(raw.get("process_name", "nightreign.exe")),
            address_expr=str(base),
            symbols=raw.get("symbols", {}) or {},
            slots=slots,
        )
    except KeyError as exc:
        raise MappingError(f"映射缺少字段: {exc}") from exc


def default_mapping_dict() -> dict[str, Any]:
    """与 CT 分析一致的一期默认映射（属性 1–3 可写）。"""
    def fields(kind_enum: str) -> dict:
        return {
            "attr1": {"offset": "+18", "type": "u32", "enum": kind_enum, "writable": True},
            "attr2": {"offset": "+1c", "type": "u32", "enum": kind_enum, "writable": True},
            "attr3": {"offset": "+20", "type": "u32", "enum": kind_enum, "writable": True},
            "debuff1": {"offset": "40", "type": "u32", "enum": "RelicIDDebuff", "writable": True},
            "debuff2": {"offset": "44", "type": "u32", "enum": "RelicIDDebuff", "writable": True},
            "debuff3": {"offset": "48", "type": "u32", "enum": "RelicIDDebuff", "writable": True},
        }

    slots = []
    index_exprs = [
        "8+8*[Gaitem]",
        "8+8*[Gaitem+4]",
        "8+8*[Gaitem+8]",
        "8+8*[Gaitem+c]",
        "8+8*[Gaitem+10]",
        "8+8*[Gaitem+14]",
    ]
    for i in range(6):
        kind = "STD" if i < 3 else "DoN"
        enum = "RelicIDStd" if i < 3 else "RelicIDDoN"
        slots.append(
            {
                "index": i + 1,
                "kind": kind,
                "index_expr": index_exprs[i],
                "fields": fields(enum),
            }
        )

    return {
        "version": 1,
        "source_ct": "Elden Ring Nighreign - Hexinton 1.1.0 汉化版.CT",
        "process_name": "nightreign.exe",
        "base": "csgaitem",
        "address_expr": "csgaitem",
        # 直接带上内置 AOB：只有 rva= TODO 占位的 mapping 会被 load_specs 判定为
        # 「不可用」而回退到内置值，虽然能跑，但会让 mapping.json 看起来像坏的。
        "symbols": {
            "csgaitem": {
                "kind": "aob_rip",
                "module": "nightreign.exe",
                "rva": None,
                "aob": CSGAITEM_AOB,
                "aob_offset": CSGAITEM_OFFSET,
                "note": "CSGaitem via AOB+RIP",
            },
            "gamedataman": {
                "kind": "aob_rip",
                "module": "nightreign.exe",
                "rva": None,
                "aob": GAMEDATAMAN_AOB,
                "aob_offset": 0,
                "note": "GameDataMan via AOB+RIP",
            },
            "Gaitem": {
                "kind": "alloc_or_resolve",
                "module": "nightreign.exe",
                "rva": None,
                "note": "6 x u32 槽 ID 表；由 GaitemCache 分配后 set_manual 填充",
            },
        },
        "slots": slots,
    }
