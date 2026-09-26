# -*- coding: utf-8 -*-
"""版本号唯一来源。

约定：语义化版本 MAJOR.MINOR.PATCH。
0.5.0 = 减益功能落地时的状态；0.5.1 = 审查报告修复批次；
0.6.0 = 移除 RNG 安全门 + 高级模式跨选 + 手动 ID（为实机验证跨选写入而先行的功能版）；
0.6.1 = 换用重新翻译的 CT，高级模式改用 CT 全量表 RelicID 并按 CT 自带分组显示；
0.7.0 = 界面重构：pywebview + Web 前端（卡片工作台），去 PySide6，改 onedir 分发；
1.0.0 = 首个对外发布版：整理仓库、完善说明书、版本号转正。
旧产物名 黑环CE助手_vN.exe 的 N 与版本号无固定关系，对应关系见 README「版本历史」。
本模块**不得**导入任何第三方库，以便 PyInstaller 的 .spec 在打包前直接读取。
"""

__version__ = "1.0.0"

APP_NAME = "黑环CE助手"
APP_NAME_EN = "BlackRing CE Assistant"
BUILD_DATE = "2026-09-26"


def version_string() -> str:
    """界面/日志用，例如：黑环CE助手 v1.0.0"""
    return f"{APP_NAME} v{__version__}"
