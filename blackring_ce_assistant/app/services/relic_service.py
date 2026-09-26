# -*- coding: utf-8 -*-
"""遗物应用服务：读槽、校验、批量写、复制、预设。"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Sequence

from domain import rules
from domain.enums import EffectCatalog
from domain.models import (
    EffectMeta,
    FieldWrite,
    Preset,
    Relic,
    RelicEffect,
    RelicSlotKind,
    RuleViolation,
    WriteReport,
)
from infra.errors import (
    AppError,
    NotAttached,
    ReadonlyMode,
    ValidationError,
    WriteFailed,
)
from infra.memory.base import IMemoryBackend, ProcessInfo
from infra.memory.pointer import PointerResolver
from infra.memory.symbols import GaitemCache, SymbolTable
from infra.memory.transaction import MemoryTransaction, TxnWrite, pack_u32, unpack_u32, now_iso
from mapping.mapping_config import MappingSet

log = logging.getLogger(__name__)


FIELD_NAMES = ("attr1", "attr2", "attr3")
DEBUFF_NAMES = ("debuff1", "debuff2", "debuff3")
ALL_WRITABLE = FIELD_NAMES + DEBUFF_NAMES

EMPTY_RAW = 0xFFFFFFFF


def normalize_id(raw: int) -> int:
    """把内存中的空位 0xFFFFFFFF 归一化为 -1。

    不归一的后果（旧版实际表现）：列表把空槽显示成「未知 4294967295」，下拉框
    findData(-1) 匹配不上，且 old=4294967295 与 new=-1 被判定为「有变更」——
    用户什么都没改也会生成 FieldWrite、污染校验栏与撤销栈。
    """
    return -1 if raw >= EMPTY_RAW else raw


class RelicService:
    def __init__(
        self,
        backend: IMemoryBackend,
        mapping: MappingSet,
        catalog: EffectCatalog,
        *,
        readonly_preview: bool = True,
        undo_limit: int = 20,
    ) -> None:
        self.backend = backend
        self.mapping = mapping
        self.catalog = catalog
        self.readonly_preview = readonly_preview
        self.undo_limit = undo_limit

        self.symbols = SymbolTable(backend)
        self.symbols.load_specs(mapping.symbols)
        self.gaitem = GaitemCache(backend, self.symbols)
        self.resolver = PointerResolver(backend, symbol_resolver=self.symbols.as_resolver())
        self.tx = MemoryTransaction(backend)

        # 栈元素 = 一次事务的 [(address, old_bytes, new_bytes), ...]。
        # 必须保存 new_bytes：撤销写 old、重做写 new，二者都需要。
        self._undo: list[list[tuple[int, bytes, bytes]]] = []
        self._redo: list[list[tuple[int, bytes, bytes]]] = []

    # --- process ---
    def attach(self) -> ProcessInfo:
        # 必须先释放旧缓存再 attach：backend.attach() 内部会先 detach 旧句柄，
        # 若在其后再 release，就是对**新进程**释放一个属于旧进程的地址。
        self.gaitem.release()
        info = self.backend.attach(self.mapping.process_name)
        self.symbols.invalidate()
        self.resolver.clear_cache()
        # 预解析符号 + 刷新 Gaitem 缓存
        self.symbols.resolve("csgaitem")
        self.symbols.resolve("GameDataMan")
        self.gaitem.refresh()
        return info

    def detach(self) -> None:
        self.gaitem.release()
        self.backend.detach()
        self.symbols.invalidate()
        self.resolver.clear_cache()

    def is_attached(self) -> bool:
        return self.backend.is_attached()

    def set_manual_symbol(self, name: str, value: int) -> None:
        self.symbols.set_manual(name, value)
        self.resolver.clear_cache()

    # --- resolve ---
    def resolve_field_address(self, slot_index: int, field_name: str, *, refresh_gaitem: bool = False) -> int:
        sm = self.mapping.slot(slot_index)
        fm = sm.fields[field_name]
        if refresh_gaitem:
            self.gaitem.refresh()
        # CE getPointerAddress: offsets[0]=最终字段偏移，offsets[-1]=先应用
        offsets = [fm.offset, sm.index_expr]
        self.resolver.clear_cache()
        return self.resolver.resolve(self.mapping.address_expr, offsets, use_cache=False)

    def read_slot(self, slot_index: int) -> Relic:
        if not self.is_attached():
            raise NotAttached("尚未附加进程")
        self.gaitem.refresh()
        return self._read_slot_no_refresh(slot_index)

    def _slot_kind(self, slot_index: int) -> RelicSlotKind:
        return (
            RelicSlotKind.STANDARD
            if self.mapping.slot(slot_index).kind == "STD"
            else RelicSlotKind.DEEP_NIGHT
        )

    def candidates_for(
        self, slot_index: int, field_name: str, *, advanced: bool
    ) -> list[EffectMeta]:
        """某个字段的候选词条 —— **界面取候选的唯一入口**。

        非高级模式维持原有的严格过滤（属性字段只给本槽类型属性词条、减益字段只给减益）；
        高级模式返回全量 497 条（属性 473 + 减益 24），于是属性字段也能选减益、
        减益字段也能选属性。界面按 EffectMeta.category 分组显示。

        新界面（Phase B）复用同一出口，不要在新界面里重写这套过滤逻辑。
        """
        if field_name not in ALL_WRITABLE:
            # 不校验的话，拼错的字段名会被当成属性字段而静默返回属性候选，
            # 调用方拿到一份看起来正常、其实答非所问的列表。
            raise AppError("未知字段: " + str(field_name) + "（应为 " + "/".join(ALL_WRITABLE) + "）")
        return self.catalog.candidates(
            self._slot_kind(slot_index), field_name, advanced=advanced
        )

    def _read_slot_no_refresh(self, slot_index: int) -> Relic:
        sm = self.mapping.slot(slot_index)
        kind = self._slot_kind(slot_index)
        effects: list[RelicEffect] = []
        addrs: dict[str, int] = {}
        for i, fname in enumerate(FIELD_NAMES):
            addr = self.resolve_field_address(slot_index, fname, refresh_gaitem=False)
            addrs[fname] = addr
            try:
                eid = normalize_id(unpack_u32(self.backend.read_bytes(addr, 4)))
            except Exception:
                log.debug("读取槽 %d 的 %s 失败 @0x%X", slot_index, fname, addr, exc_info=True)
                eid = -1
            effects.append(
                RelicEffect(
                    slot_index=slot_index,
                    position=i,
                    effect_id=eid,
                    meta=self.catalog.get(eid),
                    raw_address=addr,
                    editable=True,
                )
            )
        debuffs: list[int] = []
        for fname in DEBUFF_NAMES:
            if fname in sm.fields:
                try:
                    addr = self.resolve_field_address(slot_index, fname, refresh_gaitem=False)
                    addrs[fname] = addr
                    debuffs.append(normalize_id(unpack_u32(self.backend.read_bytes(addr, 4))))
                except Exception:
                    log.debug("读取槽 %d 的 %s 失败", slot_index, fname, exc_info=True)
                    debuffs.append(-1)
            else:
                debuffs.append(-1)
        return Relic(
            slot_index=slot_index,
            kind=kind,
            effects=effects,
            debuffs=debuffs,
            raw_addresses=addrs,
        )

    def refresh_slots(self) -> list[Relic]:
        if not self.is_attached():
            raise NotAttached("尚未附加进程")
        self.gaitem.refresh()
        return [self._read_slot_no_refresh(i) for i in range(1, 7)]

    # --- validate & apply ---
    def validate(
        self,
        relic: Relic,
        writes: Sequence[FieldWrite],
        *,
        allow_raw: bool = False,
        skip_rules: bool = False,
    ) -> list[RuleViolation]:
        """校验一组写入。skip_rules=True（高级模式）时不做任何检查。

        RNG 安全门已于 0.6.0 按用户要求彻底移除，因此这里不再有任何相关开关。
        """
        return rules.validate_proposal(
            relic,
            writes,
            self.catalog,
            allow_raw=allow_raw,
            skip_rules=skip_rules,
        )

    def apply(
        self,
        proposals: list[FieldWrite],
        *,
        allow_raw: bool = False,
        dry_run: bool = False,
        skip_rules: bool = False,
    ) -> WriteReport:
        if self.readonly_preview:
            raise ReadonlyMode("当前为只读预览模式，请先切换到编辑模式")

        # group by slot
        by_slot: dict[int, list[FieldWrite]] = {}
        for w in proposals:
            by_slot.setdefault(w.slot_index, []).append(w)

        all_violations: list[RuleViolation] = []
        relics: dict[int, Relic] = {}
        for slot_idx, writes in by_slot.items():
            relic = self.read_slot(slot_idx)
            relics[slot_idx] = relic
            all_violations.extend(
                self.validate(relic, writes, allow_raw=allow_raw, skip_rules=skip_rules)
            )

        if rules.has_block(all_violations):
            msgs = [f"{v.field}: {v.message}" for v in all_violations if v.is_block]
            raise ValidationError("校验未通过：" + "；".join(msgs))

        txn_writes: list[TxnWrite] = []
        meta_list: list[tuple[int, str, int, int]] = []
        for slot_idx, writes in by_slot.items():
            for w in writes:
                addr = w.resolved_address or self.resolve_field_address(w.slot_index, w.field_name)
                old = w.old_value
                if old is None:
                    old = normalize_id(unpack_u32(self.backend.read_bytes(addr, 4)))
                txn_writes.append(
                    TxnWrite(
                        address=addr,
                        new_bytes=pack_u32(w.new_value),
                        meta={"slot": slot_idx, "field": w.field_name, "old": old, "new": w.new_value},
                    )
                )
                meta_list.append((slot_idx, w.field_name, old, w.new_value))

        result = self.tx.execute(txn_writes, dry_run=dry_run)
        if not result.ok:
            return WriteReport(ok=False, errors=[result.error], rolled_back=result.rolled_back)

        if not dry_run and result.applied:
            self._undo.append(list(result.applied))
            if len(self._undo) > self.undo_limit:
                self._undo.pop(0)
            self._redo.clear()

        return WriteReport(ok=True, changed=meta_list)

    # --- 复制 / 预设：只产出写入提案，不落盘 ---------------------------------
    # 新界面（webui）需要「先算出要改什么、再交给统一的 apply 流程」的能力。
    # 这两个方法只读取 + 生成 FieldWrite，不写内存，因此可以脱离浏览器单测，
    # 也保证复制与预设走的是和手动改词条**完全相同**的校验与事务路径。

    def build_copy_writes(self, src_slot: int, dst_slots: Sequence[int]) -> list[FieldWrite]:
        """把 src_slot 的属性与减益搬到 dst_slots（只产出与目标不同的项）。"""
        src = self.read_slot(src_slot)
        ids = src.effect_ids()
        dbs = tuple((list(src.debuffs) + [-1, -1, -1])[:3])
        out: list[FieldWrite] = []
        for dst in dst_slots:
            if dst == src_slot:
                continue
            r = self.read_slot(dst)
            cur = r.effect_ids()
            cur_db = tuple((list(r.debuffs) + [-1, -1, -1])[:3])
            for i, f in enumerate(FIELD_NAMES):
                if cur[i] != ids[i]:
                    out.append(FieldWrite(dst, f, cur[i], ids[i]))
            for i, f in enumerate(DEBUFF_NAMES):
                if cur_db[i] != dbs[i]:
                    out.append(FieldWrite(dst, f, cur_db[i], dbs[i]))
        return out

    def build_preset_writes(self, preset: Preset, dst_slots: Sequence[int]) -> list[FieldWrite]:
        """把预设套用到 dst_slots。

        槽类型不符直接抛错：普通与深夜的词条不通用，静默套用会写出游戏内非法的数据。
        """
        dbs = tuple(preset.debuff_ids) if preset.debuff_ids else (-1, -1, -1)
        out: list[FieldWrite] = []
        for dst in dst_slots:
            kind = self._slot_kind(dst)
            if kind != preset.slot_kind:
                raise AppError(
                    "预设「" + preset.name + "」是" + preset.slot_kind.label + "词条，"
                    "不能套用到槽 " + str(dst) + "（" + kind.label + "）"
                )
            r = self.read_slot(dst)
            cur = r.effect_ids()
            cur_db = tuple((list(r.debuffs) + [-1, -1, -1])[:3])
            for i, f in enumerate(FIELD_NAMES):
                if cur[i] != preset.effect_ids[i]:
                    out.append(FieldWrite(dst, f, cur[i], preset.effect_ids[i]))
            for i, f in enumerate(DEBUFF_NAMES):
                if cur_db[i] != dbs[i]:
                    out.append(FieldWrite(dst, f, cur_db[i], dbs[i]))
        return out

    # --- undo / restore ---
    def undo(self) -> WriteReport:
        if self.readonly_preview:
            raise ReadonlyMode("只读模式无法撤销")
        if not self._undo:
            return WriteReport(ok=False, errors=["没有可撤销的操作"])
        entry = self._undo.pop()
        res = self.tx.restore([(addr, old) for addr, old, _new in entry])
        self._redo.append(entry)
        return WriteReport(ok=res.ok, errors=[res.error] if res.error else [])

    def redo(self) -> WriteReport:
        if self.readonly_preview:
            raise ReadonlyMode("只读模式无法重做")
        if not self._redo:
            return WriteReport(ok=False, errors=["没有可重做的操作"])
        entry = self._redo.pop()
        # 重做 = 把 new_bytes 写回去；写 old 会让 redo 变成空操作（早期 bug）
        res = self.tx.restore([(addr, new) for addr, _old, new in entry])
        self._undo.append(entry)
        if len(self._undo) > self.undo_limit:
            self._undo.pop(0)
        return WriteReport(ok=res.ok, errors=[res.error] if res.error else [])

    def backup_all(self, out_path: Path | str) -> Path:
        p = Path(out_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = {"created_at": now_iso(), "items": []}
        for i in range(1, 7):
            relic = self.read_slot(i)
            for e in relic.effects:
                payload["items"].append(
                    {
                        "slot": i,
                        "field": e.field_name,
                        "address": e.raw_address,
                        "value": e.effect_id,
                    }
                )
            for di, dval in enumerate(relic.debuffs):
                fname = f"debuff{di + 1}"
                payload["items"].append(
                    {
                        "slot": i,
                        "field": fname,
                        "address": relic.raw_addresses.get(fname),
                        "value": dval,
                    }
                )
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return p

    def restore_backup_file(self, path: Path | str) -> WriteReport:
        """从备份 JSON 恢复 6 槽属性与减益。

        与普通写入的区别（三处，早期实现全错）：
        1. **地址按 (slot, field) 重新解析**：备份里的绝对地址属于上次会话，重启游戏后
           必然失效；仅当重解析失败（映射变更等）才回退到记录地址。
        2. **skip_rules=True 是刻意的**：备份是游戏真实状态的快照，可能包含当前 CT 判定为
           非法的组合（CT 升级后兼容性/组别会变），而恢复恰恰是这种情况下的兜底手段，
           因此必须忠实写回、不得被规则拒绝。写入范围仍限于快照内的槽位与字段。
        3. 只读预览仍然生效（要恢复必须先退出只读）。
        """
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        proposals = []
        for item in data.get("items", []):
            slot = int(item["slot"])
            field = str(item["field"])
            try:
                addr = self.resolve_field_address(slot, field)
            except Exception:
                addr = item.get("address")
            if not addr:
                raise AppError(f"备份项无法定位：槽{slot} {field}")
            addr = int(addr)
            # 兼容旧版备份：空位曾被写成无符号 0xFFFFFFFF
            new_value = normalize_id(int(item["value"]))
            cur = normalize_id(unpack_u32(self.backend.read_bytes(addr, 4)))
            proposals.append(
                FieldWrite(
                    slot_index=slot,
                    field_name=field,
                    old_value=cur,
                    new_value=new_value,
                    resolved_address=addr,
                )
            )
        return self.apply(proposals, allow_raw=True, skip_rules=True)


# --- presets ---
class PresetService:
    def __init__(self, preset_dir: Path | str) -> None:
        self.dir = Path(preset_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    def save(self, preset: Preset) -> Path:
        safe = "".join(c for c in preset.name if c not in '\\/:*?"<>|').strip() or "preset"
        path = self.dir / f"{safe}.json"
        path.write_text(
            json.dumps(
                {
                    "name": preset.name,
                    "slot_kind": preset.slot_kind.value,
                    "effect_ids": list(preset.effect_ids),
                    "debuff_ids": list(preset.debuff_ids),
                    "created_at": preset.created_at,
                    "notes": preset.notes,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return path

    def list_all(self) -> list[Preset]:
        out = []
        for f in sorted(self.dir.glob("*.json")):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                out.append(
                    Preset(
                        name=d.get("name", f.stem),
                        slot_kind=RelicSlotKind(d.get("slot_kind", "STD")),
                        effect_ids=tuple(d.get("effect_ids", [-1, -1, -1])),  # type: ignore
                        created_at=d.get("created_at", ""),
                        debuff_ids=tuple(d.get("debuff_ids", [-1, -1, -1])),  # type: ignore
                        notes=d.get("notes", ""),
                    )
                )
            except Exception:
                log.warning("预设文件损坏、已跳过: %s", f, exc_info=True)
                continue
        return out
