# -*- coding: utf-8 -*-
"""指针链与事务测试 — 锁定 CE getPointerAddress 语义。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from infra.errors import WriteFailed
from infra.memory.pointer import PointerResolver
from infra.memory.transaction import MemoryTransaction, TxnWrite, pack_u32, unpack_u32
from infra.memory.win_process import MockMemoryBackend


def _setup():
    """模拟 CE 指针链:
    csgaitem @0x1000 -> ptr 0x5000
    Gaitem   @0x2000, [Gaitem]=7 (u32)
    链: read_ptr(0x1000) + (8+8*7) = 0x5000+0x40 = 0x5040
        read_ptr(0x5040) = 0x6000
        +0x18 = 0x6018
    """
    mem = MockMemoryBackend()
    mem.attach("nightreign.exe")
    mem.poke_u64(0x1000, 0x5000)  # *csgaitem
    mem.poke_u32(0x2000, 7)  # *Gaitem = 7
    mem.poke_u64(0x5000 + (8 + 8 * 7), 0x6000)  # 对象指针
    symbols = {"csgaitem": 0x1000, "Gaitem": 0x2000}
    pr = PointerResolver(mem, symbol_resolver=symbols.__getitem__)
    return mem, pr


def test_resolve_slot1_attr1_ce_semantics():
    mem, pr = _setup()
    # XML offsets: ['+18', '8+8*[Gaitem]'] — 第一项为最终字段偏移
    addr = pr.resolve("csgaitem", ["+18", "8+8*[Gaitem]"], use_cache=False)
    expected = 0x6018
    assert addr == expected, hex(addr)


def test_resolve_slot2_uses_gaitem_plus4():
    mem = MockMemoryBackend()
    mem.attach("nightreign.exe")
    mem.poke_u64(0x1000, 0x5000)
    mem.poke_u32(0x2000, 7)
    mem.poke_u32(0x2004, 9)
    # slot2 index = 8+8*9 = 0x50
    mem.poke_u64(0x5000 + (8 + 8 * 9), 0x7000)
    pr = PointerResolver(mem, symbol_resolver={"csgaitem": 0x1000, "Gaitem": 0x2000}.__getitem__)
    addr = pr.resolve("csgaitem", ["+1c", "8+8*[Gaitem+4]"], use_cache=False)
    assert addr == 0x701C


class _RollbackAlsoFails(MockMemoryBackend):
    """第一次写成功、第二次写失败、且回滚第一次也失败。"""

    def __init__(self):
        super().__init__()
        self._first_done = False

    def write_bytes(self, address, data):
        if address == 0x4004:
            raise WriteFailed("模拟第二次写入失败")
        if self._first_done:
            raise WriteFailed("模拟回滚也失败")
        self._first_done = True
        super().write_bytes(address, data)


def test_transaction_reports_failed_rollback():
    """回归：回滚失败曾被 except: pass 静默吞掉，却仍报告「已回滚」。

    这会让内存停在部分写入状态，而用户以为已经回滚干净了。
    """
    mem = _RollbackAlsoFails()
    mem.attach("mock.exe")
    mem.poke_u32(0x4000, 0x11111111)
    res = MemoryTransaction(mem).execute(
        [
            TxnWrite(0x4000, pack_u32(0xAAAAAAAA)),
            TxnWrite(0x4004, pack_u32(0xBBBBBBBB)),
        ]
    )
    assert res.ok is False
    assert res.rolled_back is False, "回滚实际失败，不能被报成已回滚"
    assert "回滚也失败" in res.error


def test_transaction_commit_and_rollback():
    mem = MockMemoryBackend()
    mem.attach("mock.exe")
    mem.poke_u32(0x4000, 0x11111111)
    mem.poke_u32(0x4004, 0x22222222)
    tx = MemoryTransaction(mem)

    mem.fail_write_at = 0x4004
    res = tx.execute(
        [
            TxnWrite(0x4000, pack_u32(0xAAAAAAAA)),
            TxnWrite(0x4004, pack_u32(0xBBBBBBBB)),
        ]
    )
    assert res.ok is False
    assert res.rolled_back is True
    assert unpack_u32(mem.read_bytes(0x4000, 4)) == 0x11111111

    mem.fail_write_at = None
    res = tx.execute(
        [
            TxnWrite(0x4000, pack_u32(0xAAAAAAAA)),
            TxnWrite(0x4004, pack_u32(0xBBBBBBBB)),
        ]
    )
    assert res.ok is True
    assert unpack_u32(mem.read_bytes(0x4000, 4)) == 0xAAAAAAAA

    backups = [(a, old) for a, old, _new in res.applied]
    r2 = tx.restore(backups)
    assert r2.ok is True
    assert unpack_u32(mem.read_bytes(0x4000, 4)) == 0x11111111
