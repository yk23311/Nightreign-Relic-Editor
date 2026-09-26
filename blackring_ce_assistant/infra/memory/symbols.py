# -*- coding: utf-8 -*-
"""符号解析：AOB+RIP 相对定位（对齐 CT registerBaseAddr）+ Gaitem 缓存。

CT 启用脚本关键逻辑：
  registerBaseAddr: symbol = aob_hit + offset + len + int32(aob_hit+offset+ripOffset)
  CSGaitem   aob+offset=0x10, len=7, rip=3
  GameDataMan aob,           len=7, rip=3
  Gaitem = allocate(0x1000) 并周期写入 6 个 smallint:
      [[GameDataMan]+8]+0x2F4 + i*4
"""
from __future__ import annotations

import logging
import struct
from dataclasses import dataclass
from typing import Callable, Optional

from ..errors import SymbolResolveError

log = logging.getLogger(__name__)

# 与 CT enable_script.txt 一致
CSGAITEM_AOB = "48 8D 44 24 40 48 89 44 24 50 8B 02 89 44 24 40 48 8B 0D ?? ?? ?? ?? 48 85 C9"
CSGAITEM_OFFSET = 0x10
GAMEDATAMAN_AOB = "48 8B 0D ???????? F3 48 0F 2C C0"

GAITEM_SLOTS = 6
GAITEM_STRIDE = 4
GAITEM_SRC_BASE = 0x2F4  # player_data + 0x2F4 + i*4  (smallint)


@dataclass
class SymbolSpec:
    name: str
    kind: str  # aob_rip | pointer_symbol | alloc_or_resolve | absolute
    module: str = "nightreign.exe"
    rva: Optional[int] = None
    absolute: Optional[int] = None
    aob: Optional[str] = None
    aob_offset: int = 0
    rip_len: int = 7
    rip_disp_offset: int = 3
    note: str = ""


DEFAULT_SPECS: dict[str, dict] = {
    "csgaitem": {
        "kind": "aob_rip",
        "module": "nightreign.exe",
        "aob": CSGAITEM_AOB,
        "aob_offset": CSGAITEM_OFFSET,
        "note": "CSGaitem via AOB+RIP",
    },
    "csgaitem": {
        "kind": "aob_rip",
        "module": "nightreign.exe",
        "aob": CSGAITEM_AOB,
        "aob_offset": CSGAITEM_OFFSET,
        "note": "CSGaitem via AOB+RIP",
    },
    "gamedataman": {
        "kind": "aob_rip",
        "module": "nightreign.exe",
        "aob": GAMEDATAMAN_AOB,
        "aob_offset": 0,
        "note": "GameDataMan via AOB+RIP",
    },
    "gaitem": {
        "kind": "alloc_or_resolve",
        "module": "nightreign.exe",
        "note": "6 x smallint cache allocated in target",
    },
}


