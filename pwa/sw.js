const CACHE = "hlg-v7";
const SHELL = ["./", "index.html", "manifest.webmanifest", "icon.svg", "icon-192.png", "config.js"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL).catch(() => {})));
  self.skipWaiting();
});
self.addEventListener("activate", (e) =>
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => clients.claim())),
);

self.addEventListener("fetch", (e) => {
  const u = new URL(e.request.url);
  // Third-party APIs (the OKX liquidation fallback) must never be cached: an opaque response
  // re-served offline would show a stale liquidation snapshot as if it were current.
  if (u.origin !== location.origin) return;
  if (u.pathname.endsWith(".json")) return; // always network for data
  // network-first so UI updates land immediately; cache is only an offline fallback
  e.respondWith(
    fetch(e.request)
      .then((r) => {
        if (r.ok && e.request.method === "GET") caches.open(CACHE).then((c) => c.put(e.request, r.clone()));
        return r;
      })
      .catch(() => caches.match(e.request)),
  );
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
