# -*- coding: utf-8 -*-
"""打包脚本：python build.py

产物：dist/黑环CE助手_v<版本>/（PyInstaller onedir 绿色文件夹）

**不要改回整目录清理 dist/**：历史上这里用 shutil.rmtree 同时清 "build" 和 "dist"，
而 dist/ 里是各版本唯一的一份交付产物，跑一次打包就把历史交付物全删了。
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.version import __version__  # noqa: E402

DIST = ROOT / "dist"
SPEC = ROOT / "webui.spec"


def _is_locked(path: Path) -> bool:
    """被运行中的同名 exe 占用时无法以追加方式打开。"""
    try:
        with path.open("ab"):
            return False
    except OSError:
        return True


def main() -> int:
    pyinstaller = ROOT / ".venv" / "Scripts" / "pyinstaller.exe"
    if not pyinstaller.exists():
        pyinstaller = Path(sys.executable).parent / "pyinstaller.exe"
    if not pyinstaller.exists():
        print("未找到 pyinstaller，请先: pip install -r requirements.txt")
        return 1

    name = f"黑环CE助手_v{__version__}"
    target = DIST / name
    exe = target / f"{name}.exe"

    if exe.exists() and _is_locked(exe):
        print(f"打包中止：{exe.name} 正在被占用（程序可能仍在运行）。")
        print("请先结束该进程后重试；dist/ 下其它版本的产物不受影响。")
        return 1

    # 只清 PyInstaller 的中间目录 build/；dist/ 保持不动（见模块 docstring）
    build_dir = ROOT / "build"
    if build_dir.exists():
        shutil.rmtree(build_dir, ignore_errors=True)
    DIST.mkdir(parents=True, exist_ok=True)

    print("打包目标：" + str(target))
    cmd = [str(pyinstaller), "--noconfirm", "--clean", str(SPEC)]
    print("执行:", " ".join(cmd))
    r = subprocess.call(cmd, cwd=str(ROOT))
    if r != 0:
        print("打包失败, exit=", r)
        return r

    if exe.exists():
        total = sum(f.stat().st_size for f in target.rglob("*") if f.is_file())
        print(f"OK: {target}/  (合计 {total / 1024 / 1024:.1f} MB, onedir)")
        print("提示: dist/ 下旧版本产物未被删除；如需清理请人工确认后再删。")
    else:
        print(f"未找到打包产物（期望 {exe}），请检查 dist/ 与 spec 里的 name=")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
