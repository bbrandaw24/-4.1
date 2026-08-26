const roleGrid = document.getElementById("role-grid");
const form = document.getElementById("auth-form");
const guestEnter = document.getElementById("guest-enter");
const formTitle = document.getElementById("form-title");
const switchMode = document.getElementById("switch-mode");
const displayField = document.getElementById("display-field");
const usernameInput = document.getElementById("username");
const passwordInput = document.getElementById("password");
const displayInput = document.getElementById("display-name");
const errorEl = document.getElementById("form-error");
const submitBtn = document.getElementById("submit-btn");
const backBtn = document.getElementById("back-btn");

const ROLE_NAMES = { farmer: "农户", manager: "管理者", guest: "游客" };
let selectedRole = null;
let mode = "login"; // "login" | "register"

function showError(message) {
  errorEl.textContent = message;
  errorEl.hidden = !message;
}
function redirectAfterLogin(token, user) {
  Auth.setSession(token, user);
  const search = window.location.search || "";
  window.location.replace("index.html" + search);
}
function resetView() {
  roleGrid.hidden = false;
  form.hidden = true;
  guestEnter.hidden = true;
  showError("");
}
function selectRole(role) {
  selectedRole = role;
  roleGrid.hidden = true;
  showError("");
  if (role === "guest") {
    form.hidden = true;
    guestEnter.hidden = false;
    return;
  }
  form.hidden = false;
  guestEnter.hidden = true;
  applyMode();
}
function applyMode() {
  const name = ROLE_NAMES[selectedRole];
  formTitle.textContent = `${name}${mode === "register" ? "注册" : "登录"}`;
  submitBtn.textContent = mode === "register" ? "注册并登录" : "登录";
  switchMode.textContent = mode === "register" ? "已有账号？登录" : "没有账号？注册";
  displayField.hidden = mode !== "register";
  if (mode === "register") displayInput.value = "";
  usernameInput.value = "";
  passwordInput.value = "";
}

roleGrid.querySelectorAll(".role-card").forEach((card) => {
  card.addEventListener("click", () => selectRole(card.dataset.role));
});
switchMode.addEventListener("click", () => { mode = mode === "login" ? "register" : "login"; applyMode(); });
backBtn.addEventListener("click", resetView);

guestEnter.addEventListener("click", async () => {
  try {
    const res = await fetch(`${Auth.apiBase()}/api/v1/auth/guest`, { method: "POST" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    redirectAfterLogin(data.token, data.user);
  } catch (err) {
    showError(`游客登录失败：${err.message}`);
  }
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const username = usernameInput.value.trim();
  const password = passwordInput.value;
  if (!username || !password) { showError("请输入账号和密码"); return; }
  if (mode === "register" && password.length < 6) { showError("密码至少 6 位"); return; }
  submitBtn.disabled = true;
  try {
    const path = mode === "register" ? "/api/v1/auth/register" : "/api/v1/auth/login";
    const body = mode === "register"
      ? { username, password, role: selectedRole, display_name: displayInput.value.trim() || undefined }
      : { username, password };
    const res = await fetch(`${Auth.apiBase()}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    redirectAfterLogin(data.token, data.user);
  } catch (err) {
    const messages = {
      username_invalid: "账号格式不合法（3–32 位字母数字或下划线）",
      password_too_short: "密码至少 6 位",
      role_not_allowed: "该身份不可注册",
      username_taken: "账号已被注册",
      invalid_credentials: "账号或密码错误",
    };
    showError(messages[err.message] || `操作失败：${err.message}`);
    submitBtn.disabled = false;
  }
});

if (window.lucide) window.lucide.createIcons();
resetView();
