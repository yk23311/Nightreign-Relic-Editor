# -*- coding: utf-8 -*-
"""实机诊断：对 nightreign.exe 测 AOB 符号与遗物读取。

用法: .venv\\Scripts\\python.exe tools_diag_live.py
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from infra.memory.symbols import (
    CSGAITEM_AOB,
    CSGAITEM_OFFSET,
    GAMEDATAMAN_AOB,
    GaitemCache,
    SymbolTable,
    DEFAULT_SPECS,
)
from infra.memory.win_process import WinProcessBackend, parse_aob_pattern
from mapping.mapping_config import load_mapping
from app.services.relic_service import RelicService
from domain.enums import EffectCatalog


def main() -> int:
    print("=== parse patterns ===")
    for name, raw in [("CSGaitem", CSGAITEM_AOB), ("GameDataMan", GAMEDATAMAN_AOB)]:
        p = parse_aob_pattern(raw)
        print(f"{name}: {len(p)} bytes, wildcards={sum(1 for x in p if x is None)}")

    mem = WinProcessBackend()
    print("\n=== attach nightreign.exe ===")
    try:
        info = mem.attach("nightreign.exe")
        print(f"pid={info.pid} base=0x{info.module_base:X} attached={info.is_attached}")
    except Exception as exc:
        print("ATTACH FAIL:", exc)
        traceback.print_exc()
        return 1

    mod = "nightreign.exe"
    base = mem.resolve_module_base(mod)
    size = mem.module_size(mod)
    print(f"module base=0x{base:X} size=0x{size:X} ({size/1024/1024:.1f} MB)")

    print("\n=== AOB scan ===")
    for name, aob, off in [
        ("CSGaitem", CSGAITEM_AOB, CSGAITEM_OFFSET),
        ("GameDataMan", GAMEDATAMAN_AOB, 0),
    ]:
        try:
            hits = mem.aob_scan(mod, aob)
            print(f"{name}: hits={len(hits)} " + " ".join(f"0x{h:X}" for h in hits[:5]))
            if hits:
                hit = hits[0] + off
                disp = int.from_bytes(mem.read_bytes(hit + 3, 4), "little", signed=True)
                sym = (hit + 7 + disp) & 0xFFFFFFFFFFFFFFFF
                print(f"  hit0+off=0x{hit:X}  RIP symbol=0x{sym:X}")
                try:
                    ptr = mem.read_u64(sym)
                    print(f"  *[symbol]=0x{ptr:X}")
                except Exception as e:
                    print(f"  read ptr fail: {e}")
        except Exception as exc:
            print(f"{name} FAIL: {exc}")
            traceback.print_exc()

    print("\n=== RelicService refresh ===")
    try:
        mapping = load_mapping(ROOT / "data" / "mapping.json")
        catalog = EffectCatalog.from_data_dir(ROOT / "data")
        svc = RelicService(mem, mapping, catalog, readonly_preview=True)
        info = svc.attach()
        print(f"attach ok pid={info.pid}")
        relics = svc.refresh_slots()
        for r in relics:
            ids = r.effect_ids()
            names = []
            for eid in ids:
                m = catalog.get(eid) if eid > 0 else None
                names.append(m.name if m else (f"id={eid}" if eid > 0 else "空"))
            print(f"  槽{r.slot_index} {r.kind.value}: {ids}  {[n for n in names]}  addr_attr1=0x{r.raw_addresses.get('attr1', 0):X}")
    except Exception as exc:
        print("RELIC FAIL:", exc)
        traceback.print_exc()
        return 2

    print("\nOK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
