# -*- coding: utf-8 -*-
from xml.etree import ElementTree as ET
from pathlib import Path

ROOT = Path(r"D:\练习项目\黑环ce助手")
CT = ROOT / "Elden Ring Nighreign - Hexinton 1.1.0 汉化版.CT"
OUT = ROOT / "analysis"


def t(el, tag):
    n = el.find(tag)
    return (n.text or "").strip() if n is not None and n.text else ""


def main():
    text = CT.read_text(encoding="utf-8")
    root = ET.fromstring(text)

    for e in root.iter("CheatEntry"):
        if "启用" in t(e, "Description"):
            sc = e.find("AssemblerScript")
            if sc is not None:
                s = "".join(sc.itertext())
                (OUT / "enable_script.txt").write_text(s, encoding="utf-8")
                print("ENABLE_LEN", len(s))
                low = s.lower()
                for kw in ["Gaitem", "csgaitem", "registersymbol", "globalalloc", "getAddress",
                           "aobscan", "MainPlayer", "worldchrman", "WorldChrMan", "readInteger",
                           "CSGaitem", "symbol"]:
                    print(f"  count[{kw}]={low.count(kw.lower())}")
                break

    hits = []
    for e in root.iter("CheatEntry"):
        sc = e.find("AssemblerScript")
        if sc is None:
            continue
        s = "".join(sc.itertext())
        if "gaitem" in s.lower():
            hits.append((t(e, "Description"), s))
    print("SCRIPTS_WITH_GAITEM", len(hits))
    for desc, s in hits[:20]:
        print("=" * 60)
        print("DESC:", desc[:80])
        print(s[:800])
        (OUT / f"script_{abs(hash(desc))%10**8}.txt").write_text(s, encoding="utf-8")

    # also search raw for SymbolHandler / registerSymbol('Gaitem'
    import re
    for m in re.finditer(r".{0,80}Gaitem.{0,80}", text, re.I):
        line = m.group(0).replace("\n", " ")
        if "gaitem" in line.lower():
            print("RAW:", line[:200])


if __name__ == "__main__":
    main()
