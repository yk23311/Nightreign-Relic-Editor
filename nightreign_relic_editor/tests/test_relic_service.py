# -*- coding: utf-8 -*-
"""服务层测试（Mock 后端，对齐 CE 指针语义）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.relic_service import RelicService
from domain.enums import EffectCatalog, parse_effect_line
from domain.models import FieldWrite
from infra.errors import ReadonlyMode, ValidationError
from infra.memory.win_process import MockMemoryBackend
from mapping.mapping_config import default_mapping_dict, mapping_from_dict


def _make_service():
    """内存布局（CE 两层指针 + Gaitem 缓存）:
    CSGaitem 全局 @0x1000 -> 对象表 @0x5000
    GameDataMan 全局 @0x3000 -> manager @0x8000 -> player @0x9000
    player+0x2F4.. 装备槽 smallint
    Gaitem 缓存由 GaitemCache 分配
    对象指针表: *[0x5000 + 8+8*type_id] = 对象
    """
    mem = MockMemoryBackend()
    mapping = mapping_from_dict(default_mapping_dict())
    catalog = EffectCatalog(
        std=[
            parse_effect_line("7001400:ro448|STD|N|ATTACK|物理攻击力提升"),
            parse_effect_line("7001401:ro449|STD|N|CHARA|角色强化 +1"),
            parse_effect_line("7001402:ro450|BTH|N|NONE|物理攻击力提升 +2"),
        ],
        don=[
            parse_effect_line("6001400:ro378|DoN|N|ATTACK|物理攻击力提升 +3"),
            parse_effect_line("6001401:ro379|DoN|N|ATTACK|物理攻击力提升 +4"),
        ],
        debuff=[parse_effect_line("6820000:roN/A|DoN|N|CURSE|中毒")],
    )
    svc = RelicService(mem, mapping, catalog, readonly_preview=False)
    mem.attach("nightreign.exe")

    # 手动符号（跳过 AOB）
    svc.set_manual_symbol("csgaitem", 0x1000)
    svc.set_manual_symbol("GameDataMan", 0x3000)
    svc.set_manual_symbol("CSGaitem", 0x1000)

    # 布局
    mem.poke_u64(0x1000, 0x5000)  # *CSGaitem = table
    mem.poke_u64(0x3000, 0x8000)  # *GameDataMan = manager
    mem.poke_u64(0x8000 + 8, 0x9000)  # player = *[manager+8]
    # 6 槽 type_id = 3 (smallint)，对象在 table + 8+8*3 = table+0x20
    type_id = 3
    for i in range(6):
        mem.poke_u16(0x9000 + 0x2F4 + i * 4, type_id)
        obj = 0xA000 + i * 0x100
        mem.poke_u64(0x5000 + (8 + 8 * type_id), obj)  # 所有槽共用 type 表项（测试够用）
        # 实际上每个槽 type_id 可不同；测试用不同 type 更准确
    # 改为每个槽不同 type_id = 10+i
    for i in range(6):
        tid = 10 + i
        mem.poke_u16(0x9000 + 0x2F4 + i * 4, tid)
        obj = 0xA000 + i * 0x100
        mem.poke_u64(0x5000 + (8 + 8 * tid), obj)
        # 预置属性
        mem.poke_u32(obj + 0x18, 7001400)
        mem.poke_u32(obj + 0x1C, 0)
        mem.poke_u32(obj + 0x20, 0)

    # 让 attach 路径的 refresh 可用：手动 ensure gaitem
    svc.gaitem.refresh()
    return mem, svc


def test_read_and_apply_attr():
    mem, svc = _make_service()
    r = svc.read_slot(1)
    assert r.effects[0].effect_id == 7001400
    addr = svc.resolve_field_address(1, "attr1")
    # type_id=10, index=8+8*10=0x58, obj=*[0x5000+0x58]=0xA000, field=0xA018
    assert addr == 0xA018

    report = svc.apply([FieldWrite(1, "attr1", 7001400, 7001401)])
    assert report.ok is True
    assert mem.read_u32(0xA018) == 7001401


def test_empty_slot_and_debuff_normalized_to_minus_one():
    """回归：内存空位 0xFFFFFFFF 曾被原样读成 4294967295，
    导致列表显示「未知 4294967295」、下拉框匹配不上、并产生虚假写入。"""
    mem, svc = _make_service()
    mem.poke_u32(0xA018, 0xFFFFFFFF)  # 槽1 属性1 置空
    mem.poke_u32(0xA040, 0xFFFFFFFF)  # 槽1 减益1 禁用
    r = svc.read_slot(1)
    assert r.effects[0].effect_id == -1
    assert r.debuffs[0] == -1


def test_readonly_blocks():
    mem, svc = _make_service()
    svc.readonly_preview = True
    try:
        svc.apply([FieldWrite(1, "attr1", 0, 7001400)])
        raise AssertionError("should raise")
    except ReadonlyMode:
        pass


def test_validation_blocks_unknown_id():
    mem, svc = _make_service()
    try:
        svc.apply([FieldWrite(1, "attr1", 0, 9999999)])
        raise AssertionError("should raise")
    except ValidationError:
        pass


def test_undo():
    mem, svc = _make_service()
    svc.apply([FieldWrite(1, "attr1", 7001400, 7001401)])
    assert mem.read_u32(0xA018) == 7001401
    rep = svc.undo()
    assert rep.ok
    assert mem.read_u32(0xA018) == 7001400


def test_redo_writes_new_value_back():
    """回归：redo 曾对 (addr, old) 再次 restore，等于空操作却仍报告成功。"""
    mem, svc = _make_service()
    svc.apply([FieldWrite(1, "attr1", 7001400, 7001401)])

    assert svc.undo().ok
    assert mem.read_u32(0xA018) == 7001400

    rep = svc.redo()
    assert rep.ok, rep.errors
    assert mem.read_u32(0xA018) == 7001401, "redo 未把 new 值写回"


def test_undo_redo_cycle_and_redo_cleared_by_new_apply():
    mem, svc = _make_service()
    svc.apply([FieldWrite(1, "attr1", 7001400, 7001401)])
    svc.undo()
    svc.redo()
    assert mem.read_u32(0xA018) == 7001401
    svc.undo()
    assert mem.read_u32(0xA018) == 7001400
    # 新的写入必须清空 redo 栈
    svc.apply([FieldWrite(1, "attr1", 7001400, 7001401)])
    assert svc.redo().ok is False


def test_redo_without_undo_reports_failure():
    mem, svc = _make_service()
    rep = svc.redo()
    assert rep.ok is False and rep.errors


def _test_backup_path(name):
    """备份文件写到项目目录内。

    不用 tempfile.TemporaryDirectory：目标环境的系统临时目录不可写（沙箱限制），
    且其清理会做 chmod 同样被拒，导致测试以 PermissionError 假失败。
    """
    d = ROOT / "data" / "_test_tmp"
    d.mkdir(parents=True, exist_ok=True)
    return d / name


def test_restore_backup_file_roundtrip():
    """回归：restore_backup_file 曾因借用 readonly_preview 反而打开 RNG 门而必然失败。"""
    mem, svc = _make_service()
    svc.apply([FieldWrite(1, "attr1", 7001400, 7001401)])
    path = _test_backup_path("roundtrip.json")
    try:
        svc.backup_all(path)
        svc.apply([FieldWrite(1, "attr1", 7001401, 7001400)])
        assert mem.read_u32(0xA018) == 7001400

        rep = svc.restore_backup_file(path)
        assert rep.ok, rep.errors
        assert mem.read_u32(0xA018) == 7001401
    finally:
        path.unlink(missing_ok=True)


def test_restore_backup_file_respects_readonly():
    mem, svc = _make_service()
    path = _test_backup_path("readonly.json")
    try:
        svc.backup_all(path)
        svc.readonly_preview = True
        try:
            svc.restore_backup_file(path)
            raise AssertionError("should raise ReadonlyMode")
        except ReadonlyMode:
            pass
    finally:
        path.unlink(missing_ok=True)
