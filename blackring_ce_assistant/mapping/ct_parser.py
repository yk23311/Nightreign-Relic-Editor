# -*- coding: utf-8 -*-
"""解析 Cheat Engine .CT / .XML，导出遗物映射与枚举 JSON。

不依赖 CE，仅解析 XML 数据结构。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional
from xml.etree import ElementTree as ET

from domain.enums import parse_effect_line
from infra.errors import MappingError
# AOB 的唯一来源。导出器必须直接写入它：否则每次重新导出都会把 mapping.json 里
# 可用的 AOB 抹成 TODO 占位（历史坑 #7 的复发路径），只能靠 SymbolTable.load_specs
# 的兜底合并救命。
from infra.memory.symbols import CSGAITEM_AOB, CSGAITEM_OFFSET, GAMEDATAMAN_AOB

_RELIC_SLOT_RE = re.compile(r"第([1-6])个装备遗物")
_ATTR_RE = re.compile(r"^属性\s*([1-3])$")
_DEBUFF_RE = re.compile(r"减益效果\s*([1-3])")


def _t(el: Optional[ET.Element], tag: str) -> str:
    n = el.find(tag) if el is not None else None
    return (n.text or "").strip() if n is not None and n.text else ""


def unquote(s: str) -> str:
    s = (s or "").strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'“”":
        return s[1:-1].strip()
    return s


def load_ct(path: Path | str) -> ET.Element:
    p = Path(path)
    text = p.read_text(encoding="utf-8", errors="replace")
    return ET.fromstring(text)


def iter_entries(root: ET.Element):
    yield from root.iter("CheatEntry")


def parse_dropdown_items(entry: ET.Element) -> list[str]:
    dd = entry.find("DropDownList")
    if dd is None:
        return []
    raw = dd.text or ""
    for c in dd:
        raw += "\n" + (c.text or "")
    return [ln.strip() for ln in raw.splitlines() if ln.strip()]


def extract_enums(root: ET.Element) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for e in iter_entries(root):
        name = unquote(_t(e, "Description"))
        items = parse_dropdown_items(e)
        if not items:
            continue
        # 保留首次出现的同名完整列表（后出现的小列表可能只是 -1）
        if name in out and len(out[name]) >= len(items):
            continue
        parsed = []
        for line in items:
            meta = parse_effect_line(line)
            if meta:
                parsed.append(
                    {
                        "effect_id": meta.effect_id,
                        "name": meta.name,
                        "roll_order": meta.roll_order,
                        "compatibility": meta.compatibility,
                        "req_debuff": "Y" if meta.req_debuff else "N",
                        "roll_group": meta.roll_group,
                        "raw": meta.raw,
                    }
                )
            else:
                parsed.append({"effect_id": None, "name": line, "raw": line})
        out[name] = parsed
    return out


def extract_relic_slots(root: ET.Element) -> list[dict[str, Any]]:
    """从 遗物编辑器 组提取 6 槽字段。"""
    slots: dict[int, dict[str, Any]] = {}

    def walk(node: ET.Element, path: list[str]) -> None:
        desc = unquote(_t(node, "Description"))
        m = _RELIC_SLOT_RE.search(desc)
        if m:
            idx = int(m.group(1))
            kind = "DoN" if "黑夜" in desc else "STD"
            slot = slots.setdefault(
                idx,
                {
                    "index": idx,
                    "kind": kind,
                    "index_expr": "",
                    "fields": {},
                    "address_expr": "",
                },
            )
            slot["kind"] = kind
            for child in node.findall("CheatEntries/CheatEntry"):
                cdesc = unquote(_t(child, "Description"))
                addr = _t(child, "Address")
                offsets = [o.text.strip() for o in child.findall("Offsets/Offset") if o.text]
                am = _ATTR_RE.match(cdesc)
                dm = _DEBUFF_RE.search(cdesc)
                if am:
                    n = int(am.group(1))
                    # offsets 形如 ['+18', '8+8*[Gaitem]']
                    field_off = offsets[0] if offsets else ""
                    index_expr = offsets[1] if len(offsets) > 1 else ""
                    slot["index_expr"] = index_expr
                    slot["address_expr"] = addr
                    enum_name = "RelicIDStd" if kind == "STD" else "RelicIDDoN"
                    link = unquote(_t(child, "DropDownListLink"))
                    if link:
                        enum_name = link
                    slot["fields"][f"attr{n}"] = {
                        "offset": field_off,
                        "type": "u32",
                        "enum": enum_name,
                        "writable": True,
                    }
                elif dm:
                    n = int(dm.group(1))
                    field_off = offsets[0] if offsets else ""
                    index_expr = offsets[1] if len(offsets) > 1 else ""
                    if index_expr:
                        slot["index_expr"] = index_expr
                    slot["fields"][f"debuff{n}"] = {
                        "offset": field_off,
                        "type": "u32",
                        "enum": "RelicIDDebuff",
                        "writable": True,
                    }
            return
        for child in node.findall("CheatEntries/CheatEntry"):
            walk(child, path + [desc])

    walk(root, [])
    ordered = [slots[i] for i in sorted(slots)]
    return ordered


def export_data_files(ct_path: Path | str, out_dir: Path | str) -> dict[str, Any]:
    """从 CT 导出 mapping.json 与 effects_*.json。"""
    root = load_ct(ct_path)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    enums = extract_enums(root)
    slots = extract_relic_slots(root)

    def dump_enum(name: str, filename: str) -> int:
        items = enums.get(name, [])
        if not items:
            # 静默写出空数组的后果：程序能启动，但下拉框全空且没有任何报错，
            # 排查成本极高。宁可在这里直接失败。
            raise MappingError(
                f"CT 中未找到枚举「{name}」（即将导出到 {filename}）。"
                f"若 CT 改过 DropDownList 名称，请同步更新本模块的导出清单。"
            )
        (out / filename).write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        return len(items)

    counts = {
        "RelicIDStd": dump_enum("RelicIDStd", "effects_std.json"),
        "RelicIDDoN": dump_enum("RelicIDDoN", "effects_don.json"),
        "RelicIDDebuff": dump_enum("RelicIDDebuff", "effects_debuff.json"),
        "RelicID": dump_enum("RelicID", "effects_all.json"),
    }

    mapping = {
        "version": 1,
        "source_ct": Path(ct_path).name,
        "process_name": "nightreign.exe",
        "base": "csgaitem",
        "address_expr": "csgaitem",
        "symbols": {
            "csgaitem": {
                "kind": "aob_rip",
                "module": "nightreign.exe",
                "rva": None,
                "aob": CSGAITEM_AOB,
                "aob_offset": CSGAITEM_OFFSET,
                "note": "CSGaitem via AOB+RIP（与 infra/memory/symbols.py 同源）",
            },
            "gamedataman": {
                "kind": "aob_rip",
                "module": "nightreign.exe",
                "rva": None,
                "aob": GAMEDATAMAN_AOB,
                "aob_offset": 0,
                "note": "GameDataMan via AOB+RIP（与 infra/memory/symbols.py 同源）",
            },
            "Gaitem": {
                "kind": "alloc_or_resolve",
                "module": "nightreign.exe",
                "rva": None,
                "note": "6 x u32 槽 ID；由 GaitemCache 在目标进程分配后 set_manual 填充",
            },
        },
        "slots": slots,
    }
    (out / "mapping.json").write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"enum_counts": counts, "slots": len(slots), "out_dir": str(out)}
