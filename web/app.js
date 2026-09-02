const params = new URLSearchParams(window.location.search);
if (!Auth.getToken()) Auth.redirectToLogin();
const API = Auth.apiBase();
const AI_API = Auth.aiBase();
let DEVICE_ID = params.get("device") || null; // resolved to first available device on first refresh
let selectedDeviceId = DEVICE_ID; // follows the plot switcher; drives metrics/trends/agent
const PLOT_NAMES = { "sim-plot-apple": "苹果园", "sim-plot-pear": "梨园", "sim-plot-orange": "橘园" };
const HISTORY_LIMIT = 7200;
const state = { device: null, moisture: [], samples: [], aiReady: false, pump: null, mode: "manual", notifications: false, lastAlertSignature: "", rule: null, user: Auth.getUser() };
document.querySelector("#api-url").textContent = API;
const $ = (selector) => document.querySelector(selector);
applyRole();

const fmt = (value, digits = 1, suffix = "") => value === undefined || value === null ? "--" : `${Number(value).toFixed(digits)}${suffix}`;

function setConnection(ok, message) {
  const dot = $("#connection-dot");
  if (dot) dot.classList.toggle("off", !ok);
  const label = $("#connection-label");
  if (label) label.textContent = message;
}

// Lightweight health probe that drives the top-right connection pill.
// Separated from refresh() so a slow /devices response (large payload, ~239KB
// from the Singapore server) does NOT turn the API pill red and the user can
// still see real API outages vs. transient fetch slowness.
async function probeHealthz() {
  // 网关只代理 /api 与 /ai 前缀；API 的 healthz 在无前缀的 /healthz，故用 system/status 探测
  const url = `${API}/api/v1/system/status`;
  try {
    const response = await fetch(url, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    // system/status 返回 status:"ready"（旧 /healthz 是 "ok"），两者都算健康
    if (data && (data.status === "ok" || data.status === "ready")) {
      setConnection(true, "API 已连接");
    } else {
      setConnection(false, "API 暂不可用");
    }
  } catch (_) {
    setConnection(false, "API 暂不可用");
  }
}

function setMeter(id, value) { $(id).style.width = `${Math.max(0, Math.min(100, value))}%`; }

function formatChartTime(timestamp) {
  const date = new Date(timestamp);
  if (!Number.isFinite(date.getTime())) return "--:--";
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}

function chartDomain(values) {
  const finite = values.filter(Number.isFinite);
  if (!finite.length) return [0, 1];
  const min = Math.min(...finite);
  const max = Math.max(...finite);
  const span = Math.max(max - min, Math.abs(max) * 0.05, 1);
  return [min - span * 0.08, max + span * 0.08];
}

function drawSeriesChart(canvasId, series, options = {}) {
  const canvas = $(canvasId);
  if (!canvas) return;
  const context = canvas.getContext("2d");
  const width = canvas.width;
  const height = canvas.height;
  context.clearRect(0, 0, width, height);

  const samples = options.samples || state.samples;
  const plot = { left: 68, right: options.rightAxis ? width - 68 : width - 18, top: 16, bottom: height - 39 };
  const leftSeries = series.filter((item) => (item.axis || "left") === "left");
  const rightSeries = series.filter((item) => item.axis === "right");
  const leftDomain = chartDomain(leftSeries.flatMap((item) => item.values));
  const rightDomain = chartDomain(rightSeries.flatMap((item) => item.values));
  const xCount = Math.max(...series.map((item) => item.values.length), 0);
  const hasData = xCount >= 2 && series.some((item) => item.values.some(Number.isFinite));
  const yFor = (value, axis) => {
    const domain = axis === "right" ? rightDomain : leftDomain;
    return plot.bottom - ((value - domain[0]) / (domain[1] - domain[0])) * (plot.bottom - plot.top);
  };
  const xFor = (index) => plot.left + (index / Math.max(xCount - 1, 1)) * (plot.right - plot.left);

  context.font = "12px DM Sans, Noto Sans SC, sans-serif";
  context.lineWidth = 1;
  context.strokeStyle = "#e4ebe5";
  context.fillStyle = "#6d776f";
  context.textBaseline = "middle";
  context.strokeStyle = "#e4ebe5";
  for (let line = 0; line <= 4; line += 1) {
    const y = plot.top + (line / 4) * (plot.bottom - plot.top);
    context.beginPath();
    context.moveTo(plot.left, y);
    context.lineTo(plot.right, y);
    context.stroke();
    const value = leftDomain[1] - (line / 4) * (leftDomain[1] - leftDomain[0]);
    context.textAlign = "right";
    context.fillText(options.leftFormat ? options.leftFormat(value) : value.toFixed(1), plot.left - 9, y);
    if (options.rightAxis) {
      const rightValue = rightDomain[1] - (line / 4) * (rightDomain[1] - rightDomain[0]);
      context.textAlign = "left";
      context.fillText(options.rightFormat ? options.rightFormat(rightValue) : rightValue.toFixed(1), plot.right + 9, y);
    }
  }

  context.strokeStyle = "#9aa89e";
  context.beginPath();
  context.moveTo(plot.left, plot.top);
  context.lineTo(plot.left, plot.bottom);
  context.lineTo(plot.right, plot.bottom);
  if (options.rightAxis) {
    context.moveTo(plot.right, plot.top);
    context.lineTo(plot.right, plot.bottom);
  }
  context.stroke();

  const tickCount = width < 600 ? 4 : 6;
  context.textAlign = "center";
  context.textBaseline = "top";
  for (let tick = 0; tick < tickCount; tick += 1) {
    const index = Math.round((tick / (tickCount - 1)) * Math.max(xCount - 1, 0));
    const x = xFor(index);
    context.strokeStyle = "#9aa89e";
    context.beginPath();
    context.moveTo(x, plot.bottom);
    context.lineTo(x, plot.bottom + 5);
    context.stroke();
    const sample = samples[index];
    context.fillStyle = "#6d776f";
    context.fillText(sample?.timestamp ? formatChartTime(sample.timestamp) : "--:--", x, plot.bottom + 9);
  }
  context.fillText("时间", (plot.left + plot.right) / 2, height - 16);
  context.save();
  context.translate(15, (plot.top + plot.bottom) / 2);
  context.rotate(-Math.PI / 2);
  context.textBaseline = "middle";
  context.fillText(options.leftLabel || "数值", 0, 0);
  context.restore();
  if (options.rightAxis) {
    context.save();
    context.translate(width - 15, (plot.top + plot.bottom) / 2);
    context.rotate(Math.PI / 2);
    context.fillText(options.rightLabel || "数值", 0, 0);
    context.restore();
  }
  if (!hasData) return;

  context.save();
  context.beginPath();
  context.rect(plot.left, plot.top, plot.right - plot.left, plot.bottom - plot.top);
  context.clip();
  series.forEach((item, index) => {
    if (item.values.length < 2) return;
    const axis = item.axis || "left";
    context.strokeStyle = item.color || ["#226b46", "#236a80", "#a26716"][index % 3];
    context.lineWidth = 3;
    context.lineJoin = "round";
    context.lineCap = "round";
    context.beginPath();
    let started = false;
    item.values.forEach((value, point) => {
      if (!Number.isFinite(value)) { started = false; return; }
      const x = xFor(point);
      const y = yFor(value, axis);
      started ? context.lineTo(x, y) : context.moveTo(x, y);
      started = true;
    });
    context.stroke();
  });
  context.restore();
}

function drawTrend() {
  drawSeriesChart("#trend-chart", [{ values: state.moisture, color: "#226b46" }], {
    leftLabel: "土壤湿度 (%)",
    leftFormat: (value) => `${value.toFixed(0)}%`,
  });
}

function renderTrendPanels() {
  drawSeriesChart("#moisture-chart", [{ values: state.samples.map((sample) => sample.moisture), color: "#226b46" }], {
    samples: state.samples,
    leftLabel: "土壤湿度 (%)",
    leftFormat: (value) => `${value.toFixed(0)}%`,
  });
  drawSeriesChart("#climate-chart", [
    { values: state.samples.map((sample) => sample.temperature), color: "#236a80", axis: "left" },
    { values: state.samples.map((sample) => sample.light / 1000), color: "#a26716", axis: "right" },
  ], {
    samples: state.samples,
    rightAxis: true,
    leftLabel: "温度 (°C)",
    rightLabel: "光照 (kLux)",
    leftFormat: (value) => `${value.toFixed(1)}°`,
    rightFormat: (value) => `${value.toFixed(1)}`,
  });
  const latest = state.samples[state.samples.length - 1];
  $("#sample-count").textContent = state.samples.length;
  $("#trend-moisture-value").textContent = latest ? fmt(latest.moisture, 1, " %") : "--";
  $("#trend-temperature-value").textContent = latest ? fmt(latest.temperature, 1, " °C") : "--";
}

function setRoute(route) {
  const nextRoute = ["overview", "trends", "devices", "agent", "dashboard", "reports"].includes(route) ? route : "overview";
  document.body.classList.toggle("dashboard-active", nextRoute === "dashboard");
  document.querySelectorAll("[data-view]").forEach((panel) => {
    panel.hidden = panel.dataset.view !== nextRoute;
  });
  document.querySelectorAll(".nav-item").forEach((button) => {
    button.classList.toggle("active", button.dataset.route === nextRoute);
  });
  const url = new URL(window.location.href);
  url.searchParams.set("view", nextRoute);
  window.history.replaceState({}, "", url);
  if (nextRoute === "trends") {
    if (state.device) refreshHistory(state.device.device_id, true);
    renderTrendPanels();
  }
  if (nextRoute === "dashboard") {
    renderDashboard(true);
    if (typeof window.__dashResize === "function") {
      setTimeout(window.__dashResize, 50); // re-measure after the view is shown
    }
  }
  if (nextRoute === "reports") {
    renderReports();
  }
}

function renderDevice(device) {
  state.device = device;
  const soil = device.telemetry?.soil?.payload || {};
  const climate = device.telemetry?.climate?.payload || {};
  const moisture = Number(soil.moisture_pct);
  $("#device-id").textContent = device.device_id;
  $("#device-count").textContent = "在线";
  $("#device-status").textContent = `最近上报 ${new Date(device.last_seen).toLocaleTimeString()}`;
  $("#soil-moisture").textContent = fmt(moisture, 1, " %");
  $("#air-temperature").textContent = fmt(climate.air_temperature_c, 1, " °C");
  $("#light-level").textContent = fmt(climate.light_lux, 0);
  setMeter("#soil-meter", moisture);
  setMeter("#temperature-meter", Number(climate.air_temperature_c) * 3.2);
  setMeter("#light-meter", Number(climate.light_lux) / 500);
  state.moisture.push(moisture);
  if (state.moisture.length > HISTORY_LIMIT) state.moisture.shift();
  state.samples.push({ timestamp: device.telemetry?.soil?.timestamp || device.telemetry?.climate?.timestamp || new Date().toISOString(), moisture, temperature: Number(climate.air_temperature_c), light: Number(climate.light_lux) });
  if (state.samples.length > HISTORY_LIMIT) state.samples.shift();
  const rows = [
    ["土壤温度", fmt(soil.temperature_c, 1, " °C"), "18–28 °C"],
    ["pH", fmt(soil.ph, 2), "5.8–6.8"],
    ["氮 / 磷 / 钾", `${fmt(soil.nitrogen_mg_kg, 0)} / ${fmt(soil.phosphorus_mg_kg, 0)} / ${fmt(soil.potassium_mg_kg, 0)}`, "mg/kg"],
    ["空气湿度", fmt(climate.air_humidity_pct, 1, " %"), "45–90 %"],
    ["电导率", fmt(soil.conductivity_ms_cm, 2, " mS/cm"), "0.4–1.8"],
  ];
  $("#telemetry-table").innerHTML = rows.map(([name, value, range]) => `<div class="telemetry-row"><span class="name">${name}</span><span class="value">${value}</span><span class="range">${range}</span></div>`).join("");
  // 设备详情面板元素（device-page-*）在部分页面布局中不存在，空值保护避免刷新中断
  if ($("#device-page-name")) $("#device-page-name").textContent = device.device_id;
  if ($("#device-page-id")) $("#device-page-id").textContent = device.device_id;
  if ($("#device-page-seen")) $("#device-page-seen").textContent = new Date(device.last_seen).toLocaleString();
  if ($("#device-page-api")) $("#device-page-api").textContent = API;
  if ($("#device-page-status")) { $("#device-page-status").textContent = "ONLINE"; $("#device-page-status").classList.remove("muted"); }
  if ($("#mqtt-contract")) $("#mqtt-contract").textContent = "在线";
  if ($("#http-contract")) $("#http-contract").textContent = "在线";
  renderTrendPanels();
  drawTrend();
  refreshPumpStatus();
  refreshAlerts();
  refreshRules();
  refreshHistory(device.device_id);
}

async function fetchLastAutoEvent(deviceId) {
  try {
    const response = await Auth.request(`/api/v1/devices/${encodeURIComponent(deviceId)}/irrigation-events?limit=1`, { cache: "no-store" });
    if (!response.ok) return null;
    const data = await response.json();
    return data.items?.length ? data.items[data.items.length - 1] : null;
  } catch (_) { return null; }
}

async function refreshRules() {
  if (!state.device) return;
  const deviceId = encodeURIComponent(state.device.device_id);
  try {
    const response = await Auth.request(`/api/v1/devices/${deviceId}/irrigation-rules`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const rule = await response.json();
    state.rule = rule;
    document.querySelectorAll(".mode-button").forEach((button) => {
      button.classList.toggle("active", (button.dataset.mode === "auto") === Boolean(rule.auto_enabled));
    });
    state.mode = rule.auto_enabled ? "auto" : "manual";
    const input = $("#moisture-threshold");
    if (document.activeElement !== input) input.value = Math.round(rule.start_threshold_pct);
    const lastEvent = await fetchLastAutoEvent(state.device.device_id);
    const lastAction = lastEvent ? ` · 最近自动动作 ${new Date(lastEvent.timestamp).toLocaleTimeString()} ${lastEvent.action === "start" ? "启动" : "停止"}灌溉` : "";
    const statusEl = $("#rule-status");
    if (rule.auto_enabled) {
      statusEl.textContent = `自动灌溉已启用：湿度 < ${rule.start_threshold_pct}% 启动，≥ ${rule.stop_threshold_pct}% 停止${lastAction}`;
      statusEl.classList.add("on");
    } else {
      statusEl.textContent = `自动模式未启用，使用手动控制${lastAction}`;
      statusEl.classList.remove("on");
    }
  } catch (_) { $("#rule-status").textContent = "灌溉规则服务暂不可用"; }
}

async function updateRules(payload, successMessage) {
  if (!state.device) return;
  if (!Auth.hasPermission("manage_rules")) { $("#action-result").textContent = "当前身份无规则配置权限"; return; }
  try {
    const response = await Auth.request(`/api/v1/devices/${encodeURIComponent(state.device.device_id)}/irrigation-rules`, {
      method: "PUT",
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    if (successMessage) $("#action-result").textContent = successMessage;
  } catch (error) {
    const messages = {
      stop_threshold_must_exceed_start_threshold: "停止阈值必须高于启动阈值",
      start_threshold_pct_out_of_range: "启动阈值需在 5–95% 之间",
      stop_threshold_pct_out_of_range: "停止阈值需在 5–95% 之间",
    };
    $("#action-result").textContent = `规则保存失败：${messages[error.message] || error.message}`;
  }
  await refreshRules();
}

let lastHistoryFetchAt = 0;
async function refreshHistory(deviceId, force = false) {
  // Trends data always comes from the cloud server's stored history (last 10h),
  // not from samples accumulated since the page was opened. Throttle background
  // refreshes to once per minute; force=true on page/trends open.
  const now = Date.now();
  if (!force && now - lastHistoryFetchAt < 60000) return;
  lastHistoryFetchAt = now;
  try {
    const response = await Auth.request(`/api/v1/devices/${encodeURIComponent(deviceId)}/telemetry/history?hours=10`, { cache: "no-store" });
    if (!response.ok) return;
    const data = await response.json();
    const buckets = new Map();
    (data.items || []).forEach((item) => {
      const parsed = Date.parse(item.timestamp);
      if (!Number.isFinite(parsed)) return;
      const bucketKey = Math.round(parsed / 5000) * 5000;
      const bucket = buckets.get(bucketKey) || { timestamp: item.timestamp };
      const payload = item.payload || {};
      if (item.kind === "soil") { bucket.moisture = Number(payload.moisture_pct); bucket.timestamp = bucket.timestamp || item.timestamp; }
      if (item.kind === "climate") { bucket.temperature = Number(payload.air_temperature_c); bucket.light = Number(payload.light_lux); }
      buckets.set(bucketKey, bucket);
    });
    const samples = [...buckets.values()].filter((item) => Number.isFinite(item.moisture) && Number.isFinite(item.temperature) && Number.isFinite(item.light));
    if (samples.length >= 2) { state.samples = samples.slice(-HISTORY_LIMIT); state.moisture = state.samples.map((item) => item.moisture); renderTrendPanels(); drawTrend(); }
  } catch (_) { /* live samples remain available when history is unavailable */ }
}

async function refresh() {
  try {
    const response = await Auth.request(`/api/v1/devices`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    // An empty list is a legitimate state now that plots are per-account: a
    // freshly created farmer account simply does not own a plot yet.
    const items = data.items || [];
    state.allDevices = items;
    state.plotScope = data.scope || "own";
    populatePlotSwitcher(items);
    renderPlotsStrip(items);
    if (items.length) {
      if (!selectedDeviceId || !items.some((item) => item.device_id === selectedDeviceId)) {
        selectedDeviceId = items[0].device_id;
        DEVICE_ID = selectedDeviceId;
      }
      renderDevice(items.find((item) => item.device_id === selectedDeviceId) || items[0]);
    }
    renderSensorsBoard(items);
    bindSensorActions();
    const addBtn = $("#sensor-add-btn");
    if (addBtn) addBtn.disabled = !Auth.hasPermission("manage_sensors");
    // Connection pill is owned by probeHealthz; refresh success no longer
    // flips the pill, so a slow /devices cannot mask a healthy API.
    $("#last-update").textContent = new Date().toLocaleTimeString();
    // Keep the big-data screen in sync whenever new telemetry arrives.
    renderDashboard(false);
  } catch (error) {
    // Refresh failure only shows the error in the device-status slot; the
    // top-right pill stays under healthz control.
    const status = $("#device-status");
    if (status) status.textContent = error.message;
    const board = $("#sensors-board");
    if (board && state.allDevices && state.allDevices.length) {
      // Keep the last-known device list visible so the user can see what
      // sensors were known before the refresh failed.
      renderSensorsBoard(state.allDevices);
    } else if (board && !board.querySelector(".sensor-group")) {
      board.innerHTML = `<p class="sensor-hint" style="padding:18px">暂未获取到设备列表：${error.message || error}</p>`;
    }
  }
}

function populatePlotSwitcher(items) {
  const select = $("#plot-select");
  if (!select) return;
  // Re-populated on every refresh: the visible set now depends on the signed-in
  // account, so it cannot be filled once at start-up.
  select.innerHTML = items.map((item) => {
    const plot = item.plot || {};
    const label = plot.name ? `${plot.name}（${plot.crop || item.device_id}）` : item.device_id;
    return `<option value="${escapeHtml(item.device_id)}">${escapeHtml(label)}</option>`;
  }).join("");
  if (!select.dataset.bound) {
    select.dataset.bound = "1";
    select.addEventListener("change", () => { selectDevice(select.value); });
  }
}

function selectDevice(deviceId) {
  if (!deviceId || deviceId === selectedDeviceId) return;
  selectedDeviceId = deviceId;
  DEVICE_ID = deviceId;
  const url = new URL(window.location.href);
  url.searchParams.set("device", deviceId);
  window.history.replaceState({}, "", url);
  const select = $("#plot-select");
  if (select && select.value !== deviceId) select.value = deviceId;
  // re-render the dashboard for the new plot and reset the history window
  state.samples = [];
  state.moisture = [];
  lastHistoryFetchAt = 0;
  refresh();
  refreshHistory(deviceId, true);
}

function renderPlotsStrip(items) {
  const strip = $("#plots-strip");
  if (!strip) return;
  $("#plots-count").textContent = `${items.length} 个地块`;
  strip.innerHTML = items.map((item) => {
    const soil = item.telemetry?.soil?.payload || {};
    const climate = item.telemetry?.climate?.payload || {};
    const plot = item.plot || {};
    const online = Boolean(item.last_seen);
    const active = item.device_id === selectedDeviceId;
    const moisture = Number(soil.moisture_pct);
    const moistureOk = moisture >= 40 && moisture <= 70;
    const temp = Number(climate.air_temperature_c);
    const tempOk = temp <= 30;
    return `<article class="plot-card ${active ? "active" : ""}" data-device="${item.device_id}">
      <div class="plot-card-head"><span class="plot-name">${plot.name || item.device_id}</span><span class="plot-crop">${plot.crop || "—"}</span>${active ? '<span class="badge">当前</span>' : ""}</div>
      <div class="plot-card-metrics">
        <div><span>湿度</span><b class="${moistureOk ? "ok" : "warn"}">${fmt(moisture, 1, "%")}</b></div>
        <div><span>温度</span><b class="${tempOk ? "ok" : "warn"}">${fmt(temp, 1, "°C")}</b></div>
        <div><span>光照</span><b>${fmt(climate.light_lux, 0, "")}</b></div>
      </div>
      <div class="plot-card-foot"><span class="plot-status ${online ? "on" : "off"}"></span><span>${online ? "在线" : "离线"}</span><small>${online ? new Date(item.last_seen).toLocaleTimeString() : "—"}</small></div>
    </article>`;
  }).join("");
  strip.querySelectorAll(".plot-card").forEach((card) => {
    card.addEventListener("click", () => selectDevice(card.dataset.device));
  });
}

async function refreshAlertLog() {
  const list = $("#alert-log-list");
  if (!list) return;
  try {
    const response = await Auth.request("/api/v1/alerts/logs?limit=30", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    if (!data.items?.length) {
      list.innerHTML = '<span class="alert-empty">暂无告警记录</span>';
      return;
    }
    list.innerHTML = data.items.map((item) => {
      const plot = PLOT_NAMES[item.device_id] || item.device_id;
      const cls = item.level === "critical" ? "critical" : "warning";
      return `<div class="alert-log-item ${cls}"><span class="alert-log-time">${new Date(item.timestamp).toLocaleString()}</span><span class="alert-log-device">${plot}</span><span class="alert-log-code">${item.code}</span><span class="alert-log-msg">${item.message}</span><span class="alert-log-status ${item.status}">${item.status === "active" ? "触发" : "恢复"}</span></div>`;
    }).join("");
  } catch (_) { /* keep last state */ }
}

async function refreshAiStatus() {
  const devicePageAi = $("#device-page-ai");
  const aiContract = $("#ai-contract");
  try {
    const response = await Auth.requestAI(`/api/v1/model/status`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    const classes = (data.classes || []).join(" / ");
    state.aiReady = Boolean(data.ready);
    $("#ai-status-copy").textContent = data.ready
      ? `模型 ${data.model_version} 已就绪 · 类别 ${classes}`
      : `${data.message || "模型尚未就绪"} · 阈值 ${Number(data.confidence_threshold || 0.6).toFixed(2)}`;
    $("#ai-status-badge").textContent = data.ready ? "MODEL READY" : "MODEL PENDING";
    $("#ai-status-badge").classList.toggle("muted", !data.ready);
    if (devicePageAi) devicePageAi.textContent = data.ready ? data.model_version : "待训练";
    if (aiContract) aiContract.textContent = data.ready ? "在线" : "未就绪";
  } catch (error) {
    $("#ai-status-copy").textContent = `AI 服务暂不可用：${error.message}`;
    $("#ai-status-badge").textContent = "AI OFFLINE";
    $("#ai-status-badge").classList.add("muted");
    if (devicePageAi) devicePageAi.textContent = "不可用";
    if (aiContract) aiContract.textContent = "离线";
  }
}

async function pump(action) {
  if (!state.device) return;
  if (!Auth.hasPermission("control_pump")) { $("#action-result").textContent = "当前身份无灌溉控制权限"; return; }
  const buttons = [$("#pump-start"), $("#pump-stop")];
  buttons.forEach((button) => { button.disabled = true; });
  try {
    const response = await Auth.request(`/api/v1/devices/${encodeURIComponent(state.device.device_id)}/pump`, { method: "POST", body: JSON.stringify({ action }) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    $("#pump-label").textContent = action === "start" ? "启动中" : "停止中";
    $("#pump-light").classList.remove("running");
    $("#pump-detail").textContent = `等待设备回执 · 命令 ${data.command_id.slice(0, 8)}`;
    $("#action-result").textContent = `已发布 ${action === "start" ? "启动" : "停止"} 指令，等待 MQTT 确认`;
    await refreshPumpStatus();
  } catch (error) { $("#action-result").textContent = `指令失败：${error.message}`; }
  buttons.forEach((button) => { button.disabled = false; });
}

function renderPump(data) {
  state.pump = data;
  const pump = data?.pump || {};
  const command = data?.command;
  const status = pump.status || "standby";
  const labels = { running: "运行中", standby: "待机", pending: pump.action === "start" ? "启动中" : "停止中", timeout: "确认超时", failed: "发送失败" };
  $("#pump-label").textContent = labels[status] || status;
  $("#pump-light").classList.toggle("running", status === "running");
  const latency = command?.latency_ms != null ? ` · ${command.latency_ms} ms` : "";
  $("#pump-detail").textContent = command ? `${command.status === "confirmed" ? "MQTT 已确认" : command.status === "timeout" ? "未收到设备回执" : "等待设备回执"}${latency}` : "等待设备状态";
  if (command?.status === "confirmed") $("#action-result").textContent = `指令已确认 · ${new Date(command.confirmed_at).toLocaleTimeString()}`;
  if (command?.status === "timeout") $("#action-result").textContent = "指令超时：请检查 MQTT 或设备连接";
}

async function refreshPumpStatus() {
  if (!state.device) return;
  try {
    const response = await Auth.request(`/api/v1/devices/${encodeURIComponent(state.device.device_id)}/pump`, { cache: "no-store" });
    if (response.ok) renderPump(await response.json());
  } catch (_) { /* keep last known actuator state */ }
}

async function refreshAlerts() {
  if (!state.device) return;
  try {
    const response = await Auth.request(`/api/v1/devices/${encodeURIComponent(state.device.device_id)}/alerts`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    $("#alert-list").innerHTML = data.items?.length
      ? data.items.map((item) => `<div class="alert-item">${item.message}</div>`).join("")
      : '<span class="alert-empty">当前无告警，环境在目标范围内</span>';
    const signature = (data.items || []).map((item) => item.code).join(",");
    if (signature && signature !== state.lastAlertSignature && state.notifications && "Notification" in window && Notification.permission === "granted") {
      data.items.forEach((item) => new Notification("智慧农业告警", { body: item.message }));
    }
    state.lastAlertSignature = signature;
  } catch (_) { $("#alert-list").innerHTML = '<span class="alert-empty">告警服务暂不可用</span>'; }
}

// ---- 上传图片组件：多选批量、校验、预览、进度、重试 ----
const UPLOAD_LIMITS = {
  maxBytes: 5 * 1024 * 1024,          // 单张最大 5 MiB（与后端 MAX_UPLOAD_BYTES 一致）
  maxCount: 9,                         // 单批最多 9 张
  formats: { "image/png": "PNG", "image/jpeg": "JPG/JPEG" }
};
let uploadQueue = [];                  // [{id, file, status: pending|uploading|done|error, progress, error, data}]
let uploadRunning = false;
let uploadSeq = 0;

function validateFile(file) {
  if (!(file.type in UPLOAD_LIMITS.formats)) return `不支持 ${file.type || "未知"} 格式，仅支持 ${Object.values(UPLOAD_LIMITS.formats).join(" / ")}`;
  if (file.size > UPLOAD_LIMITS.maxBytes) return `超过大小限制 ${UPLOAD_LIMITS.maxBytes / 1024 / 1024} MiB（实际 ${(file.size / 1024 / 1024).toFixed(1)} MiB）`;
  return null;
}

function pickFiles(fileList) {
  if (!fileList || !fileList.length) return;
  if (!Auth.hasPermission("upload_image")) { setUploadStatus("当前身份无图像上传权限", true); return; }
  const files = Array.from(fileList).slice(0, UPLOAD_LIMITS.maxCount);
  // 新一轮选择替换待上传队列（已完成/失败的保留在预览区，仅替换 pending）
  uploadQueue = uploadQueue.filter(e => e.status !== "pending");
  files.forEach((file) => {
    const invalid = validateFile(file);
    uploadQueue.push({
      id: `up-${++uploadSeq}`, file, status: invalid ? "error" : "pending",
      progress: 0, error: invalid || "", data: null,
      thumb: URL.createObjectURL(file),
    });
  });
  renderPreview();
  if (uploadQueue.some(e => e.status === "error")) setUploadStatus("部分文件未通过校验，已标红", true);
  runUploadQueue();
}

async function runUploadQueue() {
  if (uploadRunning) return;
  uploadRunning = true;
  const pending = uploadQueue.filter(e => e.status === "pending");
  if (!pending.length) { uploadRunning = false; return; }
  setUploadStatus(`开始上传 ${pending.length} 张图片…`);
  let ok = 0, fail = 0;
  for (const entry of pending) {
    if (entry.status !== "pending") continue;
    entry.status = "uploading";
    renderPreview();
    const success = await uploadOne(entry);
    if (success) ok++; else fail++;
    if (success) {
      // 上传成功后自动进行草莓成熟度识别（结果展示在 AI 识别区）
      predictStrawberry(entry.file).catch(() => {});
    }
  }
  uploadRunning = false;
  const total = ok + fail;
  setUploadStatus(total ? `上传完成：成功 ${ok} 张${fail ? `，失败 ${fail} 张（可点击卡片重试）` : ""}` : "没有可上传的文件");
}

function uploadOne(entry) {
  return new Promise((resolve) => {
    const xhr = new XMLHttpRequest();   // XHR 才能拿到上传进度事件
    const form = new FormData();
    form.append("file", entry.file);
    if (state.device) form.append("device_id", state.device.device_id);
    xhr.open("POST", `${API}/api/v1/images`);
    const token = Auth.getToken();
    if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) { entry.progress = Math.round((event.loaded / event.total) * 100); renderPreview(); }
    };
    xhr.onload = () => {
      let data = {};
      try { data = JSON.parse(xhr.responseText); } catch (_) {}
      if (xhr.status >= 200 && xhr.status < 300 && data.image_id) {
        entry.status = "done"; entry.data = data; entry.progress = 100;
      } else {
        entry.status = "error"; entry.error = data.error || data.message || `HTTP ${xhr.status}`;
      }
      renderPreview(); resolve(entry.status === "done");
    };
    xhr.onerror = () => { entry.status = "error"; entry.error = "网络错误，请检查连接后重试"; renderPreview(); resolve(false); };
    xhr.send(form);
  });
}

function retryEntry(id) {
  const entry = uploadQueue.find(e => e.id === id);
  if (!entry || entry.status !== "error") return;
  const invalid = validateFile(entry.file);
  if (invalid) { entry.error = invalid; renderPreview(); return; }
  entry.status = "pending"; entry.progress = 0; entry.error = "";
  renderPreview(); runUploadQueue();
}

function removeEntry(id) {
  const entry = uploadQueue.find(e => e.id === id);
  if (entry && entry.thumb) URL.revokeObjectURL(entry.thumb);
  uploadQueue = uploadQueue.filter(e => e.id !== id);
  renderPreview();
}

function setUploadStatus(message, isError = false) {
  const el = $("#upload-status");
  if (!el) return;
  el.textContent = message;
  el.classList.toggle("error", isError);
}

function renderPreview() {
  const wrap = $("#upload-preview");
  const progressWrap = $("#upload-progress-wrap");
  const fill = $("#upload-progress-fill");
  const text = $("#upload-progress-text");
  const queue = uploadQueue.filter(e => e.status !== "done");
  const done = uploadQueue.filter(e => e.status === "done");
  wrap.innerHTML = queue.map((entry) => {
    const statusCls = entry.status === "error" ? "error" : entry.status === "done" ? "ok" : "uploading";
    const statusText = entry.status === "error" ? (entry.error || "失败")
      : entry.status === "done" ? "已上传"
      : entry.status === "uploading" ? "上传中…" : "等待上传";
    const actions = entry.status === "error"
      ? `<button class="preview-retry" data-id="${entry.id}" aria-label="重试上传 ${entry.file.name}">重试</button>`
      : `<button class="preview-remove" data-id="${entry.id}" aria-label="移除 ${entry.file.name}">×</button>`;
    return `<div class="preview-card ${statusCls}" role="group" aria-label="${entry.file.name}：${statusText}">
      <img src="${entry.thumb}" alt="${entry.file.name}">
      <div class="preview-meta"><span class="preview-name" title="${entry.file.name}">${entry.file.name}</span><span class="preview-status">${statusText}</span></div>
      ${entry.status === "uploading" ? `<div class="preview-progress"><i style="width:${entry.progress}%"></i></div>` : ""}
      <div class="preview-actions">${actions}</div>
    </div>`;
  }).join("");
  const total = uploadQueue.length;
  const active = uploadQueue.filter(e => e.status === "pending" || e.status === "uploading").length;
  if (active) {
    progressWrap.hidden = false;
    const doneCount = uploadQueue.filter(e => e.status === "done").length;
    const pct = total ? Math.round(((doneCount) / total) * 100) : 0;
    fill.style.width = `${pct}%`;
    text.textContent = `${doneCount} / ${total}（剩余 ${active}）`;
    fill.setAttribute("aria-valuenow", pct);
  } else {
    progressWrap.hidden = true;
  }
  if (!queue.length && done.length) wrap.innerHTML += `<p class="preview-summary">本批 ${done.length} 张全部上传成功 ✓</p>`;
  // 事件绑定（重试/删除）
  wrap.querySelectorAll(".preview-retry").forEach(b => b.addEventListener("click", () => retryEntry(b.dataset.id)));
  wrap.querySelectorAll(".preview-remove").forEach(b => b.addEventListener("click", () => removeEntry(b.dataset.id)));
}

const STRAWBERRY_LABELS_ZH = { anomalous: "异常果", occluded: "遮挡", ripe: "成熟", unripe: "未成熟" };

async function predictStrawberry(file) {
  if (!file) return;
  $("#ai-result").innerHTML = `<span class="ai-result-empty">正在识别...（AI 推理约需 1-3 秒）</span>`;
  const form = new FormData();
  form.append("file", file);
  try {
    const response = await Auth.requestAI(`/api/v1/predict`, { method: "POST", body: form });
    const data = await response.json();
    if (data.status === "not_ready") throw new Error(data.message || "模型未就绪");
    if (data.status === "error" || !response.ok) throw new Error(data.message || `HTTP ${response.status}`);
    const probs = Object.entries(data.probabilities || {}).sort((a, b) => b[1] - a[1]);
    const bars = probs.map(([cls, prob]) =>
      `<div class="prob-row"><span>${STRAWBERRY_LABELS_ZH[cls] || cls}</span><div class="prob-bar"><i style="width:${Math.round(prob * 100)}%"></i></div><b>${(prob * 100).toFixed(1)}%</b></div>`
    ).join("");
    const accepted = Boolean(data.predicted_class);
    const topLabel = data.predicted_label || "不确定";
    $("#ai-result").innerHTML = `
      <div class="ai-result-head ${accepted ? "ok" : "low"}">
        <strong>${topLabel}</strong><span>${(data.confidence * 100).toFixed(1)}%</span>
      </div>
      <p class="ai-result-note">${accepted ? "置信度高于阈值，识别结果可信" : `置信度低于阈值 ${(data.threshold ?? 0.6) * 100}%，结果仅供参考`} · ${data.latency_ms ?? "-"} ms</p>
      ${bars}`;
  } catch (error) { $("#ai-result").innerHTML = `<span class="ai-result-empty">识别失败：${error.message}</span>`; }
}

function applyRole() {
  const user = state.user;
  const badge = $("#role-badge");
  if (badge) badge.textContent = user ? `${user.role_label || user.role} · ${user.display_name || user.username}` : "未登录";
  const canPump = Auth.hasPermission("control_pump");
  const canRules = Auth.hasPermission("manage_rules");
  const canUpload = Auth.hasPermission("upload_image");
  [$("#pump-start"), $("#pump-stop")].forEach((button) => { if (button) button.disabled = !canPump; });
  const controlPanel = document.querySelector(".control-panel");
  if (controlPanel) controlPanel.classList.toggle("locked", !canRules);
  document.querySelectorAll(".mode-button").forEach((button) => { button.disabled = !canRules; });
  const threshold = $("#moisture-threshold"); if (threshold) threshold.disabled = !canRules;
  const schedule = $("#schedule-enabled"); if (schedule) schedule.disabled = !canRules;
  const uploadPanel = document.querySelector(".upload-panel");
  if (uploadPanel) uploadPanel.classList.toggle("locked", !canUpload);
  const canManageSensors = Auth.hasPermission("manage_sensors");
  const addBtn = $("#sensor-add-btn");
  if (addBtn) addBtn.disabled = !canManageSensors;
  const logout = $("#logout-button");
  if (logout && !logout.dataset.bound) {
    logout.dataset.bound = "1";
    logout.addEventListener("click", () => { Auth.logout(); });
  }
}

async function refreshUserPermissions() {
  // Permissions can change server-side (e.g. new roles added after a user
  // logged in). Pull the freshest user/permissions from /auth/me so buttons
  // like sensor management are enabled for farmers right away without a
  // re-login.
  try {
    const response = await Auth.request("/api/v1/auth/me", { cache: "no-store" });
    if (!response.ok) return;
    const data = await response.json();
    if (data && data.user) {
      Auth.setSession(Auth.getToken(), data.user);
      state.user = data.user;
      applyRole();
      bindUsersActions();
      if (Auth.hasPermission("list_users")) loadUsers();
      // Re-render the sensor board with the freshest permissions so the
      // connect/disconnect/delete/add buttons enable as soon as /auth/me
      // returns, without waiting for the next refresh() round.
      if (state.allDevices && state.allDevices.length) renderSensorsBoard(state.allDevices);
    }
  } catch (_) { /* offline; keep cached permissions */ }
}

$("#refresh-button").addEventListener("click", refresh);
document.querySelectorAll(".nav-item").forEach((button) => button.addEventListener("click", () => setRoute(button.dataset.route)));
$("#pump-start").addEventListener("click", () => pump("start"));
$("#pump-stop").addEventListener("click", () => pump("stop"));
document.querySelectorAll(".mode-button").forEach((button) => button.addEventListener("click", () => {
  const enableAuto = button.dataset.mode === "auto";
  updateRules({ auto_enabled: enableAuto }, enableAuto ? "已启用自动灌溉，规则由服务端执行" : "已切换为手动模式");
}));
$("#moisture-threshold").addEventListener("change", (event) => {
  const start = Number(event.target.value);
  if (!Number.isFinite(start) || start < 5 || start > 95) {
    $("#action-result").textContent = "启动阈值需在 5–95% 之间";
    return;
  }
  const stop = Math.min(start + 15, 95);
  if (stop <= start) { $("#action-result").textContent = "停止阈值必须高于启动阈值"; return; }
  updateRules({ start_threshold_pct: start, stop_threshold_pct: stop }, `规则已保存：低于 ${start}% 自动启动灌溉`);
});
$("#schedule-enabled").addEventListener("change", (event) => { $("#schedule-time").disabled = !event.target.checked; });
$("#notify-button").addEventListener("click", async () => {
  if (!("Notification" in window)) { $("#action-result").textContent = "当前浏览器不支持通知"; return; }
  const permission = await Notification.requestPermission();
  state.notifications = permission === "granted";
  $("#notify-button").textContent = state.notifications ? "通知已启用" : "启用通知";
});
$("#image-input").addEventListener("change", (event) => { pickFiles(event.target.files); event.target.value = ""; });
$("#upload-btn").addEventListener("click", () => $("#image-input").click());
$("#dropzone").addEventListener("dragover", (event) => { event.preventDefault(); $("#dropzone").classList.add("dragging"); });
$("#dropzone").addEventListener("dragleave", () => $("#dropzone").classList.remove("dragging"));
$("#dropzone").addEventListener("drop", (event) => { event.preventDefault(); $("#dropzone").classList.remove("dragging"); pickFiles(event.dataTransfer.files); });
const alertLogRefresh = $("#alert-log-refresh");
if (alertLogRefresh) alertLogRefresh.addEventListener("click", refreshAlertLog);

// --- Day 16: sensor registry board -----------------------------------------
const SENSOR_TYPE_META = {
  soil_temperature:  { name: "土壤温度",  icon: "thermometer",     format: (v) => v.temperature_c !== undefined ? Number(v.temperature_c).toFixed(1) : "--" },
  soil_ph:           { name: "pH",         icon: "flask-conical",   format: (v) => v.ph !== undefined ? Number(v.ph).toFixed(2) : "--" },
  soil_npk:          { name: "氮/磷/钾",   icon: "beaker",          format: (v) => v.nitrogen_mg_kg !== undefined ? `${Math.round(v.nitrogen_mg_kg)}/${Math.round(v.phosphorus_mg_kg)}/${Math.round(v.potassium_mg_kg)}` : "--" },
  air_humidity:      { name: "空气湿度",   icon: "droplets",         format: (v) => v.air_humidity_pct !== undefined ? Number(v.air_humidity_pct).toFixed(1) : "--" },
  soil_conductivity: { name: "电导率",     icon: "activity",        format: (v) => v.conductivity_ms_cm !== undefined ? Number(v.conductivity_ms_cm).toFixed(3) : "--" },
};
const SENSOR_TYPE_ORDER = ["soil_temperature", "soil_ph", "soil_npk", "air_humidity", "soil_conductivity"];

function renderSensorCard(sensor) {
  const meta = SENSOR_TYPE_META[sensor.type] || { name: sensor.type, icon: "circle", format: () => "--" };
  const value = sensor.value || {};
  const formatted = meta.format(value);
  const isConnected = sensor.status === "connected";
  const canManage = Auth.hasPermission("manage_sensors");
  const lastSeen = sensor.last_seen ? new Date(sensor.last_seen).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "—";
  const unitText = sensor.unit && sensor.unit.length ? ` ${sensor.unit}` : "";
  const icon = `<i data-lucide="${meta.icon}"></i>`;
  return `<div class="sensor-card ${isConnected ? "" : "disconnected"}" data-sensor="${sensor.id}">
    <div class="sensor-card-head">${icon}<span>${meta.name}</span></div>
    <div class="sensor-value">${formatted}<span class="sensor-unit">${unitText}</span></div>
    <span class="sensor-status ${sensor.status}">${isConnected ? "已连接" : "已断开"}</span>
    <div class="sensor-last-seen">最近上报 ${lastSeen}</div>
    <div class="sensor-card-actions">
      <button class="toggle" data-action="toggle" data-sensor="${sensor.id}" data-status="${sensor.status}" ${canManage ? "" : "disabled"}>${isConnected ? "断开" : "连接"}</button>
      <button class="remove" data-action="remove" data-sensor="${sensor.id}" ${canManage ? "" : "disabled"}>删除</button>
    </div>
  </div>`;
}

function renderSensorsBoard(devices) {
  const board = $("#sensors-board");
  if (!board) return;
  if (!devices || !devices.length) {
    const canCreate = Auth.hasPermission("manage_sensors");
    board.innerHTML = `<div class="plot-empty-state">
      <p class="plot-empty-title">当前账户还没有地块</p>
      <p class="plot-empty-desc">地块按账户独立：每个账户只能看到自己创建或分配给自己的地块，管理员可见全部地块。${canCreate ? "" : "如需地块，请联系管理员为你创建。"}</p>
      ${canCreate ? '<button class="action primary" type="button" id="plot-empty-add-btn">+ 添加我的地块</button>' : ""}
    </div>`;
    const emptyBtn = $("#plot-empty-add-btn");
    if (emptyBtn) {
      emptyBtn.addEventListener("click", () => {
        const addBtn = $("#plot-add-btn");
        if (addBtn) addBtn.click();
      });
    }
    return;
  }
  const canManage = Auth.hasPermission("manage_sensors");
  const showOwner = state.plotScope === "all";
  const BUILTIN_PLOTS = ["sim-plot-apple", "sim-plot-pear", "sim-plot-orange"];
  board.innerHTML = devices.map((device) => {
    const sensors = (device.sensors || []).slice().sort((a, b) =>
      SENSOR_TYPE_ORDER.indexOf(a.type) - SENSOR_TYPE_ORDER.indexOf(b.type));
    const plot = device.plot || {};
    const online = Boolean(device.last_seen);
    const isBuiltin = BUILTIN_PLOTS.includes(device.device_id);
    const addSensorBtn = canManage
      ? `<button class="plot-add-sensor" data-action="add-sensor" data-device="${device.device_id}" type="button" title="为该地块添加传感器">+ 添加传感器</button>`
      : "";
    const removeBtn = (!isBuiltin && canManage)
      ? `<button class="plot-remove" data-action="remove-plot" data-device="${device.device_id}" type="button" title="删除该地块">删除地块</button>`
      : "";
    const ownerBadge = showOwner
      ? `<span class="plot-owner-badge" title="地块归属账户">${escapeHtml(device.owner_label || "未分配")}</span>`
      : "";
    return `<section class="sensor-group" data-device="${device.device_id}">
      <header class="sensor-group-header">
        <span class="sensor-group-name">${escapeHtml(plot.name || device.device_id)}</span>
        <span class="sensor-group-crop">${escapeHtml(plot.crop || "—")}</span>
        ${ownerBadge}
        <span class="sensor-group-status ${online ? "" : "off"}">
          <span class="pulse ${online ? "" : "off"}"></span>${online ? "在线" : "离线"}
        </span>
        ${addSensorBtn}${removeBtn}
      </header>
      <div class="sensor-grid">${sensors.map(renderSensorCard).join("") || '<div class="sensor-hint" style="padding:18px">该地块暂无传感器，点击"+ 添加传感器"创建。</div>'}</div>
    </section>`;
  }).join("");
  if (window.lucide) window.lucide.createIcons();
}

function setSensorHint(text, kind = "") {
  const el = $("#sensor-hint");
  if (!el) return;
  el.textContent = text;
  el.classList.remove("error", "success");
  if (kind) el.classList.add(kind);
}

async function toggleSensor(sensorId, currentStatus) {
  const next = currentStatus === "connected" ? "disconnected" : "connected";
  try {
    const response = await Auth.request(`/api/v1/sensors/${sensorId}`, {
      method: "PATCH",
      body: JSON.stringify({ status: next }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    // Apply locally FIRST so the board reflects the change even when the
    // follow-up refresh() is slow or fails (previously the old list was
    // re-rendered from stale state and the change looked like it never landed).
    if (state.allDevices) {
      state.allDevices.forEach((dev) => {
        (dev.sensors || []).forEach((s) => { if (s.id === sensorId) s.status = next; });
      });
      renderSensorsBoard(state.allDevices);
    }
    setSensorHint(`传感器已${next === "connected" ? "连接" : "断开"}`, "success");
    await refresh();
  } catch (error) {
    setSensorHint(`操作失败：${error.message || error}`, "error");
  }
}

async function deleteSensorById(sensorId) {
  try {
    const response = await Auth.request(`/api/v1/sensors/${sensorId}`, { method: "DELETE" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    // Remove locally FIRST so the sensor disappears immediately instead of
    // waiting for (or being masked by) the follow-up refresh().
    if (state.allDevices) {
      state.allDevices.forEach((dev) => {
        if (dev.sensors) dev.sensors = dev.sensors.filter((s) => s.id !== sensorId);
      });
      renderSensorsBoard(state.allDevices);
    }
    setSensorHint("传感器已删除", "success");
    await refresh();
  } catch (error) {
    setSensorHint(`删除失败：${error.message || error}`, "error");
  }
}

async function deletePlotById(deviceId) {
  try {
    const response = await Auth.request(`/api/v1/devices/${encodeURIComponent(deviceId)}`, { method: "DELETE" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.message || data.error || `HTTP ${response.status}`);
    // Local-first removal so the plot disappears immediately.
    if (state.allDevices) {
      state.allDevices = state.allDevices.filter((d) => d.device_id !== deviceId);
      if (state.device && state.device.device_id === deviceId) state.device = null;
      renderSensorsBoard(state.allDevices);
    }
    setSensorHint(`地块已删除（移除 ${data.sensors_removed ?? 0} 个传感器）`, "success");
    await refresh();
  } catch (error) {
    setSensorHint(`删除地块失败：${error.message || error}`, "error");
  }
}

async function addSensor(deviceId, sensorType) {
  try {
    const response = await Auth.request(`/api/v1/devices/${encodeURIComponent(deviceId)}/sensors`, {
      method: "POST",
      body: JSON.stringify({ type: sensorType }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    // Append locally FIRST so the new sensor card appears immediately.
    if (state.allDevices && data && data.id) {
      const dev = state.allDevices.find((d) => d.device_id === deviceId);
      if (dev) {
        if (!dev.sensors) dev.sensors = [];
        dev.sensors.push(data);
        renderSensorsBoard(state.allDevices);
      }
    }
    setSensorHint(`已创建 ${SENSOR_TYPE_META[sensorType]?.name || sensorType} 传感器`, "success");
    await refresh();
  } catch (error) {
    setSensorHint(`创建失败：${error.message || error}`, "error");
  }
}

function openAddSensorDialog(deviceId) {
  const device = (state.allDevices || []).find((d) => d.device_id === deviceId);
  if (!device) return;
  const existing = new Set((device.sensors || []).map((s) => s.type));
  const missing = SENSOR_TYPE_ORDER.filter((t) => !existing.has(t));
  const dialog = $("#sensor-add-dialog");
  const typesEl = $("#sensor-add-types");
  const label = $("#sensor-add-device-label");
  if (!dialog || !typesEl || !label) return;
  const plot = device.plot || {};
  label.textContent = `${plot.name || device.device_id}（${plot.crop || "—"}）`;
  if (!missing.length) {
    typesEl.innerHTML = '<p class="sensor-hint">该地块已配置全部 5 类传感器。</p>';
  } else {
    typesEl.innerHTML = missing.map((t) => {
      const meta = SENSOR_TYPE_META[t];
      return `<button data-type="${t}" type="button"><span>${meta.name}</span><small>${t}</small></button>`;
    }).join("");
    typesEl.querySelectorAll("button[data-type]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const type = btn.dataset.type;
        closeAddSensorDialog();
        await addSensor(deviceId, type);
      });
    });
  }
  dialog.classList.remove("hidden");
}

function closeAddSensorDialog() {
  $("#sensor-add-dialog")?.classList.add("hidden");
}

// --- Day 16: add plot (not limited to the built-in 3 plots) -----------------
async function populateCropSelect() {
  // Single source of truth for plantable crops: refresh the static options
  // from GET /api/v1/crops (v15.7.0). Falls back to the HTML defaults when
  // the catalog endpoint is unreachable.
  const select = $("#plot-crop");
  if (!select) return;
  try {
    const response = await Auth.request("/api/v1/crops", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    const crops = data.items || [];
    if (!crops.length) return;
    const current = select.value;
    select.innerHTML = crops.map((c) => `<option value="${escapeHtml(c.name)}">${escapeHtml(c.name)}</option>`).join("");
    select.value = current || crops[0].name;
  } catch (_) {
    /* keep the static <option> list in index.html */
  }
}

function openAddPlotDialog() {
  const dialog = $("#plot-add-dialog");
  if (!dialog) return;
  $("#plot-name").value = "";
  $("#plot-crop").value = "苹果";
  dialog.classList.remove("hidden");
  const nameInput = $("#plot-name");
  if (nameInput) nameInput.focus();
}

function closeAddPlotDialog() {
  $("#plot-add-dialog")?.classList.add("hidden");
}

async function addPlot() {
  const name = ($("#plot-name")?.value || "").trim();
  const crop = $("#plot-crop")?.value || "其他";
  if (!name) { setSensorHint("请填写地块名称", "error"); return; }
  try {
    const response = await Auth.request("/api/v1/devices", {
      method: "POST",
      body: JSON.stringify({ name, crop }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    closeAddPlotDialog();
    setSensorHint(`已创建地块「${name}」，模拟器约 30 秒内开始上报`, "success");
    await refresh();
  } catch (error) {
    setSensorHint(`创建地块失败：${error.message || error}`, "error");
  }
}

function bindSensorActions() {
  const board = $("#sensors-board");
  const addBtn = $("#sensor-add-btn");
  const cancelBtn = $("#sensor-add-cancel");
  const dialog = $("#sensor-add-dialog");
  if (!board || !addBtn) return;
  if (!board.dataset.bound) {
    board.dataset.bound = "1";
    board.addEventListener("click", (event) => {
      const target = event.target.closest("[data-action]");
      if (!target) return;
      const action = target.dataset.action;
      // Sensor-level actions (toggle/remove) require a sensor id.
      if (action === "toggle" || action === "remove") {
        const sensorId = target.dataset.sensor;
        if (!sensorId) return;
        if (action === "toggle") toggleSensor(sensorId, target.dataset.status);
        if (action === "remove") {
          if (confirm("确定删除该传感器？删除后云端会立即停止推送数据。")) deleteSensorById(sensorId);
        }
        return;
      }
      // Plot-level actions (delete / add sensor) use the device id instead.
      if (action === "remove-plot") {
        const deviceId = target.dataset.device;
        if (deviceId && confirm("确定删除该地块？其全部传感器将一并移除，删除后不可恢复。")) deletePlotById(deviceId);
        return;
      }
      if (action === "add-sensor") {
        const deviceId = target.dataset.device;
        if (deviceId) openAddSensorDialog(deviceId);
      }
    });
  }
  if (cancelBtn && !cancelBtn.dataset.bound) {
    cancelBtn.dataset.bound = "1";
    cancelBtn.addEventListener("click", closeAddSensorDialog);
  }
  if (dialog && !dialog.dataset.bound) {
    dialog.dataset.bound = "1";
    dialog.addEventListener("click", (event) => { if (event.target === dialog) closeAddSensorDialog(); });
  }
  if (!addBtn.dataset.bound) {
    addBtn.dataset.bound = "1";
    addBtn.addEventListener("click", () => {
      if (state.device) openAddSensorDialog(state.device.device_id);
    });
  }
  const plotAddBtn = $("#plot-add-btn");
  if (plotAddBtn && !plotAddBtn.dataset.bound) {
    plotAddBtn.dataset.bound = "1";
    plotAddBtn.addEventListener("click", openAddPlotDialog);
  }
  const plotAddConfirm = $("#plot-add-confirm");
  if (plotAddConfirm && !plotAddConfirm.dataset.bound) {
    plotAddConfirm.dataset.bound = "1";
    plotAddConfirm.addEventListener("click", addPlot);
  }
  const plotAddCancel = $("#plot-add-cancel");
  if (plotAddCancel && !plotAddCancel.dataset.bound) {
    plotAddCancel.dataset.bound = "1";
    plotAddCancel.addEventListener("click", closeAddPlotDialog);
  }
}

// --- Day 16: global MQTT broker configuration panel -------------------------
let BROKER_PRESETS = [];

function setBrokerHint(text, kind = "") {
  const el = $("#broker-hint");
  if (!el) return;
  el.textContent = text;
  el.classList.remove("error", "success");
  if (kind) el.classList.add(kind);
}

function detectPresetFor(broker) {
  if (!broker || !broker.host) return "";
  const match = BROKER_PRESETS.find((p) => p.id !== "custom" && p.host === broker.host && Number(p.port) === Number(broker.port));
  return match ? match.id : "custom";
}

function renderBroker(broker) {
  const badge = $("#broker-status-badge");
  if (badge) {
    badge.textContent = broker.source === "database" ? "已保存" : "环境变量";
    badge.classList.toggle("muted", broker.source !== "database");
  }
  $("#broker-current-host").textContent = broker.host || "--";
  $("#broker-current-port").textContent = broker.port || "--";
  $("#broker-current-user").textContent = broker.username || "(无)";
  $("#broker-current-pass").textContent = broker.password_set ? "已设置" : "(无)";
  $("#broker-current-updated").textContent = broker.updated_at ? new Date(broker.updated_at).toLocaleString() : "--";
  const form = $("#broker-form");
  if (form && !form.dataset.touched) {
    form.elements.host.value = broker.host || "";
    form.elements.port.value = broker.port || 1883;
    form.elements.username.value = broker.username || "";
    form.elements.password.value = "";
  }
  const select = $("#broker-preset-select");
  if (select) select.value = detectPresetFor(broker);
}

async function loadBroker() {
  try {
    const response = await Auth.request("/api/v1/system/mqtt-broker", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    renderBroker(await response.json());
  } catch (error) {
    const badge = $("#broker-status-badge");
    if (badge) { badge.textContent = "读取失败"; badge.classList.add("muted"); }
    setBrokerHint(`读取失败：${error.message || error}`, "error");
  }
}

async function loadBrokerPresets() {
  try {
    const response = await Auth.request("/api/v1/system/mqtt-broker-presets", { cache: "no-store" });
    if (!response.ok) return;
    const data = await response.json();
    BROKER_PRESETS = data.presets || [];
    const select = $("#broker-preset-select");
    if (!select) return;
    select.innerHTML = BROKER_PRESETS.map((p) => `<option value="${p.id}">${p.label}</option>`).join("");
    // sync the select to current broker
    try {
      const cur = await Auth.request("/api/v1/system/mqtt-broker", { cache: "no-store" });
      if (cur.ok) select.value = detectPresetFor(await cur.json());
    } catch (_) { /* offline */ }
    select.addEventListener("change", () => {
      const preset = BROKER_PRESETS.find((p) => p.id === select.value);
      if (!preset || preset.id === "custom") {
        const desc = $("#broker-preset-desc");
        if (desc) desc.textContent = "自定义 broker：请手动填写主机、端口、用户名与密码。";
        return;
      }
      const form = $("#broker-form");
      if (!form) return;
      form.dataset.touched = "1";
      form.elements.host.value = preset.host;
      form.elements.port.value = preset.port;
      form.elements.username.value = preset.username || "";
      form.elements.password.value = "";
      setBrokerHint(`已选择预设：${preset.label}（点保存后生效）`, "");
      const desc = $("#broker-preset-desc");
      if (desc) desc.textContent = preset.description || "";
    });
  } catch (_) { /* presets are convenience; don't break the panel */ }
}

function bindBrokerActions() {
  const form = $("#broker-form");
  if (!form || form.dataset.bound) return;
  form.dataset.bound = "1";
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!Auth.hasPermission("manage_sensors")) {
      setBrokerHint("当前身份无修改 broker 的权限", "error");
      return;
    }
    form.dataset.touched = "1";
    const payload = {
      host: form.elements.host.value.trim(),
      port: Number(form.elements.port.value) || 1883,
      username: form.elements.username.value.trim(),
      password: form.elements.password.value,
    };
    if (!payload.host) {
      setBrokerHint("请填写 broker 主机名", "error");
      return;
    }
    if (!payload.password) payload.password = "__KEEP__";
    const saveBtn = $("#broker-save-btn");
    if (saveBtn) saveBtn.disabled = true;
    setBrokerHint("保存中…");
    try {
      const response = await Auth.request("/api/v1/system/mqtt-broker", {
        method: "PUT",
        body: JSON.stringify(payload),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
      renderBroker({ ...data, password_set: data.password_set });
      form.elements.password.value = "";
      setBrokerHint("已保存。提示：修改 broker 地址需重启 API 与模拟器容器才能生效。", "success");
    } catch (error) {
      setBrokerHint(`保存失败：${error.message || error}`, "error");
    } finally {
      if (saveBtn) saveBtn.disabled = false;
    }
  });
  form.addEventListener("input", () => { form.dataset.touched = "1"; });
}

// --- Account management (manager: view / edit / delete farmer accounts) ----
const ROLE_LABELS_FRONT = { guest: "游客", farmer: "农户", manager: "管理者" };

function setUsersHint(text, kind = "") {
  const el = $("#users-hint");
  if (!el) return;
  el.textContent = text;
  el.classList.remove("error", "success");
  if (kind) el.classList.add(kind);
}

function renderUsers(users) {
  const list = $("#users-list");
  if (!list) return;
  if (!users || !users.length) {
    list.innerHTML = '<p class="sensor-hint">暂无账户。</p>';
    return;
  }
  const me = state.user ? String(state.user.user_id) : null;
  list.innerHTML = users.map((u) => {
    const isSelf = String(u.id) === me;
    const isManager = u.role === "manager";
    const badge = ROLE_LABELS_FRONT[u.role] || u.role;
    return `<div class="user-row" data-uid="${u.id}">
      <div class="user-info">
        <strong>${u.display_name || u.username}</strong>
        <span class="user-sub">@${u.username} · ${new Date(u.created_at).toLocaleString()}</span>
      </div>
      <span class="user-role ${u.role}">${badge}</span>
      <div class="user-actions">
        <select class="user-role-select" data-uid="${u.id}" ${isManager ? "disabled" : ""} title="${isManager ? "管理者账户不可修改" : "修改角色（农户/管理者可双向切换，需保留至少 1 名管理者）"}">
          <option value="farmer" ${u.role === "farmer" ? "selected" : ""}>农户</option>
          <option value="manager" ${u.role === "manager" ? "selected" : ""}>管理者</option>
        </select>
        <button class="user-delete" data-action="delete-user" data-uid="${u.id}" data-name="${u.display_name || u.username}" type="button" ${isManager || isSelf ? "disabled" : ""} title="${isManager ? "管理者账户不可删除" : isSelf ? "不能删除自己的账户" : "删除该账户"}">删除</button>
      </div>
    </div>`;
  }).join("");
  list.querySelectorAll(".user-role-select").forEach((select) => {
    select.addEventListener("change", () => {
      const uid = select.dataset.uid;
      updateUserRole(uid, select.value);
    });
  });
}

async function loadUsers() {
  try {
    const response = await Auth.request("/api/v1/auth/users", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    renderUsers(data.items || []);
  } catch (error) {
    setUsersHint(`加载账户失败：${error.message || error}`, "error");
  }
}

async function updateUserRole(uid, role) {
  try {
    const response = await Auth.request(`/api/v1/auth/users/${uid}`, {
      method: "PATCH",
      body: JSON.stringify({ role }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    setUsersHint("角色已更新", "success");
    loadUsers();
  } catch (error) {
    setUsersHint(`修改失败：${error.message || error}`, "error");
    loadUsers();
  }
}

async function deleteUserById(uid, name) {
  if (!confirm(`确定删除账户「${name}」？删除后该农户将无法登录，不可恢复。`)) return;
  try {
    const response = await Auth.request(`/api/v1/auth/users/${uid}`, { method: "DELETE" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    setUsersHint(`已删除账户「${data.deleted || name}」`, "success");
    loadUsers();
  } catch (error) {
    setUsersHint(`删除失败：${error.message || error}`, "error");
  }
}

function bindUsersActions() {
  const panel = $("#users-panel");
  const list = $("#users-list");
  const refreshBtn = $("#users-refresh");
  if (!panel || !list) return;
  // Visibility: only managers (list_users) see the panel.
  const canManage = Auth.hasPermission("list_users");
  panel.hidden = !canManage;
  if (!canManage) return;
  if (refreshBtn && !refreshBtn.dataset.bound) {
    refreshBtn.dataset.bound = "1";
    refreshBtn.addEventListener("click", loadUsers);
  }
  if (!list.dataset.bound) {
    list.dataset.bound = "1";
    list.addEventListener("click", (event) => {
      const target = event.target.closest("[data-action='delete-user']");
      if (!target || target.disabled) return;
      deleteUserById(target.dataset.uid, target.dataset.name);
    });
  }
}

// --- v15.8.0: AI farm steward (per-account automation butler) --------------
const STEWARD_ACTION_META = {
  pump_on:  { icon: "💧", label: "自动开泵", cls: "pump-on" },
  pump_off: { icon: "✅", label: "自动关泵", cls: "pump-off" },
  ticket:   { icon: "🛡️", label: "防治工单", cls: "ticket" },
};

function renderStewardLog(items) {
  const log = $("#steward-log");
  if (!log) return;
  if (!items || !items.length) {
    log.innerHTML = '<div class="empty">管家还没有动作。开启自动灌溉并设置湿度阈值后，跌破阈值会自动开泵并记录在这里。</div>';
    return;
  }
  log.innerHTML = items.map((item) => {
    const meta = STEWARD_ACTION_META[item.action_type] || { icon: "🤖", label: item.action_type, cls: "" };
    const time = item.created_at ? new Date(item.created_at).toLocaleString("zh-CN", { hour12: false }) : "--";
    const detail = item.detail ? `<div class="steward-detail">${escapeHtml(item.detail)}</div>` : "";
    return `<div class="alert-item steward-item ${meta.cls}">
      <span class="steward-icon">${meta.icon}</span>
      <div class="text">
        <strong>${meta.label}</strong>
        <span class="steward-plot">${escapeHtml(item.device_id)}</span>
        <p>${escapeHtml(item.reason)}</p>
        ${detail}
      </div>
      <span class="time">${time}</span>
    </div>`;
  }).join("");
}

function renderStewardConfig(cfg) {
  const wrap = $("#steward-config-wrap");
  if (!wrap) return;
  const canEdit = Auth.hasPermission("manage_rules");
  const checked = (v) => (v ? "checked" : "");
  wrap.innerHTML = `
    <h3 class="steward-timeline-title">管家设置<span class="steward-owner-tip">仅对当前账户生效</span></h3>
    <label class="steward-switch">
      <input type="checkbox" id="steward-auto-pump" ${checked(cfg.auto_pump_enabled)} ${canEdit ? "" : "disabled"}>
      <span>自动灌溉（湿度跌破阈值自动开泵，到时自动关泵）</span>
    </label>
    <label class="steward-field">土壤湿度阈值
      <input type="number" id="steward-threshold" min="10" max="90" step="0.5" value="${cfg.moisture_threshold_pct}" ${canEdit ? "" : "disabled"}>
      <span class="steward-unit">%</span>
    </label>
    <label class="steward-field">单次泵运行时长
      <input type="number" id="steward-duration" min="1" max="60" step="1" value="${cfg.pump_duration_min}" ${canEdit ? "" : "disabled"}>
      <span class="steward-unit">分钟</span>
    </label>
    <label class="steward-switch">
      <input type="checkbox" id="steward-tickets" ${checked(cfg.auto_tickets_enabled)} ${canEdit ? "" : "disabled"}>
      <span>病虫害工单（高温高湿自动生成防治建议）</span>
    </label>
    ${canEdit ? '<button class="action primary" id="steward-save" type="button">保存设置</button>' : '<p class="steward-readonly-tip">你没有管理规则权限，设置只读。</p>'}
  `;
  const saveBtn = $("#steward-save");
  if (saveBtn && !saveBtn.dataset.bound) {
    saveBtn.dataset.bound = "1";
    saveBtn.addEventListener("click", saveStewardConfig);
  }
}

async function loadSteward() {
  const panel = $("#steward-panel");
  if (!panel) return;
  panel.hidden = false;
  try {
    const [cfgResp, actResp] = await Promise.all([
      Auth.request("/api/v1/steward/config", { cache: "no-store" }),
      Auth.request("/api/v1/steward/actions?limit=30", { cache: "no-store" }),
    ]);
    if (cfgResp.ok) {
      const cfg = await cfgResp.json();
      renderStewardConfig(cfg);
    }
    if (actResp.ok) {
      const data = await actResp.json();
      renderStewardLog(data.items || []);
    }
    $("#steward-hint").textContent = "";
  } catch (error) {
    $("#steward-hint").textContent = `管家数据加载失败：${error.message || error}`;
  }
}

async function saveStewardConfig() {
  const hint = $("#steward-hint");
  const body = {
    auto_pump_enabled: $("#steward-auto-pump").checked,
    moisture_threshold_pct: Number($("#steward-threshold").value),
    pump_duration_min: Number($("#steward-duration").value),
    auto_tickets_enabled: $("#steward-tickets").checked,
  };
  try {
    const response = await Auth.request("/api/v1/steward/config", {
      method: "PUT",
      body: JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    renderStewardConfig(data);
    hint.textContent = "管家设置已保存";
    hint.style.color = "var(--green)";
  } catch (error) {
    hint.textContent = `保存失败：${error.message || error}`;
    hint.style.color = "";
  }
}

function bindStewardActions() {
  const panel = $("#steward-panel");
  const refreshBtn = $("#steward-refresh");
  if (!panel) return;
  if (refreshBtn && !refreshBtn.dataset.bound) {
    refreshBtn.dataset.bound = "1";
    refreshBtn.addEventListener("click", loadSteward);
  }
  loadSteward();
}

// --- v15.9.0: farm report + PK ranking --------------------------------------
let reportCrops = {};        // crop name -> catalog meta
let reportActions = [];      // steward timeline for the selected plot
let reportSelected = "";

async function ensureReportCrops() {
  if (Object.keys(reportCrops).length) return;
  try {
    const resp = await Auth.request("/api/v1/crops", { cache: "no-store" });
    if (resp.ok) {
      const data = await resp.json();
      reportCrops = {};
      (data.items || []).forEach((c) => { reportCrops[c.name] = c; });
    }
  } catch (_) { /* catalog unreachable → score falls back to "未知作物" */ }
}

function deviationScore(value, range) {
  // 0 = centered in range, 1 = at/beyond a boundary
  if (!Number.isFinite(value) || !Array.isArray(range) || range[0] == null) return null;
  const mid = (range[0] + range[1]) / 2;
  const half = Math.max(0.0001, (range[1] - range[0]) / 2);
  return Math.min(1, Math.abs(value - mid) / half);
}

function plotHealth(device) {
  const crop = reportCrops[(device.plot || {}).crop];
  if (!crop) return { score: null, parts: [], name: "未知作物" };
  const soil = (device.telemetry || {}).soil?.payload || {};
  const climate = (device.telemetry || {}).climate?.payload || {};
  const moisture = Number(soil.moisture_pct);
  const temp = Number(climate.air_temperature_c);
  const ph = Number(soil.ph);
  let deduct = 0;
  const parts = [];
  const m = deviationScore(moisture, crop.soil_moisture);
  if (m != null) { deduct += 40 * m; parts.push(`湿度 ${moisture.toFixed(0)}% 偏离 ${(m * 100).toFixed(0)}%`); }
  const t = deviationScore(temp, crop.air_temp);
  if (t != null) { deduct += 30 * t; parts.push(`气温 ${temp.toFixed(0)}°C 偏离 ${(t * 100).toFixed(0)}%`); }
  const p = deviationScore(ph, crop.ph);
  if (p != null) { deduct += 20 * p; parts.push(`pH ${ph.toFixed(1)} 偏离 ${(p * 100).toFixed(0)}%`); }
  const sensors = device.sensors || [];
  if (sensors.length) {
    const offline = sensors.filter((s) => s.status !== "connected").length;
    if (offline) { deduct += 10 * (offline / sensors.length); parts.push(`${offline} 个传感器离线`); }
  }
  return { score: Math.max(0, Math.round(100 - deduct)), parts, name: crop.name };
}

function plotProgress(device) {
  const crop = reportCrops[(device.plot || {}).crop];
  const created = (device.plot || {}).created_at;
  if (!crop || !created) return { pct: null, label: "种植时间未知" };
  const ageDays = Math.max(0, (Date.now() - new Date(created).getTime()) / 86400000);
  const pct = Math.min(100, Math.round((ageDays / crop.growing_days) * 100));
  const stage = pct >= 100 ? "已成熟" : pct >= 70 ? "成熟期" : pct >= 40 ? "生长期" : pct >= 10 ? "幼苗期" : "播种期";
  return { pct, label: `${ageDays.toFixed(1)} / ${crop.growing_days} 天 · ${stage}` };
}

function reportCardHtml(device) {
  const plot = device.plot || {};
  const health = plotHealth(device);
  const progress = plotProgress(device);
  const crop = reportCrops[plot.crop];
  const soil = (device.telemetry || {}).soil?.payload || {};
  const climate = (device.telemetry || {}).climate?.payload || {};
  const scoreCls = health.score == null ? "" : health.score >= 80 ? "excellent" : health.score >= 60 ? "good" : health.score >= 40 ? "fair" : "poor";
  const rangeLine = (v, r, unit) => {
    if (v == null || !Array.isArray(r) || r[0] == null) return `<span class="metric-value">--</span><span class="metric-range">参考 ${r ? (r[0] + "–" + r[1]) + (unit || "") : ""}</span>`;
    const ok = v >= r[0] && v <= r[1];
    return `<span class="metric-value ${ok ? "ok" : "warn"}">${v.toFixed ? v.toFixed(1) : v}${unit || ""}</span><span class="metric-range">${ok ? "适宜" : `参考 ${r[0]}–${r[1]}${unit || ""}`}</span>`;
  };
  const events = reportActions.slice(0, 5).map((a) => {
    const icon = a.action_type === "pump_on" ? "💧" : a.action_type === "pump_off" ? "✅" : "🛡️";
    return `<div class="report-event"><span>${icon}</span><p>${escapeHtml(a.reason)}</p><time>${new Date(a.created_at).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false })}</time></div>`;
  }).join("") || '<div class="report-event"><span>🌱</span><p>还没有管家动作，开启 AI 管家后自动记录。</p></div>';
  return `
    <div class="report-cover">
      <div class="report-cover-top"><span class="report-crop">${escapeHtml(plot.crop || "—")}</span><span class="report-owner">${escapeHtml(device.owner_label || "")}</span></div>
      <div class="report-cover-name">${escapeHtml(plot.name || device.device_id)}</div>
      <div class="report-score-ring ${scoreCls}">
        <div class="ring-inner"><strong>${health.score == null ? "--" : health.score}</strong><span>健康度</span></div>
      </div>
      <div class="report-progress">
        <div class="progress-track"><div class="progress-fill" style="width:${progress.pct == null ? 0 : progress.pct}%"></div></div>
        <span>生长进度 ${progress.label}</span>
      </div>
      <div class="report-crop-info">${crop ? `${crop.type} · 生长周期 ${crop.growing_days} 天 · ${escapeHtml(plot.crop)}` : ""}</div>
    </div>
    <div class="report-body">
      <h4>实时指标 vs 适宜区间</h4>
      <div class="report-metrics">
        <div class="metric">${rangeLine(Number(soil.moisture_pct), crop && crop.soil_moisture, "%")}<span class="metric-name">土壤湿度</span></div>
        <div class="metric">${rangeLine(Number(climate.air_temperature_c), crop && crop.air_temp, "°C")}<span class="metric-name">空气温度</span></div>
        <div class="metric">${rangeLine(Number(soil.ph), crop && crop.ph, "")}<span class="metric-name">土壤 pH</span></div>
      </div>
      <h4>关键事件</h4>
      <div class="report-events">${events}</div>
      <div class="report-deviation">${health.parts.length ? "扣分项：" + escapeHtml(health.parts.join("；")) : (health.score != null ? "所有指标均在适宜区间内 🎉" : "作物不在目录，无法评分")}</div>
    </div>`;
}

function shareReportText(device) {
  const plot = device.plot || {};
  const health = plotHealth(device);
  const progress = plotProgress(device);
  const lines = [
    `🌱 作物成长报告 · ${plot.name || device.device_id}`,
    `作物：${plot.crop || "—"}  归属：${device.owner_label || ""}`,
    `健康度：${health.score == null ? "--" : health.score + " 分"}`,
    `生长进度：${progress.label}`,
    health.parts.length ? `状态说明：${health.parts.join("；")}` : "所有指标均在适宜区间内 🎉",
    "—— 来自智慧农业大数据平台",
  ];
  return lines.join("\n");
}

async function loadReportActions(deviceId) {
  reportActions = [];
  try {
    const resp = await Auth.request(`/api/v1/steward/actions?limit=10&device_id=${encodeURIComponent(deviceId)}`, { cache: "no-store" });
    if (resp.ok) {
      const data = await resp.json();
      reportActions = (data.items || []).filter((a) => a.device_id === deviceId);
    }
  } catch (_) { /* timeline optional */ }
}

function renderRanking() {
  const wrap = $("#report-rank");
  if (!wrap) return;
  const devices = state.allDevices || [];
  const scored = devices.map((d) => ({ d, h: plotHealth(d) })).filter((x) => x.h.score != null);
  const byCrop = {};
  scored.forEach((x) => {
    const crop = x.h.name;
    (byCrop[crop] = byCrop[crop] || []).push(x);
  });
  const cropNames = Object.keys(byCrop).sort();
  if (!cropNames.length) {
    wrap.innerHTML = '<div class="rank-empty">还没有可评分的地块（需作物在目录中且有传感器数据）。</div>';
    return;
  }
  wrap.innerHTML = `<h3 class="reports-h3">🏆 同作物健康度 PK</h3>` + cropNames.map((crop) => {
    const rows = byCrop[crop].sort((a, b) => b.h.score - a.h.score);
    const medals = ["🥇", "🥈", "🥉"];
    return `<div class="rank-group">
      <div class="rank-group-head">${escapeHtml(crop)} <span>${rows.length} 块地</span></div>
      ${rows.slice(0, 5).map((x, i) => `
        <div class="rank-row ${i === 0 ? "top" : ""}" data-device="${x.d.device_id}">
          <span class="rank-medal">${medals[i] || i + 1}</span>
          <span class="rank-name">${escapeHtml((x.d.plot || {}).name || x.d.device_id)}</span>
          <span class="rank-tag">${i === 0 ? "最稳农夫" : ""}</span>
          <span class="rank-score ${x.h.score >= 80 ? "good" : x.h.score >= 60 ? "mid" : "low"}">${x.h.score}</span>
        </div>`).join("")}
    </div>`;
  }).join("");
  wrap.querySelectorAll(".rank-row").forEach((row) => {
    row.addEventListener("click", () => {
      const select = $("#report-plot-select");
      if (select) { select.value = row.dataset.device; select.dispatchEvent(new Event("change")); }
    });
  });
}

async function renderReports() {
  await ensureReportCrops();
  const devices = state.allDevices || [];
  const select = $("#report-plot-select");
  if (!select) return;
  if (!devices.length) {
    $("#report-card").innerHTML = '<div class="rank-empty">还没有地块，去「设备」页创建一个吧。</div>';
    renderRanking();
    return;
  }
  select.innerHTML = devices.map((d) => {
    const plot = d.plot || {};
    const label = plot.name ? `${plot.name}（${plot.crop || d.device_id}）` : d.device_id;
    return `<option value="${escapeHtml(d.device_id)}">${escapeHtml(label)}</option>`;
  }).join("");
  if (!reportSelected || !devices.some((d) => d.device_id === reportSelected)) {
    reportSelected = devices[0].device_id;
  }
  select.value = reportSelected;
  await loadReportActions(reportSelected);
  const device = devices.find((d) => d.device_id === reportSelected) || devices[0];
  $("#report-card").innerHTML = reportCardHtml(device);
  renderRanking();
  $("#report-hint").textContent = "";
}

function bindReportsActions() {
  const select = $("#report-plot-select");
  if (!select || select.dataset.bound) return;
  select.dataset.bound = "1";
  select.addEventListener("change", () => { reportSelected = select.value; renderReports(); });
  $("#report-refresh")?.addEventListener("click", () => { refresh().then(renderReports); });
  const shareBtn = $("#report-share");
  if (shareBtn) {
    shareBtn.addEventListener("click", async () => {
      const devices = state.allDevices || [];
      const device = devices.find((d) => d.device_id === reportSelected);
      if (!device) return;
      const text = shareReportText(device);
      try {
        await navigator.clipboard.writeText(text);
        $("#report-hint").textContent = "分享文本已复制到剪贴板 🎉";
        $("#report-hint").style.color = "var(--green)";
      } catch (_) {
        $("#report-hint").textContent = "复制失败，请手动选择文本";
        $("#report-hint").style.color = "";
      }
    });
  }
}

// --- v15.4.0: big data screen ----------------------------------------------
const DASH_SENSOR_LABELS = {
  soil_temperature: "土壤温度",
  soil_ph: "土壤 pH",
  soil_npk: "氮磷钾",
  air_humidity: "空气湿度",
  soil_conductivity: "电导率",
};

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));
}

function latestPayload(device, kind) {
  const history = Array.isArray(device?.history) ? device.history : [];
  let best = null;
  for (const entry of history) {
    if (entry?.kind !== kind) continue;
    if (!best || String(entry.timestamp) > String(best.timestamp)) best = entry;
  }
  return best?.payload || {};
}

function dashAvg(values) {
  const nums = values.filter((n) => Number.isFinite(n));
  return nums.length ? nums.reduce((a, b) => a + b, 0) / nums.length : null;
}

function dashNum(el, value, digits = 1, fallback = "--") {
  if (!el) return;
  el.textContent = Number.isFinite(value) ? value.toFixed(digits) : fallback;
}

function isDeviceOnline(device) {
  if (!device?.last_seen) return false;
  const elapsed = (Date.now() - new Date(device.last_seen).getTime()) / 1000;
  return Number.isFinite(elapsed) && elapsed < 90;
}

// Plot display metadata lives under device.plot (name/crop), not on the device itself.
function dashPlotName(device) {
  const plot = device?.plot || {};
  return plot.name || device?.device_id || "未知地块";
}

function dashPlotCrop(device) {
  return (device?.plot || {}).crop || "—";
}

function renderDashboard(force) {
  const shell = document.querySelector(".dashboard-shell");
  if (!shell || shell.hidden) return; // only render while the screen is visible
  const devices = state.allDevices || [];
  if (!devices.length) {
    const list = $("#dash-plot-list");
    if (list) list.innerHTML = '<div class="empty">等待设备数据…</div>';
    return;
  }

  const soils = devices.map((d) => latestPayload(d, "soil"));
  const climates = devices.map((d) => latestPayload(d, "climate"));
  const avgMoisture = dashAvg(soils.map((s) => Number(s.moisture_pct)));
  const avgTemp = dashAvg(climates.map((c) => Number(c.air_temperature_c)));
  const avgHumidity = dashAvg(climates.map((c) => Number(c.air_humidity_pct)));
  const avgLight = dashAvg(climates.map((c) => Number(c.light_lux)));
  const avgPh = dashAvg(soils.map((s) => Number(s.ph)));

  // --- KPI row ---
  const onlineSensors = devices.reduce(
    (sum, d) => sum + (d.sensors || []).filter((s) => s.status === "connected").length, 0
  );
  $("#kpi-plots").textContent = String(devices.length);
  $("#kpi-sensors").textContent = String(onlineSensors);
  dashNum($("#kpi-moisture"), avgMoisture, 1);
  dashNum($("#kpi-temp"), avgTemp, 1);

  const moistureTrend = $("#kpi-moisture-trend");
  if (moistureTrend) {
    const low = Number.isFinite(avgMoisture) && avgMoisture < 40;
    const high = Number.isFinite(avgMoisture) && avgMoisture > 65;
    moistureTrend.textContent = low ? "偏低 · 建议灌溉" : high ? "偏高 · 注意排水" : "目标区间 40–65%";
    moistureTrend.classList.toggle("down", low || high);
  }
  const tempTrend = $("#kpi-temp-trend");
  if (tempTrend) {
    const low = Number.isFinite(avgTemp) && avgTemp < 18;
    const high = Number.isFinite(avgTemp) && avgTemp > 28;
    tempTrend.textContent = low ? "偏低 · 注意保温" : high ? "偏高 · 注意通风" : "舒适区间 18–28°C";
    tempTrend.classList.toggle("down", low || high);
  }
  dashNum($("#dash-core-value"), avgMoisture, 1);

  // --- plot status list ---
  const plotList = $("#dash-plot-list");
  if (plotList) {
    plotList.innerHTML = devices.map((d) => {
      const online = isDeviceOnline(d);
      const name = escapeHtml(dashPlotName(d));
      const crop = escapeHtml(dashPlotCrop(d));
      return `<div class="plot-row">
        <div><div class="name">${name}</div></div>
        <span class="crop">${crop}</span>
        <span class="status ${online ? "" : "off"}"><i class="status-dot"></i>${online ? "在线" : "离线"}</span>
      </div>`;
    }).join("");
  }

  // --- derived alerts (thresholds applied to live telemetry) ---
  const alertStream = $("#dash-alert-stream");
  if (alertStream) {
    const alerts = [];
    devices.forEach((d) => {
      const soil = latestPayload(d, "soil");
      const climate = latestPayload(d, "climate");
      const label = dashPlotName(d);
      const m = Number(soil.moisture_pct);
      const t = Number(climate.air_temperature_c);
      if (Number.isFinite(m) && m < 40) alerts.push({ level: "warn", text: `${label}：土壤湿度 ${m.toFixed(1)}% 低于 40%，建议灌溉` });
      if (Number.isFinite(m) && m > 65) alerts.push({ level: "warn", text: `${label}：土壤湿度 ${m.toFixed(1)}% 高于 65%，注意排水` });
      if (Number.isFinite(t) && t > 28) alerts.push({ level: "warn", text: `${label}：气温 ${t.toFixed(1)}°C 偏高，建议通风` });
      if (Number.isFinite(t) && t < 18) alerts.push({ level: "warn", text: `${label}：气温 ${t.toFixed(1)}°C 偏低，注意保温` });
      if (!isDeviceOnline(d)) alerts.push({ level: "error", text: `${label}：设备离线，未收到近期遥测` });
    });
    alertStream.innerHTML = alerts.length
      ? alerts.map((a) => `<div class="alert-item ${a.level}">
          <span class="time">${new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
          <span class="text">${escapeHtml(a.text)}</span>
        </div>`).join("")
      : '<div class="empty">全部地块指标正常</div>';
  }

  // --- footer readouts ---
  dashNum($("#dash-light"), avgLight, 0);
  dashNum($("#dash-humidity"), avgHumidity, 1);
  dashNum($("#dash-ph"), avgPh, 2);

  // --- soil detail grid ---
  const soilGrid = $("#dash-soil-grid");
  if (soilGrid) {
    const cells = [
      ["土壤温度", dashAvg(soils.map((s) => Number(s.temperature_c))), "°C", 1],
      ["电导率", dashAvg(soils.map((s) => Number(s.conductivity_ms_cm))), "mS/cm", 2],
      ["氮", dashAvg(soils.map((s) => Number(s.nitrogen_mg_kg))), "mg/kg", 0],
      ["磷", dashAvg(soils.map((s) => Number(s.phosphorus_mg_kg))), "mg/kg", 0],
      ["钾", dashAvg(soils.map((s) => Number(s.potassium_mg_kg))), "mg/kg", 0],
      ["盐分", dashAvg(soils.map((s) => Number(s.salinity_g_l))), "g/L", 3],
    ];
    soilGrid.innerHTML = cells.map(([name, val, unit, digits]) => {
      const text = Number.isFinite(val) ? val.toFixed(digits) : "--";
      return `<div class="readout"><div class="name">${name}</div>
        <div class="num">${text}</div><div class="unit">${unit}</div></div>`;
    }).join("");
  }

  // --- recent telemetry log ---
  const log = $("#dash-telemetry-log");
  if (log) {
    const rows = [];
    devices.forEach((d) => {
      const history = Array.isArray(d.history) ? d.history : [];
      const last = history[history.length - 1];
      if (!last?.timestamp) return;
      const label = dashPlotName(d);
      rows.push({ ts: new Date(last.timestamp), label, kind: last.kind });
    });
    rows.sort((a, b) => b.ts - a.ts);
    log.innerHTML = rows.length
      ? rows.slice(0, 12).map((r) => `<div class="alert-item">
          <span class="time">${r.ts.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</span>
          <span class="text">${escapeHtml(r.label)} · ${r.kind === "soil" ? "土壤" : "气候"}遥测</span>
        </div>`).join("")
      : '<div class="empty">等待数据…</div>';
  }

  if (force && window.lucide) window.lucide.createIcons();
}

function updateDashboardClock() {
  const clock = $("#dashboard-clock");
  if (!clock) return;
  clock.textContent = new Date().toLocaleString("zh-CN", {
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit",
  });
}

// Canvas: pulsing telemetry rings driven by live moisture samples.
function startDashboardCanvas() {
  const canvas = $("#dashboard-canvas");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  let phase = 0;
  let width = 0;
  let height = 0;

  function resize() {
    const rect = canvas.parentElement.getBoundingClientRect();
    // Skip when the dashboard view is hidden (rect collapses to 0) — the
    // ResizeObserver will re-fire once the view becomes visible.
    if (!rect.width || !rect.height) return;
    const dpr = window.devicePixelRatio || 1;
    width = rect.width;
    height = rect.height;
    canvas.width = Math.max(1, Math.floor(width * dpr));
    canvas.height = Math.max(1, Math.floor(height * dpr));
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  // Hidden views report 0x0 at boot; observe the wrapper so the canvas
  // re-measures the moment the dashboard route becomes visible/resized.
  if (typeof ResizeObserver !== "undefined") {
    const ro = new ResizeObserver(() => resize());
    ro.observe(canvas.parentElement);
    window.addEventListener("resize", resize);
  } else {
    window.addEventListener("resize", resize);
    resize();
  }
  window.__dashResize = resize;

  function frame() {
    phase += 0.012;
    ctx.clearRect(0, 0, width, height);
    const cx = width / 2;
    const cy = height / 2;

    // concentric pulse rings
    for (let i = 0; i < 4; i += 1) {
      const t = (phase + i * 0.25) % 1;
      const radius = 40 + t * (Math.min(width, height) * 0.5);
      ctx.beginPath();
      ctx.arc(cx, cy, radius, 0, Math.PI * 2);
      ctx.strokeStyle = `rgba(34, 211, 238, ${(1 - t) * 0.32})`;
      ctx.lineWidth = 1.4;
      ctx.stroke();
    }

    // radial spokes
    ctx.strokeStyle = "rgba(14, 165, 233, 0.14)";
    ctx.lineWidth = 1;
    for (let a = 0; a < 12; a += 1) {
      const angle = (a / 12) * Math.PI * 2 + phase * 0.25;
      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.lineTo(cx + Math.cos(angle) * Math.min(width, height) * 0.46, cy + Math.sin(angle) * Math.min(width, height) * 0.46);
      ctx.stroke();
    }

    // data points: one per plot, distance reflects its moisture level
    const devices = state.allDevices || [];
    const points = devices.map((d, idx) => {
      const soil = latestPayload(d, "soil");
      const m = Number(soil.moisture_pct);
      const ratio = Number.isFinite(m) ? Math.min(1, Math.max(0, m / 100)) : 0.4;
      const angle = (idx / Math.max(1, devices.length)) * Math.PI * 2 - Math.PI / 2 + phase * 0.6;
      const radius = 55 + ratio * (Math.min(width, height) * 0.3);
      return { x: cx + Math.cos(angle) * radius, y: cy + Math.sin(angle) * radius, m };
    });

    if (points.length > 1) {
      ctx.beginPath();
      points.forEach((p, i) => (i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)));
      ctx.closePath();
      ctx.strokeStyle = "rgba(56, 189, 248, 0.4)";
      ctx.lineWidth = 1.4;
      ctx.stroke();
    }

    points.forEach((p) => {
      ctx.beginPath();
      ctx.arc(p.x, p.y, 4.5, 0, Math.PI * 2);
      ctx.fillStyle = "rgba(125, 211, 252, 0.95)";
      ctx.shadowColor = "rgba(56, 189, 248, 0.9)";
      ctx.shadowBlur = 12;
      ctx.fill();
      ctx.shadowBlur = 0;
    });

    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
}

if (window.lucide) window.lucide.createIcons();
bindBrokerActions();
bindUsersActions();
bindStewardActions();
bindReportsActions();
loadBrokerPresets();
loadBroker();
refreshUserPermissions();
populateCropSelect();
if (Auth.hasPermission("list_users")) loadUsers();
// Bind sensor-board actions up front, NOT only after the first successful
// refresh(): on slow links refresh() can fail for minutes and the click
// delegation never gets attached, leaving every sensor button dead.
bindSensorActions();
refresh();
refreshAiStatus();
refreshAlertLog();
probeHealthz();
setRoute(params.get("view") || "overview");
setInterval(refresh, 5000);
setInterval(probeHealthz, 10000);
setInterval(() => { if (state.device) refreshHistory(state.device.device_id); }, 60000);
setInterval(refreshPumpStatus, 1000);
setInterval(refreshAlerts, 5000);
setInterval(refreshAiStatus, 15000);
setInterval(refreshAlertLog, 30000);
setInterval(loadBroker, 30000);
setInterval(refreshUserPermissions, 60000);
// v15.4.0: big data screen — clock tick + canvas animation start
updateDashboardClock();
setInterval(updateDashboardClock, 1000);
startDashboardCanvas();
