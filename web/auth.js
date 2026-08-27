/* Shared auth helpers: token storage, API base resolution, authed fetch. */
// GitHub Pages is HTTPS, while the current cloud demo gateway is HTTP. A
// browser blocks HTTPS -> HTTP API requests as mixed content. Redirect the
// complete app to the same-origin cloud gateway before loading the dashboard.
const CLOUD_GATEWAY = "http://43.156.230.129:8080";
const ON_GITHUB_PAGES = window.location.hostname === "bbrandaw24.github.io";
if (ON_GITHUB_PAGES) {
  const page = window.location.pathname.toLowerCase().includes("login") ? "/login.html" : "/index.html";
  window.location.replace(`${CLOUD_GATEWAY}${page}${window.location.search || ""}`);
}

const Auth = (() => {
  const TOKEN_KEY = "smartagri_token";
  const USER_KEY = "smartagri_user";

  function params() { return new URLSearchParams(window.location.search); }
  function isGateway() {
    return window.location.protocol.startsWith("http") && window.location.hostname !== "bbrandaw24.github.io";
  }
  function apiBase() {
    return params().get("api") || (isGateway() ? window.location.origin : "http://43.156.230.129:8010");
  }
  function aiBase() {
    return params().get("ai") || (isGateway() ? window.location.origin : "http://43.156.230.129:8001");
  }

  function getToken() { return localStorage.getItem(TOKEN_KEY); }
  function getUser() {
    try { return JSON.parse(localStorage.getItem(USER_KEY)); } catch (_) { return null; }
  }
  function setSession(token, user) {
    localStorage.setItem(TOKEN_KEY, token);
    localStorage.setItem(USER_KEY, JSON.stringify(user));
  }
  function clear() {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
  }
  async function logout() {
    // 1. Clear app session state (token + user)
    clear();
    // 2. Clear session storage
    try { sessionStorage.clear(); } catch (_) { /* ignore */ }
    // 3. Clear Cache Storage API caches (service-worker / Cache API entries)
    try {
      if ("caches" in window) {
        const keys = await caches.keys();
        await Promise.all(keys.map((key) => caches.delete(key)));
      }
    } catch (_) { /* ignore */ }
    // 4. Clear cookies for this origin
    try {
      document.cookie.split(";").forEach((cookie) => {
        const name = cookie.split("=")[0].trim();
        if (name) document.cookie = `${name}=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/`;
      });
    } catch (_) { /* ignore */ }
    // 5. Back to the login page
    redirectToLogin();
  }
  function hasPermission(permission) {
    const user = getUser();
    return !!(user && Array.isArray(user.permissions) && user.permissions.includes(permission));
  }
  function redirectToLogin() {
    const search = window.location.search || "";
    window.location.replace("login.html" + search);
  }

  async function request(path, options = {}) {
    const token = getToken();
    const headers = new Headers(options.headers || {});
    if (!(options.body instanceof FormData)) headers.set("Content-Type", "application/json");
    if (token) headers.set("Authorization", `Bearer ${token}`);
    const response = await fetch(apiBase() + path, { ...options, headers });
    if (response.status === 401) { clear(); redirectToLogin(); }
    return response;
  }

  async function requestAI(path, options = {}) {
    const token = getToken();
    const headers = new Headers(options.headers || {});
    if (!(options.body instanceof FormData)) headers.set("Content-Type", "application/json");
    if (token) headers.set("Authorization", `Bearer ${token}`);
    return fetch(aiBase() + path, { ...options, headers });
  }

  return { apiBase, aiBase, getToken, getUser, setSession, clear, logout, hasPermission, redirectToLogin, request, requestAI };
})();
