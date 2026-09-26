# -*- coding: utf-8 -*-
"""写入事务：备份 → 写入 → 失败回滚。"""
from __future__ import annotations

import datetime as _dt
import logging
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from .base import IMemoryBackend

log = logging.getLogger(__name__)


@dataclass
class TxnWrite:
    address: int
    new_bytes: bytes
    meta: dict = field(default_factory=dict)


@dataclass
class TxnResult:
    ok: bool
    applied: list[tuple[int, bytes, bytes]] = field(default_factory=list)  # addr, old, new
    rolled_back: bool = False
    error: str = ""


class MemoryTransaction:
    def __init__(self, backend: IMemoryBackend) -> None:
        self._backend = backend
        self._backups: list[tuple[int, bytes]] = []

    def execute(self, writes: Sequence[TxnWrite], *, dry_run: bool = False) -> TxnResult:
        if not writes:
            return TxnResult(ok=True)

        backups: list[tuple[int, bytes, bytes]] = []
        # 1) 备份
        for w in writes:
            try:
                old = self._backend.read_bytes(w.address, len(w.new_bytes))
            except Exception as exc:
                return TxnResult(ok=False, error=f"备份读取失败 @0x{w.address:X}: {exc}")
            backups.append((w.address, old, w.new_bytes))

        if dry_run:
            return TxnResult(ok=True, applied=backups)

        # 2) 写入
        applied: list[tuple[int, bytes, bytes]] = []
        for addr, old, new in backups:
            try:
                self._backend.write_bytes(addr, new)
            except Exception as exc:
                # 回滚已写。**回滚失败绝不能静默**：那意味着内存处于部分写入状态，
                # 而用户只会看到「已回滚」，下次启动游戏可能读到半截数据。
                broken: list[str] = []
                for a2, o2, _n2 in applied:
                    try:
                        self._backend.write_bytes(a2, o2)
                    except Exception as exc2:
                        broken.append(f"0x{a2:X}({exc2})")
                if broken:
                    log.error("事务回滚失败 %d 项: %s", len(broken), broken)
                    return TxnResult(
                        ok=False,
                        applied=[],
                        rolled_back=False,
                        error=(
                            f"写入失败 @0x{addr:X}: {exc}；回滚也失败 {len(broken)} 项"
                            f"（{', '.join(broken)}）—— 内存可能处于不一致状态，"
                            f"请立即「备份」留证并重启游戏，不要继续写入。"
                        ),
                    )
                return TxnResult(
                    ok=False,
                    applied=[],
                    rolled_back=True,
                    error=f"写入失败 @0x{addr:X}: {exc}（已回滚 {len(applied)} 项）",
                )
            applied.append((addr, old, new))

        return TxnResult(ok=True, applied=applied)

    def restore(self, backups: Iterable[tuple[int, bytes]]) -> TxnResult:
        applied: list[tuple[int, bytes, bytes]] = []
        for addr, old in backups:
            try:
                cur = self._backend.read_bytes(addr, len(old))
                self._backend.write_bytes(addr, old)
                applied.append((addr, cur, old))
            except Exception as exc:
                return TxnResult(ok=False, applied=applied, error=f"恢复失败 @0x{addr:X}: {exc}")
        return TxnResult(ok=True, applied=applied)


def pack_u32(value: int) -> bytes:
    return int(value & 0xFFFFFFFF).to_bytes(4, "little", signed=False)


def unpack_u32(data: bytes) -> int:
    return int.from_bytes(data[:4], "little", signed=False)


def now_iso() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")
