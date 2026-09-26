# -*- coding: utf-8 -*-
"""前端与 Python 之间的**唯一**接口。

三条设计约束：

1. **薄且可单测**：方法只做参数搬运与错误包装，不实现业务规则；全部返回 JSON 可
   序列化的数据，异常一律装进 {"ok": False, "error": ...} 而不抛出。这样注入
   MockMemoryBackend 就能在没有浏览器、没有游戏的情况下完整测试。
2. **串行化**：所有碰内存的操作都走 Serializer，不指望前端自觉不并发。
3. **策略留在 Python**：候选列表与规则校验都调用既有 domain / service 层。
   前端只负责显示 —— 绝不能在前端复制一份规则，否则两边必然漂移。
"""
from __future__ import annotations

import collections
import logging
import time
from pathlib import Path
from typing import Any, Optional

from app.services.relic_service import DEBUFF_NAMES, FIELD_NAMES, PresetService, RelicService
from domain.models import FieldWrite, Preset, Relic, RelicSlotKind
from infra.errors import AppError
from webui.serial import Serializer

log = logging.getLogger(__name__)

# 前端日志抽屉的数据源：把 logging 记录也收进环形缓冲，供 log_tail 轮询
_RING: "collections.deque[dict]" = collections.deque(maxlen=800)
_SEQ = [0]


class _RingHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        _SEQ[0] += 1
        try:
            msg = record.getMessage()
        except Exception:
            msg = str(record.msg)
        _RING.append({
            "seq": _SEQ[0],
            "t": time.strftime("%H:%M:%S", time.localtime(record.created)),
            "level": record.levelname,
            "name": record.name,
            "msg": msg,
            "exc": bool(record.exc_info),
        })


def install_ring_handler() -> None:
    root = logging.getLogger()
    if any(isinstance(h, _RingHandler) for h in root.handlers):
        return
    h = _RingHandler()
    h.setLevel(logging.DEBUG)
    root.addHandler(h)


def _field_info(catalog, eid: int) -> dict:
    """把一个效果 ID 描述成前端可直接显示的结构。"""
    if eid is None:
        eid = -1
    m = catalog.get(eid) if eid > 0 else None
    if m is not None:
        name = m.name
    elif eid <= 0:
        name = "(空)"
    else:
        name = "未知 " + str(eid)
    return {
        "id": eid,
        "name": name,
        "known": m is not None,
        "compat": m.compatibility if m else "",
        "group": m.group if m else "",
        "rollOrder": m.roll_order if m else None,
        "rollGroup": m.roll_group if m else "",
        "reqDebuff": bool(m.req_debuff) if m else False,
        "category": m.category if m else "",
    }


