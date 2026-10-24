const V = "keiba-v2";
const SHELL = ["./", "./index.html", "./shutuba.html", "./nav.css", "./nav.js",
               "./manifest.webmanifest", "./icon-180.png", "./icon-512.png"];
self.addEventListener("install", e => {
  e.waitUntil(caches.open(V).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== V).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", e => {
  const r = e.request, u = new URL(r.url);
  if (r.method !== "GET" || u.origin !== location.origin) return;
  const put = res => { if (res && res.ok) { const cp = res.clone(); caches.open(V).then(c => c.put(r, cp)); } return res; };
  if (u.pathname.endsWith(".json")) {            // データ: ネット優先、失敗時は保存済みを表示
    e.respondWith(fetch(r).then(put).catch(() => caches.match(r)));
  } else {                                        // 画面・CSS・JS: 保存済みを即表示しつつ裏で更新
    e.respondWith(caches.match(r).then(hit => {
      const net = fetch(r).then(put).catch(() => hit);
      return hit || net;
    }));
  }
});
