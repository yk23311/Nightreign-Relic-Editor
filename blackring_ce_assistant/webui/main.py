# -*- coding: utf-8 -*-
"""0.7.0 新界面入口（pywebview + Web 前端）。

用法：
    python -m webui.main           正常启动（附加 nightreign.exe）
    python -m webui.main --demo     演示模式：用假内存，不需要游戏

免责声明：仅供学习与离线/私人测试使用，禁止在线作弊或反作弊绕过。
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

log = logging.getLogger(__name__)

WEBVIEW2_GUID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
WEBVIEW2_URL = "https://developer.microsoft.com/microsoft-edge/webview2/"


def base_dir() -> Path:
    """冻结后资源在 _MEIPASS（onedir 即 _internal/）；开发态是本文件同级的 web/。"""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent


def detect_webview2() -> str:
    """注册表检测 WebView2 运行时版本；未安装返回空串。"""
    import winreg

    # 注意：原始字符串不能以反斜杠结尾，这里显式拼接
    rel = r"Microsoft\EdgeUpdate\Clients" + "\\" + WEBVIEW2_GUID
    for hive, path in (
        (winreg.HKEY_LOCAL_MACHINE, "SOFTWARE\\WOW6432Node\\" + rel),
        (winreg.HKEY_LOCAL_MACHINE, "SOFTWARE\\" + rel),
        (winreg.HKEY_CURRENT_USER, "Software\\" + rel),
    ):
        try:
            with winreg.OpenKey(hive, path) as k:
                return str(winreg.QueryValueEx(k, "pv")[0])
        except OSError:
            continue
    return ""


def _fatal(title: str, text: str) -> None:
    """界面起不来时的最后手段：不依赖任何 GUI 库的原生消息框。"""
    print(title + "\n" + text, file=sys.stderr)
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, text, title, 0x10)
    except Exception:
        pass


def main(argv: list[str]) -> int:
    demo = "--demo" in argv

    from app.logging_setup import setup_logging
    from app.paths import data_dir, user_data_dir
    from app.services.relic_service import PresetService, RelicService
    from app.version import __version__

    setup_logging(user_data_dir() / "logs")
    log.info("=== 启动 黑环CE助手 v%s（新界面%s）===", __version__, "，演示模式" if demo else "")

    try:
        import webview
    except Exception as exc:
        _fatal("缺少依赖", "无法加载 pywebview：" + str(exc) + "\n\n请先执行：pip install -r requirements.txt")
        return 2

    ver = detect_webview2()
    if not ver:
        log.error("未检测到 WebView2 运行时")
        _fatal(
            "缺少 WebView2 运行时",
            "本程序的新界面依赖微软的 WebView2 运行时，当前系统未安装。\n\n"
            "请到下面地址下载安装「Evergreen Bootstrapper」后重试：\n" + WEBVIEW2_URL,
        )
        return 3
    log.info("WebView2 运行时版本 %s", ver)

    index = base_dir() / "web" / "index.html"
    if not index.exists():
        _fatal("资源缺失", "找不到界面文件：\n" + str(index))
        return 4

    from domain.enums import EffectCatalog
    from infra.memory.win_process import WinProcessBackend
    from mapping.mapping_config import load_mapping
    from webui.bridge import Bridge

    try:
        mapping = load_mapping(data_dir() / "mapping.json")
        catalog = EffectCatalog.from_data_dir(data_dir())
    except Exception as exc:
        _fatal("映射加载失败", str(exc))
        return 5

    if demo:
        from webui.demo import make_demo_service

        svc = make_demo_service(mapping, catalog)
    else:
        svc = RelicService(WinProcessBackend(), mapping, catalog, readonly_preview=True)

    bridge = Bridge(
        svc,
        presets=PresetService(user_data_dir() / "presets"),
        backup_path=user_data_dir() / "backups" / "latest.json",
        app_version=__version__,
        demo=demo,
    )

    title = "黑环CE助手 v" + __version__ + ("（演示模式）" if demo else "")
    webview.create_window(title, str(index), js_api=bridge, width=1380, height=880,
                          min_size=(1080, 680))
    # private_mode=False + 固定 storage_path：
    # 默认的 private_mode 每次启动都新建临时配置目录，既慢又容易在连续启动时初始化失败
    # （实测出现约 1/3 概率窗口加载不出页面、连 boot 都不触发）。
    # 用持久化目录后 WebView2 复用同一份 profile，启动更快也更稳。
    storage = user_data_dir() / "webview"
    storage.mkdir(parents=True, exist_ok=True)
    webview.start(debug=False, private_mode=False, storage_path=str(storage))
    log.info("=== 退出 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
