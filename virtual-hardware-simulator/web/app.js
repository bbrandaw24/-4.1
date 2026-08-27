const $ = (id) => document.getElementById(id);
let config = null;
let refreshTimer = null;

const sensorNames = {
  soil_temperature: "土壤温度", soil_moisture: "土壤湿度", soil_ph: "土壤 pH",
  air_humidity: "空气湿度", air_temperature: "空气温度", light: "光照", co2: "CO₂",
  soil_npk: "氮/磷/钾", soil_conductivity: "电导率"
};

// 平台 API 模式支持的传感器类型（对齐智慧农业 SENSOR_TYPES）
const platformSensorTypes = {
  soil_temperature: "土壤温度",
  soil_ph: "土壤 pH",
  soil_npk: "氮/磷/钾",
  air_humidity: "空气湿度",
  soil_conductivity: "电导率"
};

function showError(message) {
  $("error").textContent = message;
  $("error").classList.remove("hidden");
}
function clearError() { $("error").classList.add("hidden"); }
async function request(url, options = {}) {
  const response = await fetch(url, {headers: {"Content-Type": "application/json"}, ...options});
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
  return body;
}
function apiMode() { return Boolean(config && config.api && config.api.base_url); }
function setForm(mqtt) {
  $("mqtt-host").value = mqtt.host ?? "localhost";
  $("mqtt-port").value = mqtt.port ?? 1883;
  $("mqtt-username").value = mqtt.username ?? "";
  $("mqtt-password").value = "";
  $("mqtt-prefix").value = mqtt.topic_prefix ?? "farm";
  $("mqtt-qos").value = mqtt.qos ?? 1;
  $("mqtt-retain").checked = Boolean(mqtt.retain);
}
function setApiForm(api) {
  if (!api) return;
  $("api-base").value = api.base_url ?? "http://127.0.0.1:8010";
  $("api-username").value = api.username ?? "admin";
  $("api-password").value = api.password ?? "";
  $("api-sync").value = api.sync_seconds ?? 30;
  $("api-enabled").checked = api.base_url ? true : false;
}
function formConfig() {
  const apiBase = $("api-base").value.trim();
  const payload = {
    mqtt: {host: $("mqtt-host").value.trim(), port: Number($("mqtt-port").value), username: $("mqtt-username").value, password: $("mqtt-password").value, topic_prefix: $("mqtt-prefix").value.trim(), qos: Number($("mqtt-qos").value), retain: $("mqtt-retain").checked}
  };
  if (apiBase) {
    payload.api = {
      base_url: apiBase,
      username: $("api-username").value.trim(),
      password: $("api-password").value,
      sync_seconds: Number($("api-sync").value) || 30
    };
  }
  if (!apiMode()) payload.devices = config.devices;
  return payload;
}
function formatSensorValue(sensor) {
  // 平台模式：优先展示平台返回的当前值（value_json）
  if (apiMode()) {
    const value = sensor.value;
    if (value !== null && value !== undefined && typeof value === "object") {
      const keys = Object.keys(value);
      if (keys.length) return `${Number(value[keys[0]]).toFixed(1)} <small>${sensor.unit || ""}</small>`;
    }
    return "—";
  }
  const baseline = sensor.baseline ?? "—";
  if (sensor.type === "npk") return `<span class="npk-value">${Number(baseline).toFixed(0)}<small> N</small> / ${Number(baseline * .42).toFixed(0)}<small> P</small> / ${Number(baseline * 1.45).toFixed(0)}<small> K</small></span>`;
  if (sensor.type === "pump_state") return Number(baseline) ? "运行" : "停止";
  return `${Number(baseline).toLocaleString(undefined, {maximumFractionDigits: 2})} <small>${sensor.unit || ""}</small>`;
}
function sensorStatusBadge(sensor) {
  if (!apiMode()) return "";
  const connected = sensor.status !== "disconnected";
  return `<span class="sensor-status ${connected ? "on" : "off"}">${connected ? "已连接" : "已断开"}</span>`;
}
function renderDevices(devices) {
  $("device-count").textContent = devices.length;
  $("devices").innerHTML = devices.map(device => {
    const sensors = device.sensors || [];
    const cards = sensors.map(sensor => {
      const toggle = apiMode()
        ? `<button class="text-button sensor-toggle" data-sensor="${escapeHtml(sensor.id)}" data-status="${sensor.status === "disconnected" ? "connected" : "disconnected"}">${sensor.status === "disconnected" ? "连接" : "断开"}</button>`
        : "";
      return `<div class="sensor">
        <button class="sensor-remove" title="删除传感器" data-device="${escapeHtml(device.id)}" data-sensor="${escapeHtml(sensor.id)}">×</button>
        <div class="sensor-type"><i class="sensor-dot"></i>${escapeHtml(sensorNames[sensor.type] || sensor.type)}${sensorStatusBadge(sensor)}</div>
        <div class="sensor-value">${formatSensorValue(sensor)}</div>
        <div class="sensor-unit">${apiMode() ? `ID ${escapeHtml(sensor.id.slice(0, 8))}…` : `基准值 · 每 ${sensor.interval}s 上报`} ${toggle}</div>
      </div>`;
    }).join("");
    return `<article class="device-card">
      <div class="device-title"><div><strong>${escapeHtml(device.name || device.id)}</strong><div class="device-id">${escapeHtml(device.id)}</div>${apiMode() && device.crop ? `<div class="device-id">作物：${escapeHtml(device.crop)}</div>` : ""}</div>
      <div class="device-tools"><span class="eyebrow">${sensors.length} SENSORS</span><button class="text-button delete-device" data-device="${escapeHtml(device.id)}">删除设备</button></div></div>
      <div class="sensor-grid">${cards}<button class="sensor add-sensor" data-device="${escapeHtml(device.id)}">+ 添加传感器</button></div>
    </article>`;
  }).join("");
  document.querySelectorAll(".delete-device").forEach(button => button.addEventListener("click", () => deleteDevice(button.dataset.device)));
  document.querySelectorAll(".sensor-remove").forEach(button => button.addEventListener("click", () => deleteSensor(button.dataset.device, button.dataset.sensor)));
  document.querySelectorAll(".add-sensor").forEach(button => button.addEventListener("click", () => openSensorEditor(button.dataset.device)));
  document.querySelectorAll(".sensor-toggle").forEach(button => button.addEventListener("click", () => toggleSensor(button.dataset.sensor, button.dataset.status)));
}
function escapeHtml(value) { return String(value).replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[ch])); }
function setStatus(status) {
  const pill = document.querySelector(".status-pill");
  pill.classList.toggle("running", status.running);
  $("status-text").textContent = status.running ? `运行中 · PID ${status.pid}` : "未运行";
  if (status.logs?.length) $("logs").textContent = status.logs.join("\n");
}
function renderModeBanner() {
  const mode = apiMode();
  $("mode-badge").textContent = mode ? "数据源：智慧农业平台 API" : "数据源：独立本地配置";
  $("mode-badge").classList.toggle("api", mode);
  $("api-sync-hint").textContent = mode ? "增删设备/传感器将直接写入平台，模拟器每 " + (config.api.sync_seconds ?? 30) + " 秒自动同步平台设备。" : "填写平台 API 地址后，设备将由平台统一管理，两个页面双向同步。";
  $("device-form-name-label").textContent = mode ? "地块名称" : "设备 ID";
  $("device-form-crop-label").classList.toggle("hidden", !mode);
  // 传感器编辑器：平台模式用类型下拉，本地模式自由输入
  $("sensor-type-wrap").classList.toggle("hidden", !mode);
  $("sensor-type-free").classList.toggle("hidden", mode);
}
async function load() {
  try {
    config = await request("/api/config");
    setForm(config.mqtt);
    setApiForm(config.api);
    renderModeBanner();
    renderDevices(config.devices);
    setStatus(await request("/api/status"));
    refreshPlatformStatus();
    clearError();
  } catch (error) { showError(error.message); }
}
async function refreshPlatformStatus() {
  if (!apiMode()) return;
  try {
    const info = await request("/api/devices");
    $("platform-status").textContent = info.api_ok
      ? `平台已连接 · ${info.devices.length} 个自定义地块`
      : `平台连接异常：${info.api_error || "未知错误"}`;
    $("platform-status").classList.toggle("ok", info.api_ok);
    if (info.devices.length && JSON.stringify(info.devices) !== JSON.stringify(config.devices)) {
      config.devices = info.devices;
      renderDevices(config.devices);
    }
  } catch (_) {}
}
async function save() {
  try {
    config = await request("/api/config", {method: "POST", body: JSON.stringify(formConfig())});
    setForm(config.mqtt);
    setApiForm(config.api);
    renderModeBanner();
    renderDevices(config.devices);
    addLog("[控制台] 配置已保存");
    clearError();
  } catch (error) { showError(error.message); }
}
function addLog(text) { $("logs").textContent = `${$("logs").textContent === "等待操作…" ? "" : `${$("logs").textContent}\n`}${text}`; }
async function deleteDevice(deviceId) {
  if (!confirm(`确定删除设备 ${deviceId}？`)) return;
  try {
    config = await request("/api/device/delete", {method: "POST", body: JSON.stringify({device_id: deviceId})});
    renderDevices(config.devices);
    addLog(`[控制台] 已删除设备 ${deviceId}`);
    clearError();
  } catch (error) { showError(error.message); }
}
async function deleteSensor(deviceId, sensorId) {
  if (!confirm(`确定删除传感器 ${sensorId.slice(0, 8)}…？`)) return;
  try {
    const body = apiMode() ? {sensor_id: sensorId} : {device_id: deviceId, sensor_id: sensorId};
    config = await request("/api/sensor/delete", {method: "POST", body: JSON.stringify(body)});
    renderDevices(config.devices);
    addLog(`[控制台] 已删除传感器 ${sensorId.slice(0, 8)}…`);
    clearError();
  } catch (error) { showError(error.message); }
}
async function toggleSensor(sensorId, status) {
  try {
    config = await request("/api/sensor/status", {method: "POST", body: JSON.stringify({sensor_id: sensorId, status})});
    renderDevices(config.devices);
    addLog(`[控制台] 传感器 ${sensorId.slice(0, 8)}… ${status === "connected" ? "已连接" : "已断开"}`);
    clearError();
  } catch (error) { showError(error.message); }
}
function openSensorEditor(deviceId) {
  $("device-editor").classList.remove("hidden");
  $("device-editor").dataset.device = deviceId;
  if (apiMode()) $("new-sensor-type").focus();
  else $("new-sensor-id").focus();
}
$("cancel-sensor").addEventListener("click", () => $("device-editor").classList.add("hidden"));
$("confirm-sensor").addEventListener("click", async () => {
  const deviceId = $("device-editor").dataset.device;
  if (apiMode()) {
    const type = $("new-sensor-type").value;
    if (!type) return showError("请选择传感器类型");
    try {
      config = await request("/api/sensor/add", {method: "POST", body: JSON.stringify({device_id: deviceId, type})});
      renderDevices(config.devices);
      $("device-editor").classList.add("hidden");
      addLog(`[控制台] 已为 ${deviceId} 添加 ${platformSensorTypes[type] || type}`);
      clearError();
    } catch (error) { showError(error.message); }
    return;
  }
  const id = $("new-sensor-id").value.trim();
  const type = $("new-sensor-type-free").value.trim();
  if (!id || !type) return showError("传感器 ID 和类型不能为空");
  try {
    config = await request("/api/sensor/add", {method: "POST", body: JSON.stringify({device_id: deviceId, sensor: {id, type, unit: $("new-sensor-unit").value.trim(), baseline: Number($("new-sensor-baseline").value || 0), min: Number($("new-sensor-min").value || -100), max: Number($("new-sensor-max").value || 100), drift: 1, interval: 5, fields: ["value"]}})});
    renderDevices(config.devices);
    $("device-editor").classList.add("hidden");
    addLog(`[控制台] 已添加传感器 ${id}`);
    clearError();
  } catch (error) { showError(error.message); }
});
async function control(endpoint) {
  try {
    const status = await request(endpoint, {method: "POST", body: "{}"});
    setStatus(status);
    clearError();
  } catch (error) { showError(error.message); }
}
async function preview() {
  try {
    const result = await request("/api/preview", {method: "POST", body: "{}"});
    $("message-count").textContent = result.lines.length;
    $("logs").textContent = result.lines.join("\n");
    clearError();
  } catch (error) { showError(error.message); }
}
$("add-device").addEventListener("click", () => {
  if (!config) return;
  $("device-add-form").classList.toggle("hidden");
});
$("confirm-add-device").addEventListener("click", async () => {
  if (!config) return;
  const name = $("new-device-name").value.trim();
  if (apiMode()) {
    const crop = $("new-device-crop").value.trim();
    if (!name && !crop) return showError("请填写地块名称或作物");
    try {
      config = await request("/api/device/add", {method: "POST", body: JSON.stringify({name, crop})});
      renderDevices(config.devices);
      $("device-add-form").classList.add("hidden");
      $("new-device-name").value = ""; $("new-device-crop").value = "";
      addLog(`[控制台] 已通过平台创建设备`);
      clearError();
    } catch (error) { showError(error.message); }
    return;
  }
  if (!name) return showError("请填写设备 ID");
  try {
    config = await request("/api/device/add", {method: "POST", body: JSON.stringify({name})});
    renderDevices(config.devices);
    $("device-add-form").classList.add("hidden");
    $("new-device-name").value = "";
    addLog(`[控制台] 已添加设备 ${name}`);
    clearError();
  } catch (error) { showError(error.message); }
});
$("save-config").addEventListener("click", save);
$("start").addEventListener("click", () => control("/api/start"));
$("stop").addEventListener("click", () => control("/api/stop"));
$("preview").addEventListener("click", preview);
$("clear-log").addEventListener("click", () => $("logs").textContent = "等待操作…");
load();
refreshTimer = setInterval(async () => {
  try { setStatus(await request("/api/status")); } catch (_) {}
  try { await refreshPlatformStatus(); } catch (_) {}
}, 3000);
