/* Day 13: irrigation advisor chat. Talks to /api/v1/agent/ask which is backed by the
 * cloud knowledge base + live telemetry (RAG + rules synthesizer; pluggable LLM
 * via env vars on the server). All authenticated roles can ask. */
(function () {
  const params = new URLSearchParams(window.location.search);
  const API = typeof Auth !== "undefined" ? Auth.apiBase() : "";
  const messagesEl = document.getElementById("agent-messages");
  const formEl = document.getElementById("agent-form");
  const inputEl = document.getElementById("agent-input");
  const sendBtn = document.getElementById("agent-send");
  const contextEl = document.getElementById("agent-context");
  const metaEl = document.getElementById("agent-meta");
  const modeBadge = document.getElementById("agent-mode-badge");
  const modeHintEl = document.getElementById("agent-mode-hint");
  const modeBtns = document.querySelectorAll(".agent-mode-btn");
  const history = [];
  const MAX_HISTORY = 6;
  let sending = false;
  let mode = "kb"; // "kb" = knowledge base | "luna" = Luna model (farmer/manager only)

  if (!messagesEl || !formEl || !inputEl) return;

  function isPrivileged() {
    const user = typeof Auth !== "undefined" ? Auth.getUser() : null;
    return !!user && user.role !== "guest";
  }

  function applyModeUI() {
    modeBtns.forEach((button) => button.classList.toggle("active", button.dataset.mode === mode));
    if (mode === "luna") {
      modeBadge.textContent = "LUNA";
      modeBadge.classList.remove("muted");
      if (modeHintEl) modeHintEl.textContent = "Luna 模式 · 思考强度中等（固定）";
    } else {
      modeBadge.textContent = "RAG";
      modeBadge.classList.remove("muted");
      if (modeHintEl) modeHintEl.textContent = "";
    }
    // Guests can only use the knowledge-base mode.
    if (!isPrivileged()) {
      modeBtns.forEach((button) => {
        button.disabled = button.dataset.mode === "luna";
      });
      if (modeHintEl) modeHintEl.textContent = "游客仅支持知识库问答";
      if (mode === "luna") mode = "kb";
    } else {
      modeBtns.forEach((button) => { button.disabled = false; });
    }
  }

  function scrollToBottom() {
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function updateSendState() {
    const ready = !sending && inputEl.value.trim().length > 0 && (typeof Auth === "undefined" || !!Auth.getToken());
    sendBtn.disabled = !ready;
  }

  function renderText(text) {
    return (text || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/\n/g, "<br>");
  }

  function appendMessage(role, html) {
    const wrapper = document.createElement("div");
    wrapper.className = `agent-message ${role}`;
    const bubble = document.createElement("div");
    bubble.className = "agent-bubble";
    bubble.innerHTML = html;
    wrapper.appendChild(bubble);
    messagesEl.appendChild(wrapper);
    scrollToBottom();
    return wrapper;
  }

  function appendSources(sources) {
    if (!sources || sources.length === 0) return;
    const wrapper = document.createElement("div");
    wrapper.className = "agent-sources";
    const head = document.createElement("strong");
    head.textContent = "知识库引用";
    wrapper.appendChild(head);
    const list = document.createElement("ul");
    sources.forEach((source) => {
      const li = document.createElement("li");
      li.innerHTML = `<span class="agent-source-topic">${renderText(source.topic || "")}</span> ${renderText(source.title || "")} <small>score ${source.score ?? 0}</small>`;
      list.appendChild(li);
    });
    wrapper.appendChild(list);
    messagesEl.appendChild(wrapper);
    scrollToBottom();
  }

  function renderContext() {
    if (typeof window === "undefined" || !window.state || !window.state.device) {
      contextEl.textContent = "暂无设备数据，请先在概览页确认 MQTT 在线。";
      return;
    }
    const device = window.state.device;
    const soil = (device.telemetry && device.telemetry.soil && device.telemetry.soil.payload) || {};
    const climate = (device.telemetry && device.telemetry.climate && device.telemetry.climate.payload) || {};
    const moisture = typeof soil.moisture_pct === "number" ? `${soil.moisture_pct.toFixed(1)}%` : "--";
    const temp = typeof climate.air_temperature_c === "number" ? `${climate.air_temperature_c.toFixed(1)}°C` : "--";
    contextEl.innerHTML = `设备 <code>${renderText(device.device_id)}</code> · 土壤湿度 <b>${moisture}</b> · 空气温度 <b>${temp}</b> · <span class="agent-context-hint">回答会引用上述实时数据</span>`;
  }

  async function send(question) {
    const text = (question || "").trim();
    if (!text || sending) return;
    if (typeof Auth === "undefined" || !Auth.getToken()) {
      appendMessage("agent", "请先登录后再提问。");
      return;
    }
    sending = true;
    updateSendState();
    appendMessage("user", renderText(text));
    history.push({ question: text, answer: "" });
    if (history.length > MAX_HISTORY) history.splice(0, history.length - MAX_HISTORY);
    const pending = appendMessage("agent", `<span class="agent-typing">${mode === "luna" ? "Luna 思考中（中等强度）…" : "正在检索知识库与遥测…"}</span>`);
    try {
      const response = await Auth.request("/api/v1/agent/ask", {
        method: "POST",
        body: JSON.stringify({
          question: text,
          mode,
          history: history.slice(0, -1).map((h) => ({ question: h.question })),
          device_id: window.state && window.state.device ? window.state.device.device_id : undefined,
        }),
      });
      const data = await response.json();
      pending.remove();
      if (!response.ok) {
        const message = (data && (data.error || data.message)) || `HTTP ${response.status}`;
        appendMessage("agent", `请求失败：${renderText(String(message))}`);
        if (response.status === 403 && mode === "luna") {
          mode = "kb";
          applyModeUI();
        }
        history[history.length - 1].answer = `(error) ${message}`;
        metaEl.textContent = `失败：${message}`;
        return;
      }
      appendMessage("agent", renderText(data.answer || "暂无回答。"));
      appendSources(data.sources);
      if (mode === "luna" && data.answer_via !== "luna") {
        appendMessage("agent", "（Luna 模型暂时无响应，已自动改用知识库回答）");
      }
      history[history.length - 1].answer = data.answer || "";
      const via = data.answer_via || "synthesizer";
      if (modeBadge) modeBadge.textContent = via === "luna" ? "LUNA" : "RAG";
      if (metaEl) {
        const m = data.context || {};
        const moist = typeof m.moisture_pct === "number" ? `${m.moisture_pct.toFixed(1)}%` : "--";
        const temp = typeof m.air_temperature_c === "number" ? `${m.air_temperature_c.toFixed(1)}°C` : "--";
        const engine = via === "luna" ? "Luna" : "知识库合成";
        metaEl.textContent = `已回答 · 引用 ${data.retrieved_count || 0} 条 · 引擎 ${engine} · 当前湿度 ${moist} · 温度 ${temp}`;
      }
    } catch (error) {
      pending.remove();
      appendMessage("agent", `网络错误：${renderText(String(error.message || error))}`);
      history[history.length - 1].answer = `(error) ${error}`;
    } finally {
      sending = false;
      updateSendState();
    }
  }

  formEl.addEventListener("submit", (event) => {
    event.preventDefault();
    const text = inputEl.value;
    inputEl.value = "";
    updateSendState();
    send(text);
  });

  inputEl.addEventListener("input", updateSendState);
  inputEl.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      formEl.requestSubmit();
    }
  });

  document.querySelectorAll(".agent-suggest").forEach((button) => {
    button.addEventListener("click", () => send(button.dataset.q));
  });

  modeBtns.forEach((button) => {
    button.addEventListener("click", () => {
      const next = button.dataset.mode;
      if (next === "luna" && !isPrivileged()) return; // guests locked to kb
      if (mode === next) return;
      mode = next;
      applyModeUI();
      if (metaEl) metaEl.textContent = mode === "luna" ? "已切换 Luna 模式 · 思考强度中等（固定）" : "已切换知识库问答";
    });
  });

  // Re-render context whenever the device changes (state.device updated by app.js).
  setInterval(renderContext, 5000);
  renderContext();
  applyModeUI();
  updateSendState();
})();