class SymbolTable:
    """运行时符号表。解析顺序：manual → absolute → rva → aob_rip。"""

    def __init__(self, backend) -> None:
        self._backend = backend
        self._specs: dict[str, SymbolSpec] = {}
        self._values: dict[str, int] = {}
        self._manual: dict[str, int] = {}
        # 单个符号的 AOB 失败原因（按符号隔离，不连累其它符号）
        self._aob_errors: dict[str, str] = {}

    def load_specs(self, specs: dict[str, dict]) -> None:
        """合并 mapping 与内置 AOB。

        mapping 若只带 TODO rva / 无 aob，**不会覆盖**内置 AOB 配置。
        """
        for name, raw in DEFAULT_SPECS.items():
            self._specs[name.lower()] = self._make_spec(name, raw)
        for name, raw in (specs or {}).items():
            key = name.lower()
            incoming = self._make_spec(name, raw)
            existing = self._specs.get(key)
            if existing is not None and not self._usable(incoming) and self._usable(existing):
                # mapping 占位无效 → 保留内置 AOB
                continue
            # 有效配置：补齐缺失的 AOB 字段
            if existing is not None:
                if not incoming.aob and existing.aob:
                    incoming.aob = existing.aob
                    incoming.aob_offset = existing.aob_offset
                    if incoming.kind in ("pointer_symbol", "") and existing.kind == "aob_rip":
                        incoming.kind = "aob_rip"
                if not incoming.note:
                    incoming.note = existing.note
            self._specs[key] = incoming

    @staticmethod
    def _usable(spec: SymbolSpec) -> bool:
        return bool(spec.absolute is not None or spec.rva is not None or spec.aob)

    @staticmethod
    def _make_spec(name: str, raw: dict) -> SymbolSpec:
        return SymbolSpec(
            name=name,
            kind=raw.get("kind", "aob_rip"),
            module=raw.get("module", "nightreign.exe"),
            rva=_maybe_int(raw.get("rva")),
            absolute=_maybe_int(raw.get("absolute")),
            aob=raw.get("aob") or raw.get("fallback_aob"),
            aob_offset=int(raw.get("aob_offset") or raw.get("offset") or 0),
            rip_len=int(raw.get("rip_len") or raw.get("len") or 7),
            rip_disp_offset=int(raw.get("rip_disp_offset") or raw.get("rip_offset") or 3),
            note=raw.get("note", ""),
        )

    def set_manual(self, name: str, value: int) -> None:
        self._manual[name.lower()] = int(value)
        self._values[name.lower()] = int(value)

    def clear_manual(self, name: str) -> None:
        """撤掉某个符号的手动值（例如 Gaitem 缓存被释放后，旧地址必须作废）。"""
        key = name.lower()
        self._manual.pop(key, None)
        self._values.pop(key, None)

    def invalidate(self) -> None:
        """清空解析缓存。

        **手动地址也一并清空**：跨进程或重开游戏后，旧的绝对地址必然失效，留着只会
        让下一次解析静默用错地址。需要时由调用方重新 set_manual。
        """
        self._values.clear()
        self._manual.clear()
        self._aob_errors.clear()

    def resolve(self, name: str) -> int:
        key = name.lower()
        if key in self._values:
            return self._values[key]
        if key in self._manual:
            return self._manual[key]
        spec = self._specs.get(key)
        if spec is None:
            raise SymbolResolveError(f"未定义符号: {name}")

        val: Optional[int] = None
        if spec.absolute is not None:
            val = spec.absolute
        elif spec.rva is not None and spec.module:
            base = self._backend.resolve_module_base(spec.module)
            if base:
                val = base + spec.rva
        if val is None and spec.aob:
            self._resolve_all_aob()
            val = self._values.get(key)
        if val is None:
            reason = self._aob_errors.get(key)
            if reason:
                raise SymbolResolveError(f"{reason}\n（符号 {name}）")
            raise SymbolResolveError(
                f"无法解析符号 {name}（{spec.note or spec.kind}）。"
                f"请检查 mapping.json，或在高级模式手动填写地址。"
            )
        self._values[key] = val
        return val

    def _resolve_all_aob(self) -> None:
        """按模块分组批量扫 AOB，把命中结果写入 _values。

        旧实现有两处问题：用 pending[0] 的 module 作为**所有** pattern 的扫描模块；
        且任一 pattern 未命中就整批抛异常，导致一个坏符号拖垮其它符号。
        现在按模块分组，失败只记进 _aob_errors，等真正请求该符号时再抛。
        """
        pending = [
            (k, s)
            for k, s in self._specs.items()
            if s.aob and k not in self._values and k not in self._manual
        ]
        if not pending:
            return

        by_module: dict[str, list[tuple[str, SymbolSpec]]] = {}
        for key, spec in pending:
            by_module.setdefault(spec.module or "nightreign.exe", []).append((key, spec))

        scan_fn = getattr(self._backend, "aob_scan_multi", None)
        for module, items in by_module.items():
            patterns = {k: (s.aob or "") for k, s in items}
            results: dict[str, list[int]] = {}
            if callable(scan_fn):
                try:
                    results = scan_fn(module, patterns) or {}
                except Exception:
                    results = {}  # 批量扫描不可用时逐个退回 aob_scan
            for key, spec in items:
                if key in self._manual:
                    self._values[key] = self._manual[key]
                    continue
                hits = results.get(key)
                if not hits:
                    try:
                        hits = self._backend.aob_scan(module, spec.aob or "")
                    except Exception:
                        hits = []
                if not hits:
                    self._aob_errors[key] = (
                        f"AOB 未命中: {spec.name}（{spec.note}）\n"
                        f"pattern={spec.aob}\n模块={module}"
                    )
                    continue
                hit = hits[0] + spec.aob_offset
                try:
                    disp = struct.unpack("<i", self._backend.read_bytes(hit + spec.rip_disp_offset, 4))[0]
                except Exception as exc:
                    self._aob_errors[key] = f"读取 RIP 位移失败 @{spec.name}: {exc}"
                    continue
                self._values[key] = (hit + spec.rip_len + disp) & 0xFFFFFFFFFFFFFFFF
                self._aob_errors.pop(key, None)

    def as_resolver(self) -> Callable[[str], int]:
        return self.resolve


class GaitemCache:
    """在目标进程分配 0x1000 缓存，写入 6 个槽 smallint。"""

    def __init__(self, backend, symbols: SymbolTable) -> None:
        self._backend = backend
        self._symbols = symbols
        self._addr = 0

    @property
    def address(self) -> int:
        return self._addr

    def ensure(self) -> int:
        if not self._addr:
            self._addr = self._backend.allocate(0x1000)
            self._symbols.set_manual("Gaitem", self._addr)
        return self._addr

    def refresh(self) -> int:
        addr = self.ensure()
        try:
            gdm = self._symbols.resolve("GameDataMan")
            player = self._backend.read_u64(self._backend.read_u64(gdm) + 8)
        except Exception as exc:
            raise SymbolResolveError(f"刷新 Gaitem 失败（GameDataMan）: {exc}") from exc
        if not player:
            raise SymbolResolveError("GameDataMan 玩家数据为空（是否已进入游戏存档？）")
        for i in range(GAITEM_SLOTS):
            src = player + GAITEM_SRC_BASE + i * GAITEM_STRIDE
            try:
                raw = self._backend.read_bytes(src, 2)
                val = struct.unpack("<H", raw)[0]
            except Exception:
                # 读不到就写 0：该槽会被解析到无效对象，界面上表现为读不到遗物
                log.debug("读取 Gaitem 槽 %d 的 smallint 失败 @0x%X", i, src, exc_info=True)
                val = 0
            self._backend.write_bytes(addr + i * GAITEM_STRIDE, struct.pack("<HH", val, 0))
        return addr

    def release(self) -> None:
        if self._addr:
            try:
                self._backend.free(self._addr)
            except Exception:
                log.debug("释放 Gaitem 缓存失败 @0x%X", self._addr, exc_info=True)
            self._addr = 0
            self._symbols.clear_manual("Gaitem")


def _maybe_int(v) -> Optional[int]:
    if v is None or v == "" or str(v).upper().startswith("TODO"):
        return None
    if isinstance(v, int):
        return v
    s = str(v).strip()
    try:
        return int(s, 0)
    except ValueError:
        return None
