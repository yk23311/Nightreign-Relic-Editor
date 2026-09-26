/* Nightreign-Relic-Editor（黑环CE助手）· 新界面（0.7.0）
   原则：前端**不实现任何业务规则**。候选列表、合法性校验全部由 Python 的
   bridge 提供 —— 前端只负责显示与收集用户意图，避免两边规则漂移。 */
(function () {
  "use strict";

  // 采集前端异常。没有它的话，事件处理器里抛的错会被浏览器静默吞掉，
  // 表现成「点了没反应」—— 排查时只能靠猜。
  window.__errors = [];
  window.addEventListener("error", function (e) {
    window.__errors.push(String((e && e.message) || e));
  });
  window.addEventListener("unhandledrejection", function (e) {
    window.__errors.push("unhandledrejection: " + String(e && e.reason));
  });

  var FIELDS = ["attr1", "attr2", "attr3", "debuff1", "debuff2", "debuff3"];
  var FLABEL = { attr1: "属性 1", attr2: "属性 2", attr3: "属性 3",
                 debuff1: "减益 1", debuff2: "减益 2", debuff3: "减益 3" };

  var S = {
    slots: [], pending: {}, selected: {}, meta: new Map(),
    advanced: false, readonly: true, attached: false, demo: false,
    cand: new Map(), logSeq: 0, violations: [], validating: false, firstLoad: true,
    clipSrc: null,                 // 已复制词条的来源槽（粘贴到选中时用）
    renderCount: 0, cardRenderCount: 0, validateCount: 0
  };

  // ---------------- 与 Python 通信 ----------------
  var API = null;
  function ready() {
    if (API) return Promise.resolve(API);
    return new Promise(function (res, rej) {
      if (window.pywebview && window.pywebview.api) { API = window.pywebview.api; return res(API); }
      var n = 0;
      var iv = setInterval(function () {
        if (window.pywebview && window.pywebview.api) {
          clearInterval(iv); API = window.pywebview.api; res(API);
        } else if (++n > 200) { clearInterval(iv); rej(new Error("pywebview 未就绪")); }
      }, 50);
    });
  }

  var busyDepth = 0;
  function setBusy(on) {
    busyDepth += on ? 1 : -1;
    if (busyDepth < 0) busyDepth = 0;
    document.getElementById("busybar").className = "busybar" + (busyDepth > 0 ? " on" : "");
  }

  async function call(name) {
    var args = Array.prototype.slice.call(arguments, 1);
    var a;
    try { a = await ready(); }
    catch (e) { toast("无法连接后端：" + e.message, "err"); return { ok: false, error: e.message }; }
    setBusy(true);
    try {
      var r = await a[name].apply(a, args);
      if (!r || typeof r !== "object") { toast(name + " 返回异常", "err"); return { ok: false }; }
      if (r.ok !== true) {
        var msg = r.error || (r.errors && r.errors.join("；")) || (name + " 失败");
        // 后端错误常常是多行诊断（AOB 未命中会带上 pattern 与模块）。
        // 塞进 toast 会撑爆，所以只弹首行，完整内容在日志面板里。
        var firstLine = String(msg).split("\n")[0];
        toast(firstLine.length > 110 ? firstLine.slice(0, 110) + "…（详见日志）" : firstLine, "err");
      }
      return r;
    } catch (e) {
      log("后端调用 " + name + " 失败：" + e, "err");
      toast(name + " 调用失败：" + e, "err");
      return { ok: false, error: String(e) };
    } finally {
      setBusy(false);
    }
  }

  // ---------------- 小工具 ----------------
  function $(id) { return document.getElementById(id); }
  function pad(n) { return (n < 10 ? "0" : "") + n; }

  function toast(msg, kind) {
    var el = document.createElement("div");
    el.className = "toast " + (kind || "");
    el.textContent = msg;
    document.body.appendChild(el);
    setTimeout(function () { el.classList.add("show"); }, 10);
    setTimeout(function () {
      el.classList.remove("show");
      setTimeout(function () { el.remove(); }, 250);
    }, kind === "err" ? 4200 : 2400);
  }

  function log(msg, kind) {
    var box = $("logdock");
    var t = new Date();
    var d = document.createElement("div");
    d.innerHTML = '<span class="t">' + pad(t.getHours()) + ":" + pad(t.getMinutes()) + ":" + pad(t.getSeconds()) + "</span> " +
      '<span class="' + (kind || "") + '"></span>';
    d.lastChild.textContent = msg;
    box.insertBefore(d, box.firstChild);
    while (box.childNodes.length > 300) box.removeChild(box.lastChild);
  }

  function slotOf(n) {
    for (var i = 0; i < S.slots.length; i++) if (S.slots[i].index === n) return S.slots[i];
    return null;
  }
  function isDeb(f) { return f.indexOf("debuff") === 0; }
  function origId(slot, f) {
    var i = parseInt(f.slice(-1), 10) - 1;
    return isDeb(f) ? slot.debuffs[i].id : slot.attrs[i].id;
  }
  function curId(slot, f) {
    var k = slot.index + ":" + f;
    return (k in S.pending) ? S.pending[k] : origId(slot, f);
  }
  function infoOf(id) {
    if (id <= 0) return { id: id, name: "(空)", compat: "", known: true };
    return S.meta.get(id) || { id: id, name: "未知 " + id, compat: "", known: false };
  }
  function noteMeta(list) {
    (list || []).forEach(function (m) { if (m && m.id > 0) S.meta.set(m.id, m); });
  }

  function nselOf() { return Object.keys(S.selected).length; }

  function blockingCount() {
    if (S.advanced) return 0;   // 高级模式跳过校验，不存在阻断
    var n = 0;
    S.violations.forEach(function (v) { if (v.severity === "block") n++; });
    return n;
  }
  function blockingKeys() {
    var m = {};
    if (S.advanced) return m;
    S.violations.forEach(function (v) {
      if (v.severity === "block" && v.field) m[v.slot + ":" + v.field] = v.message;
    });
    return m;
  }

  function writes() {
    var out = [];
    Object.keys(S.pending).forEach(function (k) {
      var p = k.split(":");
      out.push({ slot: parseInt(p[0], 10), field: p[1], value: S.pending[k] });
    });
    return out;
  }

  function setValue(slot, f, v) {
    var k = slot.index + ":" + f;
    if (v === origId(slot, f)) delete S.pending[k]; else S.pending[k] = v;
    render();
  }

  // ---------------- 渲染 ----------------
  function render() {
    S.renderCount++;
    var has = S.slots.length > 0;
    $("emptystate").style.display = has ? "none" : "";
    $("mainwrap").style.display = has ? "" : "none";
    $("dot").className = "dot " + (S.attached ? "on" : (S.firstLoad ? "off" : "off"));
    $("proc").textContent = (S.bootProcess || "nightreign.exe") + " · " + (S.attached ? "已附加" : "未附加");
    $("btn-refresh").disabled = !S.attached;
    $("btn-undo").disabled = !S.attached;
    $("btn-redo").disabled = !S.attached;
    $("btn-backup").disabled = !S.attached;
    $("pill-demo").style.display = S.demo ? "" : "none";
    $("btn-attach").disabled = S.demo;
    $("btn-attach").title = S.demo ? "演示模式使用模拟数据，无需附加进程" : "";
    $("btn-detach").disabled = S.demo || !S.attached;
    $("btn-restore").disabled = !S.attached;
    $("btn-paste").disabled = !S.attached || S.clipSrc === null || nselOf() === 0;
    $("btn-paste").textContent = S.clipSrc === null ? "粘贴到选中" : ("粘贴槽" + S.clipSrc + " 到选中");
    $("btn-paste").title = "把已复制槽位的词条并入待应用改动（不直接写入，需再点「应用到选中」）";
    $("pill-mode").textContent = S.advanced ? "高级模式 · 校验关闭" : "安全模式";
    $("pill-mode").style.borderColor = S.advanced ? "rgba(176,106,214,.5)" : "";
    if (has) renderCards();
    renderSide();
    queueValidate();
  }

  function renderCards() {
    S.cardRenderCount++;
    var q = $("filter").value.trim().toLowerCase();
    var host = $("cards");
    host.innerHTML = "";
    var total = 0, shown = 0;

    S.slots.forEach(function (slot) {
      var card = document.createElement("div");
      card.className = "card" + (S.selected[slot.index] ? " sel" : "");
      card.dataset.slot = slot.index;

      var head = document.createElement("div");
      head.className = "card-head";
      head.innerHTML = '<input type="checkbox" ' + (S.selected[slot.index] ? "checked" : "") + ">" +
        '<span class="no">槽 ' + slot.index + "</span>" +
        '<span class="badge ' + (slot.kind === "STD" ? "std" : "don") + '">' + slot.kindLabel + "</span>" +
        '<span class="selmark">✓ 已选</span>' +
        '<button class="tiny ghost copybtn" title="复制本槽的 6 个字段">复制</button>';
      // 必须 stopPropagation：卡片头本身是「点选」，不复的话点复制会连带切换选中状态
      head.querySelector(".copybtn").addEventListener("click", function (e) {
        e.stopPropagation();
        S.clipSrc = slot.index;
        log("已复制 槽" + slot.index + " 的词条（点「粘贴到选中」写入其它槽）", "ok");
        render();
      });
      // 整张卡片头都是点击区。只让那个 13px 的复选框可点，用户根本找不到 ——
      // 反馈「应用到选中 一直是 0」多半就是点了卡片/标题却没有任何反应。
      head.addEventListener("click", function () {
        if (S.selected[slot.index]) delete S.selected[slot.index];
        else S.selected[slot.index] = true;
        render();
      });
      head.addEventListener("dragover", function (e) { e.preventDefault(); card.classList.add("drop"); });
      head.addEventListener("dragleave", function () { card.classList.remove("drop"); });
      head.addEventListener("drop", function () { card.classList.remove("drop"); });
      card.appendChild(head);

      var body = document.createElement("div");
      body.className = "card-body";
      [["attr", "属性"], ["debuff", "减益"]].forEach(function (pair) {
        var lab = document.createElement("div");
        lab.className = "grp-label";
        lab.textContent = pair[1];
        body.appendChild(lab);
        for (var i = 1; i <= 3; i++) {
          var f = pair[0] + i;
          var id = curId(slot, f);
          var info = infoOf(id);
          total++;
          if (q && info.name.toLowerCase().indexOf(q) < 0 && String(id).indexOf(q) < 0) return;
          shown++;

          var changed = (slot.index + ":" + f) in S.pending;
          var row = document.createElement("div");
          row.className = "slot-row" + (id <= 0 ? " empty" : "") + (changed ? " changed" : "");
          row.dataset.slot = slot.index;
          row.dataset.field = f;
          row.draggable = true;
          var cmp = id <= 0 ? "" : (info.compat || "");
          var cls = id <= 0 ? "empty" : (cmp === "BTH" ? "bth" : (cmp === "STD" ? "std" : (cmp === "DON" ? "don" : "na")));
          row.innerHTML = '<span class="idx">' + i + '</span><span class="nm"></span>' +
            '<span class="badge ' + cls + '">' + (id <= 0 ? "空" : (cmp || "?")) + '</span>' +
            '<span class="id">' + (id <= 0 ? "" : id) + "</span>";
          row.querySelector(".nm").textContent = info.name;

          row.addEventListener("click", function () {
            var s = slotOf(parseInt(this.dataset.slot, 10));
            openPicker(s, this.dataset.field);
          });
          row.addEventListener("dragstart", function (e) {
            dragSrc = { slot: parseInt(this.dataset.slot, 10), field: this.dataset.field };
            this.classList.add("dragging");
            e.dataTransfer.effectAllowed = "move";
            e.dataTransfer.setData("text/plain", this.dataset.field);
          });
          row.addEventListener("dragend", function () { this.classList.remove("dragging"); });
          row.addEventListener("dragover", function (e) { e.preventDefault(); this.classList.add("over"); });
          row.addEventListener("dragleave", function () { this.classList.remove("over"); });
          row.addEventListener("drop", function (e) {
            e.preventDefault(); this.classList.remove("over");
            if (!dragSrc) return;
            var dstField = this.dataset.field, dstSlot = slotOf(parseInt(this.dataset.slot, 10));
            var srcSlot = slotOf(dragSrc.slot);
            if (!srcSlot || !dstSlot) return;
            if (dragSrc.slot === dstSlot.index && dragSrc.field === dstField) return;
            var a = curId(srcSlot, dragSrc.field), b = curId(dstSlot, dstField);
            if (dragSrc.slot === dstSlot.index && isDeb(dragSrc.field) === isDeb(dstField)) {
              setValue(dstSlot, dstField, a); setValue(srcSlot, dragSrc.field, b);
              log("槽" + dstSlot.index + " 内交换 " + FLABEL[dragSrc.field] + " ↔ " + FLABEL[dstField], "ok");
            } else {
              setValue(dstSlot, dstField, a);
              log("搬运「" + infoOf(a).name + "」从 槽" + dragSrc.slot + " " + FLABEL[dragSrc.field] +
                  " 到 槽" + dstSlot.index + " " + FLABEL[dstField], "ok");
            }
            dragSrc = null;
          });
          body.appendChild(row);
        }
      });
      card.appendChild(body);
      host.appendChild(card);
    });
    $("filterinfo").textContent = q ? ("匹配 " + shown + " / " + total + " 行") : "";
  }

  function renderSide() {
    // 「已选数量 + 应用按钮」放在这里而不是 render()：校验结果变化时只重绘本区域，
    // 绝不能顺带重建卡片 —— 那正是卡片闪烁与点击随机丢失的来源。
    var nsel = Object.keys(S.selected).length;
    $("pill-sel").textContent = nsel ? ("已选 " + nsel + " 个槽") : "未选中任何槽（点卡片标题即可选中）";
    $("pill-sel").style.borderColor = nsel ? "rgba(76,141,255,.5)" : "";
    // 有阻断性规则冲突时不让点「应用」—— 后端本来也会拒绝，
    // 与其让用户白点一次再吃一个错误弹窗，不如当场置灰并说明原因。
    var nBlock = blockingCount();
    var btn = $("btn-apply");
    btn.textContent = "应用到选中 (" + nsel + ")" + (nBlock ? " · 有 " + nBlock + " 项冲突" : "");
    btn.disabled = nsel === 0 || nBlock > 0 || S.validating;
    btn.title = nBlock ? "当前改动违反了 " + nBlock + " 条规则，请先看右侧校验面板修正" : "";

    var keys = Object.keys(S.pending);
    $("chgn").textContent = keys.length;
    var cl = $("chglist");
    if (!keys.length) { cl.innerHTML = '<span class="hint">（无）</span>'; }
    else {
      cl.innerHTML = "";
      var badKeys = blockingKeys();
      // 列表本身可滚动，所以多渲染一些；上限只是防止极端情况卡顿
      keys.slice(0, 120).forEach(function (k) {
        var p = k.split(":"), s = slotOf(parseInt(p[0], 10));
        if (!s) return;
        var bad = badKeys[k];
        var d = document.createElement("div");
        d.className = "chg" + (bad ? " bad" : "");
        if (bad) d.title = bad;
        d.innerHTML = '<span class="mk">' + (bad ? "✗" : "·") + '</span>' +
          '<span class="f">槽' + p[0] + " " + FLABEL[p[1]] + "</span>";
        var t = document.createElement("span");
        t.textContent = infoOf(origId(s, p[1])).name + " → " + infoOf(S.pending[k]).name;
        d.appendChild(t);
        cl.appendChild(d);
      });
      if (Object.keys(badKeys).length) {
        var tip = document.createElement("div");
        tip.className = "hint";
        tip.style.color = "var(--err)";
        tip.textContent = "标 ✗ 的改动在当前模式下非法，鼠标悬停可看原因";
        cl.appendChild(tip);
      }
      if (keys.length > 120) {
        var more = document.createElement("div");
        more.className = "hint";
        more.textContent = "…还有 " + (keys.length - 120) + " 项未列出";
        cl.appendChild(more);
      }
    }

    var host = $("issues");
    if (!keys.length) {
      host.innerHTML = '<div class="issue ok"><span class="mk">✓</span><span>没有待应用的改动</span></div>';
      return;
    }
    if (S.advanced) {
      host.innerHTML = '<div class="issue warn"><span class="mk">!</span><span>高级模式：校验已跳过，写入不会被拦截</span></div>';
      return;
    }
    if (S.validating) {
      host.innerHTML = '<div class="hint">校验中…</div>';
      return;
    }
    if (!S.violations.length) {
      host.innerHTML = '<div class="issue ok"><span class="mk">✓</span><span>未发现规则冲突</span></div>';
      return;
    }
    host.innerHTML = "";
    S.violations.slice(0, 20).forEach(function (v) {
      var d = document.createElement("div");
      d.className = "issue " + (v.severity === "block" ? "block" : "warn");
      d.innerHTML = '<span class="mk">' + (v.severity === "block" ? "✗" : "!") + "</span>";
      var t = document.createElement("span");
      t.textContent = "槽" + v.slot + " " + (FLABEL[v.field] || v.field) + "：" + v.message;
      d.appendChild(t);
      host.appendChild(d);
    });
  }

  // ---------------- 校验（一律问 Python） ----------------
  var valTimer = null;
  function queueValidate() {
    if (valTimer) clearTimeout(valTimer);
    valTimer = setTimeout(function () {
      valTimer = null;
      var w = writes();
      // 这条路径**只允许** renderSide()。
      // 曾经这里调用 render()，而 render() 末尾又会 queueValidate()，
      // 形成 220ms 一轮的无限重绘：卡片悬停闪烁、点击在 mousedown 与 mouseup
      // 之间被重建吞掉（表现为随机点不中）。
      if (!w.length || S.advanced || !S.attached) {
        if (S.violations.length || S.validating) {
          S.violations = [];
          S.validating = false;
          renderSide();
        }
        return;
      }
      S.validating = true;
      renderSide();
      S.validateCount++;
      call("validate", w).then(function (r) {
        S.validating = false;
        if (r.ok) S.violations = r.violations || [];
        renderSide();
      });
    }, 220);
  }

  // ---------------- 选择器 ----------------
  var overlay = null, pkCtx = null;
  function buildPicker() {
    overlay = document.createElement("div");
    overlay.className = "overlay";
    overlay.id = "pk-overlay";
    overlay.innerHTML =
      '<div class="picker">' +
      '  <div class="picker-head"><div class="picker-title" id="pk-title">选择词条</div>' +
      '    <div class="spacer"></div><span class="badge ghost" id="pk-stat"></span>' +
      '    <button class="ghost tiny" id="pk-close">关闭 (Esc)</button></div>' +
      '  <div class="picker-search">' +
      '    <input type="search" id="pk-q" placeholder="搜索名称或 ID…" autocomplete="off">' +
      '    <label class="chk" id="pk-illegal-wrap"><input type="checkbox" id="pk-hide-illegal" checked> 隐藏「非法」组</label>' +
      "  </div>" +
      '  <div class="picker-list" id="pk-list"></div>' +
      '  <div class="picker-manual" id="pk-manual">' +
      '    <span class="hint">手动 ID：</span>' +
      '    <input type="text" id="pk-manual-id" placeholder="7001400 / 0x6ADB30 / -1" style="width:190px">' +
      '    <button id="pk-manual-go">写入该 ID</button>' +
      '    <span class="hint">高级模式下可写任意 u32</span>' +
      "  </div>" +
      '  <div class="picker-foot hint" id="pk-foot"></div>' +
      "</div>";
    document.body.appendChild(overlay);
    overlay.addEventListener("click", function (e) { if (e.target === overlay) closePicker(); });
    $("pk-close").addEventListener("click", closePicker);
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") closePicker(); });
    $("pk-q").addEventListener("input", drawPicker);
    $("pk-hide-illegal").addEventListener("change", drawPicker);
    $("pk-manual-go").addEventListener("click", function () {
      if (!pkCtx) { toast("请先点一个词条位置打开选择器", "err"); return; }
      var raw = $("pk-manual-id").value.trim();
      if (!raw) { toast("请先填写 ID", "err"); return; }
      var v = /^0x/i.test(raw) ? parseInt(raw, 16) : parseInt(raw, 10);
      if (isNaN(v)) { toast("不是合法数字：" + raw, "err"); return; }
      if (!S.advanced) { toast("手动写入任意 ID 只在高级模式下可用", "err"); return; }
      var slot = slotOf(pkCtx.slot), f = pkCtx.field;
      closePicker();
      setValue(slot, f, v);
      log("手动写入 槽" + slot.index + " " + FLABEL[f] + " = " + v, "warn");
    });
  }
  function closePicker() { if (overlay) overlay.classList.remove("open"); pkCtx = null; }

  function pick(id) {
    var slot = slotOf(pkCtx.slot), f = pkCtx.field;
    closePicker();
    setValue(slot, f, id);
    log("选择 槽" + slot.index + " " + FLABEL[f] + " → " + id + " " + infoOf(id).name, "ok");
  }

  var pkItems = [];
  function drawPicker() {
    var t0 = performance.now();
    var q = $("pk-q").value.trim().toLowerCase();
    var hideIllegal = $("pk-hide-illegal").checked;
    var list = $("pk-list"), frag = document.createDocumentFragment();
    var last = null, shown = 0, hid = 0;
    var grouped = S.advanced;
    for (var i = 0; i < pkItems.length; i++) {
      var e = pkItems[i];
      if (hideIllegal && e.group === "非法") { hid++; continue; }
      if (q && e.name.toLowerCase().indexOf(q) < 0 && String(e.id).indexOf(q) < 0) continue;
      if (grouped && e.group && e.group !== last) {
        last = e.group;
        var h = document.createElement("div");
        h.className = "pk-group" + (e.group === "非法" ? " illegal" : "");
        h.textContent = "—— " + e.group + " ——";
        frag.appendChild(h);
      }
      var row = document.createElement("div");
      row.className = "pk-row" + (e.id === pkCtx.current ? " current" : "");
      row.dataset.id = e.id;
      var cls = e.compat === "BTH" ? "bth" : (e.compat === "STD" ? "std" : (e.compat === "DON" ? "don" : "na"));
      row.innerHTML = '<span class="pk-name"></span><span class="badge ' + cls + '">' + (e.compat || "-") +
        '</span><span class="pk-id">' + e.id + "</span>";
      row.firstChild.textContent = e.name;
      row.addEventListener("click", function () { pick(parseInt(this.dataset.id, 10)); });
      frag.appendChild(row);
      shown++;
    }
    list.innerHTML = "";
    list.appendChild(frag);
    var ms = performance.now() - t0;
    $("pk-stat").textContent = shown + " / " + pkItems.length + " 条";
    $("pk-foot").textContent = "渲染 " + shown + " 行耗时 " + ms.toFixed(1) + " ms" +
      (hid ? "（已隐藏「非法」组 " + hid + " 条）" : "") +
      "　·　" + (S.advanced ? "CT 高级模式编辑器（RelicID 全量）" : "CT 安全模式编辑器");
    $("pk-illegal-wrap").style.display = S.advanced ? "flex" : "none";
  }

  async function openPicker(slot, field) {
    if (!overlay) buildPicker();
    var key = slot.kind + "|" + field + "|" + S.advanced;
    var r;
    if (S.cand.has(key)) {
      r = S.cand.get(key);
    } else {
      r = await call("candidates", slot.index, field);
      if (!r.ok) return;
      S.cand.set(key, r);
    }
    noteMeta(r.items);
    pkCtx = { slot: slot.index, field: field, current: curId(slot, field) };
    pkItems = r.items;
    if (!S.advanced) pkItems = pkItems.filter(function (e) { return true; });
    var label = isDeb(field) ? ("减益 " + field.slice(-1)) : ("属性 " + field.slice(-1));
    $("pk-title").textContent = "槽 " + slot.index + " · " + label +
      (S.advanced ? "（高级模式：全量可选）" : "");
    $("pk-manual").style.display = S.advanced ? "flex" : "none";
    $("pk-q").value = "";
    $("pk-manual-id").value = "";
    overlay.classList.add("open");
    drawPicker();
    setTimeout(function () { $("pk-q").focus(); }, 30);
  }

  // ---------------- 动作 ----------------
  var dragSrc = null;

  function applySlots(list) {
    if (!list) return;
    S.slots = list;
    list.forEach(function (s) {
      noteMeta(s.attrs); noteMeta(s.debuffs);
    });
    S.pending = {};
    render();
  }

  async function doAttach() {
    log("正在附加进程并解析符号…");
    var r = await call("attach");
    if (!r.ok) { log("附加失败：" + r.error, "err"); return; }
    S.attached = true;
    S.bootProcess = S.bootProcess || "nightreign.exe";
    log("已附加 pid=" + r.pid + " base=0x" + (r.base || 0).toString(16), "ok");
    applySlots(r.slots);
  }

  async function doRefresh() {
    var r = await call("refresh");
    if (!r.ok) { log("刷新失败：" + r.error, "err"); return; }
    applySlots(r.slots);
    log("已读取 " + r.slots.length + " 个槽位", "ok");
  }

  async function doApply() {
    var w = writes();
    if (!w.length) { toast("没有待应用的改动", "err"); return; }
    if (S.readonly) { toast("当前是只读预览，请先关闭只读", "err"); return; }
    // 本地先拦一道（校验是异步的，按钮状态可能还没刷新）；后端仍会独立校验
    if (blockingCount() > 0) {
      toast("当前改动违反 " + blockingCount() + " 条规则，请先看右侧校验面板修正", "err");
      return;
    }
    var n = Object.keys(S.selected).length;
    if (n > 1 || w.length > 6) {
      if (!await confirmBox("确认批量写入", "将向 " + n + " 个选中槽写入 " + w.length + " 项改动。继续？")) return;
    }
    var r = await call("apply", w);
    if (!r.ok) { log("应用失败：" + r.error, "err"); return; }
    (r.changed || []).forEach(function (c) {
      log("写入 槽" + c.slot + " " + FLABEL[c.field] + "：" + c.old + " → " + c.new, "ok");
    });
    applySlots(r.slots);
    log("应用成功，共 " + r.count + " 项", "ok");
  }

  async function doUndo() {
    var r = await call("undo");
    if (!r.ok) { log("撤销失败：" + ((r.errors || [])[0] || r.error), "err"); return; }
    applySlots(r.slots);
    log("已撤销", "ok");
  }
  async function doRedo() {
    var r = await call("redo");
    if (!r.ok) { log("重做失败：" + ((r.errors || [])[0] || r.error), "err"); return; }
    applySlots(r.slots);
    log("已重做", "ok");
  }
  function stageWrites(list) {
    // 把一批改动并入「待应用改动」，与手动改词条**同一套语义**：
    // 先暂存 -> 实时校验 -> 用户点「应用到选中」才真正写内存。
    // 复制/预设若直接落盘，就会出现「有的改动要确认、有的不用」的割裂。
    (list || []).forEach(function (w) {
      var s = slotOf(w.slot);
      if (!s) return;
      var k = w.slot + ":" + w.field;
      if (w.value === origId(s, w.field)) delete S.pending[k];
      else S.pending[k] = w.value;
    });
    render();
  }

  async function doPaste() {
    if (S.clipSrc === null) { toast("请先在某个卡片上点「复制」", "err"); return; }
    var dsts = Object.keys(S.selected).map(Number);
    if (!dsts.length) { toast("请先选中要粘贴到的槽", "err"); return; }
    if (dsts.length === 1 && dsts[0] === S.clipSrc) { toast("目标就是来源槽，无需粘贴", "err"); return; }
    var r = await call("preview_copy", S.clipSrc, dsts);
    if (!r.ok) { log("粘贴失败：" + (r.error || ""), "err"); return; }
    if (!r.count) { toast("目标槽与来源槽内容相同，没有需要改的字段"); return; }
    stageWrites(r.writes);
    log("已把 槽" + S.clipSrc + " 的词条并入待应用改动（" + r.count + " 项）；确认无误后点「应用到选中」", "ok");
  }

  async function doRestore() {
    if (S.readonly) { toast("当前是只读预览，请先关闭只读", "err"); return; }
    if (!await confirmBox("从备份恢复",
        "将把 6 个槽的属性与减益恢复成最近一次「备份」时的值。当前改动会被覆盖。继续？")) return;
    var r = await call("restore");
    if (!r.ok) { log("恢复失败：" + (r.error || ""), "err"); return; }
    applySlots(r.slots);
    log("已从备份恢复，共 " + r.count + " 项", "ok");
  }

  async function doPresetApply() {
    var name = $("preset").value;
    if (!name) { toast("请先选择一个预设", "err"); return; }
    var dsts = Object.keys(S.selected).map(Number);
    if (!dsts.length) { toast("请先选中要套用预设的槽", "err"); return; }
    var r = await call("preview_preset", name, dsts);
    if (!r.ok) { log("套用预设失败：" + (r.error || ""), "err"); return; }
    if (!r.count) { toast("套用该预设不会产生任何改动"); return; }
    stageWrites(r.writes);
    log("已把预设「" + name + "」并入待应用改动（" + r.count + " 项）；确认无误后点「应用到选中」", "ok");
  }

  async function doBackup() {
    var r = await call("backup");
    if (r.ok) log("备份完成：" + r.path, "ok");
  }

  function confirmBox(title, body) {
    return new Promise(function (res) {
      var ov = document.createElement("div");
      ov.className = "overlay open";
      ov.innerHTML = '<div class="picker" style="width:min(460px,92vw);height:auto">' +
        '<div class="picker-head"><div class="picker-title">' + title + "</div></div>" +
        '<div style="padding:16px 18px" class="hint">' + body + "</div>" +
        '<div style="display:flex;gap:8px;justify-content:flex-end;padding:0 18px 16px">' +
        '<button id="cf-no">取消</button><button class="danger" id="cf-yes">继续</button></div></div>';
      document.body.appendChild(ov);
      ov.querySelector("#cf-no").onclick = function () { ov.remove(); res(false); };
      ov.querySelector("#cf-yes").onclick = function () { ov.remove(); res(true); };
    });
  }

  async function loadPresets() {
    var r = await call("presets_list");
    if (!r.ok) return;
    var sel = $("preset");
    sel.innerHTML = "";
    if (!r.items.length) {
      var o = document.createElement("option");
      o.textContent = "（暂无预设）"; o.value = ""; sel.appendChild(o);
      return;
    }
    r.items.forEach(function (p) {
      var o = document.createElement("option");
      o.value = p.name;
      o.textContent = p.name + "（" + p.slotKindLabel + "）";
      sel.appendChild(o);
    });
  }

  // ---------------- 日志轮询 ----------------
  var logTimer = null;
  async function pollLog() {
    var r = await call("log_tail", S.logSeq);
    if (!r.ok || !r.items) return;
    r.items.forEach(function (it) {
      var kind = it.level === "ERROR" ? "err" : (it.level === "WARNING" ? "warn" : (it.level === "INFO" ? "" : ""));
      log("[" + it.name + "] " + it.msg, kind);
    });
    S.logSeq = r.seq;
  }

  // ---------------- 绑定 ----------------
  function bind() {
    $("btn-attach").addEventListener("click", doAttach);
    $("btn-detach").addEventListener("click", async function () {
      var r = await call("detach");
      if (r.ok) { S.attached = false; S.slots = []; S.pending = {}; render(); log("已分离", "warn"); }
    });
    $("btn-refresh").addEventListener("click", doRefresh);
    $("btn-apply").addEventListener("click", doApply);
    $("btn-undo").addEventListener("click", doUndo);
    $("btn-redo").addEventListener("click", doRedo);
    $("btn-backup").addEventListener("click", doBackup);
    $("btn-clear").addEventListener("click", function () { S.pending = {}; render(); log("已清空待应用改动", "warn"); });
    $("filter").addEventListener("input", renderCards);
    $("ro").addEventListener("change", async function () {
      S.readonly = this.checked;
      await call("set_mode", this.checked, null);
      log("只读预览：" + (this.checked ? "开" : "关（可写）"), this.checked ? "" : "warn");
    });
    $("adv").addEventListener("change", async function () {
      S.advanced = this.checked;
      S.cand.clear();
      await call("set_mode", null, this.checked);
      log(this.checked
        ? "高级模式：开 —— 校验关闭；属性/减益候选均为 CT 全量表 1076 条"
        : "高级模式：关 —— 恢复严格过滤与校验", "warn");
      render();
    });
    $("btn-preset-save").addEventListener("click", async function () {
      var sel = Object.keys(S.selected);
      if (!sel.length) { toast("请先勾选要存为预设的槽", "err"); return; }
      var name = prompt("预设名称（会把该槽当前的 3 属性 + 3 减益一起存下）", "槽" + sel[0] + " 组合");
      if (!name) return;
      var r = await call("preset_save", name, parseInt(sel[0], 10));
      if (r.ok) { log("已保存预设：" + r.name, "ok"); loadPresets(); }
    });
    $("btn-paste").addEventListener("click", doPaste);
    $("btn-restore").addEventListener("click", doRestore);
    $("btn-preset-apply").addEventListener("click", doPresetApply);
    $("btn-preset-delete").addEventListener("click", async function () {
      var name = $("preset").value;
      if (!name) { toast("请先选择一个预设", "err"); return; }
      if (!await confirmBox("删除预设", "确定删除预设「" + name + "」？此操作不可撤销。")) return;
      var r = await call("preset_delete", name);
      if (r.ok) { log("已删除预设：" + name, "warn"); loadPresets(); }
    });
  }

  // ---------------- 启动 ----------------
  async function boot() {
    // 分阶段打点：启动失败时要能看出卡在哪一步，而不是只看到「没就绪」
    window.__bootStage = "bind";
    bind();
    window.__bootStage = "waiting-api";
    // 首次 js_api 调用**偶发失败**：页面已加载完成，但 pywebview 的本地 HTTP 服务
    // 尚未完全就绪（api 调用正是走它）。实测约 1/8 概率复现，且失败后界面会停在
    // 「初始化失败」必须重启。boot() 只读且幂等，重试是安全且正确的处理。
    var r = null;
    for (var attempt = 1; attempt <= 5; attempt++) {
      r = await call("boot");
      if (r && r.ok) break;
      window.__bootStage = "boot-retry-" + attempt + "（" + ((r && r.error) || "?") + "）";
      await new Promise(function (res) { setTimeout(res, 300 * attempt); });
    }
    window.__bootStage = "got-boot-result";
    if (!r || !r.ok) {
      window.__bootStage = "boot-failed";
      $("emptymsg").textContent =
        "初始化失败：" + ((r && r.error) || "未知错误") + "（已自动重试 5 次，请点「刷新」或重启程序）";
      $("emptystate").style.display = "";
      return;
    }
    S.demo = !!r.demo;
    S.attached = r.attached;
    S.readonly = r.readonly;
    S.advanced = r.advanced;
    S.bootProcess = r.processName;
    S.logSeq = r.logSeq || 0;
    S.firstLoad = false;
    $("ro").checked = S.readonly;
    $("adv").checked = S.advanced;
    if (r.slots && r.slots.length) applySlots(r.slots);
    else {
      $("emptymsg").textContent = r.notice === "尚未附加进程"
        ? "请启动游戏并进入存档，然后点「附加进程」。"
        : ("未读取到遗物：" + (r.notice || ""));
      render();
    }
    log("界面已就绪（版本 " + r.version + "）", "ok");
    if (S.demo) log("演示模式：使用模拟数据，不会读取游戏进程", "warn");
    loadPresets();
    pollLog();
    logTimer = setInterval(pollLog, 1200);
    window.__ready = true;
    window.__bootStage = "done";
  }

  // 直接启动，不再依赖 document.readyState 判断。
  // 原来的写法有竞态：若 readyState 恰为 "loading" 而 DOMContentLoaded 又已被错过，
  // boot() 就永远不会被调用 —— 页面看起来完全正常（DOM 渲染、app.js 已执行），
  // 但界面永远停在初始状态，且**不产生任何错误**。实测约 1/4 概率复现。
  // 本脚本位于 body 末尾，上方 DOM 必然已解析，直接跑是安全的。
  function startBoot() {
    if (window.__bootStarted) return;
    window.__bootStarted = true;
    window.__bootStage = "starting";
    boot()
      .then(function () { window.__bootDone = true; })
      .catch(function (e) {
        window.__errors.push("boot 失败: " + e);
        window.__bootStage = "failed: " + e;
        window.__bootDone = true;   // 标记「已结束」，让上层能区分失败与卡死
      });
  }
  startBoot();

  // 供无头自检使用
  window.__ui = {
    state: S,
    selftest: function () {
      return {
        ready: !!window.__ready,
        bootDone: !!window.__bootDone,
        attached: S.attached,
        advanced: S.advanced,
        readonly: S.readonly,
        demo: S.demo,
        slots: S.slots.length,
        cards: document.querySelectorAll(".card").length,
        rows: document.querySelectorAll(".slot-row").length,
        pending: Object.keys(S.pending).length,
        clipSrc: S.clipSrc,
        presets: document.querySelectorAll("#preset option").length,
        violations: S.violations.length,
        logLines: document.querySelectorAll("#logdock div").length,
        errors: window.__errors.slice(0, 5),
        renders: S.renderCount,
        cardRenders: S.cardRenderCount,
        validates: S.validateCount,
        bootStage: window.__bootStage || "(未开始)",
        apiReady: !!API
      };
    }
  };
})();
