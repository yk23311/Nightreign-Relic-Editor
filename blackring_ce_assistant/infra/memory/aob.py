# -*- coding: utf-8 -*-
"""快速 AOB 扫描：只扫可执行段，单次读入 + 跳跃搜索。"""
from __future__ import annotations

from typing import Iterable, Optional, Sequence


def parse_aob_pattern(pattern: str) -> list[int | None]:
    """解析 CE 风格 AOB。None = 通配字节。"""
    raw = (pattern or "").strip()
    if not raw:
        raise ValueError("AOB 为空")
    compact = "".join(raw.split()).upper()
    out: list[int | None] = []
    i = 0
    while i < len(compact):
        ch = compact[i : i + 2]
        if len(ch) < 2:
            raise ValueError(f"AOB 长度必须为偶数个十六进制字符: {pattern!r}")
        if ch == "??" or ch[0] == "?" or ch[1] == "?":
            out.append(None)
        else:
            out.append(int(ch, 16))
        i += 2
    return out


def scan_buffer(buf: bytes, pattern: Sequence[int | None], *, max_hits: int = 8) -> list[int]:
    """在缓冲区内搜索，返回相对偏移。首字节非通配时用快速 find 跳进。"""
    n = len(pattern)
    if n == 0 or len(buf) < n:
        return []
    hits: list[int] = []
    # 找第一个非通配字节用于加速
    anchor = 0
    anchor_byte: Optional[int] = None
    for i, b in enumerate(pattern):
        if b is not None:
            anchor = i
            anchor_byte = b
            break

    if anchor_byte is None:
        return [0]  # 全通配

    start = 0
    limit = len(buf) - n + 1
    find = buf.find
    while start < limit:
        rel = find(bytes([anchor_byte]), start + anchor)
        if rel < 0:
            break
        begin = rel - anchor
        if begin < 0:
            start = rel + 1
            continue
        if begin >= limit:
            break
        ok = True
        base = begin
        for j in range(n):
            p = pattern[j]
            if p is not None and buf[base + j] != p:
                ok = False
                break
        if ok:
            hits.append(base)
            if len(hits) >= max_hits:
                break
            start = base + 1
        else:
            start = rel + 1
    return hits


def scan_regions(
    read_region,
    regions: Iterable[tuple[int, int]],
    pattern: Sequence[int | None],
    *,
    max_hits: int = 8,
    chunk: int = 8 * 1024 * 1024,
    overlap: int | None = None,
) -> list[int]:
    n = len(pattern)
    if overlap is None:
        overlap = max(0, n - 1)
    hits: list[int] = []
    for base, size in regions:
        pos = 0
        while pos < size and len(hits) < max_hits:
            want = min(chunk + overlap, size - pos)
            try:
                buf = read_region(base + pos, want)
            except Exception:
                pos += chunk
                continue
            for off in scan_buffer(buf, pattern, max_hits=max_hits - len(hits)):
                addr = base + pos + off
                if addr not in hits:
                    hits.append(addr)
                    if len(hits) >= max_hits:
                        return hits
            if want <= overlap:
                break
            pos += chunk
    return hits


def scan_regions_multi(
    read_region,
    regions: Iterable[tuple[int, int]],
    patterns: Sequence[tuple[str, Sequence[int | None]]],
    *,
    max_hits: int = 8,
    chunk: int = 8 * 1024 * 1024,
) -> dict[str, list[int]]:
    """单遍扫描多个 pattern，返回 {name: [addr...]}。"""
    nmax = max((len(p) for _n, p in patterns), default=1)
    overlap = max(0, nmax - 1)
    found: dict[str, list[int]] = {n: [] for n, _ in patterns}
    for base, size in regions:
        pos = 0
        while pos < size:
            remaining = {n for n, hits in found.items() if len(hits) < max_hits}
            if not remaining:
                return found
            want = min(chunk + overlap, size - pos)
            try:
                buf = read_region(base + pos, want)
            except Exception:
                pos += chunk
                continue
            for name, pat in patterns:
                if name not in remaining:
                    continue
                for off in scan_buffer(buf, pat, max_hits=max_hits - len(found[name])):
                    addr = base + pos + off
                    if addr not in found[name]:
                        found[name].append(addr)
            if want <= overlap:
                break
            pos += chunk
    return found
