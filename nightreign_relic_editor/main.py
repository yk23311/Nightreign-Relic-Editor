# -*- coding: utf-8 -*-
"""Nightreign-Relic-Editor（黑环CE助手）入口。

免责声明：本工具仅供学习与离线/私人测试使用，请遵守游戏 ToS 与当地法律法规。
禁止用于在线作弊、反作弊绕过或联机篡改。

0.7.0 起界面为 pywebview + Web 前端，实现在 webui/main.py。
保留本文件是为了让 python main.py 继续可用（README 与历史文档都是这么写的）。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    try:
        from webui.main import main as ui_main
    except ImportError as exc:
        print("无法加载界面（是否安装 pywebview？）:", exc)
        print("可先运行: python run_tests.py 验证核心模块")
        return 1
    return ui_main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
