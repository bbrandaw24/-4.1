/* ============================================================
   v16 UI 层：新功能与体验增强（依赖 app.js 的全局函数/state）
   - 设置抽屉（刷新频率 / 主题三态 / 相对时间 / 缓存清理 / 版本）
   - 顶栏告警徽标 + 一键查看告警
   - 趋势页：时间范围切换增强 + 多地块湿度对比 + CSV 导出
   - 告警记录：级别筛选 + 导出
   - 快捷键（1-4 切视图、R 刷新、S 设置）
   - 相对时间显示（顶栏 / 传感器卡片 / 地块卡片）
   ============================================================ */
(function () {
  "use strict";

  function $(sel) { return document.querySelector(sel); }

  function ready(fn) {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", fn);
    else fn();
  }

  var PLOT_COLORS = ["#226b46", "#236a80", "#a26716", "#68568d", "#b3541e", "#3a7d5f"];

  /* ---------------- 1. 主题三态（单例在 enhance.js 里） ---------------- */
  function applyThemeMode() {
    var mode = AG.settings.themeAuto ? "auto" : undefined;
    var saved = null;
    try { saved = localStorage.getItem("ag-theme"); } catch (_) {}
    if (mode === "auto" || !(saved === "nature" || saved === "dark" || saved === "hud")) {
      // 跟随系统
      var dark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
      if (window.AGTheme) AGTheme.applyTheme(dark ? "dark" : "nature");
    } else {
      if (window.AGTheme) AGTheme.applyTheme(saved);
    }
  }

  function setThemeOption(mode) {
    // mode: "light" | "dark" | "hud" | "auto"
    AG.setSetting("themeAuto", mode === "auto");
    if (mode === "auto") {
      try { localStorage.removeItem("ag-theme"); } catch (_) {}
    } else {
      var map = { light: "nature", dark: "dark", hud: "hud" };
      try { localStorage.setItem("ag-theme", map[mode] || "nature"); } catch (_) {}
    }
    applyThemeMode();
    AG.toast("主题已更新", "success");
  }

  /* ---------------- 2. 设置抽屉 ---------------- */
  var DRAWER = null;
  function drawer() {
    if (DRAWER) return DRAWER;
    var wrap = document.createElement("div");
    wrap.id = "ag-drawer";
    wrap.className = "ag-drawer";
    wrap.innerHTML =
      '<div class="ag-drawer-backdrop"></div>' +
      '<aside class="ag-drawer-panel" role="dialog" aria-modal="true" aria-label="设置">' +
        '<header class="ag-drawer-head"><h3>设置</h3><button type="button" class="ag-drawer-close" aria-label="关闭">✕</button></header>' +
        '<section class="ag-drawer-body">' +
          '<h4>刷新频率</h4>' +
          '<div class="ag-option-row" id="ag-opt-refresh">' +
            [5000, 10000, 15000, 30000].map(function (ms) {
              return '<label class="ag-chip"><input type="radio" name="ag-refresh" value="' + ms + '">' + ms / 1000 + "s</label>";
            }).join("") +
          '</div>' +
          '<p class="ag-drawer-note">降低频率可明显减少网络与渲染开销；页面在后台时自动暂停轮询。</p>' +
          '<h4>主题</h4>' +
          '<div class="ag-option-row" id="ag-opt-theme">' +
            '<label class="ag-chip" data-mode="light">浅色</label>' +
            '<label class="ag-chip" data-mode="dark">深色大屏</label>' +
            '<label class="ag-chip" data-mode="hud">科技蓝 HUD</label>' +
            '<label class="ag-chip" data-mode="auto">跟随系统</label>' +
          '</div>' +
          '<h4>显示</h4>' +
          '<label class="ag-switch-row"><span>相对时间（如“12 秒前”）</span><input type="checkbox" id="ag-opt-reltime"></label>' +
          '<h4>本地数据</h4>' +
          '<button type="button" class="ag-btn" id="ag-clear-cache">清理 PWA 缓存与本地数据</button>' +
          '<p class="ag-drawer-note" id="ag-cache-note">清理后下次加载会重新请求全部静态资源。</p>' +
          '<h4>关于</h4>' +
          '<p class="ag-drawer-note">智慧农业温室运行台 <b id="ag-version">' + AG.VERSION + "</b><br>访问 " +
          (window.location.hostname || "") + "</p>" +
        "</section>" +
      "</aside>";
    document.body.appendChild(wrap);
    DRAWER = wrap;
    wrap.querySelector(".ag-drawer-backdrop").addEventListener("click", closeDrawer);
    wrap.querySelector(".ag-drawer-close").addEventListener("click", closeDrawer);

    // 刷新频率
    var refreshRow = wrap.querySelector("#ag-opt-refresh");
    var radios = refreshRow.querySelectorAll("input[name='ag-refresh']");
    function syncRefresh() {
      radios.forEach(function (r) { r.checked = Number(r.value) === Number(AG.settings.refreshMs); });
    }
    radios.forEach(function (r) {
      r.addEventListener("change", function () {
        if (!r.checked) return;
        AG.setSetting("refreshMs", Number(r.value));
        AG.toast("刷新频率已改为 " + Number(r.value) / 1000 + " 秒", "success");
      });
    });
    syncRefresh();

    // 主题
    var themeRow = wrap.querySelector("#ag-opt-theme");
    var themeChips = themeRow.querySelectorAll(".ag-chip");
    function syncTheme() {
      var mode = AG.settings.themeAuto ? "auto" : (rootTheme() === "nature" ? "light" : rootTheme() === "hud" ? "hud" : "dark");
      themeChips.forEach(function (chip) {
        chip.classList.toggle("active", chip.dataset.mode === mode);
      });
    }
    function rootTheme() {
      var t = document.documentElement.getAttribute("data-theme");
      return t === "hud" ? "hud" : t === "nature" ? "nature" : "dark";
    }
    themeChips.forEach(function (chip) {
      chip.addEventListener("click", function () { setThemeOption(chip.dataset.mode); syncTheme(); });
    });
    syncTheme();

    // 相对时间
    var relTimeInput = wrap.querySelector("#ag-opt-reltime");
    relTimeInput.checked = Boolean(AG.settings.relativeTime);
    relTimeInput.addEventListener("change", function () {
      AG.setSetting("relativeTime", relTimeInput.checked);
      if (!relTimeInput.checked) restoreStaticTimes();
    });

    // 清缓存
    wrap.querySelector("#ag-clear-cache").addEventListener("click", function () {
      var note = wrap.querySelector("#ag-cache-note");
      note.textContent = "清理中…";
      if (window.caches) {
        caches.keys().then(function (keys) {
          return Promise.all(keys.map(function (k) { return caches.delete(k); }));
        }).then(function () {
          note.textContent = "缓存已清理。可点下方按钮重新加载本页。";
          AG.toast("缓存已清理", "success");
        });
      } else {
        note.textContent = "当前环境无 Cache API。";
      }
    });
    return wrap;
  }

  function openDrawer() { drawer().classList.add("open"); }
  function closeDrawer() { if (DRAWER) DRAWER.classList.remove("open"); }

  /* ---------------- 3. 顶栏：告警徽标 + 设置入口 ---------------- */
  function initTopbar() {
    var settingsBtn = $("#settings-btn");
    if (settingsBtn && !settingsBtn.dataset.bound) {
      settingsBtn.dataset.bound = "1";
      settingsBtn.addEventListener("click", function () {
        drawer().classList.contains("open") ? closeDrawer() : openDrawer();
      });
    }
    var badge = $("#alert-badge");
    if (badge && !badge.dataset.bound) {
      badge.dataset.bound = "1";
      badge.addEventListener("click", function () {
        if (typeof setRoute === "function") setRoute("overview");
        var log = $("#alert-log-panel") || document.querySelector(".alert-log-panel");
        if (log) log.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    }
  }

  /* ---------------- 4. 告警：数据捕获 + 筛选 + 徽标 + 导出 ---------------- */
  var AGAlert = { list: [], filter: "all", captured: false };

  function patchAlertLog() {
    if (typeof refreshAlertLog !== "function" || AGAlert.captured) return;
    AGAlert.captured = true;
    var original = refreshAlertLog;
    refreshAlertLog = function () {
      return Promise.resolve(original()).then(function () { catchUp(); });
    };
    function catchUp() {
      // 从已渲染的 DOM 恢复不行，直接再拉一次轻量日志用于筛选/徽标
      if (typeof Auth !== "undefined" && Auth.request) {
        Auth.request("/api/v1/alerts/logs?limit=50", { cache: "no-store" })
          .then(function (response) { return response.ok ? response.json() : null; })
          .then(function (data) {
            if (data && data.items) {
              AGAlert.list = data.items;
              renderAlertFiltered();
              updateAlertBadge();
            }
          }).catch(function () {});
      }
    }
    window.AGAlert = AGAlert;
    catchUp();
  }

  function updateAlertBadge() {
    var badge = $("#alert-badge");
    if (!badge) return;
    var active = AGAlert.list.filter(function (item) { return item.status === "active"; }).length;
    var total = AGAlert.list.length;
    var label = badge.querySelector(".ag-alert-count") || badge.appendChild(document.createElement("span"));
    label.className = "ag-alert-count";
    label.textContent = active > 99 ? "99+" : String(active);
    badge.classList.toggle("has", active > 0);
    badge.setAttribute("title", active + " 条未恢复告警 / 共 " + total + " 条记录");
  }

  function renderAlertFiltered() {
    var list = $("#alert-log-list");
    if (!list) return;
    var items = AGAlert.list;
    if (AGAlert.filter === "warning") items = items.filter(function (i) { return i.level !== "critical"; });
    if (AGAlert.filter === "critical") items = items.filter(function (i) { return i.level === "critical"; });
    if (!items.length) {
      list.innerHTML = '<span class="alert-empty">暂无告警记录</span>';
      return;
    }
    var byLevel = { critical: 0, warning: 0 };
    items.forEach(function (i) { byLevel[i.level] = (byLevel[i.level] || 0) + 1; });
    list.innerHTML =
      '<div class="ag-alert-meta">显示 ' + items.length + " 条 · 严重 " + (byLevel.critical || 0) + " · 提示 " + (byLevel.warning || 0) + "</div>" +
      items.map(function (item) {
        var plot = (typeof PLOT_NAMES !== "undefined" && PLOT_NAMES[item.device_id]) || item.device_id;
        var cls = item.level === "critical" ? "critical" : "warning";
        return '<div class="alert-log-item ' + cls + '"><span class="alert-log-time">' + new Date(item.timestamp).toLocaleString() +
          '</span><span class="alert-log-device">' + plot + '</span><span class="alert-log-code">' + (item.code || "") +
          '</span><span class="alert-log-msg">' + (item.message || "") + '</span><span class="alert-log-status ' + (item.status || "") + '">' +
          (item.status === "active" ? "触发" : "恢复") + "</span></div>";
      }).join("");
    list.querySelectorAll(".alert-log-item").forEach(function (el) {
      el.addEventListener("click", function () {
        var code = el.querySelector(".alert-log-code");
        if (code && code.textContent) AG.copy(code.textContent.trim());
      });
    });
  }

  function initAlertFilters() {
    var bar = $("#ag-alert-filter");
    if (!bar || bar.dataset.bound) return;
    bar.dataset.bound = "1";
    bar.addEventListener("click", function (event) {
      var btn = event.target.closest("[data-filter]");
      if (!btn) return;
      AGAlert.filter = btn.dataset.filter;
      bar.querySelectorAll("[data-filter]").forEach(function (b) { b.classList.toggle("active", b === btn); });
      renderAlertFiltered();
    });
    var exportBtn = $("#ag-alert-export");
    if (exportBtn && !exportBtn.dataset.bound) {
      exportBtn.dataset.bound = "1";
      exportBtn.addEventListener("click", function () {
        if (!AGAlert.list.length) { AG.toast("暂无可导出的告警", "warn"); return; }
        AG.exportCSV("告警记录-" + new Date().toISOString().slice(0, 10) + ".csv",
          ["时间", "地块", "代码", "级别", "状态", "内容"],
          AGAlert.list.map(function (i) {
            return [new Date(i.timestamp).toLocaleString(), i.device_id, i.code, i.level, i.status, i.message];
          }));
        AG.toast("告警记录已导出 CSV", "success");
      });
    }
  }

  /* ---------------- 5. 相对时间 ---------------- */
  function initRelativeTime() {
    setInterval(function () {
      if (!AG.settings.relativeTime) {
        restoreStaticTimes();
        return;
      }
      var update = $("#last-update");
      if (update && update.dataset.ts) {
        var text = update.dataset.ts.slice(0, 8);
        update.textContent = text + " · " + AG.relTime(update.dataset.iso);
      }
      var seenNodes = document.querySelectorAll("[data-seen]");
      for (var i = 0; i < seenNodes.length; i += 1) {
        var node = seenNodes[i];
        var iso = node.dataset.seen;
        if (!iso) continue;
        if (!node.__agLastSeen || node.__agLastSeen !== iso) {
          node.__agLastSeen = iso;
          node.dataset.seenAbs = node.textContent;
        }
        node.textContent = AG.relTime(iso);
      }
    }, 1000);
  }

  function restoreStaticTimes() {
    var update = $("#last-update");
    if (update && update.dataset.ts) update.textContent = update.dataset.ts.slice(0, 8);
    var nodes = document.querySelectorAll("[data-seen]");
    for (var i = 0; i < nodes.length; i += 1) {
      var node = nodes[i];
      if (node.__agLastSeenAbs) node.textContent = node.__agLastSeenAbs;
    }
  }

  /* ---------------- 6. 趋势：多地块湿度对比 + CSV ---------------- */
  var AGCompare = {
    selected: null,   // Set<deviceId>
    cache: {},        // deviceId -> {samples, at}
    active: false,
  };
  window.AGCompare = AGCompare;

  function plotLabel(deviceId) {
    var dev = (typeof state !== "undefined" && state.allDevices || []).find(function (d) { return d.device_id === deviceId; });
    if (dev && dev.plot && dev.plot.name) return dev.plot.name;
    return (typeof PLOT_NAMES !== "undefined" && PLOT_NAMES[deviceId]) || deviceId;
  }

  function initCompare() {
    var bar = $("#ag-compare-bar");
    if (!bar || bar.dataset.bound) return;
    bar.dataset.bound = "1";
    bar.addEventListener("change", function (event) {
      var input = event.target.closest("input[data-device]");
      if (!input) return;
      var deviceId = input.dataset.device;
      if (input.checked) AGCompare.selected.add(deviceId);
      else AGCompare.selected.delete(deviceId);
      AGCompare.active = AGCompare.selected.size > 1;
      refreshCompare();
    });
    var exportBtn = $("#ag-export-csv");
    if (exportBtn && !exportBtn.dataset.bound) {
      exportBtn.dataset.bound = "1";
      exportBtn.addEventListener("click", exportTrendCsv);
    }
    if (typeof refreshHistory === "function") {
      var originalRefresh = refreshHistory;
      refreshHistory = function (deviceId, force) {
        var result = originalRefresh(deviceId, force);
        return Promise.resolve(result).then(function () {
          if (AGCompare.active) refreshCompare();
        });
      };
    }
  }

  function syncCompareBar() {
    var bar = $("#ag-compare-bar");
    if (!bar) return;
    var devices = (typeof state !== "undefined" && state.allDevices) || [];
    bar.innerHTML = devices.map(function (d) {
      var checked = AGCompare.selected && AGCompare.selected.has(d.device_id) ? " checked" : "";
      return '<label class="ag-compare-chip" title="' + d.device_id + '"><input type="checkbox" data-device="' + d.device_id + '"' + checked + ">" + plotLabel(d.device_id) + "</label>";
    }).join("");
  }

  function refreshCompare() {
    if (!AGCompare.active) {
      // 恢复默认单地块图
      if (typeof renderTrendPanels === "function") renderTrendPanels();
      return;
    }
    var ids = Array.from(AGCompare.selected);
    // 用 set 检索 now，避免重复请求
    var now = Date.now();
    var need = ids.filter(function (id) {
      var c = AGCompare.cache[id];
      return !c || now - c.at > 60000;
    });
    var load = need.length
      ? Promise.all(need.map(function (id) {
          return Auth.request("/api/v1/devices/" + encodeURIComponent(id) + "/telemetry/history?hours=10", { cache: "no-store" })
            .then(function (r) { return r.ok ? r.json() : { items: [] }; })
            .then(function (data) { AGCompare.cache[id] = { samples: bucketHistory(data.items), at: Date.now() }; })
            .catch(function () {});
        }))
      : Promise.resolve();
    load.then(function () { drawCompare(); });
  }

  function bucketHistory(items) {
    var buckets = new Map();
    (items || []).forEach(function (item) {
      var parsed = Date.parse(item.timestamp);
      if (!Number.isFinite(parsed)) return;
      var key = Math.round(parsed / 5000) * 5000;
      if (!buckets.has(key)) buckets.set(key, { ts: key });
      var payload = item.payload || {};
      if (item.kind === "soil" && Number.isFinite(Number(payload.moisture_pct))) buckets.get(key).moisture = Number(payload.moisture_pct);
    });
    var out = [];
    buckets.forEach(function (v) { out.push(v); });
    out.sort(function (a, b) { return a.ts - b.ts; });
    return out;
  }

  function drawCompare() {
    var own = (typeof state !== "undefined" && state.samples) ? state.samples : [];
    var ownMap = new Map(own.map(function (s) { return [Math.round(Date.parse(s.timestamp) / 5000) * 5000, s.moisture]; }));
    var all = new Set(own.map(function (s) { return Math.round(Date.parse(s.timestamp) / 5000) * 5000; }));
    var per = {};
    AGCompare.selected.forEach(function (id) {
      var c = AGCompare.cache[id];
      if (!c) return;
      per[id] = new Map(c.samples.map(function (s) { return [s.ts, s.moisture]; }));
      c.samples.forEach(function (s) { all.add(s.ts); });
    });
    var sorted = Array.from(all).sort(function (a, b) { return a - b; });
    var series = [];
    var index = 0;
    series.push({ name: plotLabel(typeof state !== "undefined" && state.device ? state.device.device_id : "当前"), values: sorted.map(function (t) { return ownMap.get(t); }), color: PLOT_COLORS[0], axis: "left" });
    AGCompare.selected.forEach(function (id) {
      index += 1;
      series.push({ name: plotLabel(id), values: sorted.map(function (t) { var m = per[id]; return m ? m.get(t) : undefined; }), color: PLOT_COLORS[index % PLOT_COLORS.length], axis: "left" });
    });
    var samples = sorted.map(function (t) { return { timestamp: new Date(t).toISOString() }; });
    if (typeof drawSeriesChart === "function") {
      drawSeriesChart("#moisture-chart", series, { samples: samples, leftLabel: "土壤湿度 (%)", leftFormat: function (v) { return v.toFixed(0) + "%"; } });
    }
    var legend = $("#ag-compare-legend");
    if (legend) {
      legend.innerHTML = series.map(function (s, i) {
        return '<span class="ag-legend-chip"><i style="background:' + s.color + '"></i>' + s.name + "</span>";
      }).join("");
    }
  }

  function exportTrendCsv() {
    if (AGCompare.active) {
      var rows = [];
      var all = new Set();
      var per = {};
      AGCompare.selected.forEach(function (id) {
        var c = AGCompare.cache[id];
        if (!c) return;
        per[id] = new Map(c.samples.map(function (s) { return [s.ts, s.moisture]; }));
        c.samples.forEach(function (s) { all.add(s.ts); });
      });
      var sorted = Array.from(all).sort(function (a, b) { return a - b; });
      var headers = ["时间"].concat(Array.from(AGCompare.selected).map(plotLabel));
      rows = sorted.map(function (t) {
        return [new Date(t).toLocaleString("zh-CN", { hour12: false })].concat(Array.from(AGCompare.selected).map(function (id) {
          var m = per[id];
          var v = m && m.get(t);
          return Number.isFinite(v) ? v.toFixed(1) : "";
        }));
      });
      AG.exportCSV("多地块湿度对比-" + new Date().toISOString().slice(0, 10) + ".csv", headers, rows);
      AG.toast("对比数据已导出 CSV", "success");
      return;
    }
    var samples = (typeof state !== "undefined" && state.samples) ? state.samples : [];
    var rows2 = samples.map(function (s) {
      return [new Date(s.timestamp).toLocaleString("zh-CN", { hour12: false }),
        Number.isFinite(s.moisture) ? s.moisture.toFixed(1) : "",
        Number.isFinite(s.temperature) ? s.temperature.toFixed(1) : "",
        Number.isFinite(s.light) ? String(Math.round(s.light)) : ""];
    });
    AG.exportCSV("土壤湿度趋势-" + new Date().toISOString().slice(0, 10) + ".csv",
      ["时间", "土壤湿度%", "空气温度℃", "光照lx"], rows2);
    AG.toast("趋势数据已导出 CSV（" + rows2.length + " 行）", "success");
  }

  /* ---------------- 7. 快捷键 ---------------- */
  function initShortcuts() {
    document.addEventListener("keydown", function (event) {
      var tag = (event.target && event.target.tagName) || "";
      if (/INPUT|TEXTAREA|SELECT/.test(tag)) return;
      var key = event.key.toLowerCase();
      if (key >= "1" && key <= "4") {
        var routes = ["overview", "trends", "devices", "agent"];
        if (typeof setRoute === "function") setRoute(routes[Number(key) - 1]);
        event.preventDefault();
      } else if (key === "r" && !event.metaKey && !event.ctrlKey) {
        if (typeof refresh === "function") refresh();
        if (typeof refreshAiStatus === "function") refreshAiStatus();
        if (typeof refreshAlertLog === "function") refreshAlertLog();
        AG.toast("已手动刷新", "info", 1200);
      } else if (key === "s" && !event.metaKey && !event.ctrlKey) {
        drawer().classList.contains("open") ? closeDrawer() : openDrawer();
      }
    });
  }

  /* ---------------- 8. 启动 ---------------- */
  function boot() {
    try { initTopbar(); } catch (error) { report(error, "initTopbar"); }
    try { patchAlertLog(); } catch (error) { report(error, "patchAlertLog"); }
    try { initAlertFilters(); } catch (error) { report(error, "initAlertFilters"); }
    try { initCompare(); } catch (error) { report(error, "initCompare"); }
    try { syncCompareBar(); } catch (error) { report(error, "syncCompareBar"); }
    try { initRelativeTime(); } catch (error) { report(error, "initRelativeTime"); }
    try { initShortcuts(); } catch (error) { report(error, "initShortcuts"); }
    AG.mark("ui-loaded");
  }

  function report(error, where) {
    if (window.AG && AG.errors) {
      AG.errors.push({ kind: "v16-ui-" + where, message: String((error && error.message) || error).slice(0, 300), at: new Date().toISOString() });
    }
  }

  if (window.AG) {
    AG.listeners.settings.push(function () {
      applyThemeMode();
      drawer() && syncThemeChips();
      var bar = $("#ag-compare-bar");
      if (bar) syncCompareBar();
    });
  }

  function syncThemeChips() {
    var wrap = $("#ag-drawer");
    if (!wrap) return;
    var mode = AG.settings.themeAuto ? "auto" : (function () {
      var t = document.documentElement.getAttribute("data-theme");
      return t === "nature" ? "light" : t === "hud" ? "hud" : "dark";
    })();
    wrap.querySelectorAll("#ag-opt-theme .ag-chip").forEach(function (chip) {
      chip.classList.toggle("active", chip.dataset.mode === mode);
    });
    var relTimeInput = wrap.querySelector("#ag-opt-reltime");
    if (relTimeInput) relTimeInput.checked = Boolean(AG.settings.relativeTime);
  }

  window.AGOpenDrawer = openDrawer;
  window.AGCloseDrawer = closeDrawer;
  window.AGThemeOption = setThemeOption;
  window.AGSyncCompareBar = syncCompareBar;

  ready(boot);
})();