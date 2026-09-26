# -*- coding: utf-8 -*-
"""bridge 层测试（Mock 后端，不需要浏览器、不需要游戏）。

bridge 是新界面与 Python 之间的唯一接口，它一旦出错，前端所有功能都错。
所以这里覆盖：候选策略（两种模式）、校验、写入、撤销重做、备份、预设、
错误包装（不抛异常而是返回 ok=False），以及**并发保护**。
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.relic_service import PresetService, RelicService
from domain.enums import EffectCatalog
from infra.memory.win_process import MockMemoryBackend
from mapping.mapping_config import default_mapping_dict, mapping_from_dict
from tests.test_relic_service import _make_service
from webui.bridge import Bridge

TMP = ROOT / "data" / "_test_tmp"


def _bridge_mock():
    """Mock 内存 + 合成小目录：用于写入类测试。"""
    mem, svc = _make_service()
    b = Bridge(
        svc,
        presets=PresetService(TMP / "presets"),
        backup_path=TMP / "backup.json",
        app_version="test",
    )
    return mem, svc, b


def _bridge_real():
    """真实目录（1076 条）+ Mock 后端未附加：用于候选策略测试。"""
    cat = EffectCatalog.from_data_dir(ROOT / "data")
    svc = RelicService(MockMemoryBackend(), mapping_from_dict(default_mapping_dict()), cat)
    return Bridge(svc, app_version="test")


# ---------- 启动 / 状态 ----------

def test_boot_reports_six_slots_when_attached():
    _mem, _svc, b = _bridge_mock()
    r = b.boot()
    assert r["ok"] is True
    assert r["attached"] is True
    assert len(r["slots"]) == 6
    s1 = r["slots"][0]
    assert s1["index"] == 1 and s1["kind"] == "STD" and s1["kindLabel"] == "普通"
    assert len(s1["attrs"]) == 3 and len(s1["debuffs"]) == 3
    assert s1["attrs"][0]["id"] == 7001400
    assert s1["attrs"][0]["name"] == "物理攻击力提升"


def test_boot_without_attach_is_not_an_error():
    b = _bridge_real()
    r = b.boot()
    assert r["ok"] is True, "未附加是正常状态，不该当成错误"
    assert r["attached"] is False
    assert r["slots"] == []
    assert "notice" in r


def test_demo_mode_attach_does_not_fake_aob_failure():
    """回归：演示模式下点「附加进程」曾抛出与真实失败**一模一样**的「AOB 未命中」。

    演示后端是 Mock 内存，模块里没有真实字节可扫，AOB 必然不命中；
    若照常走附加流程，用户会看到一条与真实符号解析失败无法区分的报错，
    从而去排查一个根本不存在的 bug（实际发生过）。演示模式必须自报家门。
    """
    from webui.demo import make_demo_service

    cat = EffectCatalog.from_data_dir(ROOT / "data")
    svc = make_demo_service(mapping_from_dict(default_mapping_dict()), cat)
    b = Bridge(svc, demo=True, app_version="test")

    boot = b.boot()
    assert boot["ok"] and boot["demo"] is True
    assert len(boot["slots"]) == 6

    a = b.attach()
    assert a["ok"] is True, "演示模式不应报错"
    assert a["demo"] is True
    assert "演示" in a["notice"]
    assert len(a["slots"]) == 6

    d = b.detach()
    assert d["ok"] is True and d["demo"] is True
    assert b.status()["demo"] is True


def test_set_mode_roundtrip():
    _mem, _svc, b = _bridge_mock()
    assert b.status()["advanced"] is False
    b.set_mode(advanced=True, readonly=False)
    st = b.status()
    assert st["advanced"] is True and st["readonly"] is False


# ---------- 候选策略 ----------

def test_candidates_safe_mode_is_strict():
    b = _bridge_real()
    b.set_mode(advanced=False)
    r = b.candidates(1, "attr1")
    assert r["ok"] and len(r["items"]) == 340
    assert all(i["category"] == "attr" for i in r["items"])
    d = b.candidates(1, "debuff1")
    assert len(d["items"]) == 24
    assert all(i["category"] == "debuff" for i in d["items"])
    don = b.candidates(4, "attr1")
    assert len(don["items"]) == 333


def test_candidates_advanced_is_full_ct_list_with_groups():
    b = _bridge_real()
    b.set_mode(advanced=True)
    r = b.candidates(1, "attr1")
    assert len(r["items"]) == 1076
    assert r["groups"] == ["FP/HP 恢复", "角色", "攻击", "装备", "潜伏之力", "非法",
                           "诅咒", "杂项"] or set(r["groups"]) == {
        "攻击", "角色", "装备", "FP/HP 恢复", "潜伏之力", "杂项", "诅咒", "非法"}
    # 减益字段在高级模式下也拿到同一份全量
    assert len(b.candidates(1, "debuff1")["items"]) == 1076


def test_candidates_rejects_bad_field():
    """回归：字段名拼错时不得静默返回属性候选，否则调用方会拿到答非所问的列表。"""
    b = _bridge_real()
    r = b.candidates(1, "nope")
    assert r["ok"] is False and "未知字段" in r["error"]


# ---------- 校验 / 写入 ----------

def test_validate_reports_violation_as_data():
    _mem, _svc, b = _bridge_mock()
    r = b.validate([{"slot": 1, "field": "attr1", "value": 9999999}])
    assert r["ok"] is True, "校验本身成功，违规应作为数据返回而不是错误"
    assert any(v["severity"] == "block" for v in r["violations"])
    assert r["violations"][0]["slot"] == 1


def test_apply_writes_and_returns_fresh_slots():
    mem, _svc, b = _bridge_mock()
    b.set_mode(readonly=False)
    r = b.apply([{"slot": 1, "field": "attr1", "value": 7001401}])
    assert r["ok"] is True, r
    assert r["count"] == 1
    assert mem.read_u32(0xA018) == 7001401
    assert r["slots"][0]["attrs"][0]["id"] == 7001401


def test_apply_blocked_in_readonly_mode():
    mem, _svc, b = _bridge_mock()
    b.set_mode(readonly=True)
    r = b.apply([{"slot": 1, "field": "attr1", "value": 7001401}])
    assert r["ok"] is False
    assert mem.read_u32(0xA018) == 7001400, "只读模式下内存不得被改动"


def test_apply_blocks_unknown_id_in_safe_mode():
    mem, _svc, b = _bridge_mock()
    b.set_mode(readonly=False, advanced=False)
    r = b.apply([{"slot": 1, "field": "attr1", "value": 1234567}])
    assert r["ok"] is False
    assert mem.read_u32(0xA018) == 7001400


def test_advanced_mode_allows_arbitrary_manual_id():
    mem, _svc, b = _bridge_mock()
    b.set_mode(readonly=False, advanced=True)
    r = b.apply([{"slot": 1, "field": "attr1", "value": 1234567}])
    assert r["ok"] is True, r
    assert mem.read_u32(0xA018) == 1234567


def test_apply_with_no_writes_is_ok():
    _mem, _svc, b = _bridge_mock()
    b.set_mode(readonly=False)
    r = b.apply([])
    assert r["ok"] is True and r["count"] == 0


def test_bad_field_name_returns_error_not_exception():
    _mem, _svc, b = _bridge_mock()
    b.set_mode(readonly=False)
    r = b.apply([{"slot": 1, "field": "attrX", "value": 1}])
    assert r["ok"] is False and "未知字段" in r["error"]


# ---------- 撤销 / 重做 ----------

def test_undo_redo_roundtrip_through_bridge():
    mem, _svc, b = _bridge_mock()
    b.set_mode(readonly=False)
    b.apply([{"slot": 1, "field": "attr1", "value": 7001401}])
    assert mem.read_u32(0xA018) == 7001401

    u = b.undo()
    assert u["ok"] and mem.read_u32(0xA018) == 7001400
    assert u["slots"][0]["attrs"][0]["id"] == 7001400

    r = b.redo()
    assert r["ok"] and mem.read_u32(0xA018) == 7001401


def test_undo_without_history_returns_error_dict():
    _mem, _svc, b = _bridge_mock()
    b.set_mode(readonly=False)
    r = b.undo()
    assert r["ok"] is False and r["errors"]


# ---------- 备份 / 预设 ----------

def test_backup_writes_file():
    TMP.mkdir(parents=True, exist_ok=True)
    _mem, _svc, b = _bridge_mock()
    r = b.backup()
    try:
        assert r["ok"] is True, r
        data = json.loads(Path(r["path"]).read_text(encoding="utf-8"))
        assert len(data["items"]) > 0
    finally:
        Path(r.get("path", TMP / "x")).unlink(missing_ok=True)


def test_preset_save_and_list():
    TMP.mkdir(parents=True, exist_ok=True)
    _mem, _svc, b = _bridge_mock()
    s = b.preset_save("桥接测试", 1)
    assert s["ok"] is True, s
    lst = b.presets_list()
    assert lst["ok"] is True
    names = [p["name"] for p in lst["items"]]
    assert "桥接测试" in names
    Path(s["path"]).unlink(missing_ok=True)


# ---------- 复制 / 预设套用 / 恢复 ----------

def test_preview_copy_returns_writes_without_touching_memory():
    """复制是「先暂存、后应用」：预览阶段**绝不能写内存**。

    这条语义是用户明确要求的 —— 复制/预设若直接落盘，就会出现
    「有的改动要确认、有的不用」的割裂。
    """
    mem, _svc, b = _bridge_mock()
    b.set_mode(readonly=False)
    # 夹具里 6 个槽初始内容相同，先造出差异
    b.apply([{"slot": 1, "field": "attr1", "value": 7001401}])
    before = mem.read_u32(0xA118)

    r = b.preview_copy(1, [2])
    assert r["ok"] is True, r
    assert r["count"] == 1, r
    assert r["writes"] == [{"slot": 2, "field": "attr1", "value": 7001401}]
    assert mem.read_u32(0xA118) == before, "预览阶段不得写内存"

    # 再把预览结果交给 apply，才真正落盘
    rep = b.apply(r["writes"])
    assert rep["ok"] is True, rep
    assert mem.read_u32(0xA118) == 7001401


def test_preview_copy_ignores_source_in_targets():
    _mem, _svc, b = _bridge_mock()
    b.set_mode(readonly=False)
    r = b.preview_copy(1, [1])
    assert r["ok"] is True
    assert r["count"] == 0 and r["writes"] == []


def test_preview_preset_returns_writes_without_touching_memory():
    TMP.mkdir(parents=True, exist_ok=True)
    mem, _svc, b = _bridge_mock()
    b.set_mode(readonly=False)
    s = b.preset_save("往返", 1)
    assert s["ok"] is True, s
    try:
        b.apply([{"slot": 1, "field": "attr1", "value": 7001401}])
        before = mem.read_u32(0xA018)
        r = b.preview_preset("往返", [1])
        assert r["ok"] is True, r
        assert r["count"] == 1
        assert mem.read_u32(0xA018) == before, "预览阶段不得写内存"

        rep = b.apply(r["writes"])
        assert rep["ok"] is True, rep
        assert mem.read_u32(0xA018) == 7001400
        assert rep["slots"][0]["attrs"][0]["id"] == 7001400
    finally:
        Path(s["path"]).unlink(missing_ok=True)


def test_preview_preset_rejects_wrong_slot_kind():
    """回归：普通槽的预设不得套到深夜槽 —— 两类槽词条不通用。

    注意这个校验必须在**预览阶段**就发生：否则用户会先看到一批待应用改动，
    点应用时才失败。
    """
    TMP.mkdir(parents=True, exist_ok=True)
    _mem, _svc, b = _bridge_mock()
    b.set_mode(readonly=False)
    s = b.preset_save("仅普通", 1)
    try:
        r = b.preview_preset("仅普通", [4])
        assert r["ok"] is False
        assert "不能套用" in r["error"]
    finally:
        Path(s["path"]).unlink(missing_ok=True)


def test_preview_preset_unknown_name_is_error_dict():
    _mem, _svc, b = _bridge_mock()
    b.set_mode(readonly=False)
    r = b.preview_preset("不存在的预设", [1])
    assert r["ok"] is False and "找不到预设" in r["error"]


def test_restore_without_backup_file_is_error_dict():
    _mem, _svc, b = _bridge_mock()
    b.set_mode(readonly=False)
    Path(TMP / "backup.json").unlink(missing_ok=True)
    r = b.restore()
    assert r["ok"] is False and "还没有备份文件" in r["error"]


def test_backup_then_restore_roundtrip():
    TMP.mkdir(parents=True, exist_ok=True)
    mem, _svc, b = _bridge_mock()
    b.set_mode(readonly=False)
    bk = b.backup()
    try:
        assert bk["ok"] is True, bk
        b.apply([{"slot": 1, "field": "attr1", "value": 7001401}])
        assert mem.read_u32(0xA018) == 7001401
        r = b.restore()
        assert r["ok"] is True, r
        assert mem.read_u32(0xA018) == 7001400
    finally:
        Path(bk.get("path", TMP / "x")).unlink(missing_ok=True)


# ---------- 日志 ----------

def test_log_tail_returns_records_with_increasing_seq():
    _mem, _svc, b = _bridge_mock()
    b.set_mode(advanced=True)          # 会写一条 info 日志
    r = b.log_tail(0)
    assert r["ok"] is True
    assert r["seq"] > 0
    assert all("seq" in i and "msg" in i for i in r["items"])
    first = r["items"][-1]["seq"]
    r2 = b.log_tail(first)
    assert all(i["seq"] > first for i in r2["items"])


# ---------- 并发保护（这是 Serializer 存在的唯一理由）----------

def test_serializer_prevents_interleaving():
    _mem, _svc, b = _bridge_mock()
    order = []

    def slow():
        with b.serial.run("A"):
            order.append("A-in")
            time.sleep(0.18)
            order.append("A-out")

    def other():
        time.sleep(0.05)
        with b.serial.run("B"):
            order.append("B-in")
            order.append("B-out")

    t1 = threading.Thread(target=slow)
    t2 = threading.Thread(target=other)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert order == ["A-in", "A-out", "B-in", "B-out"], "内存操作不得交错执行: " + str(order)


def test_status_reports_busy_label_while_running():
    _mem, _svc, b = _bridge_mock()
    seen = []

    def worker():
        with b.serial.run("读取 6 个槽位"):
            seen.append(b.status()["busy"])
            time.sleep(0.05)

    t = threading.Thread(target=worker)
    t.start()
    time.sleep(0.02)
    t.join()
    assert seen == ["读取 6 个槽位"]
    assert b.status()["busy"] is None
