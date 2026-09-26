# -*- coding: utf-8 -*-
"""日志初始化：写文件 + stderr。

为什么需要：本项目此前**没有任何 logging**，21 处宽泛 except 直接 pass，
worker 线程只 emit str(exc)、traceback 全部丢失。用户机器上出错后无法事后取证。

日志文件写在可写目录（源码态=项目根，冻结态=exe 旁）的 data/logs/ 下。
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

_LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def setup_logging(log_dir: Path | str, *, level: int = logging.INFO) -> Path:
    """配置根 logger，返回日志文件路径。重复调用不会叠加 handler。"""
    d = Path(log_dir)
    d.mkdir(parents=True, exist_ok=True)
    log_file = d / f"app-{datetime.now():%Y%m%d}.log"

    root = logging.getLogger()
    root.setLevel(level)

    # 幂等：避免重复调用时同一条日志写两遍
    for h in list(root.handlers):
        if getattr(h, "_blackring_handler", False):
            root.removeHandler(h)

    fmt = logging.Formatter(_LOG_FORMAT)

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    fh.setLevel(level)
    fh._blackring_handler = True  # type: ignore[attr-defined]
    root.addHandler(fh)

    # 打包成 -w（无控制台）时 sys.stderr 可能为 None
    if sys.stderr is not None:
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        sh.setLevel(level)
        sh._blackring_handler = True  # type: ignore[attr-defined]
        root.addHandler(sh)

    return log_file
