const params = new URLSearchParams(window.location.search);
if (!Auth.getToken()) Auth.redirectToLogin();
const API = Auth.apiBase();
const AI_API = Auth.aiBase();
const DEVICE_ID = params.get("device") || null; // resolved to first available device on first refresh
const HISTORY_LIMIT = 7200;
const state = { device: null, moisture: [], samples: [], aiReady: false, pump: null, mode: "manual", notifications: false, lastAlertSignature: "", rule: null, user: Auth.getUser() };
document.querySelector("#api-url").textContent = API;
const $ = (selector) => document.querySelector(selector);
applyRole();

const fmt = (value, digits = 1, suffix = "") => value === undefined || value === null ? "--" : `${Number(value).toFixed(digits)}${suffix}`;

function setConnection(ok, message) {
  $("#connection-dot").classList.toggle("off", !ok);
  $("#connection-label").textContent = message;
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
  const nextRoute = ["overview", "trends", "devices"].includes(route) ? route : "overview";
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
  $("#device-page-name").textContent = device.device_id;
  $("#device-page-id").textContent = device.device_id;
  $("#device-page-seen").textContent = new Date(device.last_seen).toLocaleString();
  $("#device-page-api").textContent = API;
  $("#device-page-status").textContent = "ONLINE";
  $("#device-page-status").classList.remove("muted");
  $("#mqtt-contract").textContent = "在线";
  $("#http-contract").textContent = "在线";
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
    if (!data.items?.length) throw new Error("暂无设备");
    renderDevice(data.items.find((item) => item.device_id === DEVICE_ID) || data.items[0]);
    setConnection(true, "API 已连接");
    $("#last-update").textContent = new Date().toLocaleTimeString();
  } catch (error) {
    setConnection(false, "API 暂不可用");
    $("#device-status").textContent = error.message;
  }
}

async function refreshAiStatus() {
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
    $("#device-page-ai").textContent = data.ready ? data.model_version : "待训练";
    $("#ai-contract").textContent = data.ready ? "在线" : "未就绪";
  } catch (error) {
    $("#ai-status-copy").textContent = `AI 服务暂不可用：${error.message}`;
    $("#ai-status-badge").textContent = "AI OFFLINE";
    $("#ai-status-badge").classList.add("muted");
    $("#device-page-ai").textContent = "不可用";
    $("#ai-contract").textContent = "离线";
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

async function upload(file) {
  if (!file) return;
  if (!Auth.hasPermission("upload_image")) { $("#image-result").textContent = "当前身份无图像上传权限"; return; }
  const form = new FormData();
  form.append("file", file);
  if (state.device) form.append("device_id", state.device.device_id);
  $("#image-result").textContent = "正在上传...";
  try {
    const response = await Auth.request(`/api/v1/images`, { method: "POST", body: form });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    $("#image-result").innerHTML = `<img src="${API}${data.thumbnail_url}" alt="最新作物图像"><p>${data.width} × ${data.height} · 已生成缩略图</p>`;
  } catch (error) { $("#image-result").textContent = `上传失败：${error.message}`; }
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
  const logout = $("#logout-button");
  if (logout && !logout.dataset.bound) {
    logout.dataset.bound = "1";
    logout.addEventListener("click", () => { Auth.logout(); });
  }
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
$("#image-input").addEventListener("change", (event) => upload(event.target.files[0]));
$("#dropzone").addEventListener("dragover", (event) => event.preventDefault());
$("#dropzone").addEventListener("drop", (event) => { event.preventDefault(); upload(event.dataTransfer.files[0]); });
if (window.lucide) window.lucide.createIcons();
refresh();
refreshAiStatus();
setRoute(params.get("view") || "overview");
setInterval(refresh, 5000);
setInterval(() => { if (state.device) refreshHistory(state.device.device_id); }, 60000);
setInterval(refreshPumpStatus, 1000);
setInterval(refreshAlerts, 5000);
setInterval(refreshAiStatus, 15000);
