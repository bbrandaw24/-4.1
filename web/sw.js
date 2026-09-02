/*
 * 智慧农业 PWA Service Worker
 * 策略：静态资源网络优先 + 离线回退；/api /ai 等数据请求永不缓存。
 * 每次发版请把 CACHE 版本号 +1（如 v1 -> v2），否则用户端不更新缓存。
 */
const CACHE = 'smartagri-v17';

const APP_SHELL = [
  './',
  './index.html',
  './login.html',
  './theme.css',
  './enhance.js',
  './styles.css',
  './day08.css',
  './auth.css',
  './agent.css',
  './kitten.css',
  './sensor.css',
  './day13.css',
  './trace.html',
  './app.js',
  './auth.js',
  './icons.js',
  './three.min.js',
  './agent.js',
  './kitten.js',
  './login.js',
  './manifest.json',
  './icon-192.png',
  './icon-512.png',
  './icon-maskable-512.png'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE)
      .then((cache) => cache.addAll(APP_SHELL))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return; // 跨域请求不接管

  // 数据接口：永不缓存，直走网络
  if (/^\/(api|ai|healthz|mqtt)(\/|$)/.test(url.pathname)) return;

  // 静态资源：网络优先，失败回退缓存（弱网/离线可用）
  event.respondWith(
    fetch(req)
      .then((res) => {
        if (res && res.status === 200 && (res.type === 'basic' || res.type === 'default')) {
          const clone = res.clone();
          caches.open(CACHE).then((cache) => cache.put(req, clone));
        }
        return res;
      })
      .catch(() =>
        caches.match(req).then((hit) => hit || caches.match('./index.html'))
      )
  );
});
