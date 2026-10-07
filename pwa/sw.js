const CACHE = "hlg-v31";
const SHELL = ["./", "index.html", "manifest.webmanifest", "icon.svg", "icon-192.png", "apple-touch-icon.png", "config.js"];

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
  // One slot in the tray (tag), but renotify: without it a new alert that replaces one still sitting in
  // the tray arrives silently -- no sound, no banner -- and reads as "no notifications".
  const shown = self.registration.showNotification(d.title, {
    body: d.body, icon: "icon-192.png", badge: "icon-192.png", tag: "hlg-push", renotify: true, data: { url: d.url },
  });
  // delivery log for the dashboard: when the monitor sent it vs when this phone got it (last 20)
  const logged = d.sent ? caches.open("hlg-meta").then(async (c) => {
    let log = [];
    try { log = await (await c.match("push-log")).json(); } catch (_) {}
    log.push({ sent: d.sent, got: Date.now() });
    await c.put("push-log", new Response(JSON.stringify(log.slice(-20)), { headers: { "Content-Type": "application/json" } }));
  }).catch(() => {}) : Promise.resolve();
  e.waitUntil(Promise.all([shown, logged]));
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || "./";
  e.waitUntil(clients.matchAll({ type: "window" }).then((ws) => (ws.length ? ws[0].focus() : clients.openWindow(url))));
});
