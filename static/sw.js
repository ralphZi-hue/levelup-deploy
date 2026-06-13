// FamBank Service Worker – minimaler App-Shell-Cache (Phase 1).
// Bewusst schlank: Netzwerk-zuerst, Cache nur als Fallback für statische Assets.
const CACHE = 'fambank-v2';
const ASSETS = ['/static/style.css', '/static/geo.js', '/static/icon.svg', '/static/manifest.webmanifest'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(ASSETS)));
  self.skipWaiting();
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
  );
  self.clients.claim();
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  // Nur statische Assets cachen; dynamische Seiten immer frisch aus dem Netz.
  if (url.pathname.startsWith('/static/')) {
    e.respondWith(
      fetch(e.request).then((res) => {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(e.request, copy));
        return res;
      }).catch(() => caches.match(e.request))
    );
  }
});
