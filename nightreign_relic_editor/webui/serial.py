# -*- coding: utf-8 -*-
"""把内存操作串行化。

为什么必须要有这一层：pywebview 的 js_api 方法由**不同的线程**调用，而
RelicService 内部的 PointerResolver 是共享状态（地址表达式求值器曾经就是因为它
不可重入而出过问题，见 0.5.1 审查 P2-5）。前端可能同时发起"刷新"和"应用"，
所以**不能指望调用方自觉不并发**，必须在后端强制排队。
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator, Optional


class Serializer:
    """可重入锁 + 当前操作标签（供前端显示"正在做什么"）。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._busy: Optional[str] = None
        self._owner: Optional[int] = None

    @property
    def busy(self) -> Optional[str]:
        return self._busy

    @contextmanager
    def run(self, label: str) -> Iterator[None]:
        with self._lock:
            self._busy = label
            self._owner = threading.get_ident()
            try:
                yield
            finally:
                self._busy = None
                self._owner = None

    def status(self) -> dict:
        return {"busy": self._busy, "reentrantDepth": 0}
