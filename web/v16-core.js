/* ============================================================
   v16 性能与交互核心（AG 命名空间）
   - 可见性感知定时器：切后台自动暂停轮询，回前台立即补刷
   - 请求去重：同一函数 in-flight 时跳过新一轮
   - 差量 DOM：内容不变不写（消除布局抖动与闪烁）
   - Toast / 离线提示条 / 错误上报（暴露 window.__agErrors 供自测）
   - 设置持久化 / CSV 导出 / 复制 / 相对时间
   依赖：icons.js（可选的图标渲染）
   ============================================================ */
(function () {
  "use strict";

  var AG = {
    VERSION: "v16.0",
    startedAt: (typeof performance !== "undefined" ? performance.now() : Date.now()),
    visible: !document.hidden,
    listeners: { settings: [], refresh: [] },
  };

  AG.mark = function (name) {
    try {
      if (window.__agMarks) window.__agMarks.push([name, (typeof performance !== "undefined" ? performance.now() : Date.now()) - AG.startedAt]);
      if (window.__agDebug) console.log("[v16]", name);
    } catch (_) {}
  };

  /* ---------------- 设置 ---------------- */
  var SETTINGS_KEY = "ag-settings-v1";
  AG.defaults = { refreshMs: 5000, themeAuto: true, relativeTime: true };
  AG.settings = (function () {
    try {
      var raw = localStorage.getItem(SETTINGS_KEY);
      if (raw) {
        var parsed = JSON.parse(raw);
        var out = {};
        Object.keys(AG.defaults).forEach(function (k) { out[k] = parsed[k] !== undefined ? parsed[k] : AG.defaults[k]; });
        return out;
      }
    } catch (_) {}
    return Object.assign({}, AG.defaults);
  })();

  AG.saveSettings = function () {
    try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(AG.settings)); } catch (_) {}
    for (var i = 0; i < AG.listeners.settings.length; i += 1) {
      try { AG.listeners.settings[i](AG.settings); } catch (_) {}
    }
  };

  AG.setSetting = function (key, value) {
    AG.settings[key] = value;
    AG.saveSettings();
  };

  /* ---------------- 可见性感知定时器（单调度器） ----------------
     所有 AG.every 任务共用一个 250ms 调度器；页面隐藏时暂停，
     回到前台时按 40% 延迟阈值立即补刷。动态改 ms 只需替换任务即可。 */
  var TASKS = [];
  var SCHEDULER = null;

  function pump() {
    if (document.hidden) return;
    var now = Date.now();
    for (var i = 0; i < TASKS.length; i += 1) {
      var task = TASKS[i];
      if (task.stopped) continue;
      if (now < task.next) continue;
      if (task.dedup && task.inFlight) continue;
      task.next = now + task.ms;
      task.lastRun = now;
      task.inFlight = true;
      var result = null;
      try { result = task.fn(); } catch (error) { task.inFlight = false; continue; }
      if (result && typeof result.then === "function") {
        Promise.resolve(result).then(function () { task.inFlight = false; }, function () { task.inFlight = false; });
      } else {
        task.inFlight = false;
      }
    }
  }

  function ensureScheduler() {
    if (SCHEDULER) return;
    SCHEDULER = setInterval(pump, 250);
    document.addEventListener("visibilitychange", function () {
      AG.visible = !document.hidden;
      if (AG.visible) {
        // 回前台：任务已超时 40% 的直接补跑，其余按原节奏
        var now = Date.now();
        TASKS.forEach(function (task) {
          var elapsed = now - task.lastRun;
          if (task.ms > 0 && elapsed > task.ms * 1.4) task.next = now;
        });
        pump();
      }
    });
  }

  AG.every = function (opts) {
    var task = {
      ms: opts.ms || 5000,
      fn: opts.fn,
      dedup: opts.dedup !== false,
      inFlight: false,
      stopped: false,
      lastRun: 0,
      next: 0,
    };
    ensureScheduler();
    if (opts.immediate !== false) task.next = 0;
    else task.next = Date.now() + task.ms;
    TASKS.push(task);
    return {
      stop: function () { task.stopped = true; },
      tick: function () { task.next = 0; pump(); },
      resume: function (ms) { task.ms = ms || task.ms; task.next = 0; },
    };
  };

  AG.registerRefresh = function (fn) {
    /* 主页数据刷新：跟随设置里的刷新频率，改设置即换节奏 */
    var active = null;
    function start() {
      var ms = Number(AG.settings.refreshMs) || 5000;
      if (active) active.stop();
      var ticker = AG.every({ ms: ms, fn: fn, immediate: true });
      ticker.resume = undefined;
      active = ticker;
    }
    AG.listeners.settings.push(start);
    start();
    return function () { if (active) active.stop(); };
  };

  AG.notifyRefresh = function () {
    pump();
  };

  /* ---------------- 差量 DOM ---------------- */
  AG.setText = function (el, value) {
    if (!el) return false;
    var text = String(value == null ? "--" : value);
    if (el.textContent !== text) { el.textContent = text; return true; }
    return false;
  };

  AG.setHtml = function (el, html, sig) {
    if (!el) return false;
    if (el.__agHtmlSig === sig) return false;
    el.__agHtmlSig = sig;
    el.innerHTML = html;
    return true;
  };

  /* ---------------- Toast ---------------- */
  var STACK = null;
  function stack() {
    if (!STACK) {
      STACK = document.createElement("div");
      STACK.id = "ag-toast-stack";
      document.body.appendChild(STACK);
    }
    return STACK;
  }

  AG.toast = function (message, kind, duration) {
    kind = kind || "info";
    duration = duration || (kind === "error" ? 7000 : kind === "success" ? 2600 : 3800);
    var box = stack();
    var el = document.createElement("div");
    el.className = "ag-toast ag-toast-" + kind;
    var iconName = kind === "error" ? "alert-triangle" : kind === "success" ? "check" : "info";
    var icon = "";
    if (window.LucideIcon) icon = window.LucideIcon(iconName, "ag-toast-icon");
    el.innerHTML = icon + "<span></span>";
    el.querySelector("span").textContent = message;
    box.appendChild(el);
    while (box.children.length > 4) box.removeChild(box.firstChild);
    requestAnimationFrame(function () { el.classList.add("show"); });
    var timer = setTimeout(function () { dismiss(); }, duration);
    function dismiss() {
      clearTimeout(timer);
      if (!el.parentNode) return;
      el.classList.remove("show");
      setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, 260);
    }
    el.addEventListener("click", dismiss);
    return dismiss;
  };

  /* ---------------- 离线提示条 ---------------- */
  var OFFBAR = null;
  function offbar() {
    if (!OFFBAR) {
      OFFBAR = document.createElement("div");
      OFFBAR.id = "ag-offline-bar";
      OFFBAR.innerHTML =
        (window.LucideIcon ? window.LucideIcon("wifi-off") : "") +
        "<span>网络已断开，数据暂停更新。恢复后自动重连</span>" +
        '<button type="button" id="ag-offline-retry">重试</button>';
      document.body.appendChild(OFFBAR);
      OFFBAR.querySelector("#ag-offline-retry").addEventListener("click", function () { AG.notifyRefresh(); });
    }
    return OFFBAR;
  }

  function setOffline(show) {
    var bar = OFFBAR || offbar();
    bar.classList.toggle("show", show);
  }

  /* ---------------- 错误上报（供 CDP/浏览器自测） ---------------- */
  AG.errors = [];
  window.__agErrors = AG.errors;
  var lastReportAt = 0;
  function reportError(kind, message) {
    var now = Date.now();
    AG.errors.push({ kind: kind, message: String(message).slice(0, 400), at: new Date().toISOString() });
    if (AG.errors.length > 60) AG.errors.shift();
    try { if (window.__agDebug) console.error("[v16]", kind, message); } catch (_) {}
    if (now - lastReportAt > 6000) {
      lastReportAt = now;
      var text = String(message).slice(0, 120);
      if (/abort|AbortError|Failed to fetch/i.test(text)) return;
      try { AG.toast("运行异常: " + text, "error", 8000); } catch (_) {}
    }
  }

  AG.installErrorReporter = function () {
    window.addEventListener("error", function (event) {
      reportError("window", event.message || (event.error && event.error.message) || "unknown");
    });
    window.addEventListener("unhandledrejection", function (event) {
      var reason = event.reason;
      var text = reason && (reason.message || reason.stack) ? (reason.message || reason.stack) : String(reason);
      reportError("promise", text);
    });
  };

  /* ---------------- 网络状态 ---------------- */
  AG.online = function () { return navigator.onLine !== false; };
  AG.installNetworkWatcher = function () {
    window.addEventListener("offline", function () { setOffline(true); AG.toast("网络已断开", "warn"); });
    window.addEventListener("online", function () { setOffline(false); AG.toast("网络已恢复", "success"); AG.notifyRefresh(); });
    setInterval(function () {
      if (!navigator.onLine) { setOffline(true); return; }
      if (OFFBAR && OFFBAR.classList.contains("show")) setOffline(false);
    }, 8000);
  };

  /* ---------------- 相对时间 ---------------- */
  AG.relTime = function (iso) {
    if (!iso) return "—";
    var t = Date.parse(iso);
    if (!Number.isFinite(t)) return "—";
    var s = Math.max(0, Math.floor((Date.now() - t) / 1000));
    if (s < 3) return "刚刚";
    if (s < 60) return s + " 秒前";
    var m = Math.floor(s / 60);
    if (m < 60) return m + " 分钟前";
    var h = Math.floor(m / 60);
    if (h < 24) return h + " 小时前";
    var d = Math.floor(h / 24);
    return d + " 天前";
  };

  /* ---------------- CSV 导出 ---------------- */
  AG.exportCSV = function (filename, headers, rows) {
    function esc(value) {
      var s = String(value == null ? "" : value);
      if (/[",\n]/.test(s)) return '"' + s.replace(/"/g, '""') + '"';
      return s;
    }
    var lines = [headers.map(esc).join(",")];
    for (var i = 0; i < rows.length; i += 1) lines.push(rows[i].map(esc).join(","));
    var csv = "\uFEFF" + lines.join("\r\n");
    var blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    setTimeout(function () {
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    }, 400);
  };

  AG.copy = function (text) {
    function fallback() {
      var ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand("copy"); } catch (_) {}
      document.body.removeChild(ta);
      return true;
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text).then(function () { AG.toast("已复制到剪贴板", "success"); }, function () { fallback(); AG.toast("已复制到剪贴板", "success"); });
    }
    fallback();
    AG.toast("已复制到剪贴板", "success");
    return Promise.resolve(true);
  };

  /* ---------------- 启动标记 ---------------- */
  AG.mark("core-loaded");

  window.AG = AG;
})();

/* 页面加载后统一启动：错误上报 + 网络监视 + 性能标记 */
(function () {
  function boot() {
    if (window.AG) {
      AG.installErrorReporter();
      AG.installNetworkWatcher();
      AG.mark("boot");
    }
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();