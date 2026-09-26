# -*- coding: utf-8 -*-
"""多级指针链求值 — 对齐 CE getPointerAddress 算法。

CE CEFuncProc.getPointerAddress:
    realaddress2 := address
    for i := length(offsets)-1 downto 0 do
    begin
      realaddress := read_ptr(realaddress2)
      realaddress2 := realaddress + offsets[i]
    end
    result := realaddress2

XML 中 Offsets 第一项 = pointeroffsets[0] = 最终字段偏移（最后应用）。
本 CT: Offsets = ['+18', '8+8*[Gaitem]']
  p = read_ptr(csgaitem) + (8+8*[Gaitem])
  p = read_ptr(p) + 0x18
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Sequence

from mapping.address_expr import AddressExprEvaluator, ExprError, parse_offset


@dataclass
class ResolvedAddress:
    address: int
    resolved_at: float
    steps: tuple[int, ...]


class PointerResolver:
    def __init__(
        self,
        backend,
        *,
        symbol_resolver: Callable[[str], int],
        cache_ttl: float = 0.5,
    ) -> None:
        self._backend = backend
        self._cache_ttl = cache_ttl
        self._cache: dict[tuple, ResolvedAddress] = {}
        self._current_symbols: Callable[[str], int] = symbol_resolver
        # 字段/槽位公式里的 [Gaitem] 按 32 位读取（与 writeSmallInteger 缓存兼容）
        self._eval = AddressExprEvaluator(
            resolve_symbol=self._resolve_sym_wrapper,
            read_u32=backend.read_u32,
            read_u64=backend.read_u64,
            deref_bits=32,
        )
        self._eval64 = AddressExprEvaluator(
            resolve_symbol=self._resolve_sym_wrapper,
            read_u32=backend.read_u32,
            read_u64=backend.read_u64,
            deref_bits=64,
        )

    def _resolve_sym_wrapper(self, name: str) -> int:
        return self._current_symbols(name)

    def set_symbol_resolver(self, fn: Callable[[str], int]) -> None:
        self._current_symbols = fn

    def eval_expr(self, expr: str) -> int:
        return self._eval.eval(expr)

    def eval_expr_u64(self, expr: str) -> int:
        return self._eval64.eval(expr)

    def clear_cache(self) -> None:
        self._cache.clear()

    def resolve(
        self,
        base_expr: str,
        offsets: Sequence[str],
        *,
        use_cache: bool = True,
    ) -> int:
        """按 CE 语义解析指针链，返回最终字段地址。"""
        key = (base_expr, tuple(offsets))
        now = time.time()
        if use_cache and key in self._cache:
            hit = self._cache[key]
            if now - hit.resolved_at < self._cache_ttl:
                return hit.address

        # 基址：符号/表达式地址（不自动解引用 — 解引用由 CE 循环完成）
        try:
            p = self._eval64.eval(base_expr)
        except ExprError:
            raise

        steps = [p]
        # CE: for i := length(offsets)-1 downto 0
        for off in reversed(list(offsets)):
            off = (off or "").strip()
            if not off:
                continue
            try:
                p = self._backend.read_u64(p)
            except Exception as exc:
                raise AddressInvalidLike(f"指针解引用失败 @0x{p:X}: {exc}") from exc
            if any(ch in off for ch in "[]*"):
                delta = self._eval.eval(off)
            else:
                delta = parse_offset(off)
            p = (p + delta) & 0xFFFFFFFFFFFFFFFF
            steps.append(p)

        if use_cache:
            self._cache[key] = ResolvedAddress(address=p, resolved_at=now, steps=tuple(steps))
        return p


class AddressInvalidLike(Exception):
    """指针链中断。"""


def resolve_ce_field(
    backend,
    symbol_resolver: Callable[[str], int],
    address_expr: str,
    offsets: Sequence[str],
) -> int:
    pr = PointerResolver(backend, symbol_resolver=symbol_resolver)
    return pr.resolve(address_expr, offsets, use_cache=False)
