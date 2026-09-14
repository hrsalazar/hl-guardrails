const CACHE = "hlg-v1";
const SHELL = ["./", "index.html", "manifest.webmanifest", "icon.svg", "icon-192.png", "config.js"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL).catch(() => {})));
  self.skipWaiting();
});
self.addEventListener("activate", (e) => e.waitUntil(clients.claim()));

self.addEventListener("fetch", (e) => {
  const u = new URL(e.request.url);
  if (u.pathname.endsWith(".json")) return; // always network for data
  e.respondWith(caches.match(e.request).then((r) => r || fetch(e.request)));
});

self.addEventListener("push", (e) => {
  let d = { title: "HL guardrails", body: "new alert" };
  try { d = e.data.json(); } catch (_) { d.body = e.data && e.data.text(); }
  e.waitUntil(self.registration.showNotification(d.title, { body: d.body, icon: "icon-192.png", tag: "hlg-push", data: { url: d.url } }));
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || "./";
  e.waitUntil(clients.matchAll({ type: "window" }).then((ws) => (ws.length ? ws[0].focus() : clients.openWindow(url))));
});
