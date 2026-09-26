# -*- coding: utf-8 -*-
"""深度诊断：核对槽对象字段与 Gaitem 缓存。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from app.services.relic_service import RelicService
from domain.enums import EffectCatalog
from infra.memory.win_process import WinProcessBackend
from mapping.mapping_config import load_mapping


def hexdump(mem, addr, n=64):
    raw = mem.read_bytes(addr, n)
    for i in range(0, n, 16):
        chunk = raw[i : i + 16]
        hx = " ".join(f"{b:02X}" for b in chunk)
        print(f"  {addr+i:016X}  {hx}")


def main() -> int:
    mem = WinProcessBackend()
    mem.attach("nightreign.exe")
    mapping = load_mapping(ROOT / "data" / "mapping.json")
    catalog = EffectCatalog.from_data_dir(ROOT / "data")
    svc = RelicService(mem, mapping, catalog, readonly_preview=True)
    svc.attach()

    print("Gaitem cache @", hex(svc.gaitem.address))
    hexdump(mem, svc.gaitem.address, 32)
    for i in range(6):
        v = int.from_bytes(mem.read_bytes(svc.gaitem.address + i * 4, 2), "little")
        print(f"  Gaitem+{i*4:X} smallint={v}")

    print("\n=== per slot ===")
    for i in range(1, 7):
        r = svc.read_slot(i)
        a1 = r.raw_addresses.get("attr1", 0)
        obj = a1 - 0x18
        print(f"\n槽{i} {r.kind.value} obj=0x{obj:X}")
        hexdump(mem, obj, 0x50)
        for name in ("attr1", "attr2", "attr3", "debuff1", "debuff2", "debuff3"):
            addr = r.raw_addresses.get(name)
            if not addr:
                continue
            val = int.from_bytes(mem.read_bytes(addr, 4), "little")
            m = catalog.get(val)
            print(f"  {name} @0x{addr:X} = {val} ({m.name if m else '?'})")

    # 校验：attr1/2/3 是否相邻 u32
    r = svc.read_slot(1)
    a1, a2, a3 = r.raw_addresses["attr1"], r.raw_addresses["attr2"], r.raw_addresses["attr3"]
    print(f"\noffset check: a2-a1={a2-a1:X} a3-a2={a3-a2:X} (期望 4, 4)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
