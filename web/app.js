const params = new URLSearchParams(window.location.search);
const API = params.get("api") || "http://192.168.128.129:8000";
const AI_API = params.get("ai") || "http://192.168.128.129:8001";
const state = { device: null, moisture: [] };
document.querySelector("#api-url").textContent = API;

const $ = (selector) => document.querySelector(selector);
const fmt = (value, digits = 1, suffix = "") => value === undefined || value === null ? "--" : `${Number(value).toFixed(digits)}${suffix}`;

function setConnection(ok, message) {
  $("#connection-dot").classList.toggle("off", !ok);
  $("#connection-label").textContent = message;
}

function setMeter(id, value) { $(id).style.width = `${Math.max(0, Math.min(100, value))}%`; }

function drawTrend() {
  const canvas = $("#trend-chart");
  const context = canvas.getContext("2d");
  const width = canvas.width;
  const height = canvas.height;
  context.clearRect(0, 0, width, height);
  if (state.moisture.length < 2) return;
  const min = Math.min(...state.moisture) - 2;
  const max = Math.max(...state.moisture) + 2;
  context.strokeStyle = "#226b46";
  context.lineWidth = 3;
  context.lineJoin = "round";
  context.beginPath();
  state.moisture.forEach((value, index) => {
    const x = (index / (state.moisture.length - 1)) * width;
    const y = height - ((value - min) / (max - min)) * (height - 20) - 10;
    index ? context.lineTo(x, y) : context.moveTo(x, y);
  });
  context.stroke();
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
  if (state.moisture.length > 18) state.moisture.shift();
  const rows = [
    ["土壤温度", fmt(soil.temperature_c, 1, " °C"), "18–28 °C"],
    ["pH", fmt(soil.ph, 2), "5.8–6.8"],
    ["氮 / 磷 / 钾", `${fmt(soil.nitrogen_mg_kg, 0)} / ${fmt(soil.phosphorus_mg_kg, 0)} / ${fmt(soil.potassium_mg_kg, 0)}`, "mg/kg"],
    ["空气湿度", fmt(climate.air_humidity_pct, 1, " %"), "45–90 %"],
    ["电导率", fmt(soil.conductivity_ms_cm, 2, " mS/cm"), "0.4–1.8"],
  ];
  $("#telemetry-table").innerHTML = rows.map(([name, value, range]) => `<div class="telemetry-row"><span class="name">${name}</span><span class="value">${value}</span><span class="range">${range}</span></div>`).join("");
  drawTrend();
}

async function refresh() {
  try {
    const response = await fetch(`${API}/api/v1/devices`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    if (!data.items?.length) throw new Error("暂无设备");
    renderDevice(data.items[0]);
    setConnection(true, "API 已连接");
    $("#last-update").textContent = new Date().toLocaleTimeString();
  } catch (error) {
    setConnection(false, "API 暂不可用");
    $("#device-status").textContent = error.message;
  }
}

async function refreshAiStatus() {
  try {
    const response = await fetch(`${AI_API}/api/v1/model/status`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    const classes = (data.classes || []).join(" / ");
    $("#ai-status-copy").textContent = data.ready
      ? `模型 ${data.model_version} 已就绪 · 类别 ${classes}`
      : `${data.message || "模型尚未就绪"} · 阈值 ${Number(data.confidence_threshold || 0.6).toFixed(2)}`;
    $("#ai-status-badge").textContent = data.ready ? "MODEL READY" : "MODEL PENDING";
    $("#ai-status-badge").classList.toggle("muted", !data.ready);
  } catch (error) {
    $("#ai-status-copy").textContent = `AI 服务暂不可用：${error.message}`;
    $("#ai-status-badge").textContent = "AI OFFLINE";
    $("#ai-status-badge").classList.add("muted");
  }
}

async function pump(action) {
  if (!state.device) return;
  const buttons = [$("#pump-start"), $("#pump-stop")];
  buttons.forEach((button) => { button.disabled = true; });
  try {
    const response = await fetch(`${API}/api/v1/devices/${encodeURIComponent(state.device.device_id)}/pump`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action }) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    $("#pump-label").textContent = action === "start" ? "运行中" : "待机";
    $("#pump-light").classList.toggle("running", action === "start");
    $("#action-result").textContent = `已发布 ${action === "start" ? "启动" : "停止"} 指令 · ${new Date().toLocaleTimeString()}`;
  } catch (error) { $("#action-result").textContent = `指令失败：${error.message}`; }
  buttons.forEach((button) => { button.disabled = false; });
}

async function upload(file) {
  if (!file) return;
  const form = new FormData();
  form.append("file", file);
  if (state.device) form.append("device_id", state.device.device_id);
  $("#image-result").textContent = "正在上传...";
  try {
    const response = await fetch(`${API}/api/v1/images`, { method: "POST", body: form });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    $("#image-result").innerHTML = `<img src="${API}${data.thumbnail_url}" alt="最新作物图像"><p>${data.width} × ${data.height} · 已生成缩略图</p>`;
  } catch (error) { $("#image-result").textContent = `上传失败：${error.message}`; }
}

$("#refresh-button").addEventListener("click", refresh);
$("#pump-start").addEventListener("click", () => pump("start"));
$("#pump-stop").addEventListener("click", () => pump("stop"));
$("#image-input").addEventListener("change", (event) => upload(event.target.files[0]));
$("#dropzone").addEventListener("dragover", (event) => event.preventDefault());
$("#dropzone").addEventListener("drop", (event) => { event.preventDefault(); upload(event.dataTransfer.files[0]); });
if (window.lucide) window.lucide.createIcons();
refresh();
refreshAiStatus();
setInterval(refresh, 5000);
setInterval(refreshAiStatus, 15000);
