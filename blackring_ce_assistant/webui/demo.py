# -*- coding: utf-8 -*-
"""演示用后端：在内存里伪造一份遗物数据，让新界面不需要游戏就能打开。

用途：① 开发与验证新界面；② 用户想先看看新界面长什么样。
它复用 MockMemoryBackend 与真实的 mapping/catalog，所以走的**是同一条代码路径**，
不是另写一套假逻辑。
"""
from __future__ import annotations

from app.services.relic_service import RelicService
from domain.enums import EffectCatalog
from domain.models import RelicSlotKind
from infra.memory.win_process import MockMemoryBackend
from mapping.mapping_config import MappingSet

GAITEM = 0x2000  # 由 GaitemCache 分配，这里不用它，直接用 csgaitem 那条链


def _pick_valid(catalog, kind, count=3, seed=0):
    """挑 count 条 Roll_Order 递增、抽取组互不重复的属性词条 —— 保证初始状态合法。

    做法：先把池子按 Roll_Order 排序（这样窗口内天然递增），再按槽位错开窗口起点
    取一个窗口，在窗口内挑组名不重复的条目。起初按「全局扫描 + 跳过同组」实现，
    结果 6 个槽的取法都被挤到同几条上，演示数据看起来像是坏的。
    """
    rows = [
        m for m in catalog.list_for_slot(kind)
        if m.roll_order is not None and m.roll_group and m.roll_group != "NONE" and not m.req_debuff
    ]
    rows.sort(key=lambda m: (m.roll_order, m.effect_id))
    if not rows:
        return [None] * count
    # 窗口必须**封口**：不加长度限制的话，循环会一路扫到列表末尾，
    # 于是不同槽位又收敛到同几条上，等于没做多样性。
    width = 60
    # 用取模绕回而不是 min 夹取：池子小的时候 min 会把多个槽位的起点夹到同一个上限，
    # 结果它们拿到完全相同的三条词条。
    span = max(1, len(rows) - width)
    start = (seed * 17) % span
    window = rows[start:start + width]
    out, used = [], set()
    for m in window:
        if len(out) >= count:
            break
        if m.roll_group in used:
            continue
        used.add(m.roll_group)
        out.append(m)
    while len(out) < count:
        out.append(None)
    return out


def make_demo_service(mapping: MappingSet, catalog: EffectCatalog) -> RelicService:
    mem = MockMemoryBackend()
    mem.attach("nightreign.exe")
    svc = RelicService(mem, mapping, catalog, readonly_preview=False)

    mem.poke_u64(0x1000, 0x5000)          # *csgaitem -> 对象表
    mem.poke_u64(0x3000, 0x8000)          # *GameDataMan -> manager
    mem.poke_u64(0x8000 + 8, 0x9000)      # player = *[manager+8]

    debuffs = catalog.list_debuffs()
    for i in range(6):
        tid = 10 + i
        mem.poke_u16(0x9000 + 0x2F4 + i * 4, tid)     # 槽 i 的 type_id
        obj = 0xA000 + i * 0x100
        mem.poke_u64(0x5000 + (8 + 8 * tid), obj)     # *[table + 8+8*tid] = obj

        kind = RelicSlotKind.STANDARD if i < 3 else RelicSlotKind.DEEP_NIGHT
        picked = _pick_valid(catalog, kind, 3, seed=i * 7)
        for j, m in enumerate(picked):
            mem.poke_u32(obj + 0x18 + j * 4, m.effect_id if m else 0xFFFFFFFF)
        for j in range(3):
            val = 0xFFFFFFFF
            if i >= 3 and j == 0 and debuffs:
                val = debuffs[i % len(debuffs)].effect_id
            mem.poke_u32(obj + 0x40 + j * 4, val)

    svc.set_manual_symbol("csgaitem", 0x1000)
    svc.set_manual_symbol("GameDataMan", 0x3000)
    svc.gaitem.refresh()                  # 建立 6 个槽的 Gaitem 缓存
    return svc
