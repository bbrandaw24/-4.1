/* Day 16: Luna kitten mascot state machine.
 * Drives #luna-kitten[data-state] and [data-mode]; agent.js calls
 * window.LunaKitten.thinking()/talking()/error()/idle()/setMode().
 */
(function () {
  const root = document.getElementById("luna-kitten");
  if (!root) return;
  const statusEl = root.querySelector(".kitten-status");
  const LABELS = {
    idle: { kb: "知识库待命", luna: "待命中" },
    thinking: { kb: "检索中…", luna: "思考中…" },
    talking: { kb: "回答中…", luna: "回答中…" },
    error: "卡住啦…",
  };
  let timer = null;

  function stateLabel(state, mode) {
    const value = LABELS[state];
    if (!value) return "";
    if (typeof value === "string") return value;
    return value[mode] || value.luna || "";
  }

  function paint() {
    const state = root.dataset.state || "idle";
    const mode = root.dataset.mode || "kb";
    if (statusEl) statusEl.textContent = stateLabel(state, mode);
  }

  const api = {
    setMode(mode) {
      root.dataset.mode = mode === "luna" ? "luna" : "kb";
      paint();
    },
    idle() {
      clearTimeout(timer);
      root.dataset.state = "idle";
      paint();
    },
    thinking() {
      clearTimeout(timer);
      root.dataset.state = "thinking";
      paint();
    },
    talking(ms) {
      clearTimeout(timer);
      root.dataset.state = "talking";
      paint();
      timer = setTimeout(() => api.idle(), ms || 5000);
    },
    error() {
      clearTimeout(timer);
      root.dataset.state = "error";
      paint();
      timer = setTimeout(() => api.idle(), 6500);
    },
  };

  window.LunaKitten = api;
  paint();
})();
