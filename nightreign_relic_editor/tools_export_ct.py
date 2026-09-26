# -*- coding: utf-8 -*-
"""从 CT 导出 data/ 下映射与枚举。用法：
python tools_export_ct.py "path/to/table.CT" data
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from mapping.ct_parser import export_data_files


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    ct = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else "data"
    print(export_data_files(ct, out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