class Bridge:
    """注册给 pywebview 的 js_api 对象。"""

    def __init__(
        self,
        service: RelicService,
        *,
        presets: Optional[PresetService] = None,
        backup_path: Optional[Path] = None,
        advanced: bool = False,
        app_version: str = "",
        demo: bool = False,
    ) -> None:
        self.svc = service
        self.presets = presets
        self.backup_path = backup_path
        self.advanced = advanced
        self.app_version = app_version
        # 演示模式：后端是 Mock 内存。**必须显式告诉前端**，否则用户点「附加进程」
        # 会拿到一条「AOB 未命中」，和真实的符号解析失败一模一样 ——
        # 这会让人去排查一个根本不存在的 bug（实际发生过一次）。
        self.demo = demo
        self.serial = Serializer()
        self.catalog = service.catalog
        install_ring_handler()

    # ---------- 内部 ----------
    def _ok(self, **kw) -> dict:
        d = {"ok": True}
        d.update(kw)
        return d

    def _err(self, exc: Any, where: str) -> dict:
        msg = str(exc) or exc.__class__.__name__
        log.warning("%s 失败: %s", where, msg, exc_info=isinstance(exc, Exception))
        return {"ok": False, "error": msg, "where": where}

    def _relic(self, r: Relic) -> dict:
        return {
            "index": r.slot_index,
            "kind": r.kind.value,
            "kindLabel": r.kind.label,
            "attrs": [_field_info(self.catalog, r.effect_ids()[i]) for i in range(3)],
            "debuffs": [
                _field_info(self.catalog, (r.debuffs + [-1, -1, -1])[i]) for i in range(3)
            ],
            "addresses": {k: v for k, v in r.raw_addresses.items()},
        }

    def _build_writes(self, payload: Any) -> list:
        """把前端传的 [{slot, field, value}] 变成 FieldWrite，并带上真实旧值。"""
        relics: dict[int, Relic] = {}
        out = []
        for w in payload or []:
            slot = int(w["slot"])
            field = str(w["field"])
            val = int(w["value"])
            if field not in FIELD_NAMES and field not in DEBUFF_NAMES:
                raise AppError("未知字段: " + field)
            if slot not in relics:
                relics[slot] = self.svc.read_slot(slot)
            r = relics[slot]
            if field.startswith("debuff"):
                pos = DEBUFF_NAMES.index(field)
                old = (r.debuffs + [-1, -1, -1])[pos]
            else:
                old = r.effect_ids()[FIELD_NAMES.index(field)]
            out.append(FieldWrite(slot, field, old, val))
        return out

    @staticmethod
    def _writes_json(fw: list) -> list:
        """把 FieldWrite 列表转成前端可直接并入待应用改动的结构。"""
        return [
            {"slot": w.slot_index, "field": w.field_name, "value": w.new_value}
            for w in fw
        ]

    def _apply_writes(self, fw: list, label: str) -> dict:
        """统一「算好写入 -> 落盘 -> 回读槽位」的收尾。

        手动改词条、复制、套预设三条路径共用它，避免各写一份导致行为不一致。
        """
        if not fw:
            return self._ok(changed=[], count=0, note="没有需要写入的改动",
                            slots=[self._relic(r) for r in self.svc.refresh_slots()])
        rep = self.svc.apply(fw, allow_raw=self.advanced, skip_rules=self.advanced)
        slots = [self._relic(r) for r in self.svc.refresh_slots()]
        return self._ok(
            changed=[{"slot": s, "field": f, "old": o, "new": n} for s, f, o, n in rep.changed],
            count=len(rep.changed),
            rolledBack=rep.rolled_back,
            errors=rep.errors,
            slots=slots,
        )

    # ---------- 启动 / 状态 ----------
    def boot(self) -> dict:
        """前端加载完成后的第一次调用：拿到全部初始状态。

        未附加是**正常状态**，不是错误：此时 slots 为空并给出 notice 说明原因，
        ok 仍为 True，前端据此显示「请先附加进程」而不是弹错误。
        """
        slots: list = []
        notice = None
        if not self.svc.is_attached():
            notice = "尚未附加进程"
        else:
            try:
                slots = [self._relic(r) for r in self.svc.refresh_slots()]
            except Exception as exc:
                notice = str(exc)
        log.info(
            "界面已就绪：前端已完成首次 boot（%s，%d 个槽位）",
            "演示模式" if self.demo else "真实模式",
            len(slots),
        )
        return self._ok(
            version=self.app_version,
            demo=self.demo,
            attached=self.svc.is_attached(),
            readonly=self.svc.readonly_preview,
            advanced=self.advanced,
            processName=self.svc.mapping.process_name,
            slots=slots,
            notice=notice,
            logSeq=_SEQ[0],
        )

    def status(self) -> dict:
        return self._ok(
            demo=self.demo,
            attached=self.svc.is_attached(),
            readonly=self.svc.readonly_preview,
            advanced=self.advanced,
            busy=self.serial.busy,
        )

    def set_mode(self, readonly: Optional[bool] = None, advanced: Optional[bool] = None) -> dict:
        if readonly is not None:
            self.svc.readonly_preview = bool(readonly)
            log.info("只读预览：%s", "开" if readonly else "关（可写）")
        if advanced is not None:
            self.advanced = bool(advanced)
            log.info("高级模式：%s", "开（校验关闭，候选为 CT 全量表）" if advanced else "关（严格过滤 + 校验）")
        return self.status()

    # ---------- 进程 ----------
    def attach(self) -> dict:
        if self.demo:
            # 演示后端是 Mock 内存，模块里没有真实字节可扫，AOB 必然不命中。
            # 与其抛一个与真实失败无法区分的错误，不如直接说明这是模拟数据。
            log.info("演示模式：忽略附加请求，继续使用模拟数据")
            try:
                with self.serial.run("读取演示数据"):
                    slots = [self._relic(r) for r in self.svc.refresh_slots()]
                return self._ok(demo=True, pid=0, base=0, slots=slots,
                                notice="演示模式使用模拟数据，无需附加进程")
            except Exception as exc:
                return self._err(exc, "读取演示数据")
        try:
            with self.serial.run("附加进程并解析符号"):
                info = self.svc.attach()
                slots = [self._relic(r) for r in self.svc.refresh_slots()]
            return self._ok(pid=info.pid, base=info.module_base, slots=slots)
        except Exception as exc:
            return self._err(exc, "附加进程")

    def detach(self) -> dict:
        if self.demo:
            return self._ok(demo=True, notice="演示模式：模拟数据不会被卸载")
        try:
            with self.serial.run("分离"):
                self.svc.detach()
            return self._ok()
        except Exception as exc:
            return self._err(exc, "分离")

    def refresh(self) -> dict:
        try:
            with self.serial.run("读取 6 个槽位"):
                slots = [self._relic(r) for r in self.svc.refresh_slots()]
            return self._ok(slots=slots)
        except Exception as exc:
            return self._err(exc, "刷新槽位")

    # ---------- 候选（策略在 Python）----------
    def candidates(self, slot: int, field: str) -> dict:
        try:
            rows = self.svc.candidates_for(int(slot), str(field), advanced=self.advanced)
            return self._ok(
                advanced=self.advanced,
                groups=sorted({m.group for m in rows if m.group}),
                items=[
                    {
                        "id": m.effect_id,
                        "name": m.name,
                        "compat": m.compatibility,
                        "group": m.group,
                        "rollOrder": m.roll_order,
                        "rollGroup": m.roll_group,
                        "reqDebuff": bool(m.req_debuff),
                        "category": m.category,
                    }
                    for m in rows
                ],
            )
        except Exception as exc:
            return self._err(exc, "取候选词条")

    # ---------- 校验 / 写入 ----------
    def validate(self, writes: Any) -> dict:
        try:
            with self.serial.run("校验"):
                fw = self._build_writes(writes)
                by_slot: dict[int, list] = {}
                for w in fw:
                    by_slot.setdefault(w.slot_index, []).append(w)
                out = []
                for slot, ws in by_slot.items():
                    relic = self.svc.read_slot(slot)
                    for v in self.svc.validate(
                        relic, ws, allow_raw=self.advanced, skip_rules=self.advanced
                    ):
                        out.append({
                            "slot": slot, "field": v.field, "rule": v.rule_id,
                            "severity": v.severity, "message": v.message,
                        })
            return self._ok(violations=out, skipped=self.advanced)
        except Exception as exc:
            return self._err(exc, "校验")

    def apply(self, writes: Any) -> dict:
        try:
            with self.serial.run("写入内存"):
                return self._apply_writes(self._build_writes(writes), "apply")
        except Exception as exc:
            return self._err(exc, "应用写入")

    def undo(self) -> dict:
        try:
            with self.serial.run("撤销"):
                rep = self.svc.undo()
                slots = [self._relic(r) for r in self.svc.refresh_slots()]
            # ok 表示**这次撤销是否真的发生了**（没有历史可撤销时是 False），
            # 这样前端可以用同一套逻辑处理失败，不必区分「调用失败」与「操作失败」。
            return {"ok": rep.ok, "errors": rep.errors, "slots": slots}
        except Exception as exc:
            return self._err(exc, "撤销")

    def redo(self) -> dict:
        try:
            with self.serial.run("重做"):
                rep = self.svc.redo()
                slots = [self._relic(r) for r in self.svc.refresh_slots()]
            return {"ok": rep.ok, "errors": rep.errors, "slots": slots}
        except Exception as exc:
            return self._err(exc, "重做")

    def backup(self) -> dict:
        try:
            if self.backup_path is None:
                raise AppError("未配置备份路径")
            with self.serial.run("备份"):
                p = self.svc.backup_all(self.backup_path)
            return self._ok(path=str(p))
        except Exception as exc:
            return self._err(exc, "备份")

    def preview_copy(self, src: int, dsts) -> dict:
        """算出「把 src 槽的词条复制到 dsts」需要改哪些字段 —— **只算不写**。

        前端把结果并入「待应用改动」，等用户看过校验、点「应用到选中」才真正落盘。
        与手动改词条保持同一套语义：所有修改都先暂存、后统一应用。
        这样也顺带让复制走的是同一条校验/事务路径，而不是另开一条写入捷径。
        """
        try:
            with self.serial.run("预览复制"):
                fw = self.svc.build_copy_writes(int(src), [int(d) for d in (dsts or [])])
            return self._ok(writes=self._writes_json(fw), count=len(fw))
        except Exception as exc:
            return self._err(exc, "预览复制")

    def restore(self) -> dict:
        """从最近一次备份恢复。

        只读预览下会被后端拒绝（这是产品规则：写入必须先退出只读），
        因此这里如实把错误交给前端显示，不做绕过。
        """
        try:
            if self.backup_path is None:
                raise AppError("未配置备份路径")
            p = Path(self.backup_path)
            if not p.exists():
                raise AppError("还没有备份文件，请先点「备份」")
            with self.serial.run("从备份恢复"):
                rep = self.svc.restore_backup_file(p)
                slots = [self._relic(r) for r in self.svc.refresh_slots()]
            return self._ok(count=len(rep.changed), errors=rep.errors, slots=slots)
        except Exception as exc:
            return self._err(exc, "从备份恢复")

    # ---------- 预设 ----------
    def presets_list(self) -> dict:
        try:
            if self.presets is None:
                return self._ok(items=[])
            return self._ok(items=[
                {
                    "name": p.name,
                    "slotKind": p.slot_kind.value,
                    "slotKindLabel": p.slot_kind.label,
                    "effectIds": list(p.effect_ids),
                    "debuffIds": list(p.debuff_ids),
                    "createdAt": p.created_at,
                }
                for p in self.presets.list_all()
            ])
        except Exception as exc:
            return self._err(exc, "读取预设")

    def preset_save(self, name: str, slot: int) -> dict:
        try:
            if self.presets is None:
                raise AppError("未配置预设目录")
            with self.serial.run("保存预设"):
                r = self.svc.read_slot(int(slot))
                p = Preset(
                    name=str(name) or ("槽" + str(slot)),
                    slot_kind=r.kind,
                    effect_ids=r.effect_ids(),
                    created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
                    debuff_ids=tuple((list(r.debuffs) + [-1, -1, -1])[:3]),
                )
                path = self.presets.save(p)
            return self._ok(path=str(path), name=p.name)
        except Exception as exc:
            return self._err(exc, "保存预设")

    def preview_preset(self, name: str, slots) -> dict:
        """算出「把预设套用到指定槽位」需要改哪些字段 —— **只算不写**。

        与预览复制同理，并入待应用改动后再统一应用。槽类型不符由服务层抛错。
        """
        try:
            if self.presets is None:
                raise AppError("未配置预设目录")
            want = str(name)
            preset = next((p for p in self.presets.list_all() if p.name == want), None)
            if preset is None:
                raise AppError("找不到预设：「" + want + "」")
            with self.serial.run("预览套用预设"):
                fw = self.svc.build_preset_writes(preset, [int(s) for s in (slots or [])])
            return self._ok(writes=self._writes_json(fw), count=len(fw))
        except Exception as exc:
            return self._err(exc, "预览套用预设")

    def preset_delete(self, name: str) -> dict:
        try:
            if self.presets is None:
                raise AppError("未配置预设目录")
            want = str(name)
            safe = "".join(c for c in want if c not in '\\/:*?"<>|').strip() or "preset"
            p = Path(self.presets.dir) / (safe + ".json")
            if not p.exists():
                raise AppError("找不到预设文件：「" + want + "」")
            p.unlink()
            log.info("已删除预设：%s", want)
            return self._ok()
        except Exception as exc:
            return self._err(exc, "删除预设")

    def log_tail(self, since: int = 0, limit: int = 200) -> dict:
        items = [r for r in _RING if r["seq"] > int(since)]
        return self._ok(items=items[-int(limit):], seq=_SEQ[0])
