# -*- coding: utf-8 -*-
"""端到端验证新界面：在 WebView2 里真实驱动 UI，最后检查内存是否真被写入。

不做「文件存在即通过」这种事。这里模拟真实操作序列：
  等就绪 -> 点卡片行开选择器 -> 切高级模式再看 -> 搜索 -> 选词条 -> 勾选槽 -> 点应用
并且对比 apply 前后 **Mock 后端里的真实字节**，确认写入链路是通的。
"""
from __future__ import annotations

import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import logging

import webview

# 验证脚本自己也要看得到日志：否则 bridge 里的失败警告会被丢掉，排查时完全盲飞
logging.basicConfig(
    level=logging.INFO,
    format="  [py] %(levelname)-7s %(name)s: %(message)s",
)

from app.services.relic_service import PresetService
from domain.enums import EffectCatalog
from mapping.mapping_config import load_mapping
from webui.bridge import Bridge
from webui.demo import make_demo_service

R: dict = {}
SVC = None
MEM = None


def worker() -> None:
    time.sleep(2.5)
    w = webview.windows[0]

    def ev(js):
        return w.evaluate_js(js)

    def apply_now(wait=3.0):
        """点「应用」，若弹出批量确认框则确认它。

        不处理确认框会导致确认框残留：后续步骤再查 #cf-yes 会命中**上一个**浮层，
        于是点错按钮、等错条件（踩过一次，症状是恢复备份恢复出了上一次运行的旧数据）。
        """
        ev("document.getElementById('btn-apply').click()")
        time.sleep(0.7)
        if ev("!!document.getElementById('cf-yes')"):
            ev("document.getElementById('cf-yes').click()")
        time.sleep(wait)

    def clear_pending():
        ev("document.getElementById('btn-clear').click()")
        time.sleep(0.8)

    def wait_for(js_cond, timeout=8.0, label="条件"):
        """轮询等待条件成立。固定 sleep 会随机器负载飘，是假失败的常见来源。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if ev("!!(" + js_cond + ")"):
                    return True
            except Exception:
                pass
            time.sleep(0.12)
        R.setdefault("timeouts", []).append(label)
        return False

    def snap(tag):
        R[tag] = ev("JSON.stringify(window.__ui.selftest())")

    # 等前端就绪
    ok = False
    for _ in range(160):  # 最多等 40 秒
        try:
            if ev("!!(window.__ui && window.__ui.selftest().ready)"):
                ok = True
                break
            # 启动流程已结束却没就绪 = 明确的启动失败，不必再干等
            if ev("!!window.__bootDone"):
                break
        except Exception:
            pass
        time.sleep(0.25)
    R["ready"] = ok
    if not ok:
        # 区分「启动失败」与「一直卡着」：前者是明确的 bug，后者才需要继续查时序
        try:
            R["boot_stage"] = ev("String((window.__ui && window.__ui.selftest().bootStage) || window.__bootStage)")
            R["js_errors"] = ev("JSON.stringify(window.__errors || [])")
        except Exception as exc:
            R["boot_stage"] = "读取失败: " + repr(exc)
    if not ok:
        try:
            w.destroy()
        except Exception:
            pass
        return

    snap("1_initial")

    # --- 空闲不得重绘（回归：render 与 queueValidate 曾互相触发，220ms 一轮无限重建，
    #     表现为卡片悬停闪烁、点击在 mousedown 与 mouseup 之间被吞掉）---
    time.sleep(0.6)
    _a = json.loads(ev("JSON.stringify(window.__ui.selftest())"))
    time.sleep(3.0)
    _b = json.loads(ev("JSON.stringify(window.__ui.selftest())"))
    R["0_idle_3s"] = "render +%d, renderCards +%d" % (
        _b["renders"] - _a["renders"], _b["cardRenders"] - _a["cardRenders"])

    # --- 连续点击 12 次：间隔刻意落在原 220ms 周期的不同相位上 ---
    _ok, _bad = 0, []
    for _i in range(12):
        time.sleep(0.11)
        ev("document.querySelectorAll('.card-head')[0].click()")
        time.sleep(0.13)
        _sel = ev("JSON.stringify(Object.keys(window.__ui.state.selected))")
        _want = '[\"1\"]' if _i % 2 == 0 else "[]"
        if _sel == _want:
            _ok += 1
        else:
            _bad.append("第%d次 期望%s 实际%s" % (_i + 1, _want, _sel))
    R["0b_click_reliability"] = "%d/12 正确" % _ok
    R["0c_click_failures"] = _bad[:3]
    # 清掉点击测试留下的选中，避免影响后续步骤
    ev("document.querySelectorAll('.card.sel').forEach(function(c){c.querySelector('.card-head').click();})")
    time.sleep(0.5)

    # --- 打开选择器（安全模式）---
    ev("document.querySelector('.slot-row').click()")
    time.sleep(0.8)
    R["2_picker_safe"] = ev(
        "JSON.stringify({open:document.getElementById('pk-overlay').classList.contains('open'),"
        "title:document.getElementById('pk-title').textContent,"
        "stat:document.getElementById('pk-stat').textContent,"
        "rows:document.querySelectorAll('#pk-list .pk-row').length})")
    ev("document.getElementById('pk-close').click()")
    time.sleep(0.3)

    # --- 切高级模式后再开 ---
    ev("document.getElementById('adv').click()")
    time.sleep(1.0)
    ev("document.querySelector('.slot-row').click()")
    time.sleep(1.0)
    R["3_picker_advanced"] = ev(
        "JSON.stringify({title:document.getElementById('pk-title').textContent,"
        "stat:document.getElementById('pk-stat').textContent,"
        "groups:document.querySelectorAll('#pk-list .pk-group').length,"
        "rows:document.querySelectorAll('#pk-list .pk-row').length,"
        "manualVisible:document.getElementById('pk-manual').style.display})")

    # 取消隐藏「非法」组：应变成 8 组、1076 行
    ev("document.getElementById('pk-hide-illegal').click()")
    time.sleep(0.6)
    R["3b_show_illegal"] = ev(
        "JSON.stringify({groups:document.querySelectorAll('#pk-list .pk-group').length,"
        "rows:document.querySelectorAll('#pk-list .pk-row').length,"
        "stat:document.getElementById('pk-stat').textContent})")
    ev("document.getElementById('pk-hide-illegal').click()")
    time.sleep(0.4)

    # --- 搜索 ---
    ev("var q=document.getElementById('pk-q');q.value='攻击';q.dispatchEvent(new Event('input'));")
    time.sleep(0.5)
    R["4_search"] = ev(
        "JSON.stringify({rows:document.querySelectorAll('#pk-list .pk-row').length,"
        "foot:document.getElementById('pk-foot').textContent})")
    ev("var q=document.getElementById('pk-q');q.value='';q.dispatchEvent(new Event('input'));")
    time.sleep(0.4)

    # --- 选第一条 -> 产生 pending ---
    R["5_picked"] = ev("(function(){var r=document.querySelector('#pk-list .pk-row');"
                       "var id=r.dataset.id;r.click();return id;})()")
    time.sleep(0.8)
    snap("6_after_pick")

    # --- 勾选槽 1 并应用 ---
    ev("document.querySelector('.card-head input').click()")
    time.sleep(0.5)
    R["7_before_apply_mem"] = MEM.read_u32(0xA018)
    ev("document.getElementById('btn-apply').click()")
    time.sleep(3.0)
    R["8_after_apply_mem"] = MEM.read_u32(0xA018)
    snap("9_after_apply")

    # --- 撤销 ---
    ev("document.getElementById('btn-undo').click()")
    time.sleep(2.0)
    R["10_after_undo_mem"] = MEM.read_u32(0xA018)

    # --- 选中方式：整张卡片头都应可点（只让 13px 复选框可点，用户会找不到）---
    ev("document.querySelectorAll('.card.sel').forEach(function(c){c.querySelector('.card-head').click();})")
    time.sleep(0.8)
    ev("document.querySelectorAll('.card-head .no')[0].click()")
    time.sleep(0.8)
    R["11_select_by_title"] = ev(
        "JSON.stringify({apply:document.getElementById('btn-apply').textContent,"
        "sel:document.querySelectorAll('.card.sel').length,"
        "btnDisabled:document.getElementById('btn-apply').disabled})")

    # --- 选项 B：非法改动要置灰「应用」并在列表中标出 ---
    # 注意：按**目标状态**设置而不是盲目 click()。前面几步已经可能把它打开了，
    # 再 toggle 一次反而会关掉，于是手动 ID 被「仅高级模式可用」拦下（踩过一次）。
    ev("if(!window.__ui.state.advanced){document.getElementById('adv').click();}")
    time.sleep(1.0)
    R["13_pre_advanced"] = ev("JSON.stringify({advanced:window.__ui.state.advanced})")
    ev("document.querySelectorAll('.card')[0].querySelectorAll('.slot-row')[1].click()")  # 槽1 属性2
    time.sleep(0.9)
    # 用「手动 ID」写一个 DoN 词条进普通槽：高级模式下不拦，切回安全模式后非法
    ev("document.getElementById('pk-manual-id').value='6001400';"
       "document.getElementById('pk-manual-go').click()")
    time.sleep(0.9)
    R["13a_advanced_pending"] = ev(
        "JSON.stringify({pending:window.__ui.selftest().pending,"
        "btnDisabled:document.getElementById('btn-apply').disabled})")
    ev("if(window.__ui.state.advanced){document.getElementById('adv').click();}")  # 切回安全模式
    time.sleep(2.0)
    R["13b_after_back_to_safe"] = ev(
        "JSON.stringify({pending:window.__ui.selftest().pending,"
        "btnText:document.getElementById('btn-apply').textContent,"
        "btnDisabled:document.getElementById('btn-apply').disabled,"
        "markedBad:document.querySelectorAll('#chglist .chg.bad').length,"
        "blockIssues:document.querySelectorAll('#issues .issue.block').length,"
        "badTip:(function(){var t=document.querySelector('#chglist .hint');"
        "return !!(t \u0026\u0026 t.textContent.indexOf('非法')>=0);})()})")
    # 清空改动后应恢复可点
    ev("document.getElementById('btn-clear').click()")
    time.sleep(1.2)
    R["13c_after_clear"] = ev(
        "JSON.stringify({pending:window.__ui.selftest().pending,"
        "btnDisabled:document.getElementById('btn-apply').disabled})")

    # --- 大量待应用改动时，列表要能滚动且按钮不被挤出视野 ---
    for ci in range(2):
        for ri in range(6):
            ev("document.querySelectorAll('.card')[%d].querySelectorAll('.slot-row')[%d].click()" % (ci, ri))
            time.sleep(0.5)
            ev("var rs=document.querySelectorAll('#pk-list .pk-row');(rs[3]||rs[0]).click();")
            time.sleep(0.42)
    time.sleep(0.8)
    R["12_many_pending_layout"] = ev(
        "JSON.stringify({pending:window.__ui.selftest().pending,"
        "listScrollable:(function(){var l=document.getElementById('chglist');return l.scrollHeight>l.clientHeight+2;})(),"
        "applyVisible:(function(){var b=document.getElementById('btn-apply').getBoundingClientRect();"
        "return b.bottom<=window.innerHeight \u0026\u0026 b.top>=0 \u0026\u0026 b.height>0;})()})")

    # ================= 复制 / 粘贴 · 预设 · 备份恢复 =================
    ev("if(window.__ui.state.readonly){document.getElementById('ro').click();}")
    ev("document.querySelectorAll('.card.sel').forEach(function(c){c.querySelector('.card-head').click();})")
    time.sleep(0.8)
    # 前面步骤可能留下待应用改动（第 12 步留了 12 项），先清干净再验证复制，
    # 否则 apply 会因改动超过 6 项而弹批量确认框。
    clear_pending()

    # --- 复制槽1 -> 粘贴到槽2 ---
    # 每步都等条件成立，不靠固定 sleep —— 否则会变成「加一行诊断就通过」的时序依赖
    ev("document.querySelectorAll('.card-head .copybtn')[0].click()")
    wait_for("window.__ui.state.clipSrc === 1", label="复制记录了来源槽")
    ev("document.querySelectorAll('.card-head')[1].click()")
    wait_for("Object.keys(window.__ui.state.selected).indexOf('2') >= 0", label="槽2 被选中")
    R["14a_mem_slot2_before"] = MEM.read_u32(0xA118)   # 槽2 对象在 0xA100，attr1 在 +0x18
    R["14a2_pre_click"] = ev(
        "JSON.stringify({clip:window.__ui.state.clipSrc,"
        "sel:Object.keys(window.__ui.state.selected),"
        "pasteDisabled:document.getElementById('btn-paste').disabled,"
        "pasteText:document.getElementById('btn-paste').textContent,"
        "headCount:document.querySelectorAll('.card-head').length})")
    ev("document.getElementById('btn-paste').click()")
    wait_for("window.__ui.selftest().pending > 0", label="粘贴并入待应用改动")
    R["14b_staged"] = ev(
        "JSON.stringify({pending:window.__ui.selftest().pending,"
        "lastLog:(document.querySelector('#logdock div')||{}).textContent})")
    # 关键：粘贴只并入待应用改动，此时内存**必须未被写入**
    R["14c_mem_slot2_after_stage"] = MEM.read_u32(0xA118)
    apply_now()
    R["14d_mem_slot2_after_apply"] = MEM.read_u32(0xA118)
    R["14_paste"] = ev(
        "JSON.stringify({clip:window.__ui.state.clipSrc,"
        "a1:window.__ui.state.slots[0].attrs.map(function(x){return x.id;}),"
        "a2:window.__ui.state.slots[1].attrs.map(function(x){return x.id;}),"
        "d2:window.__ui.state.slots[1].debuffs.map(function(x){return x.id;})})")

    # --- 备份 -> 改坏 -> 恢复 ---
    R["15a_before_backup"] = ev(
        "JSON.stringify(window.__ui.state.slots[0].attrs.map(function(x){return x.id;}))")
    ev("document.getElementById('btn-backup').click()")
    wait_for("[].some.call(document.querySelectorAll('#logdock div'),"
             "function(d){return d.textContent.indexOf('备份完成')>=0;})",
             label="备份完成")
    ev("if(!window.__ui.state.advanced){document.getElementById('adv').click();}")
    time.sleep(1.0)
    ev("document.querySelectorAll('.card')[0].querySelectorAll('.slot-row')[0].click()")
    time.sleep(0.9)
    ev("document.getElementById('pk-manual-id').value='7001401';"
       "document.getElementById('pk-manual-go').click()")
    time.sleep(0.9)
    apply_now(2.5)
    R["15b_after_damage"] = ev(
        "JSON.stringify(window.__ui.state.slots[0].attrs.map(function(x){return x.id;}))")
    assert not ev("!!document.getElementById('cf-yes')"), "恢复前不应有残留确认框"
    ev("document.getElementById('btn-restore').click()")
    wait_for("document.getElementById('cf-yes')", label="恢复确认框出现")
    ev("document.getElementById('cf-yes').click()")
    time.sleep(2.5)
    R["15c_after_restore"] = ev(
        "JSON.stringify(window.__ui.state.slots[0].attrs.map(function(x){return x.id;}))")

    # --- 预设：存 -> 改 -> 套用还原 ---
    ev("if(window.__ui.state.advanced){document.getElementById('adv').click();}")
    time.sleep(1.0)
    ev("document.querySelectorAll('.card.sel').forEach(function(c){c.querySelector('.card-head').click();});"
       "document.querySelectorAll('.card-head')[0].click();")
    time.sleep(0.8)
    R["16a_slot1"] = ev("JSON.stringify(window.__ui.state.slots[0].attrs.map(function(x){return x.id;}))")
    ev("window.prompt=function(){return '验证预设';};")
    ev("document.getElementById('btn-preset-save').click()")
    # 等预设真的出现在下拉里再继续，而不是赌 2 秒够用
    wait_for("[].some.call(document.querySelectorAll('#preset option'),"
             "function(o){return o.textContent.indexOf('验证预设')>=0;})",
             label="预设出现在下拉")
    R["16b_presets"] = ev(
        "JSON.stringify([].map.call(document.querySelectorAll('#preset option'),function(o){return o.textContent;}))")
    ev("if(!window.__ui.state.advanced){document.getElementById('adv').click();}")
    time.sleep(1.0)
    ev("document.querySelectorAll('.card')[0].querySelectorAll('.slot-row')[0].click()")
    time.sleep(0.9)
    ev("document.getElementById('pk-manual-id').value='7001402';"
       "document.getElementById('pk-manual-go').click()")
    time.sleep(0.9)
    apply_now(2.5)
    R["16c_after_damage"] = ev("JSON.stringify(window.__ui.state.slots[0].attrs.map(function(x){return x.id;}))")
    ev("if(window.__ui.state.advanced){document.getElementById('adv').click();}")
    wait_for("!window.__ui.state.advanced", label="切回安全模式")
    ev("var s=document.getElementById('preset');"
       "for(var i=0;i<s.options.length;i++){if(s.options[i].textContent.indexOf('验证预设')>=0){s.selectedIndex=i;break;}}")
    R["16c2_mem_before_preset"] = MEM.read_u32(0xA018)
    ev("document.getElementById('btn-preset-apply').click()")
    wait_for("window.__ui.selftest().pending > 0", label="预设并入待应用改动")
    R["16c3_mem_after_stage"] = MEM.read_u32(0xA018)   # 套预设同样只暂存
    apply_now()
    R["16d_after_preset"] = ev("JSON.stringify(window.__ui.state.slots[0].attrs.map(function(x){return x.id;}))")
    R["16e_errors"] = ev("JSON.stringify(window.__errors)")

    try:
        w.destroy()
    except Exception:
        pass


def main() -> int:
    global SVC, MEM
    mapping = load_mapping(ROOT / "data" / "mapping.json")
    catalog = EffectCatalog.from_data_dir(ROOT / "data")
    SVC = make_demo_service(mapping, catalog)
    MEM = SVC.backend

    bridge = Bridge(
        SVC,
        presets=PresetService(ROOT / "data" / "_test_tmp" / "presets"),
        backup_path=ROOT / "data" / "_test_tmp" / "verify_backup.json",
        app_version="verify",
    )
    webview.create_window("verify newui", str(ROOT / "webui" / "web" / "index.html"),
                          js_api=bridge, width=1400, height=900)
    store = ROOT / "data" / "_test_tmp" / "webview"
    store.mkdir(parents=True, exist_ok=True)
    webview.start(worker, debug=False, private_mode=False, storage_path=str(store))

    print("=" * 78)
    for k in sorted(R.keys(), key=lambda s: (len(s), s)):
        v = R[k]
        print("  " + k.ljust(22) + " " + (v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)))
    print("=" * 78)

    fails = []
    if not R.get("ready"):
        fails.append("前端未就绪")
    ini = json.loads(R.get("1_initial") or "{}")
    if ini.get("cards") != 6:
        fails.append("卡片数不是 6：" + str(ini.get("cards")))
    if ini.get("readonly") is not False:
        fails.append("演示模式应可写")
    ps = json.loads(R.get("2_picker_safe") or "{}")
    if ps.get("rows") != 340:
        fails.append("安全模式候选不是 340：" + str(ps.get("rows")))
    pa = json.loads(R.get("3_picker_advanced") or "{}")
    if "1076" not in (pa.get("stat") or ""):
        fails.append("高级模式候选总量不是 1076：" + str(pa.get("stat")))
    # 默认隐藏「非法」组 -> 7 组 / 497 行
    if pa.get("groups") != 7 or pa.get("rows") != 497:
        fails.append("隐藏非法组时应为 7 组 497 行，实际 " + str(pa.get("groups")) + " 组 " + str(pa.get("rows")) + " 行")
    if pa.get("manualVisible") != "flex":
        fails.append("高级模式应显示手动 ID 输入")
    # 取消隐藏 -> 8 组 / 1076 行
    pb = json.loads(R.get("3b_show_illegal") or "{}")
    if pb.get("groups") != 8 or pb.get("rows") != 1076:
        fails.append("显示非法组时应为 8 组 1076 行，实际 " + str(pb.get("groups")) + " 组 " + str(pb.get("rows")) + " 行")
    idle = R.get("0_idle_3s") or ""
    if "render +0, renderCards +0" not in idle:
        fails.append("空闲时仍在重绘（应完全静止）：" + idle)
    rel = R.get("0b_click_reliability") or ""
    if not rel.startswith("12/12"):
        fails.append("点击丢事件：" + rel + " " + str(R.get("0c_click_failures")))

    sel = json.loads(R.get("11_select_by_title") or "{}")
    if "(1)" not in (sel.get("apply") or ""):
        fails.append("点卡片标题应能选中，实际按钮为：" + str(sel.get("apply")))
    if sel.get("sel") != 1 or sel.get("btnDisabled") is not False:
        fails.append("选中状态或按钮启用状态不对：" + str(sel))
    b1 = json.loads(R.get("13b_after_back_to_safe") or "{}")
    if b1.get("pending") != 1:
        fails.append("非法改动不该被悄悄丢掉：" + str(b1.get("pending")))
    if b1.get("btnDisabled") is not True:
        fails.append("存在规则冲突时「应用」应置灰")
    if "冲突" not in (b1.get("btnText") or ""):
        fails.append("按钮未提示冲突：" + str(b1.get("btnText")))
    if b1.get("markedBad", 0) < 1:
        fails.append("待应用列表未标出非法项")
    if b1.get("blockIssues", 0) < 1:
        fails.append("校验面板未列出阻断项")
    pre = json.loads(R.get("13_pre_advanced") or "{}")
    if pre.get("advanced") is not True:
        fails.append("前置条件不成立：该步应为高级模式")
    if b1.get("badTip") is not True:
        fails.append("待应用列表缺少「非法」说明文字")
    b2 = json.loads(R.get("13c_after_clear") or "{}")
    if b2.get("btnDisabled") is not False:
        fails.append("清空改动后「应用」应恢复可点")

    lay = json.loads(R.get("12_many_pending_layout") or "{}")
    if not lay.get("listScrollable"):
        fails.append("待应用改动很多时列表不可滚动")
    if not lay.get("applyVisible"):
        fails.append("待应用改动很多时「应用」按钮被挤出视野")
    pa = json.loads(R.get("14_paste") or "{}")
    if pa.get("clip") != 1:
        fails.append("复制未记录来源槽：" + str(pa.get("clip")))
    st = json.loads(R.get("14b_staged") or "{}")
    if not st.get("pending"):
        fails.append("粘贴没有并入待应用改动；点击前状态=" + str(R.get("14a2_pre_click"))
                     + " 之后日志=" + str(st.get("lastLog")))
    if R.get("14c_mem_slot2_after_stage") != R.get("14a_mem_slot2_before"):
        fails.append("粘贴在暂存阶段就写了内存（应先暂存、点应用才写）：%s -> %s"
                     % (R.get("14a_mem_slot2_before"), R.get("14c_mem_slot2_after_stage")))
    if R.get("14d_mem_slot2_after_apply") == R.get("14a_mem_slot2_before"):
        fails.append("点应用后槽2 内存仍未变化")
    if pa.get("a1") != pa.get("a2"):
        fails.append("粘贴并应用后槽2 的属性应与槽1 一致：%s vs %s" % (pa.get("a1"), pa.get("a2")))

    b0 = R.get("15a_before_backup")
    b1 = R.get("15b_after_damage")
    b2 = R.get("15c_after_restore")
    if b0 == b1:
        fails.append("备份后的破坏性写入没生效，无法验证恢复")
    if b2 != b0:
        fails.append("从备份恢复后未回到备份时的值：%s -> %s -> %s" % (b0, b1, b2))

    p0 = R.get("16a_slot1")
    p1 = R.get("16c_after_damage")
    p2 = R.get("16d_after_preset")
    if p0 == p1:
        fails.append("套预设前的破坏性写入没生效，无法验证套用")
    if R.get("16c3_mem_after_stage") != R.get("16c2_mem_before_preset"):
        fails.append("套预设在暂存阶段就写了内存（应先暂存、点应用才写）")
    if p2 != p0:
        fails.append("套用预设并应用后未回到保存时的值：%s -> %s -> %s" % (p0, p1, p2))
    pres = json.loads(R.get("16b_presets") or "[]")
    if not any("验证预设" in str(x) for x in pres):
        fails.append("保存的预设没有出现在下拉里：" + str(pres))
    errs = R.get("16e_errors")
    if errs and errs != "[]":
        fails.append("前端出现 JS 错误：" + str(errs))

    if R.get("7_before_apply_mem") == R.get("8_after_apply_mem"):
        fails.append("点应用后内存没有变化")
    if R.get("10_after_undo_mem") != R.get("7_before_apply_mem"):
        fails.append("撤销后内存未还原")
    for k in ("1_initial", "6_after_pick", "9_after_apply"):
        s = json.loads(R.get(k) or "{}")
        if s.get("logLines", 0) == 0:
            fails.append(k + " 日志面板为空")

    if R.get("timeouts"):
        fails.append("等待超时（说明界面没进入预期状态）：" + str(R["timeouts"]))
    if fails:
        print("失败项：")
        for f in fails:
            print("  - " + f)
        return 1
    print("全部通过：界面渲染、选择器两种模式、搜索、写入内存、撤销均正常")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
