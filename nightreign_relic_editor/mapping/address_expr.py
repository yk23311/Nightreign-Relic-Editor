# -*- coding: utf-8 -*-
"""CE 风格地址表达式子集求值器。

支持（覆盖本 CT 遗物字段所需）：
- 整型常量（**CE 风格十六进制**，如 ``18`` ``1c`` ``8``；可选 ``0x`` 前缀）
- 符号名（大小写不敏感查找）
- ``[expr]`` 解引用（默认 32 位，可用 deref64）
- ``+`` ``-`` ``*`` 二元运算（左结合）
- 括号

示例：``8+8*[Gaitem+4]``、``+18``、``csgaitem``、``csgaitem+4``
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Protocol


class SupportsReadU32(Protocol):
    def read_u32(self, address: int) -> int: ...


class SupportsReadU64(Protocol):
    def read_u64(self, address: int) -> int: ...


class ExprError(ValueError):
    pass


_TOKEN_RE = re.compile(
    r"""
    \s*(?:
        (?P<num>0[xX][0-9A-Fa-f]+|[0-9][0-9A-Fa-f]*)
      | (?P<ident>[A-Za-z_][A-Za-z0-9_]*)
      | (?P<op>[\[\]+\-*()])
    )
    """,
    re.VERBOSE,
)


@dataclass(frozen=True)
class _Tok:
    kind: str
    value: str


def _parse_num(text: str) -> int:
    """CE 地址表达式中的裸数字按十六进制解释。"""
    t = text.strip()
    if t.lower().startswith("0x"):
        return int(t, 16)
    return int(t, 16)


def _tokenize(src: str) -> list[_Tok]:
    tokens: list[_Tok] = []
    pos = 0
    while pos < len(src):
        m = _TOKEN_RE.match(src, pos)
        if not m or m.end() == pos:
            raise ExprError(f"非法表达式位置: {src[pos:]!r}")
        if m.group("num"):
            tokens.append(_Tok("num", m.group("num")))
        elif m.group("ident"):
            tokens.append(_Tok("ident", m.group("ident")))
        else:
            tokens.append(_Tok("op", m.group("op")))
        pos = m.end()
    return tokens


class _Parser:
    """一次性使用的递归下降解析器。

    解析状态（记号表 + 游标）必须放在每次 eval 新建的实例上，**不能**挂在
    AddressExprEvaluator 上：求值器是长生命周期对象，RelicService 只建一个
    PointerResolver 并共享给所有 QThread worker，游标共享会让并发 eval() 互相踩状态。
    """

    __slots__ = ("_tokens", "_i", "_deref", "_resolve_symbol")

    def __init__(self, tokens, *, deref, resolve_symbol) -> None:
        self._tokens = tokens
        self._i = 0
        self._deref = deref
        self._resolve_symbol = resolve_symbol

    def at_end(self) -> bool:
        return self._i == len(self._tokens)

    def _peek(self) -> _Tok | None:
        return self._tokens[self._i] if self._i < len(self._tokens) else None

    def _next(self) -> _Tok:
        if self._i >= len(self._tokens):
            raise ExprError("表达式意外结束")
        t = self._tokens[self._i]
        self._i += 1
        return t

    def parse_expr(self) -> int:
        left = self._parse_term()
        while True:
            tok = self._peek()
            if tok and tok.kind == "op" and tok.value in ("+", "-"):
                self._next()
                right = self._parse_term()
                left = left + right if tok.value == "+" else left - right
            else:
                return left

    def _parse_term(self) -> int:
        left = self._parse_factor()
        while True:
            tok = self._peek()
            if tok and tok.kind == "op" and tok.value == "*":
                self._next()
                right = self._parse_factor()
                left = left * right
            else:
                return left

    def _parse_factor(self) -> int:
        tok = self._peek()
        if tok is None:
            raise ExprError("表达式意外结束")
        if tok.kind == "num":
            self._next()
            return _parse_num(tok.value)
        if tok.kind == "ident":
            self._next()
            try:
                return self._resolve_symbol(tok.value)
            except Exception:
                # CE 风格：Gaitem+c / +1c 里裸的 a-f 被当作十六进制偏移
                if re.fullmatch(r"[0-9A-Fa-f]+", tok.value):
                    return int(tok.value, 16)
                raise
        if tok.kind == "op":
            if tok.value == "(":
                self._next()
                v = self.parse_expr()
                closer = self._next()
                if closer.value != ")":
                    raise ExprError("缺少 )")
                return v
            if tok.value == "[":
                self._next()
                inner = self.parse_expr()
                closer = self._next()
                if closer.value != "]":
                    raise ExprError("缺少 ]")
                return self._deref(inner)
            if tok.value == "+":
                self._next()
                return self._parse_factor()
            if tok.value == "-":
                self._next()
                return -self._parse_factor()
        raise ExprError(f"无法解析记号: {tok}")


class AddressExprEvaluator:
    """求值 CE 表达式子集。符号通过 ``resolve_symbol(name) -> int`` 注入。"""

    def __init__(
        self,
        resolve_symbol: Callable[[str], int],
        *,
        read_u32: Callable[[int], int] | None = None,
        read_u64: Callable[[int], int] | None = None,
        deref_bits: int = 32,
    ) -> None:
        self._resolve_symbol = resolve_symbol
        self._read_u32 = read_u32
        self._read_u64 = read_u64
        self._deref_bits = deref_bits

    def eval(self, expr: str) -> int:
        expr = (expr or "").strip()
        if not expr:
            return 0
        # 允许前导 +  (如 "+18")
        if expr.startswith("+") and not expr.startswith("++"):
            expr = expr[1:]
        # 每次求值新建解析器：解析状态绝不能挂在求值器实例上（见 _Parser 注释）
        parser = _Parser(
            _tokenize(expr),
            deref=self._deref,
            resolve_symbol=self._resolve_symbol,
        )
        value = parser.parse_expr()
        if not parser.at_end():
            raise ExprError(f"表达式尾部有多余内容: {expr!r}")
        return value

    def _deref(self, address: int) -> int:
        if self._deref_bits == 64 and self._read_u64 is not None:
            return self._read_u64(address) & 0xFFFFFFFFFFFFFFFF
        if self._read_u32 is None:
            raise ExprError("表达式需要解引用，但未提供 read_u32")
        return self._read_u32(address) & 0xFFFFFFFF


def parse_offset(offset: str) -> int:
    """把 '+18' / '18' / '0x1c' 解析为带符号偏移（CE 十六进制）。"""
    s = (offset or "").strip()
    if not s:
        return 0
    sign = 1
    if s.startswith("+"):
        s = s[1:]
    elif s.startswith("-"):
        sign = -1
        s = s[1:]
    return sign * _parse_num(s)
