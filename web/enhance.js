/* ============================================================
   预览增强脚本（preview-enhance.js）
   - 仅做视觉增强：主题切换、图表重绘、指标卡动画、装饰元素
   - 不改动任何业务逻辑；所有功能函数签名与调用保持不变
   - 任何异常都被吞掉，确保原功能不受影响
   ============================================================ */
(function () {
  "use strict";

  var THEME_KEY = "ag-theme";
  var root = document.documentElement;

  /* ---------------- 1. 主题切换 ---------------- */
  function currentTheme() {
    var saved = null;
    try { saved = localStorage.getItem(THEME_KEY); } catch (e) {}
    if (saved === "nature" || saved === "dark") return saved;
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "nature";
  }

  function applyTheme(theme) {
    root.setAttribute("data-theme", theme);
    var btn = document.getElementById("ag-theme-toggle");
    if (btn) {
      btn.setAttribute("title", theme === "dark" ? "切换到自然清新主题" : "切换到深色数据大屏");
      btn.innerHTML =
        (theme === "dark"
          ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>'
          : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M19.1 4.9l-1.4 1.4M6.3 17.7l-1.4 1.4"/></svg>') +
        "<span>" + (theme === "dark" ? "深色大屏" : "自然清新") + "</span>";
    }
    // 同步 PWA/移动端地址栏配色（跟随当前主题）
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", theme === "dark" ? "#0b1a12" : "#faf7f0");
    // 主题变化后重绘图表以套用新配色
    try { redrawAllCharts(); } catch (e) {}
  }

  function initThemeToggle() {
    applyTheme(currentTheme());
    var actions = document.querySelector(".topbar-actions");
    if (!actions || document.getElementById("ag-theme-toggle")) return;
    var btn = document.createElement("button");
    btn.type = "button";
    btn.id = "ag-theme-toggle";
    btn.className = "ag-theme-toggle";
    btn.addEventListener("click", function () {
      var next = root.getAttribute("data-theme") === "dark" ? "nature" : "dark";
      try { localStorage.setItem(THEME_KEY, next); } catch (e) {}
      applyTheme(next);
    });
    actions.insertBefore(btn, actions.firstChild);
    applyTheme(currentTheme());
  }

  /* ---------------- 2. 图表增强绘制 ---------------- */
  var origDraw = window.drawSeriesChart;      // 原绘制函数（保底）
  var chartState = {};                        // canvasId -> {series, options, samples}
  var chartRange = {};                        // canvasId -> "1h" | "6h" | "10h"

  function cssVar(name, fallback) {
    var v = getComputedStyle(root).getPropertyValue(name).trim();
    return v || fallback;
  }

  function isDark() { return root.getAttribute("data-theme") === "dark"; }

  function niceDomain(values) {
    var nums = values.filter(function (v) { return Number.isFinite(v); });
    if (!nums.length) return [0, 1];
    var min = Math.min.apply(null, nums);
    var max = Math.max.apply(null, nums);
    if (min === max) { min -= 1; max += 1; }
    var pad = (max - min) * 0.18;
    return [min - pad, max + pad];
  }

  // 平滑曲线（Catmull-Rom 转贝塞尔）
  function smoothPath(ctx, pts) {
    if (!pts.length) return;
    ctx.moveTo(pts[0].x, pts[0].y);
    if (pts.length === 1) return;
    for (var i = 0; i < pts.length - 1; i += 1) {
      var p0 = pts[i - 1] || pts[i];
      var p1 = pts[i];
      var p2 = pts[i + 1];
      var p3 = pts[i + 2] || p2;
      var c1x = p1.x + (p2.x - p0.x) / 6;
      var c1y = p1.y + (p2.y - p0.y) / 6;
      var c2x = p2.x - (p3.x - p1.x) / 6;
      var c2y = p2.y - (p3.y - p1.y) / 6;
      ctx.bezierCurveTo(c1x, c1y, c2x, c2y, p2.x, p2.y);
    }
  }

  function filteredSamples(samples, range) {
    if (!range || range === "10h" || !samples) return { list: samples || [], idx: null };
    var hours = range === "1h" ? 1 : 6;
    var cutoff = Date.now() - hours * 3600 * 1000;
    var idx = [];
    var list = [];
    for (var i = 0; i < samples.length; i += 1) {
      var t = Date.parse(samples[i] && samples[i].timestamp);
      if (!isNaN(t) && t >= cutoff) { idx.push(i); list.push(samples[i]); }
    }
    return list.length >= 2 ? { list: list, idx: idx } : { list: samples || [], idx: null };
  }

  function drawEnhanced(canvasId, series, options) {
    options = options || {};
    var canvas = document.querySelector(canvasId);
    if (!canvas) return;
    var ctx = canvas.getContext("2d");
    if (!ctx) return;

    var W = canvas.width, H = canvas.height;
    var range = chartRange[canvasId] || "10h";
    var full = options.samples || (typeof state !== "undefined" ? state.samples : []) || [];
    var filt = filteredSamples(full, range);
    var samples = filt.list;
    var idx = filt.idx;

    var useSeries = series.map(function (s) {
      var vals = idx ? idx.map(function (i) { return s.values[i]; }) : s.values;
      return { values: vals, color: s.color, axis: s.axis || "left" };
    });

    ctx.clearRect(0, 0, W, H);
    var plot = {
      left: 68,
      right: options.rightAxis ? W - 68 : W - 18,
      top: 18,
      bottom: H - 40
    };
    var leftVals = [];
    var rightVals = [];
    useSeries.forEach(function (s) {
      (s.axis === "right" ? rightVals : leftVals).push(s.values);
    });
    var leftDomain = niceDomain([].concat.apply([], leftVals.length ? leftVals : [[]]));
    var rightDomain = niceDomain([].concat.apply([], rightVals.length ? rightVals : [[]]));
    var xCount = Math.max.apply(null, useSeries.map(function (s) { return s.values.length; }).concat([0]));

    var gridColor = cssVar("--ag-grid", "#e7e3d7");
    var mutedColor = cssVar("--ag-muted", "#7b857c");
    var dark = isDark();

    ctx.font = "12px DM Sans, Noto Sans SC, sans-serif";
    ctx.textBaseline = "middle";

    // 淡虚线网格 + 轴标签
    ctx.save();
    ctx.setLineDash([4, 5]);
    ctx.lineWidth = 1;
    ctx.strokeStyle = gridColor;
    ctx.fillStyle = mutedColor;
    for (var line = 0; line <= 4; line += 1) {
      var y = plot.top + (line / 4) * (plot.bottom - plot.top);
      ctx.beginPath();
      ctx.moveTo(plot.left, y);
      ctx.lineTo(plot.right, y);
      ctx.stroke();
      var lv = leftDomain[1] - (line / 4) * (leftDomain[1] - leftDomain[0]);
      ctx.textAlign = "right";
      ctx.fillText(options.leftFormat ? options.leftFormat(lv) : lv.toFixed(1), plot.left - 9, y);
      if (options.rightAxis) {
        var rv = rightDomain[1] - (line / 4) * (rightDomain[1] - rightDomain[0]);
        ctx.textAlign = "left";
        ctx.fillText(options.rightFormat ? options.rightFormat(rv) : rv.toFixed(1), plot.right + 9, y);
      }
    }
    ctx.restore();

    // 基线
    ctx.save();
    ctx.strokeStyle = gridColor;
    ctx.lineWidth = 1.2;
    ctx.beginPath();
    ctx.moveTo(plot.left, plot.top);
    ctx.lineTo(plot.left, plot.bottom);
    ctx.lineTo(plot.right, plot.bottom);
    ctx.stroke();
    ctx.restore();

    var xFor = function (i) {
      return plot.left + (i / Math.max(xCount - 1, 1)) * (plot.right - plot.left);
    };

    // 各序列：渐变面积 + 平滑曲线 + （深色）辉光
    useSeries.forEach(function (s) {
      var domain = s.axis === "right" ? rightDomain : leftDomain;
      var pts = [];
      for (var i = 0; i < s.values.length; i += 1) {
        var v = s.values[i];
        if (!Number.isFinite(v)) continue;
        var yy = plot.bottom - ((v - domain[0]) / (domain[1] - domain[0])) * (plot.bottom - plot.top);
        pts.push({ x: xFor(i), y: Math.max(plot.top, Math.min(plot.bottom, yy)), v: v, i: i });
      }
      if (pts.length < 2) return;

      var color = s.color || "#226b46";
      if (dark && color === "#226b46") color = cssVar("--ag-green", "#55c67b");

      // 渐变面积
      var grad = ctx.createLinearGradient(0, plot.top, 0, plot.bottom);
      grad.addColorStop(0, hexA(color, dark ? 0.42 : 0.28));
      grad.addColorStop(1, hexA(color, 0));
      ctx.save();
      ctx.beginPath();
      smoothPath(ctx, pts);
      ctx.lineTo(pts[pts.length - 1].x, plot.bottom);
      ctx.lineTo(pts[0].x, plot.bottom);
      ctx.closePath();
      ctx.fillStyle = grad;
      ctx.fill();
      ctx.restore();

      // 曲线（深色加辉光）
      ctx.save();
      ctx.beginPath();
      smoothPath(ctx, pts);
      ctx.lineWidth = dark ? 2.6 : 2.2;
      ctx.strokeStyle = color;
      ctx.lineJoin = "round";
      ctx.lineCap = "round";
      if (dark) { ctx.shadowColor = color; ctx.shadowBlur = 12; }
      ctx.stroke();
      ctx.restore();

      // 端点高亮
      ctx.save();
      var lastPt = pts[pts.length - 1];
      ctx.beginPath();
      ctx.arc(lastPt.x, lastPt.y, dark ? 4 : 3.2, 0, Math.PI * 2);
      ctx.fillStyle = color;
      if (dark) { ctx.shadowColor = color; ctx.shadowBlur = 10; }
      ctx.fill();
      ctx.restore();
    });

    // 时间轴标签（首尾 + 中点）
    ctx.save();
    ctx.fillStyle = mutedColor;
    ctx.textAlign = "center";
    if (samples && samples.length > 1) {
      [0, Math.floor((samples.length - 1) / 2), samples.length - 1].forEach(function (i, n, arr) {
        if (n > 0 && i === arr[n - 1]) return;
        var t = Date.parse(samples[i] && samples[i].timestamp);
        if (isNaN(t)) return;
        var d = new Date(t);
        var label = String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
        ctx.fillText(label, xFor(i), plot.bottom + 20);
      });
    }
    ctx.restore();

    // 缓存用于 tooltip
    chartState[canvasId] = { series: useSeries, samples: samples, plot: plot, xFor: xFor, leftDomain: leftDomain, rightDomain: rightDomain, options: options };
    attachTooltip(canvas, canvasId);
  }

  function hexA(hex, alpha) {
    var h = String(hex).replace("#", "");
    if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
    var r = parseInt(h.slice(0, 2), 16), g = parseInt(h.slice(2, 4), 16), b = parseInt(h.slice(4, 6), 16);
    return "rgba(" + r + "," + g + "," + b + "," + alpha + ")";
  }

  /* 图表 tooltip */
  function attachTooltip(canvas, canvasId) {
    if (canvas.__agTipBound) return;
    canvas.__agTipBound = true;
    var wrap = canvas.parentElement;
    if (!wrap) return;
    wrap.style.position = "relative";
    var tip = document.createElement("div");
    tip.className = "ag-chart-tip";
    wrap.appendChild(tip);

    canvas.addEventListener("mousemove", function (e) {
      var st = chartState[canvasId];
      if (!st || !st.samples || !st.samples.length) return;
      var rect = canvas.getBoundingClientRect();
      var scale = canvas.width / rect.width;
      var x = (e.clientX - rect.left) * scale;
      var n = st.samples.length;
      var best = 0, bestDist = Infinity;
      for (var i = 0; i < n; i += 1) {
        var d = Math.abs(st.xFor(i) - x);
        if (d < bestDist) { bestDist = d; best = i; }
      }
      var s = st.samples[best];
      if (!s) return;
      var t = Date.parse(s.timestamp);
      var time = isNaN(t) ? "--" : new Date(t).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
      var txt = "<b>" + time + "</b>";
      if (Number.isFinite(s.moisture)) txt += " · 湿度 <b>" + s.moisture.toFixed(1) + "%</b>";
      if (Number.isFinite(s.temperature)) txt += " · 温度 <b>" + s.temperature.toFixed(1) + "℃</b>";
      if (Number.isFinite(s.light)) txt += " · 光照 <b>" + Math.round(s.light) + " lx</b>";
      tip.innerHTML = txt;
      tip.classList.add("show");
      tip.style.left = (st.xFor(best) / scale) + "px";
      tip.style.top = (e.clientY - rect.top) + "px";
    });
    canvas.addEventListener("mouseleave", function () { tip.classList.remove("show"); });
  }

  /* 时间范围切换控件 */
  function injectRangeControls() {
    [["#moisture-chart", "moisture"], ["#climate-chart", "climate"]].forEach(function (pair) {
      var canvas = document.querySelector(pair[0]);
      if (!canvas) return;
      var heading = canvas.closest(".panel") && canvas.closest(".panel").querySelector(".panel-heading");
      if (!heading || heading.querySelector(".ag-chart-range")) return;
      var box = document.createElement("div");
      box.className = "ag-chart-range";
      ["1h", "6h", "10h"].forEach(function (r) {
        var b = document.createElement("button");
        b.type = "button";
        b.textContent = r;
        if ((chartRange[pair[0]] || "10h") === r) b.className = "active";
        b.addEventListener("click", function () {
          chartRange[pair[0]] = r;
          box.querySelectorAll("button").forEach(function (x) { x.classList.remove("active"); });
          b.classList.add("active");
          var st = chartState[pair[0]];
          if (st) redrawAllCharts();
        });
        box.appendChild(b);
      });
      heading.appendChild(box);
    });
  }

  function redrawAllCharts() {
    ["#moisture-chart", "#climate-chart"].forEach(function (id) {
      var st = chartState[id];
      if (st) drawEnhanced(id, st.series, st.options);
    });
  }

  function overrideChart() {
    if (typeof origDraw !== "function") return;
    window.drawSeriesChart = function (canvasId, series, options) {
      try {
        drawEnhanced(canvasId, series, options);
        injectRangeControls();
      } catch (e) {
        try { origDraw(canvasId, series, options); } catch (e2) {}
      }
    };
    // 立即用新样式重画一次
    try { if (typeof renderTrendPanels === "function") renderTrendPanels(); } catch (e) {}
  }

  /* ---------------- 3. 指标卡：sparkline + 数字滚动 + 状态标签 ---------------- */
  function sparkSvg(values, color) {
    if (!values || values.length < 2) return "";
    var vals = values.slice(-40).filter(function (v) { return Number.isFinite(v); });
    if (vals.length < 2) return "";
    var min = Math.min.apply(null, vals), max = Math.max.apply(null, vals);
    if (min === max) { min -= 1; max += 1; }
    var w = 120, h = 30;
    var pts = vals.map(function (v, i) {
      return [ (i / (vals.length - 1)) * w, h - ((v - min) / (max - min)) * (h - 4) - 2 ];
    });
    var d = "M" + pts.map(function (p) { return p[0].toFixed(1) + "," + p[1].toFixed(1); }).join(" L");
    var area = d + " L" + w + "," + h + " L0," + h + " Z";
    return '<svg viewBox="0 0 ' + w + " " + h + '" preserveAspectRatio="none">' +
      '<path d="' + area + '" fill="' + color + '" opacity=".12"/>' +
      '<path class="ag-spark-line" d="' + d + '"/>' +
      '<circle cx="' + pts[pts.length - 1][0].toFixed(1) + '" cy="' + pts[pts.length - 1][1].toFixed(1) + '" r="2.6" fill="currentColor"/>' +
      "</svg>";
  }

  function metricKey(metric) {
    var id = metric.id || "";
    var label = (metric.querySelector(".metric-label") || {}).textContent || "";
    if (label.indexOf("土壤湿度") >= 0) return "moisture";
    if (label.indexOf("空气温度") >= 0) return "temperature";
    if (label.indexOf("光照") >= 0) return "light";
    return "";
  }

  function updateMetrics() {
    var metrics = document.querySelectorAll(".metric");
    metrics.forEach(function (metric) {
      var host = metric.querySelector(".ag-spark");
      if (!host) {
        host = document.createElement("div");
        host.className = "ag-spark";
        metric.appendChild(host);
      }
      var key = metricKey(metric);
      var samples = (typeof state !== "undefined" && state.samples) ? state.samples : [];
      // 关键：仅在内容真正变化时写 DOM。
      // 无条件 innerHTML 会在 MutationObserver 回调里再次触发 Observer，
      // 形成无限循环把主线程吃满 → 页面点击全部失效。
      var svg = (key && samples.length > 1)
        ? sparkSvg(samples.map(function (s) { return s[key]; }), getComputedStyle(metric).color)
        : "";
      if (host.__agSvg !== svg) {
        host.__agSvg = svg;
        host.innerHTML = svg;
        host.style.color = svg ? getComputedStyle(metric).color : "";
      }
      // 状态标签：依据目标区间判定
      var span = metric.querySelector("span:not(.ag-spark)");
      var strong = metric.querySelector("strong");
      if (span && strong) {
        var text = span.textContent || "";
        var rangeNums = (text.match(/-?\d+(\.\d+)?/g) || []).map(Number);
        var value = parseFloat(String(strong.textContent).replace(/[^\d.-]/g, ""));
        var status = "";
        if (rangeNums.length >= 2 && Number.isFinite(value)) {
          var lo = Math.min(rangeNums[0], rangeNums[1]);
          var hi = Math.max(rangeNums[0], rangeNums[1]);
          if (value < lo) status = "low";
          else if (value > hi) status = "high";
        }
        if (metric.__agStatus !== status) {
          metric.__agStatus = status;
          if (status) metric.setAttribute("data-status", status);
          else metric.removeAttribute("data-status");
        }

        // 数字滚动动画（值变化才触发；动画期间的写入不再递归触发重绘）
        if (strong.__agLast !== value && Number.isFinite(value)) {
          var from = Number.isFinite(strong.__agLast) ? strong.__agLast : value;
          strong.__agLast = value;
          animateNumber(strong, from, value, strong.textContent);
        }
      }
    });
  }

  function animateNumber(el, from, to, template) {
    var start = performance.now();
    var dur = 420;
    var suffix = String(template).replace(/[\d.\-]/g, "");
    var decimals = (String(to).split(".")[1] || "").length;
    function step(now) {
      var p = Math.min((now - start) / dur, 1);
      var eased = 1 - Math.pow(1 - p, 3);
      var v = from + (to - from) * eased;
      el.textContent = v.toFixed(decimals) + suffix;
      if (p < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  }

  /* ---------------- 4. 地块卡片：作物图标 + hover 读数 ---------------- */
  var CROP_ICON = {
    "苹果": "🍎", "梨": "🍐", "橘子": "🍊", "草莓": "🍓",
    "番茄": "🍅", "黄瓜": "🥒", "葡萄": "🍇", "水稻": "🌾", "其他": "🌱"
  };

  function decoratePlots() {
    var strip = document.querySelector(".plots-strip");
    if (!strip) return;
    Array.prototype.forEach.call(strip.children, function (card) {
      if (card.__agDeco) return;
      card.__agDeco = true;
      var title = (card.querySelector("strong, h3, .plot-name") || {}).textContent || "";
      var crop = "";
      Object.keys(CROP_ICON).forEach(function (k) { if (title.indexOf(k) >= 0) crop = k; });
      var icon = document.createElement("div");
      icon.className = "ag-plot-icon";
      icon.style.cssText = "font-size:26px;display:grid;place-items:center";
      icon.textContent = CROP_ICON[crop] || "🌿";
      card.insertBefore(icon, card.firstChild);

      var reads = document.createElement("div");
      reads.className = "ag-plot-reads";
      reads.innerHTML = "<div><span>湿度</span><b>--</b><span>温度</span><b>--</b><span>光照</span><b>--</b></div>";
      card.appendChild(reads);
      card.addEventListener("mouseenter", function () {
        var s = (typeof state !== "undefined" && state.samples) ? state.samples : [];
        var last = s[s.length - 1];
        if (!last) return;
        var bs = reads.querySelectorAll("b");
        if (bs[0]) bs[0].textContent = Number.isFinite(last.moisture) ? last.moisture.toFixed(1) + "%" : "--";
        if (bs[1]) bs[1].textContent = Number.isFinite(last.temperature) ? last.temperature.toFixed(1) + "℃" : "--";
        if (bs[2]) bs[2].textContent = Number.isFinite(last.light) ? Math.round(last.light) + "lx" : "--";
      });
    });
  }

  /* ---------------- 5. 传感器小卡片装饰 ---------------- */
  var SENSOR_ICON = {
    temperature: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M14 14.8V4a2 2 0 1 0-4 0v10.8a4 4 0 1 0 4 0z"/></svg>',
    humidity: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 2.7S6 9.2 6 13.5a6 6 0 0 0 12 0C18 9.2 12 2.7 12 2.7z"/></svg>',
    ph: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M9 3v6a3 3 0 0 0 6 0V3"/><path d="M9 12a3 3 0 0 0 6 0"/></svg>',
    npk: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 21V9"/><path d="M12 9C12 6 9 5 7 7c2 0 3 1 3 2"/><path d="M12 9c0-3 3-4 5-2-2 0-3 1-3 2"/></svg>',
    conductivity: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M13 2L4 14h6l-1 8 9-12h-6z"/></svg>'
  };

  function decorateSensors() {
    // 传感器卡片结构由 sensor.css 决定，不再强塞新子元素，
    // 仅依赖 theme.css 的样式增强（圆角/阴影/hover/状态标签 pill）。
    // 此处保留空函数作为 hook，便于后续在不破坏布局前提下追加装饰。
  }

  /* ---------------- 6. 智能体：打字动画 / 空态装饰 ---------------- */
  function decorateAgent() {
    var box = document.querySelector("#agent-messages");
    if (!box) return;
    Array.prototype.forEach.call(box.querySelectorAll(".agent-message"), function (msg) {
      var bubble = msg.querySelector(".agent-bubble");
      if (!bubble || bubble.__agTyping) return;
      if (/思考中|正在|加载中|…/.test(bubble.textContent || "")) {
        bubble.__agTyping = true;
        var dots = document.createElement("span");
        dots.className = "ag-typing";
        dots.innerHTML = "<i></i><i></i><i></i>";
        bubble.appendChild(dots);
      }
    });
    // 空态插画
    Array.prototype.forEach.call(document.querySelectorAll(".empty-state"), function (el) {
      if (el.__agDeco) return;
      el.__agDeco = true;
      var deco = document.createElement("div");
      deco.className = "ag-empty-deco";
      deco.innerHTML = '<svg viewBox="0 0 64 64" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="color:var(--ag-green)"><path d="M32 54V30"/><path d="M32 30c0-8-7-13-15-12 1 7 6 11 12 12"/><path d="M32 38c0-6 5-10 11-9-1 5-4 8-9 9"/><path d="M20 58h24"/></svg>';
      el.insertBefore(deco, el.firstChild);
    });
  }

  /* ---------------- 7. 面板角落叶片装饰 ---------------- */
  function decoratePanels() {
    Array.prototype.forEach.call(document.querySelectorAll(".panel"), function (panel) {
      if (panel.querySelector(":scope > .ag-leaf-deco")) return;
      if (panel.classList.contains("chart-panel")) return;
      var deco = document.createElement("div");
      deco.className = "ag-leaf-deco";
      deco.innerHTML = '<svg viewBox="0 0 64 64" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" style="color:var(--ag-green)"><path d="M52 12C30 12 14 26 14 46c0 3 .4 5 .8 6 2-16 14-27 30-29-11 4-19 12-23 23 16 2 28-9 30-24z"/></svg>';
      panel.appendChild(deco);
    });
  }

  /* ---------------- 启动 ---------------- */
  function boot() {
    try { initThemeToggle(); } catch (e) {}
    try { overrideChart(); } catch (e) {}
    [decoratePanels, decoratePlots, decorateSensors, decorateAgent, updateMetrics].forEach(function (fn) {
      try { fn(); } catch (e) {}
    });

    // app.js 每 5 秒刷新，这里跟随刷新装饰
    setInterval(function () {
      [decoratePlots, decorateSensors, decorateAgent, updateMetrics].forEach(function (fn) {
        try { fn(); } catch (e) {}
      });
    }, 2000);

    var mo = new MutationObserver(function () {
      try { decoratePlots(); decorateSensors(); decorateAgent(); updateMetrics(); } catch (e) {}
    });
    var shell = document.querySelector(".shell");
    if (shell) mo.observe(shell, { childList: true, subtree: true });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
